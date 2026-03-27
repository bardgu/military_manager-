"""Service for managing soldier availability constraints.

Constraints define when a soldier cannot be assigned to shifts.
For example:
- A soldier departing Tuesday morning cannot be on Tuesday morning shift.
- By default, they also can't do the night shift before (to allow sleep).
- With ignore_sleep=True, night shift before departure is allowed.
- duty_only: soldier can only do guard/duty tasks, not field missions.
- medical: medical limitation blocking specific tasks.
- custom: fully flexible task + shift restrictions.
"""

from __future__ import annotations

import json as _json
from datetime import date, timedelta
from sqlalchemy import select, and_

from military_manager.database import get_session, SoldierConstraint, Soldier, PeriodSoldier
from military_manager.logger import log_action


# ─── Shift number mapping ────────────────────────────────────
# Standard: 1=morning (06-14), 2=afternoon (14-22), 3=night (22-06)
SHIFT_LABELS = {1: "morning", 2: "afternoon", 3: "night"}
SHIFT_LABEL_HEB = {1: "בוקר", 2: "צהריים", 3: "לילה"}
TIME_LABELS_HEB = {
    "morning": "בוקר",
    "afternoon": "צהריים",
    "night": "לילה",
    "all_day": "כל היום",
}

# ─── Guard / duty task patterns (used by duty_only constraints) ───
GUARD_TASK_PATTERNS = ("שמיר", "ש\"\u05d2", "תורנ")


# ─── CRUD ─────────────────────────────────────────────────────

def add_constraint(
    period_id: int,
    soldier_id: int,
    constraint_type: str,
    constraint_date: date,
    constraint_time: str = "morning",
    ignore_sleep: bool = False,
    notes: str | None = None,
    created_by: str | None = None,
    end_date: date | None = None,
    custom_reason: str | None = None,
    requires_pitzul: bool = False,
    blocked_tasks: list[str] | None = None,
) -> SoldierConstraint | None:
    """Add a constraint for a soldier. Returns None if duplicate exists."""
    with get_session() as session:
        # --- Duplicate check ---
        dup_filters = [
            SoldierConstraint.period_id == period_id,
            SoldierConstraint.soldier_id == soldier_id,
            SoldierConstraint.constraint_type == constraint_type,
            SoldierConstraint.constraint_date == constraint_date,
            SoldierConstraint.constraint_time == constraint_time,
        ]
        if end_date is not None:
            dup_filters.append(SoldierConstraint.end_date == end_date)
        else:
            dup_filters.append(SoldierConstraint.end_date.is_(None))
        existing = session.execute(
            select(SoldierConstraint).where(and_(*dup_filters))
        ).scalars().first()
        if existing:
            return None  # duplicate — skip

        c = SoldierConstraint(
            period_id=period_id,
            soldier_id=soldier_id,
            constraint_type=constraint_type,
            constraint_date=constraint_date,
            end_date=end_date,
            constraint_time=constraint_time,
            ignore_sleep=ignore_sleep,
            custom_reason=custom_reason,
            requires_pitzul=requires_pitzul,
            blocked_tasks=_json.dumps(blocked_tasks, ensure_ascii=False) if blocked_tasks else None,
            notes=notes,
            created_by=created_by,
        )
        session.add(c)
        session.commit()
        session.refresh(c)
        log_action("constraint_added", {
            "soldier_id": soldier_id,
            "type": constraint_type,
            "date": str(constraint_date),
            "end_date": str(end_date) if end_date else None,
            "time": constraint_time,
        })
        return c


def get_soldier_constraints(period_id: int, soldier_id: int) -> list[dict]:
    """Get all constraints for a specific soldier in a period."""
    with get_session() as session:
        stmt = (
            select(SoldierConstraint)
            .where(
                SoldierConstraint.period_id == period_id,
                SoldierConstraint.soldier_id == soldier_id,
            )
            .order_by(SoldierConstraint.constraint_date)
        )
        rows = session.execute(stmt).scalars().all()
        return [_to_dict(r) for r in rows]


