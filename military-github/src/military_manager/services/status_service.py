"""Service for managing daily status grid."""

from __future__ import annotations

from datetime import date, timedelta
from collections import defaultdict
from sqlalchemy import select, func, and_, delete
from sqlalchemy.orm import Session

from military_manager.database import (
    get_session, DailyStatus, PeriodSoldier, Soldier, StatusOption,
)
from military_manager.logger import log_action
from military_manager.config import IRRELEVANT_UNIT


def set_status(period_id: int, soldier_id: int, day: date, status: str,
               updated_by: str | None = None, notes: str | None = None) -> DailyStatus:
    """Set or update a soldier's status for a specific date."""
    with get_session() as session:
        stmt = select(DailyStatus).where(
            DailyStatus.period_id == period_id,
            DailyStatus.soldier_id == soldier_id,
            DailyStatus.date == day,
        )
        existing = session.execute(stmt).scalar_one_or_none()
        if existing:
            existing.status = status
            existing.updated_by = updated_by
            if notes is not None:
                existing.notes = notes
            session.commit()
            session.refresh(existing)
            return existing
        else:
            ds = DailyStatus(
                period_id=period_id,
                soldier_id=soldier_id,
                date=day,
                status=status,
                updated_by=updated_by,
                notes=notes,
            )
            session.add(ds)
            session.commit()
            session.refresh(ds)
            return ds


def bulk_set_status(period_id: int, soldier_ids: list[int], day: date,
                    status: str, updated_by: str | None = None) -> int:
    """Set same status for multiple soldiers on a single date.

    Returns count of updates.
    """
    count = 0
    with get_session() as session:
        for soldier_id in soldier_ids:
            stmt = select(DailyStatus).where(
                DailyStatus.period_id == period_id,
                DailyStatus.soldier_id == soldier_id,
                DailyStatus.date == day,
            )
            existing = session.execute(stmt).scalar_one_or_none()
            if existing:
                if existing.status != status:
                    existing.status = status
                    existing.updated_by = updated_by
                    count += 1
            else:
                ds = DailyStatus(
                    period_id=period_id,
                    soldier_id=soldier_id,
                    date=day,
                    status=status,
                    updated_by=updated_by,
                )
                session.add(ds)
                count += 1
        session.commit()
    log_action("bulk_status_update", {"period_id": period_id, "date": str(day), "count": count})
    return count


def bulk_clear_status(period_id: int, soldier_ids: list[int], day: date) -> int:
    """Remove status records for multiple soldiers on a single date.

    Returns count of deleted records.
    """
    count = 0
    with get_session() as session:
        for soldier_id in soldier_ids:
            stmt = select(DailyStatus).where(
                DailyStatus.period_id == period_id,
                DailyStatus.soldier_id == soldier_id,
                DailyStatus.date == day,
            )
            existing = session.execute(stmt).scalar_one_or_none()
            if existing:
                session.delete(existing)
                count += 1
        session.commit()
    log_action("bulk_status_clear", {"period_id": period_id, "date": str(day), "count": count})
    return count


