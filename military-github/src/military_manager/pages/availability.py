"""Availability Report — דו"ח זמינות למ"מ

Helps squad leaders (מ"מים) build manual shift schedules by showing:
- Which soldiers are available on each day in a selected date range
- Who is arriving/departing and when
- Shift availability based on constraints (morning/afternoon/night)
- Soldiers on leave hidden by default (expandable)
- Excludes irrelevant soldiers (חפ"ק, רס"פ, staff roles)
"""

from __future__ import annotations

from datetime import date, timedelta
from collections import defaultdict

import streamlit as st

from military_manager.components.navigation import render_page_header
from military_manager.components.filters import period_guard, sub_unit_filter
from military_manager.services.soldier_service import get_period_soldiers
from military_manager.services.status_service import get_daily_status_grid
from military_manager.services.constraint_service import (
    get_period_constraints,
    get_blocked_shifts,
    get_task_restrictions_for_date,
    SHIFT_LABEL_HEB,
)

# ── Statuses ──
PRESENT_STATUSES = {"בבסיס", "התייצב", "חוזר מחופש", "סיפוח מאוחר", "צפוי להתייצב"}
HOME_STATUSES = {"חופש", "יוצא לחופש", "יוצא לפיצול", "פיצול", "גימלים"}
ARRIVING_STATUSES = {"חוזר מחופש", "צפוי להתייצב", "סיפוח מאוחר"}
DEPARTING_STATUSES = {"יוצא לחופש", "יוצא לפיצול", "משתחרר"}

HEB_DAYS_FULL = {
    0: "שני",
    1: "שלישי",
    2: "רביעי",
    3: "חמישי",
    4: "שישי",
    5: "שבת",
    6: "ראשון",
}

SHIFT_NAMES = {1: "בוקר (06-14)", 2: "צהריים (14-22)", 3: "לילה (22-06)"}

# ── Roles to exclude from the report ──
EXCLUDE_ROLES_EXACT = {"רס\"פ", "סרס\"פ", "מ\"פ", "סמ\"פ"}
EXCLUDE_ROLE_PREFIXES = ("ע.מ\"פ",)


def _should_exclude_soldier(role: str, assignment_notes: str) -> bool:
    """Check if a soldier should be excluded from the availability report."""
    if not role:
        return False
    # Exact role matches
    if role in EXCLUDE_ROLES_EXACT:
        return True
    # Prefix matches (ע.מ"פ...)
    for prefix in EXCLUDE_ROLE_PREFIXES:
        if role.startswith(prefix):
            return True
    # חפקון roles (driver/comms/medic for חפ"ק)
    if "חפקון" in role:
        return True
    # Assignment notes: "לא לשבץ"
    if assignment_notes and "לא לשבץ" in assignment_notes:
        return True
    return False


def _get_shift_availability(period_id: int, soldier_id: int, day: date) -> dict[int, bool]:
    """Determine which shifts are available for a soldier on a given day.

    Uses the authoritative get_blocked_shifts from constraint_service
    which properly handles multi-day constraints.

    Returns {1: True/False, 2: True/False, 3: True/False}
    """
    blocked = get_blocked_shifts(period_id, soldier_id, day, shifts_per_day=3)
    return {1: 1 not in blocked, 2: 2 not in blocked, 3: 3 not in blocked}


def _status_for_day(statuses: dict, day: date) -> str:
    """Get the status string for a specific day."""
    return statuses.get(day.isoformat(), "")