def get_period_constraints(period_id: int) -> list[dict]:
    """Get all constraints for a period (all soldiers)."""
    with get_session() as session:
        stmt = (
            select(SoldierConstraint, Soldier)
            .join(Soldier, SoldierConstraint.soldier_id == Soldier.id)
            .where(SoldierConstraint.period_id == period_id)
            .order_by(SoldierConstraint.constraint_date, Soldier.last_name)
        )
        results = session.execute(stmt).all()
        out = []
        for c, s in results:
            d = _to_dict(c)
            d["full_name"] = f"{s.first_name} {s.last_name}"
            out.append(d)
        return out


def delete_constraint(constraint_id: int) -> bool:
    """Delete a constraint."""
    with get_session() as session:
        c = session.get(SoldierConstraint, constraint_id)
        if not c:
            return False
        session.delete(c)
        session.commit()
        return True


def _to_dict(c: SoldierConstraint) -> dict:
    bt_raw = getattr(c, "blocked_tasks", None)
    try:
        bt = _json.loads(bt_raw) if bt_raw else []
    except (_json.JSONDecodeError, TypeError):
        bt = []
    return {
        "id": c.id,
        "period_id": c.period_id,
        "soldier_id": c.soldier_id,
        "constraint_type": c.constraint_type,
        "constraint_date": c.constraint_date,
        "end_date": c.end_date,
        "constraint_time": c.constraint_time,
        "ignore_sleep": c.ignore_sleep,
        "custom_reason": c.custom_reason,
        "requires_pitzul": c.requires_pitzul,
        "blocked_tasks": bt,
        "notes": c.notes,
        "created_by": c.created_by,
    }


# ─── Availability checking ───────────────────────────────────