def get_daily_status_grid(period_id: int, start_date: date | None = None,
                          end_date: date | None = None,
                          sub_unit: str | None = None) -> dict:
    """Get the full status grid for a period.

    Returns:
        {
            "dates": [date1, date2, ...],
            "soldiers": [
                {
                    "soldier_id": 1,
                    "full_name": "...",
                    "sub_unit": "...",
                    "role": "...",
                    "statuses": {"2026-07-13": "בבסיס", "2026-07-14": "חופש", ...}
                },
                ...
            ],
            "summary": {
                "2026-07-13": {"בבסיס": 30, "חופש": 10, ...},
                ...
            }
        }
    """
    with get_session() as session:
        # Get soldiers for this period
        soldier_stmt = (
            select(PeriodSoldier, Soldier)
            .join(Soldier, PeriodSoldier.soldier_id == Soldier.id)
            .where(
                PeriodSoldier.period_id == period_id,
                PeriodSoldier.is_active == True,
                PeriodSoldier.sub_unit != IRRELEVANT_UNIT,
            )
        )
        if sub_unit:
            soldier_stmt = soldier_stmt.where(PeriodSoldier.sub_unit == sub_unit)
        soldier_stmt = soldier_stmt.order_by(PeriodSoldier.sub_unit, PeriodSoldier.sort_order, Soldier.last_name)

        soldier_rows = session.execute(soldier_stmt).all()

        # Get all status entries for the period
        status_stmt = select(DailyStatus).where(DailyStatus.period_id == period_id)
        if start_date:
            status_stmt = status_stmt.where(DailyStatus.date >= start_date)
        if end_date:
            status_stmt = status_stmt.where(DailyStatus.date <= end_date)

        status_rows = session.execute(status_stmt).scalars().all()

        # Build lookup: (soldier_id, date) -> status
        status_lookup: dict[tuple[int, date], str] = {}
        notes_lookup: dict[tuple[int, date], str] = {}
        for ds in status_rows:
            status_lookup[(ds.soldier_id, ds.date)] = ds.status
            if ds.notes:
                notes_lookup[(ds.soldier_id, ds.date)] = ds.notes

        # Build result
        soldiers = []
        flat_statuses: dict[str, str] = {}  # "soldier_id_date" -> status
        flat_notes: dict[str, str] = {}     # "soldier_id_date" -> note

        for ps, soldier in soldier_rows:
            soldier_statuses = {}
            for (sid, d), st_val in status_lookup.items():
                if sid == soldier.id:
                    soldier_statuses[d.isoformat()] = st_val
                    flat_statuses[f"{soldier.id}_{d.isoformat()}"] = st_val
            # Gather notes too
            soldier_notes = {}
            for (sid, d), note_val in notes_lookup.items():
                if sid == soldier.id:
                    soldier_notes[d.isoformat()] = note_val
                    flat_notes[f"{soldier.id}_{d.isoformat()}"] = note_val
            soldiers.append({
                "soldier_id": soldier.id,
                "military_id": soldier.military_id,
                "full_name": f"{soldier.first_name} {soldier.last_name}",
                "sub_unit": ps.sub_unit,
                "role": ps.role,
                "task_role": ps.task_role,
                "rank": ps.rank,
                "statuses": soldier_statuses,
                "notes": soldier_notes,
            })

        # Collect all dates
        all_dates = sorted(set(d for _, d in status_lookup.keys()))

        # Build daily summary
        summary: dict[str, dict[str, int]] = {}
        for d in all_dates:
            day_statuses: dict[str, int] = defaultdict(int)
            for (sid, sd), st in status_lookup.items():
                if sd == d:
                    day_statuses[st] += 1
            summary[d.isoformat()] = dict(day_statuses)

        return {
            "dates": all_dates,
            "soldiers": soldiers,
            "statuses": flat_statuses,
            "notes": flat_notes,
            "summary": summary,
        }


def set_status_notes(period_id: int, soldier_id: int, day: date,
                     notes: str) -> bool:
    """Set or update just the notes on a status record.

    If no status record exists yet, creates one with an empty status so
    the note is still persisted.
    Returns True on success.
    """
    with get_session() as session:
        stmt = select(DailyStatus).where(
            DailyStatus.period_id == period_id,
            DailyStatus.soldier_id == soldier_id,
            DailyStatus.date == day,
        )
        existing = session.execute(stmt).scalar_one_or_none()
        if existing:
            existing.notes = notes or None
        else:
            ds = DailyStatus(
                period_id=period_id,
                soldier_id=soldier_id,
                date=day,
                status="",
                notes=notes or None,
            )
            session.add(ds)
        session.commit()
        return True


def get_daily_counts(period_id: int, day: date) -> dict[str, int]:
    """Get status counts for a single day (only active soldiers, excluding לא רלוונטי unit)."""
    with get_session() as session:
        stmt = (
            select(DailyStatus.status, func.count())
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
            .group_by(DailyStatus.status)
        )
        results = session.execute(stmt).all()
        return {status: count for status, count in results}


