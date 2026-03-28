"""WhatsApp daily briefing generator — copy-ready text summary for commanders."""

from __future__ import annotations

from datetime import date
from collections import defaultdict

from sqlalchemy import select, and_

from military_manager.database import (
    get_session, DailyStatus, PeriodSoldier, Soldier,
    Task, ShiftAssignment, DutyOfficer, SoldierConstraint,
)
from military_manager.config import IRRELEVANT_UNIT

# ── Constant sets ──
PRESENT_SET = {"בבסיס", "התייצב", "חוזר מחופש"}
NA_SET = {"לא בשמפ", 'לא בשמ"פ'}

# Hebrew day names
_HEB_DAYS = {0: "שני", 1: "שלישי", 2: "רביעי", 3: "חמישי", 4: "שישי", 5: "שבת", 6: "ראשון"}

# Shift labels
_SHIFT_LABELS = {1: "בוקר", 2: "צהריים", 3: "לילה"}


def generate_briefing(period_id: int, day: date, period_name: str = "") -> str:
    """Generate a WhatsApp-ready daily briefing text.

    Combines:
      - Date header
      - Presence summary (present / away / split / leave / unassigned)
      - Who arrived today (חוזר מחופש, התייצב, סיפוח מאוחר)
      - Who is leaving today (יוצא לחופש, יוצא לפיצול, משתחרר)
      - Shift assignments per task
      - Duty officer
      - Active constraints for the day
    """
    heb_day = _HEB_DAYS.get(day.weekday(), "")
    lines: list[str] = []

    # ── Header ──
    lines.append(f"📋 *תדריך יומי — יום {heb_day} {day.strftime('%d/%m/%Y')}*")
    if period_name:
        lines.append(f"📍 {period_name}")
    lines.append("")

    with get_session() as session:
        # ── Load soldiers ──
        ps_stmt = (
            select(PeriodSoldier, Soldier)
            .join(Soldier, PeriodSoldier.soldier_id == Soldier.id)
            .where(
                PeriodSoldier.period_id == period_id,
                PeriodSoldier.is_active == True,
                PeriodSoldier.sub_unit != IRRELEVANT_UNIT,
            )
        )
        ps_rows = session.execute(ps_stmt).all()
        soldier_map = {}  # soldier_id -> full_name
        soldier_unit = {}  # soldier_id -> sub_unit
        for ps, s in ps_rows:
            soldier_map[s.id] = f"{s.first_name} {s.last_name}"
            soldier_unit[s.id] = ps.sub_unit

        total = len(soldier_map)

        # ── Load statuses for today ──
        ds_stmt = (
            select(DailyStatus)
            .where(
                DailyStatus.period_id == period_id,
                DailyStatus.date == day,
                DailyStatus.soldier_id.in_(list(soldier_map.keys())) if soldier_map else DailyStatus.soldier_id == -1,
            )
        )
        statuses = session.execute(ds_stmt).scalars().all()
        status_map: dict[int, str] = {ds.soldier_id: ds.status for ds in statuses}
        notes_map: dict[int, str] = {ds.soldier_id: ds.notes for ds in statuses if ds.notes}

        # ── Also load yesterday's statuses for arrival/departure detection ──
        from datetime import timedelta
        yesterday = day - timedelta(days=1)
        yds_stmt = (
            select(DailyStatus)
            .where(
                DailyStatus.period_id == period_id,
                DailyStatus.date == yesterday,
                DailyStatus.soldier_id.in_(list(soldier_map.keys())) if soldier_map else DailyStatus.soldier_id == -1,
            )
        )
        yesterday_statuses = session.execute(yds_stmt).scalars().all()
        yesterday_map: dict[int, str] = {ds.soldier_id: ds.status for ds in yesterday_statuses}

        # ── Count by status ──
        counts: dict[str, int] = defaultdict(int)
        for sid in soldier_map:
            st_val = status_map.get(sid, "")
            if st_val:
                counts[st_val] += 1

        present = sum(v for k, v in counts.items() if k in PRESENT_SET)
        na = sum(v for k, v in counts.items() if k in NA_SET)
        active = total - na

        # Returning / en-route soldiers
        RETURNING_SET = {"חוזר מחופש", "צפוי להתייצב", "סיפוח מאוחר"}
        returning = sum(v for k, v in counts.items() if k in RETURNING_SET)

        away = active - present - sum(v for k, v in counts.items() if not k or k in NA_SET)
        
        # ── Presence section ──
        pct = round(present / active * 100) if active else 0
        lines.append(f"👥 *נוכחות: {present}/{active} ({pct}%)*")
        if returning:
            lines.append(f"🚌 *חוזרים (בדרך): {returning}*")
        lines.append("")

        # Breakdown of non-present
        non_present = {k: v for k, v in counts.items() if k not in PRESENT_SET and k not in NA_SET and v > 0}
        if non_present:
            emoji_map = {
                "חופש": "🏖️", "יוצא לחופש": "🚪", "חוזר מחופש": "🔙",
                "פיצול": "✂️", "יוצא לפיצול": "✂️",
                "נפקד": "🚨", "משתחרר": "👋", "גימלים": "🎖️",
                "צפוי להתייצב": "⏳", "סיפוח מאוחר": "⏳",
                "יוצא לקורס": "📚", "רספ/סרספ": "🎯", "סמבצים": "🎯",
                "סוואנה": "🎯",
            }
            for status, cnt in sorted(non_present.items(), key=lambda x: -x[1]):
                emoji = emoji_map.get(status, "•")
                lines.append(f"  {emoji} {status}: {cnt}")
            lines.append("")

        # ── Arrivals today ──
        ARRIVING_STATUSES = {"התייצב", "חוזר מחופש", "סיפוח מאוחר", "צפוי להתייצב"}
        arrivals = []
        for sid, st_val in status_map.items():
            if st_val in ARRIVING_STATUSES:
                # Only count as "arriving" if yesterday they weren't present
                yesterday_st = yesterday_map.get(sid, "")
                if yesterday_st not in PRESENT_SET:
                    arrivals.append(soldier_map[sid])
        
        if arrivals:
            lines.append(f"🟢 *מגיעים היום ({len(arrivals)}):*")
            for name in sorted(arrivals):
                lines.append(f"  • {name}")
            lines.append("")

        # ── Departures today ──
        LEAVING_STATUSES = {"יוצא לחופש", "יוצא לפיצול", "משתחרר", "יוצא לקורס"}
        departures = []
        for sid, st_val in status_map.items():
            if st_val in LEAVING_STATUSES:
                departures.append((soldier_map[sid], st_val))

        if departures:
            lines.append(f"🔴 *יוצאים היום ({len(departures)}):*")
            for name, reason in sorted(departures):
                lines.append(f"  • {name} ({reason})")
            lines.append("")

        # ── Duty officer ──
        do_stmt = select(DutyOfficer).where(
            DutyOfficer.period_id == period_id,
            DutyOfficer.date == day,
        )
        duty_officer = session.execute(do_stmt).scalar_one_or_none()
        if duty_officer:
            lines.append(f"🎖️ *קצין תורן:* {duty_officer.commander_name}")
            lines.append("")

        # ── Shift assignments ──
        tasks_stmt = select(Task).where(
            Task.period_id == period_id,
            Task.is_active == True,
        ).order_by(Task.name)
        tasks = session.execute(tasks_stmt).scalars().all()

        assign_stmt = (
            select(ShiftAssignment, Soldier, Task)
            .join(Soldier, ShiftAssignment.soldier_id == Soldier.id)
            .join(Task, ShiftAssignment.task_id == Task.id)
            .where(
                ShiftAssignment.date == day,
                Task.period_id == period_id,
            )
            .order_by(Task.name, ShiftAssignment.shift_number)
        )
        assign_rows = session.execute(assign_stmt).all()

        if assign_rows:
            lines.append("⚔️ *שיבוצים:*")
            # Group by task
            task_assigns: dict[str, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
            task_shifts: dict[str, int] = {}
            for sa, soldier, task in assign_rows:
                name = f"{soldier.first_name} {soldier.last_name}"
                role = sa.role_in_shift or ""
                entry = f"{name}" + (f" ({role})" if role else "")
                task_assigns[task.name][sa.shift_number].append(entry)
                task_shifts[task.name] = task.shifts_per_day

            for task_name in sorted(task_assigns.keys()):
                shifts = task_assigns[task_name]
                num_shifts = task_shifts.get(task_name, 1)
                lines.append(f"  *{task_name}:*")
                for sn in sorted(shifts.keys()):
                    shift_label = _SHIFT_LABELS.get(sn, f"משמרת {sn}")
                    names = ", ".join(shifts[sn])
                    if num_shifts > 1:
                        lines.append(f"    {shift_label}: {names}")
                    else:
                        lines.append(f"    {names}")
            lines.append("")

        # ── Constraints active today ──
        constraint_stmt = (
            select(SoldierConstraint, Soldier)
            .join(Soldier, SoldierConstraint.soldier_id == Soldier.id)
            .where(
                SoldierConstraint.period_id == period_id,
                SoldierConstraint.constraint_date <= day,
            )
        )
        constraints = session.execute(constraint_stmt).all()

        # Filter constraints active on this day
        active_constraints = []
        CONSTRAINT_LABELS = {
            "departure": "🚪 יציאה",
            "arrival": "🟢 הגעה",
            "unavailable": "⛔ לא זמין",
            "medical": "🏥 רפואי",
            "custom": "📝",
        }
        for c, s in constraints:
            # Single day or range constraint
            if c.end_date:
                if not (c.constraint_date <= day <= c.end_date):
                    continue
            else:
                if c.constraint_date != day:
                    continue

            name = f"{s.first_name} {s.last_name}"
            label = CONSTRAINT_LABELS.get(c.constraint_type, c.constraint_type)
            time_label = ""
            if c.constraint_time and c.constraint_time != "all_day":
                time_map = {"morning": "בוקר", "afternoon": "צהריים", "night": "לילה"}
                time_label = f" ({time_map.get(c.constraint_time, c.constraint_time)})"
            reason = f" — {c.custom_reason}" if c.custom_reason else ""
            active_constraints.append(f"  {label} {name}{time_label}{reason}")

        if active_constraints:
            lines.append(f"⚠️ *אילוצים ({len(active_constraints)}):*")
            for line in sorted(active_constraints):
                lines.append(line)
            lines.append("")

        # ── Notes for today ──
        today_notes = [(soldier_map.get(sid, "?"), note) for sid, note in notes_map.items()]
        if today_notes:
            lines.append("📝 *הערות:*")
            for name, note in sorted(today_notes):
                lines.append(f"  • {name}: {note}")
            lines.append("")

    # ── Footer ──
    from datetime import datetime
    if day == date.today():
        lines.append(f"_נוצר אוטומטית • {datetime.now().strftime('%d/%m/%Y %H:%M')}_")
    else:
        lines.append(f"_נוצר אוטומטית • {day.strftime('%d/%m/%Y')}_")

    return "\n".join(lines)