def render():
    render_page_header(
        "📋 דו\"ח זמינות",
        "דו\"ח זמינות חיילים לבניית שבצ\"ק ידני — מי זמין, מתי ובאיזה משמרות",
    )

    period = period_guard()
    if not period:
        return

    pid = period["id"]
    p_start = date.fromisoformat(str(period["start_date"]))
    p_end = date.fromisoformat(str(period["end_date"]))

    # ── Date range selector ──
    st.markdown("### 📅 בחירת טווח תאריכים")
    col1, col2 = st.columns(2)
    today = date.today()
    default_start = max(today, p_start)
    default_end = min(default_start + timedelta(days=2), p_end)

    with col1:
        start_date = st.date_input(
            "מתאריך",
            value=default_start,
            min_value=p_start,
            max_value=p_end,
            key="avail_start",
        )
    with col2:
        end_date = st.date_input(
            "עד תאריך",
            value=default_end,
            min_value=p_start,
            max_value=p_end,
            key="avail_end",
        )

    if start_date > end_date:
        st.error("תאריך ההתחלה חייב להיות לפני תאריך הסיום")
        return

    # ── Sub-unit filter ──
    selected_unit = sub_unit_filter(pid, key="avail_sub_unit")

    # ── Load data ──
    all_soldiers = get_period_soldiers(pid, exclude_irrelevant_unit=True)
    grid = get_daily_status_grid(pid, start_date, end_date)
    all_constraints = get_period_constraints(pid)

    # Build constraints lookup: soldier_id -> [constraint_dicts]
    constraint_map: dict[int, list[dict]] = defaultdict(list)
    for c in all_constraints:
        constraint_map[c["soldier_id"]].append(c)

    # Build status lookup from grid
    status_map: dict[int, dict] = {}
    for gs in grid["soldiers"]:
        status_map[gs["soldier_id"]] = gs.get("statuses", {})

    # Generate date list
    dates = []
    d = start_date
    while d <= end_date:
        dates.append(d)
        d += timedelta(days=1)

    # ── Filter and classify soldiers ──
    available_soldiers = []  # Soldiers that can go on missions
    home_soldiers = []  # Soldiers on leave (hidden by default)

    for sol in all_soldiers:
        sid = sol["soldier_id"]
        role = (sol.get("role") or "").strip()
        assignment_notes = (sol.get("assignment_notes") or "").strip()

        # Filter by sub-unit
        if selected_unit and sol.get("sub_unit") != selected_unit:
            continue

        # Exclude irrelevant soldiers
        if _should_exclude_soldier(role, assignment_notes):
            continue

        # Check if soldier is on leave for ALL days in range
        statuses = status_map.get(sid, {})
        all_home = True
        for day in dates:
            st_val = _status_for_day(statuses, day)
            if st_val not in HOME_STATUSES or st_val == "":
                all_home = False
                break

        if all_home and len(dates) > 0:
            home_soldiers.append(sol)
        else:
            available_soldiers.append(sol)

    # Count how many soldiers have NO status at all in the date range
    no_status_count = 0
    for sol in available_soldiers:
        sid = sol["soldier_id"]
        statuses = status_map.get(sid, {})
        has_any = any(_status_for_day(statuses, d) for d in dates)
        if not has_any:
            no_status_count += 1

    # ── Render report ──
    st.markdown("---")

    if not available_soldiers and not home_soldiers:
        st.info("אין חיילים להצגה בטווח הנבחר")
        return

    total_available = len(available_soldiers)
    total_home = len(home_soldiers)
    confirmed_count = total_available - no_status_count
    summary_parts = [f"**{confirmed_count}** חיילים מאושרים"]
    if no_status_count:
        summary_parts.append(f"**{no_status_count}** ❓ ללא עדכון")
    summary_parts.append(f"**{total_home}** בחופש")
    st.markdown("### 📊 סיכום: " + " &nbsp;|&nbsp; ".join(summary_parts))
    if no_status_count:
        st.warning(
            f"⚠️ {no_status_count} חיילים לא עודכנו בדו\"ח 1 לטווח הנבחר — "
            "הם מוצגים כ\"ללא עדכון\" ולא כזמינים."
        )

    # ── Per-day availability ──
    for day in dates:
        day_name = HEB_DAYS_FULL.get(day.weekday(), "")
        day_str = day.strftime("%d/%m")

        st.markdown(f"## 📅 יום {day_name} — {day_str}")

        # Categorize soldiers for this day
        on_base = []  # בבסיס כל היום
        arriving = []  # חוזר מחופש ביום זה
        departing = []  # יוצא הביתה ביום זה
        day_home = []  # בחופש ביום זה

        for sol in available_soldiers:
            sid = sol["soldier_id"]
            statuses = status_map.get(sid, {})
            st_val = _status_for_day(statuses, day)
            sol_constraints = constraint_map.get(sid, [])
            shifts = _get_shift_availability(pid, sid, day)

            entry = {
                **sol,
                "status": st_val,
                "shifts": shifts,
                "constraints": sol_constraints,
            }

            # Check constraints for departure/arrival on this specific day
            # Handles multi-day constraints properly:
            #   departure: first day = departing, intermediate/end = away
            #   arrival: last day = arriving, earlier days = away
            is_departing_today = False
            is_arriving_today = False
            is_departing_tomorrow = False
            is_away_today = False
            dep_time = ""
            arr_time = ""
            dep_tomorrow_time = ""

            tomorrow = day + timedelta(days=1)

            for c in sol_constraints:
                c_type = c.get("constraint_type", "")
                c_date = c.get("constraint_date")
                c_end = c.get("end_date")
                if c_date and isinstance(c_date, str):
                    c_date = date.fromisoformat(c_date)
                if c_end and isinstance(c_end, str):
                    c_end = date.fromisoformat(c_end)
                actual_end = c_end or c_date

                if c_type == "departure":
                    if c_date == day:
                        # First day of departure
                        is_departing_today = True
                        dep_time = c.get("constraint_time", "")
                    elif c_date and c_date < day <= actual_end:
                        # Intermediate/last days — soldier is away
                        is_away_today = True
                    # Departure-eve: departing tomorrow (or multi-day starts tomorrow)
                    if c_date == tomorrow:
                        is_departing_tomorrow = True
                        dep_tomorrow_time = c.get("constraint_time", "")

                elif c_type == "arrival":
                    if c_date and c_end and c_date != c_end:
                        # Multi-day arrival: last day = arriving, earlier = away
                        if c_date <= day < c_end:
                            is_away_today = True
                        elif day == c_end:
                            is_arriving_today = True
                            arr_time = c.get("constraint_time", "")
                    elif c_date == day:
                        # Single-day arrival
                        is_arriving_today = True
                        arr_time = c.get("constraint_time", "")

                elif c_type == "unavailable":
                    if c_date and c_date <= day <= actual_end:
                        is_away_today = True

            # Check for task-restriction type constraints active today
            task_restriction_badges = []
            for c in sol_constraints:
                c_type = c.get("constraint_type", "")
                c_date = c.get("constraint_date")
                c_end = c.get("end_date")
                if c_date and isinstance(c_date, str):
                    c_date = date.fromisoformat(c_date)
                if c_end and isinstance(c_end, str):
                    c_end = date.fromisoformat(c_end)
                actual_end = c_end or c_date
                if not c_date or not (c_date <= day <= actual_end):
                    continue
                if c_type == "duty_only":
                    task_restriction_badges.append("🛡️ תורן בלבד")
                elif c_type == "medical":
                    reason = c.get("custom_reason") or "הגבלה רפואית"
                    task_restriction_badges.append(f"🏥 {reason}")
                elif c_type == "custom":
                    reason = c.get("custom_reason") or "אילוץ מותאם"
                    task_restriction_badges.append(f"📝 {reason}")

            entry["is_departing"] = is_departing_today
            entry["dep_time"] = dep_time
            entry["is_arriving"] = is_arriving_today
            entry["arr_time"] = arr_time
            entry["is_departing_tomorrow"] = is_departing_tomorrow
            entry["dep_tomorrow_time"] = dep_tomorrow_time
            entry["is_away"] = is_away_today
            entry["task_restriction_badges"] = task_restriction_badges

            # Categorize
            if is_away_today or st_val in HOME_STATUSES:
                day_home.append(entry)
            elif is_arriving_today or st_val in ARRIVING_STATUSES:
                arriving.append(entry)
            elif is_departing_today or st_val in DEPARTING_STATUSES:
                departing.append(entry)
            else:
                on_base.append(entry)

        # Split on_base into confirmed vs unknown
        confirmed_on_base = [e for e in on_base if e["status"] in PRESENT_STATUSES]
        unknown_soldiers = [e for e in on_base if e["status"] not in PRESENT_STATUSES]

        # ── Confirmed on-base soldiers ──
        if confirmed_on_base:
            st.markdown(f"#### ✅ בבסיס ({len(confirmed_on_base)})")
            _render_soldier_table(confirmed_on_base, day)

        # ── Unknown status soldiers ──
        if unknown_soldiers:
            st.markdown(f"#### ❓ ללא עדכון ({len(unknown_soldiers)})")
            st.caption("חיילים שלא הוזן להם סטטוס בדו\"ח 1 — לא ניתן לדעת אם הם זמינים")
            _render_soldier_table(unknown_soldiers, day, unknown=True)

        # ── Arriving soldiers ──
        if arriving:
            st.markdown(f"#### 🔵 חוזרים מהבית ({len(arriving)})")
            for entry in arriving:
                arr_time = entry.get("arr_time", "")
                shifts = entry["shifts"]
                avail_shifts = _format_available_shifts(shifts)
                name = entry["full_name"]
                role = entry.get("role", "")
                if arr_time:
                    time_heb = _time_to_hebrew(arr_time)
                    arrival_text = f"חוזר ב{time_heb}"
                else:
                    arrival_text = "עתיד לחזור היום מהבית"
                st.markdown(
                    f"- **{name}** ({role}) — {arrival_text}"
                    f" &nbsp;→&nbsp; זמין ל: {avail_shifts}"
                )

        # ── Departing soldiers ──
        if departing:
            st.markdown(f"#### 🔴 יוצאים הביתה ({len(departing)})")
            for entry in departing:
                dep_time = entry.get("dep_time", "")
                shifts = entry["shifts"]
                avail_shifts = _format_available_shifts(shifts)
                name = entry["full_name"]
                role = entry.get("role", "")
                if dep_time:
                    time_heb = _time_to_hebrew(dep_time)
                    dep_text = f"יוצא ב{time_heb}"
                else:
                    dep_text = "יוצא היום הביתה"
                st.markdown(
                    f"- **{name}** ({role}) — {dep_text}"
                    f" &nbsp;→&nbsp; זמין ל: {avail_shifts}"
                )

        # ── Day-specific home soldiers ──
        if day_home:
            with st.expander(f"🏠 בחופש ביום זה ({len(day_home)})", expanded=False):
                for entry in day_home:
                    name = entry["full_name"]
                    role = entry.get("role", "")
                    st.markdown(f"- {name} ({role})")

        st.markdown("---")

    # ── Soldiers on leave for entire range ──
    if home_soldiers:
        show_home = st.checkbox(
            f"🏠 הצג חיילים בחופש לכל התקופה ({len(home_soldiers)})",
            value=False,
            key="show_full_home",
        )
        if show_home:
            for sol in home_soldiers:
                name = sol["full_name"]
                role = sol.get("role", "")
                st.markdown(f"- {name} ({role})")