def count_na_soldiers(period_id: int, day: date) -> int:
    """Count soldiers whose effective status is 'לא בשמפ'.

    Checks the selected day first. If a soldier has no status for that day,
    falls back to the most recent prior status in the period. This ensures
    that soldiers marked 'לא בשמפ' on earlier days are still excluded
    even if they have no entry for today.
    """
    NA_STATUSES = {"לא בשמפ", 'לא בשמ"פ'}
    with get_session() as session:
        # Get all active soldiers (excluding irrelevant unit)
        soldiers_stmt = (
            select(PeriodSoldier.soldier_id)
            .where(
                PeriodSoldier.period_id == period_id,
                PeriodSoldier.is_active == True,
                PeriodSoldier.sub_unit != IRRELEVANT_UNIT,
            )
        )
        soldier_ids = [r[0] for r in session.execute(soldiers_stmt).all()]

        if not soldier_ids:
            return 0

        # Get all statuses up to and including `day` for these soldiers
        status_stmt = (
            select(DailyStatus.soldier_id, DailyStatus.date, DailyStatus.status)
            .where(
                DailyStatus.period_id == period_id,
                DailyStatus.soldier_id.in_(soldier_ids),
                DailyStatus.date <= day,
            )
            .order_by(DailyStatus.soldier_id, DailyStatus.date.desc())
        )
        rows = session.execute(status_stmt).all()

        # For each soldier, take the most recent status (up to `day`)
        latest: dict[int, str] = {}
        for sid, d, status in rows:
            if sid not in latest:
                latest[sid] = status

        return sum(1 for s in latest.values() if s in NA_STATUSES)


def get_soldier_status_history(period_id: int, soldier_id: int) -> list[dict]:
    """Get status history for a soldier in a period."""
    with get_session() as session:
        stmt = (
            select(DailyStatus)
            .where(
                DailyStatus.period_id == period_id,
                DailyStatus.soldier_id == soldier_id,
            )
            .order_by(DailyStatus.date)
        )
        results = session.execute(stmt).scalars().all()
        return [
            {
                "date": ds.date,
                "status": ds.status,
                "updated_by": ds.updated_by,
                "updated_at": ds.updated_at,
            }
            for ds in results
        ]


def calculate_leave_stats(period_id: int,
                          start_date: date | None = None,
                          end_date: date | None = None,
                          sub_unit: str | None = None) -> list[dict]:
    """Calculate leave statistics per soldier."""
    with get_session() as session:
        # Get all period soldiers
        soldier_stmt = (
            select(PeriodSoldier, Soldier)
            .join(Soldier, PeriodSoldier.soldier_id == Soldier.id)
            .where(
                PeriodSoldier.period_id == period_id,
                PeriodSoldier.is_active == True,
                PeriodSoldier.sub_unit != IRRELEVANT_UNIT,
            )
        )
        if sub_unit:
            soldier_stmt = soldier_stmt.where(PeriodSoldier.sub_unit == sub_unit)
        soldier_rows = session.execute(soldier_stmt).all()

        AWAY_STATUSES = {"חופש", "יוצא לחופש", "גימלים"}
        PITZUL_STATUSES = {"פיצול", "יוצא לפיצול"}
        PRESENT_STATUSES = {"בבסיס", "התייצב", "רספ/סרספ", "סמבצים", "סוואנה"}

        stats = []
        for ps, soldier in soldier_rows:
            status_stmt = (
                select(DailyStatus)
                .where(
                    DailyStatus.period_id == period_id,
                    DailyStatus.soldier_id == soldier.id,
                )
            )
            if start_date:
                status_stmt = status_stmt.where(DailyStatus.date >= start_date)
            if end_date:
                status_stmt = status_stmt.where(DailyStatus.date <= end_date)

            statuses = session.execute(status_stmt).scalars().all()

            days_total = len(statuses)
            days_leave = sum(1 for s in statuses if s.status in AWAY_STATUSES)
            days_pitzul = sum(1 for s in statuses if s.status in PITZUL_STATUSES)
            days_present = sum(1 for s in statuses if s.status in PRESENT_STATUSES)
            # Actual reserve days = total minus פיצול (פיצול is not reserve service)
            days_reserve = days_total - days_pitzul
            # Leave percentage is relative to actual reserve days, not total
            leave_pct = round(days_leave / days_reserve * 100, 1) if days_reserve > 0 else 0

            stats.append({
                "soldier_id": soldier.id,
                "full_name": f"{soldier.first_name} {soldier.last_name}",
                "sub_unit": ps.sub_unit,
                "days_total": days_total,
                "days_reserve": days_reserve,
                "days_pitzul": days_pitzul,
                "days_leave": days_leave,
                "days_present": days_present,
                "leave_pct": leave_pct,
            })

        return sorted(stats, key=lambda x: x["leave_pct"], reverse=True)
