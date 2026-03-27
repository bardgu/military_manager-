"""Service for status group calculations, dynamic stats, and app settings."""

from __future__ import annotations

import json
from datetime import date, timedelta
from collections import defaultdict

from sqlalchemy import select, func

from military_manager.database import (
    get_session, StatusGroup, AppSetting, DailyStatus, PeriodSoldier, Soldier,
)
from military_manager.config import IRRELEVANT_UNIT


# ─── Default status groups ────────────────────────────────────

DEFAULT_STATUS_GROUPS = [
    {
        "name": "בשמ\"פ",
        "statuses": [
            "בבסיס", "התייצב", "חוזר מחופש", "יוצא לחופש",
            "חופש", "יוצא לפיצול", "פיצול", "נפקד",
            "רספ/סרספ", "סמבצים", "סוואנה", "משתחרר",
            "צפוי להתייצב", "סיפוח מאוחר", "יוצא לקורס",
            "גימלים",
        ],
        "color": "#1976D2",
    },
    {
        "name": "בבסיס",
        "statuses": [
            "בבסיס", "התייצב", "חוזר מחופש",
            "רספ/סרספ", "סמבצים", "סוואנה",
        ],
        "color": "#4CAF50",
    },
    {
        "name": "בחופש",
        "statuses": ["חופש", "יוצא לחופש", "פיצול", "יוצא לפיצול"],
        "color": "#2196F3",
    },
    {
        "name": "בדרכים",
        "statuses": [
            "חוזר מחופש", "יוצא לחופש", "יוצא לפיצול",
            "צפוי להתייצב", "סיפוח מאוחר",
        ],
        "color": "#FF9800",
    },
]


# ─── Status Groups CRUD ───────────────────────────────────────

def get_status_groups(period_id: int) -> list[dict]:
    """Get all status groups for a period."""
    with get_session() as session:
        groups = session.execute(
            select(StatusGroup)
            .where(StatusGroup.period_id == period_id)
            .order_by(StatusGroup.sort_order)
        ).scalars().all()
        return [
            {
                "id": g.id,
                "name": g.name,
                "statuses": json.loads(g.statuses_json) if g.statuses_json else [],
                "color": g.color,
                "sort_order": g.sort_order,
            }
            for g in groups
        ]


def save_status_group(period_id: int, name: str, statuses: list[str],
                      color: str = "#9E9E9E") -> StatusGroup:
    """Create or update a status group."""
    with get_session() as session:
        existing = session.execute(
            select(StatusGroup).where(
                StatusGroup.period_id == period_id,
                StatusGroup.name == name,
            )
        ).scalar_one_or_none()

        if existing:
            existing.statuses_json = json.dumps(statuses, ensure_ascii=False)
            existing.color = color
        else:
            max_order = session.execute(
                select(func.max(StatusGroup.sort_order))
                .where(StatusGroup.period_id == period_id)
            ).scalar() or 0
            existing = StatusGroup(
                period_id=period_id,
                name=name,
                statuses_json=json.dumps(statuses, ensure_ascii=False),
                color=color,
                sort_order=max_order + 1,
            )
            session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing


def delete_status_group(group_id: int) -> bool:
    """Delete a status group."""
    with get_session() as session:
        g = session.get(StatusGroup, group_id)
        if g:
            session.delete(g)
            session.commit()
            return True
        return False


def init_default_groups(period_id: int) -> None:
    """Initialize default status groups if none exist, and ensure
    existing groups have all required statuses (migration)."""
    existing = get_status_groups(period_id)
    if not existing:
        for grp in DEFAULT_STATUS_GROUPS:
            save_status_group(period_id, grp["name"], grp["statuses"], grp.get("color", "#9E9E9E"))
        return

    # ── Migration: ensure בשמ"פ group contains גימלים ──
    for grp in existing:
        if grp["name"] == 'בשמ"פ':
            current_statuses = grp["statuses"]
            if "גימלים" not in current_statuses:
                updated = current_statuses + ["גימלים"]
                save_status_group(period_id, grp["name"], updated, grp.get("color", "#1976D2"))
            break


# ─── App Settings ─────────────────────────────────────────────

def get_setting(period_id: int, key: str, default: str = "") -> str:
    """Get a setting value."""
    with get_session() as session:
        setting = session.execute(
            select(AppSetting).where(
                AppSetting.period_id == period_id,
                AppSetting.key == key,
            )
        ).scalar_one_or_none()
        return setting.value if setting else default


def set_setting(period_id: int, key: str, value: str) -> None:
    """Set a setting value."""
    with get_session() as session:
        existing = session.execute(
            select(AppSetting).where(
                AppSetting.period_id == period_id,
                AppSetting.key == key,
            )
        ).scalar_one_or_none()
        if existing:
            existing.value = value
        else:
            session.add(AppSetting(period_id=period_id, key=key, value=value))
        session.commit()


# ─── Calculations ─────────────────────────────────────────────