def _render_soldier_table(soldiers: list[dict], day: date, *, unknown: bool = False):
    """Render a compact table of available soldiers with shift availability."""
    rows = []
    for entry in soldiers:
        shifts = entry.get("shifts", {1: True, 2: True, 3: True})
        if unknown:
            morning = "❓"
            afternoon = "❓"
            night = "❓"
        else:
            morning = "✅" if shifts.get(1, True) else "❌"
            afternoon = "✅" if shifts.get(2, True) else "❌"
            night = "✅" if shifts.get(3, True) else "❌"
        role = entry.get("role", "") or ""
        task_role = entry.get("task_role", "") or ""
        display_role = task_role if task_role else role
        note = ""
        if entry.get("is_departing"):
            dep_time = entry.get("dep_time", "")
            note = f"יוצא ב{_time_to_hebrew(dep_time)}" if dep_time else "יוצא היום הביתה"
        elif entry.get("is_arriving"):
            arr_time = entry.get("arr_time", "")
            note = f"חוזר ב{_time_to_hebrew(arr_time)}" if arr_time else "עתיד לחזור היום מהבית"
        elif entry.get("is_departing_tomorrow"):
            dep_t = entry.get("dep_tomorrow_time", "")
            if dep_t:
                note = f"⚠️ יוצא מחר ב{_time_to_hebrew(dep_t)}"
            else:
                note = "⚠️ יוצא מחר הביתה"

        a_notes = (entry.get("assignment_notes") or "").strip()
        if a_notes:
            if note:
                note += " | " + a_notes
            else:
                note = a_notes

        # Add task restriction badges (duty_only / medical / custom)
        for badge in entry.get("task_restriction_badges", []):
            if note:
                note += " | " + badge
            else:
                note = badge

        rows.append({
            "שם": entry["full_name"],
            "תפקיד": display_role,
            "בוקר": morning,
            "צהריים": afternoon,
            "לילה": night,
            "הערות": note,
        })

    if rows:
        # Use st.dataframe for a clean table
        import pandas as pd
        df = pd.DataFrame(rows)
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "שם": st.column_config.TextColumn(width="medium"),
                "תפקיד": st.column_config.TextColumn(width="medium"),
                "בוקר": st.column_config.TextColumn(width="small"),
                "צהריים": st.column_config.TextColumn(width="small"),
                "לילה": st.column_config.TextColumn(width="small"),
                "הערות": st.column_config.TextColumn(width="large"),
            },
        )


def _time_to_hebrew(time_str: str) -> str:
    """Convert constraint time to Hebrew."""
    mapping = {
        "morning": "בוקר",
        "afternoon": "צהריים",
        "night": "לילה",
        "all_day": "כל היום",
    }
    return mapping.get(time_str, time_str or "")


def _format_available_shifts(shifts: dict[int, bool]) -> str:
    """Format available shifts as a Hebrew string."""
    names = {1: "בוקר", 2: "צהריים", 3: "לילה"}
    available = [names[s] for s in sorted(shifts) if shifts[s]]
    if not available:
        return "❌ לא זמין"
    if len(available) == 3:
        return "✅ כל המשמרות"
    return " | ".join(f"✅ {a}" for a in available)