def get_blocked_shifts(period_id: int, soldier_id: int, day: date,
                       shifts_per_day: int = 3) -> set[int]:
    """Get shift numbers that a soldier CANNOT be assigned to on a given day.

    Supports single-day and multi-day (date range) constraints.
    For range constraints, every day in the range is treated as the constraint date.

    Logic for departure constraints:
    - "departure" on date D at time "morning":
        → Block shift 1 (morning) on day D
        → Block shift 3 (night) on day D-1 (needs sleep before leaving)
          UNLESS ignore_sleep=True
    - "departure" on date D at time "afternoon":
        → Block shifts 2,3 on day D (afternoon + night)
        → Block shift 3 (night) on day D-1 (needs sleep)
          UNLESS ignore_sleep=True
    - "departure" on date D at time "all_day":
        → Block ALL shifts on day D
        → Block shift 3 (night) on day D-1
          UNLESS ignore_sleep=True

    For "unavailable" constraints:
        → Block all shifts matching the time on that day

    For "arrival" constraints on date D at time T:
        → Block shifts BEFORE T on that day
    """
    constraints = get_soldier_constraints(period_id, soldier_id)
    blocked: set[int] = set()

    for c in constraints:
        c_start = c["constraint_date"]
        c_end = c.get("end_date") or c_start  # single day if no end_date
        c_time = c["constraint_time"]
        c_type = c["constraint_type"]
        ignore_sleep = c["ignore_sleep"]

        # For range constraints: check if 'day' falls within the range
        # For departure: the departure logic applies on the LAST day of the range,
        # all intermediate days are fully blocked
        # For arrival: the arrival logic applies on the FIRST day of the range,
        # all intermediate days are fully blocked
        # For unavailable: all days in range are blocked at the specified time

        day_in_range = c_start <= day <= c_end
        is_multi_day = c_start != c_end

        if c_type == "departure":
            if is_multi_day:
                # Multi-day departure: first day uses departure logic (leave),
                # intermediate/last days fully blocked (soldier is away)
                if day == c_start:
                    # First day: departure logic applies
                    if c_time == "morning":
                        blocked.add(1); blocked.add(2)
                        if shifts_per_day >= 3: blocked.add(3)
                    elif c_time == "afternoon":
                        blocked.add(2)
                        if shifts_per_day >= 3: blocked.add(3)
                    elif c_time == "night":
                        if shifts_per_day >= 3: blocked.add(3)
                    elif c_time == "all_day":
                        for sn in range(1, shifts_per_day + 1): blocked.add(sn)
                elif c_start < day <= c_end:
                    # Intermediate / last day — soldier is away
                    for sn in range(1, shifts_per_day + 1): blocked.add(sn)
                # Sleep check: day before first day of range
                if c_start == day + timedelta(days=1):
                    if c_time in ("morning", "all_day"):
                        if not ignore_sleep and shifts_per_day >= 3:
                            blocked.add(3)
            else:
                # Single-day departure (original logic)
                if c_start == day:
                    if c_time == "morning":
                        blocked.add(1); blocked.add(2)
                        if shifts_per_day >= 3: blocked.add(3)
                    elif c_time == "afternoon":
                        blocked.add(2)
                        if shifts_per_day >= 3: blocked.add(3)
                    elif c_time == "night":
                        if shifts_per_day >= 3: blocked.add(3)
                    elif c_time == "all_day":
                        for sn in range(1, shifts_per_day + 1): blocked.add(sn)
                if c_start == day + timedelta(days=1):
                    if c_time in ("morning", "all_day"):
                        if not ignore_sleep and shifts_per_day >= 3:
                            blocked.add(3)

        elif c_type == "arrival":
            if is_multi_day:
                # Multi-day arrival: intermediate days fully blocked,
                # last day uses arrival logic
                if c_start <= day < c_end:
                    for sn in range(1, shifts_per_day + 1): blocked.add(sn)
                elif day == c_end:
                    # Last day: arrival logic
                    if c_time == "afternoon":
                        blocked.add(1)
                    elif c_time == "night":
                        blocked.add(1); blocked.add(2)
                    elif c_time == "all_day":
                        for sn in range(1, shifts_per_day + 1): blocked.add(sn)
            else:
                if c_start == day:
                    if c_time == "afternoon":
                        blocked.add(1)
                    elif c_time == "night":
                        blocked.add(1); blocked.add(2)
                    elif c_time == "all_day":
                        for sn in range(1, shifts_per_day + 1): blocked.add(sn)

        elif c_type == "unavailable":
            if day_in_range:
                if c_time == "morning":
                    blocked.add(1)
                elif c_time == "afternoon":
                    blocked.add(2)
                elif c_time == "night":
                    if shifts_per_day >= 3:
                        blocked.add(3)
                elif c_time == "all_day":
                    for sn in range(1, shifts_per_day + 1):
                        blocked.add(sn)

        elif c_type in ("duty_only", "medical", "custom"):
            # These types can optionally block shifts too
            if day_in_range and c_time and c_time != "all_shifts_allowed":
                if c_time == "morning":
                    blocked.add(1)
                elif c_time == "afternoon":
                    blocked.add(2)
                elif c_time == "night":
                    if shifts_per_day >= 3:
                        blocked.add(3)
                elif c_time == "all_day":
                    for sn in range(1, shifts_per_day + 1):
                        blocked.add(sn)
            # Note: task-level blocking is handled by get_task_restrictions_for_date

    return blocked


def is_soldier_available(period_id: int, soldier_id: int, day: date,
                         shift_number: int, shifts_per_day: int = 3) -> bool:
    """Check if a soldier is available for a specific shift on a specific day."""
    blocked = get_blocked_shifts(period_id, soldier_id, day, shifts_per_day)
    return shift_number not in blocked