def get_group_counts(period_id: int, day: date) -> dict[str, int]:
    """Calculate counts for each status group on a given day.

    Returns: {"בבסיס": 25, "בחופש": 10, "בשמ\"פ": 40, ...}

    Soldiers with גימלים status or marked is_irrelevant are excluded from
    all groups unless their status explicitly appears in a group.
    """
    groups = get_status_groups(period_id)
    if not groups:
        init_default_groups(period_id)
        groups = get_status_groups(period_id)

    # Get all statuses for the day
    with get_session() as session:
        rows = session.execute(
            select(DailyStatus.soldier_id, DailyStatus.status)
            .join(
                PeriodSoldier,
                (PeriodSoldier.period_id == DailyStatus.period_id)
                & (PeriodSoldier.soldier_id == DailyStatus.soldier_id),
            )
            .where(
                DailyStatus.period_id == period_id,
                DailyStatus.date == day,
                PeriodSoldier.is_active == True,
                PeriodSoldier.sub_unit != IRRELEVANT_UNIT,
            )
        ).all()

        # Get irrelevant soldiers
        irr = session.execute(
            select(PeriodSoldier.soldier_id)
            .where(
                PeriodSoldier.period_id == period_id,
                PeriodSoldier.is_irrelevant == True,
                PeriodSoldier.is_active == True,
            )
        ).scalars().all()
        irrelevant_ids = set(irr)

    # Map soldier_id -> status
    soldier_status = {sid: status for sid, status in rows}

    result = {}
    for grp in groups:
        grp_statuses = set(grp["statuses"])
        count = 0
        for sid, status in soldier_status.items():
            if sid in irrelevant_ids:
                continue
            if status in grp_statuses:
                count += 1
        result[grp["name"]] = count

    return result


def get_total_relevant_soldiers(period_id: int) -> int:
    """Get total number of active, relevant (non-irrelevant, non-גימלים) soldiers.
    Also excludes soldiers in the 'לא רלוונטי' sub-unit."""
    with get_session() as session:
        return session.execute(
            select(func.count())
            .where(
                PeriodSoldier.period_id == period_id,
                PeriodSoldier.is_active == True,
                PeriodSoldier.is_irrelevant == False,
                PeriodSoldier.sub_unit != IRRELEVANT_UNIT,
            )
        ).scalar() or 0


def get_irrelevant_soldiers(period_id: int) -> list[dict]:
    """Get list of soldiers marked as irrelevant."""
    with get_session() as session:
        rows = session.execute(
            select(PeriodSoldier, Soldier)
            .join(Soldier, PeriodSoldier.soldier_id == Soldier.id)
            .where(
                PeriodSoldier.period_id == period_id,
                PeriodSoldier.is_irrelevant == True,
                PeriodSoldier.is_active == True,
            )
            .order_by(Soldier.last_name)
        ).all()
        return [
            {
                "period_soldier_id": ps.id,
                "soldier_id": ps.soldier_id,
                "name": f"{sol.first_name} {sol.last_name}",
                "sub_unit": ps.sub_unit,
                "role": ps.role,
            }
            for ps, sol in rows
        ]


def set_soldier_irrelevant(period_soldier_id: int, irrelevant: bool) -> None:
    """Mark/unmark a soldier as irrelevant."""
    with get_session() as session:
        ps = session.get(PeriodSoldier, period_soldier_id)
        if ps:
            ps.is_irrelevant = irrelevant
            session.commit()


def compute_percentages(period_id: int, day: date) -> dict:
    """Compute dynamic percentages for status groups.

    Returns dict with:
    - groups: {group_name: {count, percent}}
    - total_in_shmap: count in שמ"פ group
    - total_relevant: total active non-irrelevant soldiers
    - alerts: list of alert messages (e.g. too many at home)
    """
    groups = get_status_groups(period_id)
    if not groups:
        init_default_groups(period_id)
        groups = get_status_groups(period_id)

    group_counts = get_group_counts(period_id, day)
    total_relevant = get_total_relevant_soldiers(period_id)

    # The בשמ"פ group is the denominator for percentage calculations
    shmap_count = group_counts.get("בשמ\"פ", total_relevant)
    if shmap_count == 0:
        shmap_count = total_relevant  # fallback

    result_groups = {}
    for grp in groups:
        cnt = group_counts.get(grp["name"], 0)
        pct = round((cnt / shmap_count * 100), 1) if shmap_count > 0 else 0
        result_groups[grp["name"]] = {
            "count": cnt,
            "percent": pct,
            "color": grp.get("color", "#9E9E9E"),
        }

    # Check alert threshold
    threshold_str = get_setting(period_id, "home_alert_percent", "25")
    try:
        threshold = float(threshold_str)
    except ValueError:
        threshold = 25.0

    alerts = []
    leave_group = result_groups.get("בחופש", {})
    if leave_group.get("percent", 0) > threshold:
        alerts.append({
            "message": f"⚠️ {leave_group['percent']}% מהחיילים בחופש — מעל הסף ({threshold}%)",
            "percent": leave_group["percent"],
            "threshold": threshold,
        })

    return {
        "groups": result_groups,
        "total_in_shmap": shmap_count,
        "total_relevant": total_relevant,
        "alerts": alerts,
    }


def compute_weekly_summary(period_id: int, start_date: date, num_days: int = 7) -> list[dict]:
    """Compute status group counts for each day in a range.

    Returns list of dicts per day with group counts and percentages.
    """
    results = []
    for i in range(num_days):
        day = start_date + timedelta(days=i)
        day_stats = compute_percentages(period_id, day)
        results.append({
            "date": day,
            "stats": day_stats,
        })
    return results