def get_constraints_for_date(period_id: int, day: date) -> dict[int, set[int]]:
    """Get all blocked shifts for all soldiers on a given date.

    Returns {soldier_id: {blocked_shift_numbers}}.
    Handles both single-day and date-range constraints.
    """
    from sqlalchemy import or_
    with get_session() as session:
        # Match: single-day constraints on this day or next day (sleep),
        # OR range constraints that overlap with this day or next day
        next_day = day + timedelta(days=1)
        stmt = (
            select(SoldierConstraint)
            .where(
                SoldierConstraint.period_id == period_id,
                or_(
                    # Single-day: constraint_date is today or tomorrow
                    and_(
                        SoldierConstraint.end_date.is_(None),
                        SoldierConstraint.constraint_date.in_([day, next_day]),
                    ),
                    # Range: range overlaps with today (or tomorrow for sleep)
                    and_(
                        SoldierConstraint.end_date.isnot(None),
                        SoldierConstraint.constraint_date <= next_day,
                        SoldierConstraint.end_date >= day,
                    ),
                ),
            )
        )
        constraints = session.execute(stmt).scalars().all()

    soldier_ids = {c.soldier_id for c in constraints}

    result: dict[int, set[int]] = {}
    for sid in soldier_ids:
        blocked = get_blocked_shifts(period_id, sid, day)
        if blocked:
            result[sid] = blocked
    return result


# ─── Task-level restrictions ─────────────────────────────────

def _is_guard_task(task_name: str) -> bool:
    """Check if a task name matches guard/duty patterns."""
    for pattern in GUARD_TASK_PATTERNS:
        if pattern in task_name:
            return True
    return False


def _task_matches_patterns(task_name: str, patterns: list[str]) -> bool:
    """Check if a task name matches any of the given patterns."""
    task_lower = task_name.strip()
    for p in patterns:
        p = p.strip()
        if p and p in task_lower:
            return True
    return False


def get_task_restrictions_for_date(period_id: int, day: date) -> dict[int, dict]:
    """Get task-level restrictions for all soldiers on a given date.

    Returns {soldier_id: {
        "duty_only": bool,    # only guard/duty tasks allowed
        "blocked_tasks": [...],  # task name patterns to block
        "reasons": [...],     # human-readable reasons
    }}

    Only returns entries for soldiers that have active task-restricting
    constraints (duty_only / medical / custom with blocked_tasks).
    """
    from sqlalchemy import or_
    with get_session() as session:
        stmt = (
            select(SoldierConstraint)
            .where(
                SoldierConstraint.period_id == period_id,
                SoldierConstraint.constraint_type.in_(["duty_only", "medical", "custom"]),
                or_(
                    # Single-day on this day
                    and_(
                        SoldierConstraint.end_date.is_(None),
                        SoldierConstraint.constraint_date == day,
                    ),
                    # Range overlapping this day
                    and_(
                        SoldierConstraint.end_date.isnot(None),
                        SoldierConstraint.constraint_date <= day,
                        SoldierConstraint.end_date >= day,
                    ),
                ),
            )
        )
        constraints = session.execute(stmt).scalars().all()

    result: dict[int, dict] = {}
    for c in constraints:
        sid = c.soldier_id
        if sid not in result:
            result[sid] = {"duty_only": False, "blocked_tasks": [], "reasons": []}

        entry = result[sid]

        if c.constraint_type == "duty_only":
            entry["duty_only"] = True
            reason = c.custom_reason or "תורן בלבד"
            entry["reasons"].append(f"🛡️ {reason}")
        elif c.constraint_type in ("medical", "custom"):
            bt_raw = getattr(c, "blocked_tasks", None)
            try:
                bt = _json.loads(bt_raw) if bt_raw else []
            except (_json.JSONDecodeError, TypeError):
                bt = []
            if bt:
                entry["blocked_tasks"].extend(bt)
            reason = c.custom_reason or (
                "הגבלה רפואית" if c.constraint_type == "medical" else "אילוץ מותאם"
            )
            icon = "🏥" if c.constraint_type == "medical" else "📝"
            entry["reasons"].append(f"{icon} {reason}")

    return result


def is_task_allowed(task_name: str, restriction: dict) -> bool:
    """Check if a task is allowed for a soldier given their restrictions.

    Args:
        task_name: The task name to check.
        restriction: The restriction dict from get_task_restrictions_for_date.

    Returns True if the task IS allowed, False if blocked.
    """
    if not restriction:
        return True

    # duty_only: only guard/duty tasks allowed
    if restriction.get("duty_only"):
        if not _is_guard_task(task_name):
            return False

    # blocked_tasks: specific task patterns blocked
    blocked = restriction.get("blocked_tasks", [])
    if blocked and _task_matches_patterns(task_name, blocked):
        return False

    return True


# ─── פיצול auto-application ──────────────────────────────────

PITZUL_THRESHOLD_DAYS = 4  # absence > this many days → auto פיצול from day 4


def apply_pitzul_statuses(period_id: int):
    """Scan constraints that require פיצול and auto-set status.

    Rules:
    1. If requires_pitzul=True, set "פיצול" for ALL days of the constraint range.
    2. If constraint spans > PITZUL_THRESHOLD_DAYS days (regardless of requires_pitzul flag),
       set "פיצול" from day 4 onward.
    Returns list of (soldier_id, date, reason) tuples that were set.
    """
    from military_manager.services.status_service import set_status

    with get_session() as session:
        stmt = (
            select(SoldierConstraint)
            .where(SoldierConstraint.period_id == period_id)
        )
        constraints = session.execute(stmt).scalars().all()

    applied: list[tuple[int, date, str]] = []

    for c in constraints:
        c_start = c.constraint_date
        c_end = c.end_date or c_start
        duration = (c_end - c_start).days + 1

        if c.requires_pitzul:
            # Set פיצול for all days (first day = יוצא לפיצול, rest = פיצול)
            current = c_start
            while current <= c_end:
                status = "יוצא לפיצול" if current == c_start else "פיצול"
                set_status(period_id, c.soldier_id, current, status)
                applied.append((c.soldier_id, current, "requires_pitzul"))
                current += timedelta(days=1)
        elif duration > PITZUL_THRESHOLD_DAYS:
            # Auto-פיצול from day 4 onward
            day4_start = c_start + timedelta(days=PITZUL_THRESHOLD_DAYS - 1)
            current = day4_start
            while current <= c_end:
                status = "יוצא לפיצול" if current == day4_start else "פיצול"
                set_status(period_id, c.soldier_id, current, status)
                applied.append((c.soldier_id, current, "auto_4day_rule"))
                current += timedelta(days=1)

    return applied


def get_pitzul_constraints(period_id: int) -> list[dict]:
    """Get constraints that trigger פיצול (either explicit or >4 days)."""
    with get_session() as session:
        stmt = (
            select(SoldierConstraint, Soldier)
            .join(Soldier, SoldierConstraint.soldier_id == Soldier.id)
            .where(SoldierConstraint.period_id == period_id)
            .order_by(SoldierConstraint.constraint_date)
        )
        results = session.execute(stmt).all()

    pitzul_list = []
    for c, s in results:
        c_start = c.constraint_date
        c_end = c.end_date or c_start
        duration = (c_end - c_start).days + 1
        needs_pitzul = c.requires_pitzul or duration > PITZUL_THRESHOLD_DAYS
        if needs_pitzul:
            d = _to_dict(c)
            d["full_name"] = f"{s.first_name} {s.last_name}"
            d["duration"] = duration
            d["pitzul_reason"] = "סימון ידני" if c.requires_pitzul else f"העדרות {duration} ימים (מעל {PITZUL_THRESHOLD_DAYS})"
            pitzul_list.append(d)
    return pitzul_list
