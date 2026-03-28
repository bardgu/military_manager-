"""Service for managing tasks, task slots, and shift assignments."""

from __future__ import annotations

import json
from datetime import date, timedelta
from collections import defaultdict
from sqlalchemy import select, func, and_
from sqlalchemy.orm import Session

from military_manager.database import (
    get_session, Task, TaskSlot, ShiftAssignment, Soldier, PeriodSoldier, DutyOfficer,
)
from military_manager.logger import log_action


# ─── Task CRUD ────────────────────────────────────────────────

def create_task(period_id: int, name: str, **kwargs) -> Task:
    """Create a new task/mission."""
    with get_session() as session:
        task = Task(
            period_id=period_id,
            name=name,
            **{k: v for k, v in kwargs.items() if v is not None}
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        log_action("task_created", {"task_id": task.id, "name": name})
        return task


def get_period_tasks(period_id: int, active_only: bool = True) -> list[Task]:
    """Get all tasks for a period."""
    with get_session() as session:
        stmt = select(Task).where(Task.period_id == period_id)
        if active_only:
            stmt = stmt.where(Task.is_active == True)
        stmt = stmt.order_by(Task.name)
        return list(session.execute(stmt).scalars().all())


def update_task(task_id: int, **kwargs) -> Task | None:
    """Update a task."""
    with get_session() as session:
        task = session.get(Task, task_id)
        if not task:
            return None
        for key, value in kwargs.items():
            if hasattr(task, key):
                setattr(task, key, value)
        session.commit()
        session.refresh(task)
        return task


def delete_task(task_id: int) -> bool:
    """Soft-delete a task (set inactive)."""
    with get_session() as session:
        task = session.get(Task, task_id)
        if not task:
            return False
        task.is_active = False
        session.commit()
        return True


# ─── Task Slots (role-based positions) ───────────────────────

def add_task_slot(task_id: int, slot_name: str, quantity: int = 1,
                  allowed_roles: list[str] | None = None,
                  slot_order: int = 0) -> TaskSlot:
    """Add a role-slot to a task."""
    with get_session() as session:
        slot = TaskSlot(
            task_id=task_id,
            slot_name=slot_name,
            slot_order=slot_order,
            quantity=quantity,
            allowed_roles=json.dumps(allowed_roles or [], ensure_ascii=False),
        )
        session.add(slot)
        session.commit()
        session.refresh(slot)
        return slot


def get_task_slots(task_id: int) -> list[dict]:
    """Get all slots for a task, ordered by slot_order."""
    with get_session() as session:
        stmt = (
            select(TaskSlot)
            .where(TaskSlot.task_id == task_id)
            .order_by(TaskSlot.slot_order)
        )
        slots = session.execute(stmt).scalars().all()
        result = []
        for s in slots:
            try:
                roles = json.loads(s.allowed_roles) if s.allowed_roles else []
            except json.JSONDecodeError:
                roles = []
            result.append({
                "id": s.id,
                "task_id": s.task_id,
                "slot_name": s.slot_name,
                "slot_order": s.slot_order,
                "quantity": s.quantity,
                "allowed_roles": roles,
            })
        return result


def update_task_slot(slot_id: int, **kwargs) -> TaskSlot | None:
    """Update a task slot."""
    with get_session() as session:
        slot = session.get(TaskSlot, slot_id)
        if not slot:
            return None
        for key, value in kwargs.items():
            if key == "allowed_roles" and isinstance(value, list):
                slot.allowed_roles = json.dumps(value, ensure_ascii=False)
            elif hasattr(slot, key):
                setattr(slot, key, value)
        session.commit()
        session.refresh(slot)
        return slot


def delete_task_slot(slot_id: int) -> bool:
    """Delete a task slot."""
    with get_session() as session:
        slot = session.get(TaskSlot, slot_id)
        if not slot:
            return False
        session.delete(slot)
        session.commit()
        return True


def replace_task_slots(task_id: int, slots_data: list[dict]) -> list[TaskSlot]:
    """Replace all slots for a task with new definitions.

    slots_data: [{"slot_name": "נהג", "quantity": 1, "allowed_roles": ["נהג"]}, ...]
    """
    with get_session() as session:
        # Delete existing slots — first nullify FK references in ShiftAssignment
        stmt = select(TaskSlot).where(TaskSlot.task_id == task_id)
        existing = session.execute(stmt).scalars().all()
        old_ids = [s.id for s in existing]
        if old_ids:
            session.execute(
                ShiftAssignment.__table__.update()
                .where(ShiftAssignment.task_slot_id.in_(old_ids))
                .values(task_slot_id=None)
            )
        for s in existing:
            session.delete(s)

        # Create new slots
        new_slots = []
        for i, sd in enumerate(slots_data):
            slot = TaskSlot(
                task_id=task_id,
                slot_name=sd.get("slot_name", f"תפקיד {i+1}"),
                slot_order=i,
                quantity=sd.get("quantity", 1),
                allowed_roles=json.dumps(
                    sd.get("allowed_roles", []), ensure_ascii=False
                ),
            )
            session.add(slot)
            new_slots.append(slot)

        session.commit()
        for s in new_slots:
            session.refresh(s)
        return new_slots


# ─── Role-filtered soldier queries ───────────────────────────

def _role_word_match(pattern: str, text: str) -> bool:
    """Check if pattern appears in text at a word boundary.
    
    Ensures 'מ\"פ' does NOT match 'סמ\"פ' — the character before must be
    start-of-string, space, slash, dash, or opening paren.
    NOTE: '.' is intentionally NOT a boundary so 'ע.מ\"פ' won't expose 'מ\"פ'.
    """
    idx = 0
    while idx <= len(text) - len(pattern):
        pos = text.find(pattern, idx)
        if pos < 0:
            return False
        # Check start boundary
        if pos > 0:
            prev = text[pos - 1]
            if prev not in (' ', '/', '-', '(', ','):
                idx = pos + 1
                continue
        # Check end boundary
        end = pos + len(pattern)
        if end < len(text):
            nxt = text[end]
            if nxt not in (' ', '/', '-', ')', ','):
                idx = pos + 1
                continue
        return True
    return False


def _soldier_matches_roles(soldier_data: dict, allowed_roles: list[str],
                           soldier_qualifications: list[str] | None = None) -> bool:
    """Check if a soldier's role, task_role, or qualifications match allowed roles.

    Matching uses PREFIX (startswith): the soldier's role must START WITH
    the allowed role string.  This correctly handles Hebrew military
    compound roles where qualifiers are appended after the base role.

    Examples:
     - allowed 'מ\"כ'  matches soldier 'מ\"כ א\''           (prefix) ✓
     - allowed 'מ\"פ'  matches soldier 'מ\"פ'               (exact)  ✓
     - allowed 'מ\"פ'  does NOT match 'נהג מ\"פ (חפקון)'   (not prefix) ✗
     - allowed 'נהג מ\"פ (חפקון)' matches 'נהג מ\"פ (חפקון) ע\"ת' (prefix) ✓
    """
    if not allowed_roles:
        return True

    soldier_role = (soldier_data.get("role") or "").strip()
    soldier_task_role = (soldier_data.get("task_role") or "").strip()
    quals = soldier_qualifications or []

    for allowed in allowed_roles:
        allowed_clean = allowed.strip()
        if not allowed_clean:
            continue
        # Check if soldier's role starts with the allowed role (prefix match)
        if soldier_role and soldier_role.startswith(allowed_clean):
            return True
        if soldier_task_role and soldier_task_role.startswith(allowed_clean):
            return True
        # Check qualifications (exact match)
        if allowed_clean in quals:
            return True
    return False


# Driver-role keywords — if any allowed_roles contain these, use approved driver list
DRIVER_KEYWORDS = ["נהג"]


def _slot_requires_driver(allowed_roles: list[str]) -> bool:
    """Check if a slot's allowed_roles imply a driver position."""
    for role in allowed_roles:
        for kw in DRIVER_KEYWORDS:
            if kw in role:
                return True
    return False


def _load_soldier_qualifications_map(period_id: int) -> dict[int, list[str]]:
    """Load all qualifications for all soldiers in a period.

    Returns {soldier_id: [qual_name1, qual_name2, ...]}.
    """
    from military_manager.services.qualification_service import get_period_qualifications
    assignments = get_period_qualifications(period_id)
    result: dict[int, list[str]] = {}
    for a in assignments:
        result.setdefault(a["soldier_id"], []).append(a["qualification_name"])
    return result


def get_eligible_soldiers_for_slot(period_id: int, slot_id: int,
                                    exclude_ids: set[int] | None = None) -> list[dict]:
    """Get soldiers eligible for a specific task slot based on role + qualification matching.

    For driver slots (allowed_roles containing 'נהג'), only approved drivers
    from the PeriodDriver table are eligible.
    For other slots, uses standard role matching AND qualification matching.
    """
    from military_manager.services.soldier_service import get_period_soldiers

    with get_session() as session:
        slot = session.get(TaskSlot, slot_id)
        if not slot:
            return []
        try:
            allowed_roles = json.loads(slot.allowed_roles) if slot.allowed_roles else []
        except json.JSONDecodeError:
            allowed_roles = []

    all_soldiers = get_period_soldiers(period_id, exclude_irrelevant_unit=True)
    exclude = exclude_ids or set()

    # If this is a driver slot, restrict to approved drivers
    if _slot_requires_driver(allowed_roles):
        from military_manager.services.driver_service import get_approved_driver_ids
        approved_ids = get_approved_driver_ids(period_id)
        eligible = []
        for s in all_soldiers:
            if s["soldier_id"] in exclude:
                continue
            if s["soldier_id"] in approved_ids:
                eligible.append(s)
        return eligible

    # Load qualifications for all soldiers in this period
    qual_map = _load_soldier_qualifications_map(period_id)

    eligible = []
    for s in all_soldiers:
        if s["soldier_id"] in exclude:
            continue
        soldier_quals = qual_map.get(s["soldier_id"], [])
        if _soldier_matches_roles(s, allowed_roles, soldier_quals):
            eligible.append(s)
    return eligible


def get_eligible_soldiers_for_roles(period_id: int, allowed_roles: list[str],
                                     exclude_ids: set[int] | None = None) -> list[dict]:
    """Get soldiers eligible based on a list of allowed role values.

    Like get_eligible_soldiers_for_slot but takes role list directly.
    Also checks qualifications.
    """
    from military_manager.services.soldier_service import get_period_soldiers

    all_soldiers = get_period_soldiers(period_id, exclude_irrelevant_unit=True)
    exclude = exclude_ids or set()
    qual_map = _load_soldier_qualifications_map(period_id)

    eligible = []
    for s in all_soldiers:
        if s["soldier_id"] in exclude:
            continue
        soldier_quals = qual_map.get(s["soldier_id"], [])
        if _soldier_matches_roles(s, allowed_roles, soldier_quals):
            eligible.append(s)
    return eligible


# Duty officer eligible roles (officers only)
DUTY_OFFICER_ROLES = ["מ\"פ", "סמ\"פ", "מ\"מ", "רס\"פ", "ע.מ\"פ"]


def get_duty_officer_eligible(period_id: int) -> list[dict]:
    """Get soldiers eligible to serve as duty officer (officers only)."""
    return get_eligible_soldiers_for_roles(period_id, DUTY_OFFICER_ROLES)


# ─── Shift Assignments ───────────────────────────────────────

def assign_shift(task_id: int, day: date, shift_number: int,
                 soldier_id: int, task_slot_id: int | None = None,
                 role_in_shift: str | None = None,
                 assigned_by: str | None = None) -> ShiftAssignment:
    """Assign a soldier to a shift, optionally linked to a task slot."""
    with get_session() as session:
        # Check for duplicate
        stmt = select(ShiftAssignment).where(
            ShiftAssignment.date == day,
            ShiftAssignment.task_id == task_id,
            ShiftAssignment.shift_number == shift_number,
            ShiftAssignment.soldier_id == soldier_id,
        )
        existing = session.execute(stmt).scalar_one_or_none()
        if existing:
            raise ValueError("החייל כבר משובץ למשמרת זו")

        # Auto-fill role_in_shift from slot name if not provided
        if not role_in_shift and task_slot_id:
            slot = session.get(TaskSlot, task_slot_id)
            if slot:
                role_in_shift = slot.slot_name

        sa = ShiftAssignment(
            date=day,
            task_id=task_id,
            shift_number=shift_number,
            soldier_id=soldier_id,
            task_slot_id=task_slot_id,
            role_in_shift=role_in_shift,
            assigned_by=assigned_by,
        )
        session.add(sa)
        session.commit()
        session.refresh(sa)
        return sa


def remove_shift_assignment(task_id: int, day: date, shift_number: int,
                            soldier_id: int) -> bool:
    """Remove a shift assignment by criteria."""
    with get_session() as session:
        stmt = select(ShiftAssignment).where(
            ShiftAssignment.task_id == task_id,
            ShiftAssignment.date == day,
            ShiftAssignment.shift_number == shift_number,
            ShiftAssignment.soldier_id == soldier_id,
        )
        sa = session.execute(stmt).scalar_one_or_none()
        if not sa:
            return False
        session.delete(sa)
        session.commit()
        return True


def get_daily_assignments(period_id: int, day: date) -> dict:
    """Get all shift assignments for a day, grouped by task name and shift number.

    Returns:
        {
            "task_name": {
                "task_id": int,
                "shifts_per_day": int,
                "slots": [slot_dict, ...],
                1: [{"soldier_id": 1, "name": "...", "role": "...", "slot_id": ...}, ...],
                2: [...],
            },
            ...
        }
    """
    with get_session() as session:
        # Get tasks for the period
        tasks_stmt = select(Task).where(
            Task.period_id == period_id,
            Task.is_active == True,
        )
        tasks = session.execute(tasks_stmt).scalars().all()

        # Get assignments
        assign_stmt = (
            select(ShiftAssignment, Soldier)
            .join(Soldier, ShiftAssignment.soldier_id == Soldier.id)
            .join(Task, ShiftAssignment.task_id == Task.id)
            .where(
                ShiftAssignment.date == day,
                Task.period_id == period_id,
            )
            .order_by(ShiftAssignment.task_id, ShiftAssignment.shift_number)
        )
        assignment_rows = session.execute(assign_stmt).all()

        # Get all slots for each task
        task_slots_map: dict[int, list[dict]] = {}
        for task in tasks:
            slots_stmt = (
                select(TaskSlot)
                .where(TaskSlot.task_id == task.id)
                .order_by(TaskSlot.slot_order)
            )
            slots = session.execute(slots_stmt).scalars().all()
            task_slots_map[task.id] = []
            for s in slots:
                try:
                    roles = json.loads(s.allowed_roles) if s.allowed_roles else []
                except json.JSONDecodeError:
                    roles = []
                task_slots_map[task.id].append({
                    "id": s.id,
                    "slot_name": s.slot_name,
                    "quantity": s.quantity,
                    "allowed_roles": roles,
                })

        # Build result grouped by task name
        result: dict = {}
        task_id_to_name = {}

        for task in tasks:
            task_id_to_name[task.id] = task.name
            result[task.name] = {
                "task_id": task.id,
                "shifts_per_day": task.shifts_per_day,
                "slots": task_slots_map.get(task.id, []),
            }
            for sn in range(1, task.shifts_per_day + 1):
                result[task.name][sn] = []

        for sa, soldier in assignment_rows:
            task_name = task_id_to_name.get(sa.task_id, f"task_{sa.task_id}")
            if task_name not in result:
                result[task_name] = {"task_id": sa.task_id, "shifts_per_day": 1, "slots": []}
            if sa.shift_number not in result[task_name]:
                result[task_name][sa.shift_number] = []
            result[task_name][sa.shift_number].append({
                "soldier_id": soldier.id,
                "name": f"{soldier.first_name} {soldier.last_name}",
                "role": sa.role_in_shift,
                "slot_id": sa.task_slot_id,
            })

        return result


def set_duty_officer(period_id: int, day: date, soldier_id: int,
                     notes: str | None = None) -> DutyOfficer:
    """Set the duty officer for a day using soldier ID."""
    with get_session() as session:
        # Look up soldier name
        soldier = session.get(Soldier, soldier_id)
        commander_name = f"{soldier.first_name} {soldier.last_name}" if soldier else str(soldier_id)

        stmt = select(DutyOfficer).where(
            DutyOfficer.period_id == period_id,
            DutyOfficer.date == day,
        )
        existing = session.execute(stmt).scalar_one_or_none()
        if existing:
            existing.commander_name = commander_name
            existing.soldier_id = soldier_id
            existing.notes = notes
            session.commit()
            session.refresh(existing)
            return existing

        do = DutyOfficer(
            period_id=period_id,
            date=day,
            soldier_id=soldier_id,
            commander_name=commander_name,
            notes=notes,
        )
        session.add(do)
        session.commit()
        session.refresh(do)
        return do


def get_soldier_shift_count(period_id: int, soldier_id: int,
                            start_date: date | None = None,
                            end_date: date | None = None) -> int:
    """Count shifts assigned to a soldier."""
    with get_session() as session:
        stmt = (
            select(func.count())
            .select_from(ShiftAssignment)
            .join(Task, ShiftAssignment.task_id == Task.id)
            .where(Task.period_id == period_id, ShiftAssignment.soldier_id == soldier_id)
        )
        if start_date:
            stmt = stmt.where(ShiftAssignment.date >= start_date)
        if end_date:
            stmt = stmt.where(ShiftAssignment.date <= end_date)
        return session.execute(stmt).scalar() or 0


def get_fairness_report(period_id: int) -> list[dict]:
    """Get shift fairness report — how many shifts each soldier has done."""
    with get_session() as session:
        stmt = (
            select(
                Soldier.id,
                Soldier.first_name,
                Soldier.last_name,
                PeriodSoldier.sub_unit,
                func.count(ShiftAssignment.id).label("shift_count"),
            )
            .join(PeriodSoldier, and_(
                PeriodSoldier.soldier_id == Soldier.id,
                PeriodSoldier.period_id == period_id,
            ))
            .outerjoin(ShiftAssignment, ShiftAssignment.soldier_id == Soldier.id)
            .outerjoin(Task, and_(
                Task.id == ShiftAssignment.task_id,
                Task.period_id == period_id,
            ))
            .where(PeriodSoldier.is_active == True)
            .group_by(Soldier.id, Soldier.first_name, Soldier.last_name, PeriodSoldier.sub_unit)
            .order_by(func.count(ShiftAssignment.id).desc())
        )
        results = session.execute(stmt).all()
        return [
            {
                "soldier_id": r[0],
                "full_name": f"{r[1]} {r[2]}",
                "sub_unit": r[3],
                "shift_count": r[4],
            }
            for r in results
        ]


def get_detailed_fairness_report(period_id: int) -> list[dict]:
    """Get detailed fairness report with breakdown by shift type, task, and role.

    Returns per-soldier:
    - total_shifts: total assignments
    - morning_shifts: shift_number=1
    - afternoon_shifts: shift_number=2
    - night_shifts: shift_number=3
    - per-task counts
    - per-role counts
    - duty_officer_count
    """
    with get_session() as session:
        # Get all soldiers in period
        soldiers_stmt = (
            select(Soldier.id, Soldier.first_name, Soldier.last_name,
                   PeriodSoldier.sub_unit, PeriodSoldier.role)
            .join(PeriodSoldier, and_(
                PeriodSoldier.soldier_id == Soldier.id,
                PeriodSoldier.period_id == period_id,
            ))
            .where(PeriodSoldier.is_active == True)
            .order_by(PeriodSoldier.sort_order)
        )
        soldiers = session.execute(soldiers_stmt).all()

        # Get all assignments for this period
        assign_stmt = (
            select(
                ShiftAssignment.soldier_id,
                ShiftAssignment.shift_number,
                ShiftAssignment.role_in_shift,
                Task.name.label("task_name"),
            )
            .join(Task, ShiftAssignment.task_id == Task.id)
            .where(Task.period_id == period_id)
        )
        assignments = session.execute(assign_stmt).all()

        # Get duty officer counts
        duty_stmt = (
            select(DutyOfficer.soldier_id, func.count().label("cnt"))
            .where(DutyOfficer.period_id == period_id)
            .group_by(DutyOfficer.soldier_id)
        )
        duty_counts = {r[0]: r[1] for r in session.execute(duty_stmt).all()}

        # Get unique task names
        task_names = sorted({a[3] for a in assignments})

    # Build per-soldier stats
    stats: dict[int, dict] = {}
    for sid, fn, ln, sub, role in soldiers:
        stats[sid] = {
            "soldier_id": sid,
            "full_name": f"{fn} {ln}",
            "sub_unit": sub or "",
            "role": role or "",
            "total_shifts": 0,
            "morning_shifts": 0,
            "afternoon_shifts": 0,
            "night_shifts": 0,
            "duty_officer": duty_counts.get(sid, 0),
            "tasks": {tn: 0 for tn in task_names},
            "roles": defaultdict(int),
        }

    for sid, shift_num, role_in_shift, task_name in assignments:
        if sid not in stats:
            continue
        s = stats[sid]
        s["total_shifts"] += 1
        if shift_num == 1:
            s["morning_shifts"] += 1
        elif shift_num == 2:
            s["afternoon_shifts"] += 1
        elif shift_num >= 3:
            s["night_shifts"] += 1
        if task_name in s["tasks"]:
            s["tasks"][task_name] += 1
        if role_in_shift:
            s["roles"][role_in_shift] += 1

    # Convert roles defaultdict to regular dict
    for s in stats.values():
        s["roles"] = dict(s["roles"])

    return list(stats.values()), task_names


# ─── Known role values (for UI suggestions) ──────────────────

# Common organizational roles (from PeriodSoldier.role)
KNOWN_ORG_ROLES = [
    "מ\"פ", "סמ\"פ", "ע.מ\"פ", "מ\"מ", "רס\"פ", "מהנדס", "סמל מחלקה",
    "מ\"כ", "מחלץ", "מחלץ ע\"ת", "לוחם", "חובש", "נהג", "נהג מ\"פ",
    "נהג משא", "קשר עורף", "מחסנאי", "מש\"ק תחזוקה", "סמב\"ץ",
]

# Common task/occupation roles (from PeriodSoldier.task_role)
KNOWN_TASK_ROLES = [
    "ק' חילוץ והצלה", "מהנדס בניין/חילוץ", "נהג רכב פרטי",
    "קשר עורף", "מ\"כ חילוץ והצלה", "חייל בתפקיד כללי",
    "נגד לוגיסטיקה", "נהג משא", "סמב\"ץ",
]

# Merged list for task slot role picker (static)
ALL_KNOWN_ROLES = sorted(set(KNOWN_ORG_ROLES + KNOWN_TASK_ROLES))

# ─── Role-based auto-assignment exclusions ────────────────────

def _is_chapak_only_role(role: str) -> bool:
    """Check if a soldier role should ONLY be auto-assigned to חפ"ק tasks.

    Matches:
    - מ"פ / סמ"פ  (exact)
    - Any role containing 'חפקון' (e.g. 'נהג מ"פ (חפקון)', 'ע.מ"פ ע"ת (חפקון)')
    """
    if not role:
        return False
    if role in ("מ\"פ", "סמ\"פ"):
        return True
    if "חפקון" in role:
        return True
    return False


# Roles that should NEVER be auto-assigned to any task
NEVER_ASSIGN_ROLES = {
    "רס\"פ", "סרס\"פ",
}

# Role prefixes that should never be auto-assigned
# ע.מ"פ = assistant commander (staff role, doesn't go on missions)
NEVER_ASSIGN_PREFIXES = ("ע.מ\"פ",)


def get_all_role_options() -> list[str]:
    """Get all role options including dynamic qualifications.

    Returns the static KNOWN roles PLUS all defined qualification names,
    so they can be selected in task slot allowed_roles.
    """
    from military_manager.services.qualification_service import get_qualification_names
    qual_names = get_qualification_names()
    return sorted(set(ALL_KNOWN_ROLES + qual_names))


# ─── Auto Assignment Engine ──────────────────────────────────

# ─── Carmel (כיתת כוננות) Linking ─────────────────────────────

def link_carmel_to_patrol(carmel_task_id: int, patrol_task_id: int,
                          mode: str = "auto") -> bool:
    """Link a כרמל task to a סיור task bidirectionally.

    mode: 'auto' | 'shared' | 'separate'
    """
    with get_session() as session:
        carmel = session.get(Task, carmel_task_id)
        patrol = session.get(Task, patrol_task_id)
        if not carmel or not patrol:
            return False
        carmel.linked_task_id = patrol_task_id
        carmel.carmel_mode = mode
        patrol.linked_task_id = carmel_task_id
        patrol.carmel_mode = mode
        session.commit()
        return True


def unlink_carmel(task_id: int) -> bool:
    """Unlink a כרמל-סיור pairing."""
    with get_session() as session:
        task = session.get(Task, task_id)
        if not task or not task.linked_task_id:
            return False
        partner = session.get(Task, task.linked_task_id)
        task.linked_task_id = None
        task.carmel_mode = "auto"
        if partner:
            partner.linked_task_id = None
            partner.carmel_mode = "auto"
        session.commit()
        return True


def get_linked_task(task_id: int) -> dict | None:
    """Get the linked partner task info."""
    with get_session() as session:
        task = session.get(Task, task_id)
        if not task or not task.linked_task_id:
            return None
        partner = session.get(Task, task.linked_task_id)
        if not partner:
            return None
        return {
            "id": partner.id,
            "name": partner.name,
            "mode": task.carmel_mode or "auto",
        }


def set_carmel_mode(task_id: int, mode: str) -> bool:
    """Set the carmel assignment mode for a linked pair."""
    with get_session() as session:
        task = session.get(Task, task_id)
        if not task:
            return False
        task.carmel_mode = mode
        if task.linked_task_id:
            partner = session.get(Task, task.linked_task_id)
            if partner:
                partner.carmel_mode = mode
        session.commit()
        return True


def _compute_carmel_approach(period_id: int, carmel_task: Task,
                             patrol_task: Task,
                             available_soldiers: list[dict],
                             date_constraints: dict,
                             day: date) -> str:
    """Determine whether to use shared or separate approach.

    Counts how many soldiers are available (not constrained) and compares
    to total personnel needed for both tasks combined.

    Returns 'shared' or 'separate'.
    """
    # Count personnel needed per shift for each task
    carmel_slots = get_task_slots(carmel_task.id)
    patrol_slots = get_task_slots(patrol_task.id)

    carmel_per_shift = sum(s.get("quantity", 1) for s in carmel_slots)
    patrol_per_shift = sum(s.get("quantity", 1) for s in patrol_slots)

    # Total unique personnel needed if separate:
    # each shift needs carmel_per_shift + patrol_per_shift soldiers
    separate_needed = (carmel_per_shift + patrol_per_shift) * max(
        carmel_task.shifts_per_day, patrol_task.shifts_per_day
    )

    # With shared approach we need max(carmel_per_shift, patrol_per_shift) per pair
    # and soldiers rotate between the two tasks
    shared_needed = max(carmel_per_shift, patrol_per_shift) * max(
        carmel_task.shifts_per_day, patrol_task.shifts_per_day
    )

    # Count available soldiers (not constrained for any shift)
    available_count = 0
    for sol in available_soldiers:
        sid = sol["soldier_id"]
        constrained_shifts = date_constraints.get(sid, set())
        if len(constrained_shifts) < max(carmel_task.shifts_per_day, patrol_task.shifts_per_day):
            available_count += 1

    # If we have enough for separate, use separate (better for soldiers — more rest)
    # Threshold: need at least 120% of separate_needed to comfortably use separate
    if available_count >= separate_needed * 1.2:
        return "separate"
    else:
        return "shared"


def get_carmel_recommendation(period_id: int, day: date) -> dict | None:
    """Get recommendation for carmel approach for a specific day.

    Returns None if no carmel-patrol pair exists, otherwise:
    {
        "carmel_task": str, "patrol_task": str,
        "mode": str, "recommended": str,
        "available_soldiers": int,
        "needed_shared": int, "needed_separate": int,
    }
    """
    from military_manager.services.soldier_service import get_period_soldiers
    from military_manager.services.constraint_service import get_constraints_for_date

    tasks = get_period_tasks(period_id, active_only=True)

    # Find linked pairs
    for task in tasks:
        if not task.linked_task_id:
            continue
        partner = next((t for t in tasks if t.id == task.linked_task_id), None)
        if not partner:
            continue
        # Only process once (lower ID first)
        if task.id > partner.id:
            continue

        # Determine which is carmel and which is patrol
        carmel = task if "כרמל" in task.name else partner
        patrol = partner if "כרמל" in task.name else task

        all_soldiers = get_period_soldiers(period_id, exclude_irrelevant_unit=True)
        date_constraints = get_constraints_for_date(period_id, day)

        carmel_slots = get_task_slots(carmel.id)
        patrol_slots = get_task_slots(patrol.id)
        carmel_per_shift = sum(s.get("quantity", 1) for s in carmel_slots)
        patrol_per_shift = sum(s.get("quantity", 1) for s in patrol_slots)
        max_shifts = max(carmel.shifts_per_day, patrol.shifts_per_day)

        needed_shared = max(carmel_per_shift, patrol_per_shift) * max_shifts
        needed_separate = (carmel_per_shift + patrol_per_shift) * max_shifts

        available = sum(
            1 for s in all_soldiers
            if len(date_constraints.get(s["soldier_id"], set())) < max_shifts
        )

        recommended = _compute_carmel_approach(
            period_id, carmel, patrol, all_soldiers, date_constraints, day
        )

        return {
            "carmel_task": carmel.name,
            "patrol_task": patrol.name,
            "carmel_id": carmel.id,
            "patrol_id": patrol.id,
            "mode": carmel.carmel_mode or "auto",
            "recommended": recommended,
            "available_soldiers": available,
            "needed_shared": needed_shared,
            "needed_separate": needed_separate,
            "carmel_per_shift": carmel_per_shift,
            "patrol_per_shift": patrol_per_shift,
        }

    return None


def _is_rotation_day(task: Task, day: date) -> bool:
    """Check if *day* is a rotation day for a non-continuous task.

    Returns True when:
    - task.non_continuous is False (i.e. normal task — always rotates)
    - task has no rotation config
    - today IS one of the configured rotation days

    Returns False only when task is non_continuous and today is NOT a rotation day,
    meaning soldiers should carry over from the previous day.
    """
    if not getattr(task, 'non_continuous', False):
        return True  # normal task — always rotates

    rtype = getattr(task, 'rotation_type', None)
    rconf = getattr(task, 'rotation_config', None)
    if not rtype or not rconf:
        return True  # no config — treat as normal

    import json as _json
    try:
        config = _json.loads(rconf)
    except (ValueError, TypeError):
        return True

    if rtype == "fixed_days":
        # config = list of weekday numbers (0=Mon..6=Sun)
        return day.weekday() in config
    elif rtype == "specific_dates":
        # config = list of date strings "YYYY-MM-DD"
        return day.strftime("%Y-%m-%d") in config

    return True


def _carry_forward_assignments(task: Task, day: date, period_id: int,
                                summary_assigned: list, assigned_today: set):
    """For non-continuous tasks when today is NOT a rotation day,
    copy the previous day's assignments forward.

    Walks back up to 14 days to find the last day with assignments.
    """
    from datetime import timedelta as _td

    prev_day = day - _td(days=1)
    for _ in range(14):
        prev_assignments = get_daily_assignments(period_id, prev_day)
        task_data = prev_assignments.get(task.name, {})
        has_assignments = False
        for key, value in task_data.items():
            if isinstance(key, int) and isinstance(value, list) and value:
                has_assignments = True
                break

        if has_assignments:
            # Copy all shifts from prev_day to today
            for shift_num in range(1, task.shifts_per_day + 1):
                shift_soldiers = task_data.get(shift_num, [])
                if not isinstance(shift_soldiers, list):
                    continue
                for sol in shift_soldiers:
                    sid = sol.get("soldier_id")
                    slot_id = sol.get("slot_id")
                    if not sid:
                        continue
                    try:
                        assign_shift(
                            task.id, day, shift_num, sid,
                            task_slot_id=slot_id,
                            assigned_by="auto-carry",
                        )
                        assigned_today.add(sid)
                        summary_assigned.append((
                            sol.get("name", "?"), task.name, shift_num,
                            sol.get("slot_name", "המשך")
                        ))
                    except ValueError:
                        pass  # already assigned or conflict
            return True
        prev_day -= _td(days=1)

    return False  # no previous assignments found


def auto_assign_day(period_id: int, day: date,
                    clear_existing: bool = False) -> dict:
    """Automatically assign soldiers to all task slots for a given day.

    Handles linked Carmel-Patrol pairs with two approaches:
    - **shared**: Same soldiers rotate between כרמל and סיור (low manpower).
      Pattern per soldier: כרמל→סיור or סיור→כרמל (never same task twice).
      If כרמל needs more personnel than סיור, extra soldiers are added.
    - **separate**: Different soldiers for each task (high manpower).
    - **auto**: System decides based on available manpower.

    For all other (non-linked) tasks: standard fairness-based assignment.

    Returns summary dict.
    """
    from military_manager.services.soldier_service import get_period_soldiers
    from military_manager.services.constraint_service import get_constraints_for_date, get_task_restrictions_for_date

    # Optionally clear existing assignments for the day
    if clear_existing:
        _clear_day_assignments(period_id, day)

    all_soldiers = get_period_soldiers(period_id, exclude_irrelevant_unit=True)
    soldier_map = {s["soldier_id"]: s for s in all_soldiers}

    # Build bidirectional buddy map: soldier_id -> set of buddy_ids
    import json as _json
    buddy_map: dict[int, set[int]] = {}
    for sol in all_soldiers:
        sid = sol["soldier_id"]
        raw = sol.get("preferred_buddies") or "[]"
        try:
            buddy_ids = _json.loads(raw) if isinstance(raw, str) else []
        except (_json.JSONDecodeError, TypeError):
            buddy_ids = []
        for bid in buddy_ids:
            buddy_map.setdefault(sid, set()).add(bid)
            buddy_map.setdefault(bid, set()).add(sid)  # reciprocal

    # Load fairness data: {soldier_id: shift_count}
    fairness = _get_shift_counts_map(period_id)

    # Load night shift counts for night-fairness
    night_counts = _get_night_shift_counts_map(period_id)

    # Load constraints for this day
    date_constraints = get_constraints_for_date(period_id, day)

    # Load task restrictions (duty_only / medical / custom)
    task_restrictions = get_task_restrictions_for_date(period_id, day)

    # Load qualifications
    qual_map = _load_soldier_qualifications_map(period_id)

    # Load approved drivers
    from military_manager.services.driver_service import get_approved_driver_ids
    approved_drivers = get_approved_driver_ids(period_id)

    # Get current assignments for the day (what's already filled)
    existing = get_daily_assignments(period_id, day)
    assigned_today: set[int] = set()
    for task_name, task_data in existing.items():
        for key, value in task_data.items():
            if isinstance(key, int) and isinstance(value, list):
                for s in value:
                    assigned_today.add(s.get("soldier_id"))

    # Get tasks
    tasks = get_period_tasks(period_id, active_only=True)

    summary_assigned = []
    summary_unassigned = []

    # ── Identify linked Carmel-Patrol pairs ──
    linked_pairs = []       # [(carmel_task, patrol_task, mode)]
    linked_task_ids = set()  # IDs of tasks that are part of a linked pair

    for task in tasks:
        if not task.linked_task_id or task.id in linked_task_ids:
            continue
        partner = next((t for t in tasks if t.id == task.linked_task_id), None)
        if not partner or partner.id in linked_task_ids:
            continue

        # Determine which is carmel, which is patrol
        if "כרמל" in task.name:
            carmel, patrol = task, partner
        elif "כרמל" in partner.name:
            carmel, patrol = partner, task
        else:
            # If neither has כרמל in name, treat first as carmel
            carmel, patrol = task, partner

        mode = carmel.carmel_mode or "auto"
        if mode == "auto":
            mode = _compute_carmel_approach(
                period_id, carmel, patrol, all_soldiers, date_constraints, day
            )

        linked_pairs.append((carmel, patrol, mode))
        linked_task_ids.add(carmel.id)
        linked_task_ids.add(patrol.id)

    # ── Process linked Carmel-Patrol pairs ──
    for carmel, patrol, mode in linked_pairs:
        carmel_slots = get_task_slots(carmel.id)
        patrol_slots = get_task_slots(patrol.id)

        if not carmel_slots and not patrol_slots:
            continue

        if mode == "shared":
            _assign_carmel_shared(
                carmel, patrol, carmel_slots, patrol_slots,
                day, all_soldiers, assigned_today, date_constraints,
                approved_drivers, qual_map, fairness, night_counts,
                existing, summary_assigned, summary_unassigned,
                buddy_map=buddy_map,
                task_restrictions=task_restrictions,
            )
        else:
            # Separate mode: assign each independently (handled below with normal tasks)
            # But mark them so we track soldiers used by each
            _assign_carmel_separate(
                carmel, patrol, carmel_slots, patrol_slots,
                day, all_soldiers, assigned_today, date_constraints,
                approved_drivers, qual_map, fairness, night_counts,
                existing, summary_assigned, summary_unassigned,
                buddy_map=buddy_map,
                task_restrictions=task_restrictions,
            )

    # ── Process remaining (non-linked) tasks normally ──
    for task in tasks:
        if task.id in linked_task_ids:
            continue  # Already handled above

        # Non-continuous rotation: if today is NOT a rotation day, carry forward
        if not _is_rotation_day(task, day):
            _carry_forward_assignments(task, day, period_id,
                                        summary_assigned, assigned_today)
            continue

        slots = get_task_slots(task.id)
        if not slots:
            continue

        for shift_num in range(1, task.shifts_per_day + 1):
            _assign_task_shift(
                task, slots, shift_num, day,
                all_soldiers, assigned_today, date_constraints,
                approved_drivers, qual_map, fairness, night_counts,
                existing, summary_assigned, summary_unassigned,
                buddy_map=buddy_map,
                task_restrictions=task_restrictions,
            )

    return {
        "assigned": summary_assigned,
        "unassigned": summary_unassigned,
        "total_assigned": len(summary_assigned),
        "total_unassigned": len(summary_unassigned),
    }


def _assign_task_shift(task, slots, shift_num, day,
                       all_soldiers, assigned_today, date_constraints,
                       approved_drivers, qual_map, fairness, night_counts,
                       existing, summary_assigned, summary_unassigned,
                       exclude_ids: set | None = None,
                       buddy_map: dict | None = None,
                       task_restrictions: dict | None = None):
    """Assign soldiers to all slots of a single task/shift. Core building block.

    חפ"ק tasks are now split into separate tasks (חפ"ק מ"פ / חפ"ק סמ"פ),
    each with type-specific slots.  No runtime commander decision needed.
    """
    existing_task = existing.get(task.name, {})
    shift_soldiers = existing_task.get(shift_num, []) if isinstance(existing_task.get(shift_num), list) else []
    assigned_in_shift = {s["soldier_id"] for s in shift_soldiers}

    slot_filled: dict[int, int] = {}
    for s in shift_soldiers:
        sid = s.get("slot_id")
        if sid:
            slot_filled[sid] = slot_filled.get(sid, 0) + 1

    newly_assigned: list[int] = []
    extra_exclude = set(exclude_ids) if exclude_ids else set()
    _task_restrictions = task_restrictions or {}

    is_chapak_task = "חפ\"ק" in task.name

    for slot in slots:
        slot_id = slot["id"]
        slot_name = slot["slot_name"]
        allowed_roles = slot.get("allowed_roles", [])
        quantity = slot.get("quantity", 1)
        already_filled = slot_filled.get(slot_id, 0)
        needed = quantity - already_filled

        if needed <= 0:
            continue

        effective_roles = list(allowed_roles)

        is_driver = _slot_requires_driver(effective_roles)
        # For driver slots with specific roles (not just generic "נהג"),
        # also check role matching in addition to approved-driver status.
        has_specific_driver_roles = is_driver and any(
            r.strip() != "נהג" for r in effective_roles if r.strip()
        )
        effective_roles_set = set(r.strip() for r in effective_roles if r.strip())

        candidates = []
        for sol in all_soldiers:
            sid = sol["soldier_id"]
            if sid in assigned_today or sid in assigned_in_shift or sid in extra_exclude:
                continue
            if sid in date_constraints and shift_num in date_constraints[sid]:
                continue

            # ── Task restriction check (duty_only / medical / custom) ──
            if sid in _task_restrictions:
                from military_manager.services.constraint_service import is_task_allowed
                if not is_task_allowed(task.name, _task_restrictions[sid]):
                    continue

            # ── Role-based exclusions ──
            sol_role = (sol.get("role") or "").strip()
            # Never assign רס"פ/סרס"פ to any task
            if sol_role in NEVER_ASSIGN_ROLES:
                continue
            # Never assign assistant command staff (ע.מ"פ) roles
            if any(sol_role.startswith(p) for p in NEVER_ASSIGN_PREFIXES):
                continue
            # Only assign חפ"ק-exclusive roles to חפ"ק tasks
            if _is_chapak_only_role(sol_role) and not is_chapak_task:
                continue

            # ── Assignment notes exclusions ──
            a_notes = (sol.get("assignment_notes") or "").strip()
            if a_notes:
                skip = False
                notes_lower = a_notes.lower()
                # "לא לשבץ" — skip entirely
                if "לא לשבץ" in a_notes:
                    skip = True
                # "לילה בלבד" — only allow night shifts (shift >= 3)
                elif "לילה בלבד" in a_notes and shift_num < 3:
                    skip = True
                # "ללא לילה" — never assign to night shifts
                elif "ללא לילה" in a_notes and shift_num >= 3:
                    skip = True
                # "נהיגה בלבד" — skip unless this slot requires a driver
                elif "נהיגה בלבד" in a_notes and not is_driver:
                    skip = True
                # "חפ"ק בלבד" — skip if not חפ"ק task
                elif "חפ\"ק בלבד" in a_notes and not is_chapak_task:
                    skip = True
                # "שמירה בלבד" — skip if not שמירה/ש"ג task
                elif "שמירה בלבד" in a_notes and "שמיר" not in task.name and "ש\"ג" not in task.name:
                    skip = True
                if skip:
                    continue

            if is_driver:
                # Soldiers whose role starts with "נהג" are implicit drivers
                is_implicit_driver = sol_role.startswith("נהג")
                # Soldiers with a matching qualification also bypass driver list
                soldier_quals = qual_map.get(sid, [])
                has_matching_qual = any(q in effective_roles_set for q in soldier_quals) if effective_roles else False
                if not is_implicit_driver and not has_matching_qual and sid not in approved_drivers:
                    continue
                # For specific driver slots, also require role/qualification match
                if has_specific_driver_roles:
                    if not _soldier_matches_roles(sol, effective_roles, soldier_quals):
                        continue
            else:
                soldier_quals = qual_map.get(sid, [])
                if not _soldier_matches_roles(sol, effective_roles, soldier_quals):
                    continue
            candidates.append(sol)

        # Buddy bonus: candidates who are preferred buddies of already-assigned
        # soldiers in this shift get a slight advantage (tiebreaker only).
        _bmap = buddy_map or {}
        def _has_buddy_in_shift(sid):
            buddies = _bmap.get(sid, set())
            return 0 if buddies & assigned_in_shift else 1

        if shift_num >= 3:
            candidates.sort(key=lambda s: (
                night_counts.get(s["soldier_id"], 0),
                _has_buddy_in_shift(s["soldier_id"]),
                fairness.get(s["soldier_id"], 0),
            ))
        else:
            candidates.sort(key=lambda s: (
                fairness.get(s["soldier_id"], 0),
                _has_buddy_in_shift(s["soldier_id"]),
                night_counts.get(s["soldier_id"], 0),
            ))

        assigned_count = 0
        for sol in candidates[:needed]:
            sid = sol["soldier_id"]
            try:
                assign_shift(
                    task.id, day, shift_num, sid,
                    task_slot_id=slot_id,
                    assigned_by="auto",
                )
                assigned_today.add(sid)
                assigned_in_shift.add(sid)
                newly_assigned.append(sid)
                fairness[sid] = fairness.get(sid, 0) + 1
                if shift_num >= 3:
                    night_counts[sid] = night_counts.get(sid, 0) + 1
                summary_assigned.append((
                    sol["full_name"], task.name, shift_num, slot_name
                ))
                assigned_count += 1

            except ValueError:
                pass

        remaining = needed - assigned_count
        if remaining > 0:
            reason = "אין חיילים זמינים מתאימים" if not candidates else f"נמצאו רק {len(candidates)} מועמדים"
            summary_unassigned.append((
                task.name, shift_num, slot_name, f"חסרים {remaining} — {reason}"
            ))

    return newly_assigned


def _assign_carmel_shared(carmel, patrol, carmel_slots, patrol_slots,
                          day, all_soldiers, assigned_today, date_constraints,
                          approved_drivers, qual_map, fairness, night_counts,
                          existing, summary_assigned, summary_unassigned,
                          buddy_map: dict | None = None,
                          task_restrictions: dict | None = None):
    """Shared mode: same soldiers rotate between כרמל and סיור.

    For each shift pair (shift N):
    - Odd shifts: soldiers do כרמל first, then סיור next shift
    - Even shifts: soldiers do סיור first, then כרמל next shift
    Pattern ensures a soldier never does the same task twice consecutively.

    If כרמל needs more personnel than סיור (or vice versa), extra soldiers
    are added to fill the difference.
    """
    max_shifts = max(carmel.shifts_per_day, patrol.shifts_per_day)

    carmel_per_shift = sum(s.get("quantity", 1) for s in carmel_slots)
    patrol_per_shift = sum(s.get("quantity", 1) for s in patrol_slots)

    # We need to track which soldiers were assigned to which task in prev shift
    # so we can alternate them
    prev_carmel_soldiers: set[int] = set()
    prev_patrol_soldiers: set[int] = set()

    for shift_num in range(1, max_shifts + 1):
        # ── Decide who goes where this shift ──
        # Soldiers from previous כרמל → now do סיור
        # Soldiers from previous סיור → now do כרמל
        # For shift 1, assign fresh based on fairness

        if shift_num == 1:
            # First shift: assign כרמל, then use those soldiers for next סיור
            # But first, build a pool of eligible soldiers for BOTH tasks combined
            # We need enough soldiers for max(carmel, patrol) roles per shift
            # Assign כרמל first
            if shift_num <= carmel.shifts_per_day:
                newly_carmel = _assign_task_shift(
                    carmel, carmel_slots, shift_num, day,
                    all_soldiers, assigned_today, date_constraints,
                    approved_drivers, qual_map, fairness, night_counts,
                    existing, summary_assigned, summary_unassigned,
                    buddy_map=buddy_map,
                    task_restrictions=task_restrictions,
                )
                prev_carmel_soldiers = set(newly_carmel)
            # Then assign סיור from different pool
            if shift_num <= patrol.shifts_per_day:
                newly_patrol = _assign_task_shift(
                    patrol, patrol_slots, shift_num, day,
                    all_soldiers, assigned_today, date_constraints,
                    approved_drivers, qual_map, fairness, night_counts,
                    existing, summary_assigned, summary_unassigned,
                    buddy_map=buddy_map,
                    task_restrictions=task_restrictions,
                )
                prev_patrol_soldiers = set(newly_patrol)
        else:
            # Subsequent shifts: SWAP
            # Previous כרמל soldiers → now assigned to סיור (if still available)
            # Previous סיור soldiers → now assigned to כרמל (if still available)

            # First, UN-mark previous soldiers from assigned_today so they can be reused
            swap_to_patrol = prev_carmel_soldiers.copy()  # were on כרמל, now go to סיור
            swap_to_carmel = prev_patrol_soldiers.copy()  # were on סיור, now go to כרמל

            # Remove these soldiers from assigned_today so they can be re-assigned
            for sid in swap_to_patrol | swap_to_carmel:
                assigned_today.discard(sid)

            # Assign כרמל — prefer soldiers who just did סיור (swap_to_carmel)
            new_carmel = set()
            if shift_num <= carmel.shifts_per_day:
                new_carmel = set(_assign_task_shift_with_preference(
                    carmel, carmel_slots, shift_num, day,
                    all_soldiers, assigned_today, date_constraints,
                    approved_drivers, qual_map, fairness, night_counts,
                    existing, summary_assigned, summary_unassigned,
                    preferred_ids=swap_to_carmel,
                    buddy_map=buddy_map,
                    task_restrictions=task_restrictions,
                ))

            # Assign סיור — prefer soldiers who just did כרמל (swap_to_patrol)
            new_patrol = set()
            if shift_num <= patrol.shifts_per_day:
                new_patrol = set(_assign_task_shift_with_preference(
                    patrol, patrol_slots, shift_num, day,
                    all_soldiers, assigned_today, date_constraints,
                    approved_drivers, qual_map, fairness, night_counts,
                    existing, summary_assigned, summary_unassigned,
                    preferred_ids=swap_to_patrol,
                    buddy_map=buddy_map,
                    task_restrictions=task_restrictions,
                ))

            prev_carmel_soldiers = new_carmel
            prev_patrol_soldiers = new_patrol


def _assign_task_shift_with_preference(task, slots, shift_num, day,
                                        all_soldiers, assigned_today, date_constraints,
                                        approved_drivers, qual_map, fairness, night_counts,
                                        existing, summary_assigned, summary_unassigned,
                                        preferred_ids: set | None = None,
                                        buddy_map: dict | None = None,
                                        task_restrictions: dict | None = None):
    """Like _assign_task_shift but with preferred soldiers getting priority.

    Preferred soldiers are sorted first (they still must match roles).
    This is used for the Carmel swap pattern.
    """
    existing_task = existing.get(task.name, {})
    shift_soldiers = existing_task.get(shift_num, []) if isinstance(existing_task.get(shift_num), list) else []
    assigned_in_shift = {s["soldier_id"] for s in shift_soldiers}

    slot_filled: dict[int, int] = {}
    for s in shift_soldiers:
        sid = s.get("slot_id")
        if sid:
            slot_filled[sid] = slot_filled.get(sid, 0) + 1

    preferred = preferred_ids or set()
    _task_restrictions = task_restrictions or {}
    newly_assigned: list[int] = []

    for slot in slots:
        slot_id = slot["id"]
        slot_name = slot["slot_name"]
        allowed_roles = slot.get("allowed_roles", [])
        quantity = slot.get("quantity", 1)
        already_filled = slot_filled.get(slot_id, 0)
        needed = quantity - already_filled

        if needed <= 0:
            continue

        is_driver = _slot_requires_driver(allowed_roles)
        allowed_roles_set = set(r.strip() for r in allowed_roles if r.strip())

        candidates = []
        for sol in all_soldiers:
            sid = sol["soldier_id"]
            if sid in assigned_today or sid in assigned_in_shift:
                continue
            if sid in date_constraints and shift_num in date_constraints[sid]:
                continue
            # Task restriction check (duty_only / medical / custom)
            if sid in _task_restrictions:
                from military_manager.services.constraint_service import is_task_allowed
                if not is_task_allowed(task.name, _task_restrictions[sid]):
                    continue
            if is_driver:
                sol_role = (sol.get("role") or "").strip()
                is_implicit_driver = sol_role.startswith("נהג")
                soldier_quals = qual_map.get(sid, [])
                has_matching_qual = any(q in allowed_roles_set for q in soldier_quals) if allowed_roles else False
                if not is_implicit_driver and not has_matching_qual and sid not in approved_drivers:
                    continue
                if not _soldier_matches_roles(sol, allowed_roles, soldier_quals):
                    continue
            else:
                soldier_quals = qual_map.get(sid, [])
                if not _soldier_matches_roles(sol, allowed_roles, soldier_quals):
                    continue
            candidates.append(sol)

        # Sort: preferred soldiers first, then buddy bonus, then by fairness
        # (note: _assign_task_shift_with_preference is for Carmel swaps only —
        # no ChaPaK-specific driver logic needed here)
        _bmap = buddy_map or {}
        def sort_key(s):
            sid = s["soldier_id"]
            is_pref = 0 if sid in preferred else 1
            has_buddy = 0 if (_bmap.get(sid, set()) & assigned_in_shift) else 1
            if shift_num >= 3:
                return (is_pref, night_counts.get(sid, 0), has_buddy, fairness.get(sid, 0))
            return (is_pref, fairness.get(sid, 0), has_buddy, night_counts.get(sid, 0))

        candidates.sort(key=sort_key)

        assigned_count = 0
        for sol in candidates[:needed]:
            sid = sol["soldier_id"]
            try:
                assign_shift(
                    task.id, day, shift_num, sid,
                    task_slot_id=slot_id,
                    assigned_by="auto",
                )
                assigned_today.add(sid)
                assigned_in_shift.add(sid)
                newly_assigned.append(sid)
                fairness[sid] = fairness.get(sid, 0) + 1
                if shift_num >= 3:
                    night_counts[sid] = night_counts.get(sid, 0) + 1
                summary_assigned.append((
                    sol["full_name"], task.name, shift_num, slot_name
                ))
                assigned_count += 1
            except ValueError:
                pass

        remaining = needed - assigned_count
        if remaining > 0:
            reason = "אין חיילים זמינים מתאימים" if not candidates else f"נמצאו רק {len(candidates)} מועמדים"
            summary_unassigned.append((
                task.name, shift_num, slot_name, f"חסרים {remaining} — {reason}"
            ))

    return newly_assigned


def _assign_carmel_separate(carmel, patrol, carmel_slots, patrol_slots,
                            day, all_soldiers, assigned_today, date_constraints,
                            approved_drivers, qual_map, fairness, night_counts,
                            existing, summary_assigned, summary_unassigned,
                            buddy_map: dict | None = None,
                            task_restrictions: dict | None = None):
    """Separate mode: different soldiers for כרמל and סיור.

    Assign כרמל first, then סיור — assigned_today ensures no overlap.
    """
    for shift_num in range(1, carmel.shifts_per_day + 1):
        _assign_task_shift(
            carmel, carmel_slots, shift_num, day,
            all_soldiers, assigned_today, date_constraints,
            approved_drivers, qual_map, fairness, night_counts,
            existing, summary_assigned, summary_unassigned,
            buddy_map=buddy_map,
            task_restrictions=task_restrictions,
        )

    for shift_num in range(1, patrol.shifts_per_day + 1):
        _assign_task_shift(
            patrol, patrol_slots, shift_num, day,
            all_soldiers, assigned_today, date_constraints,
            approved_drivers, qual_map, fairness, night_counts,
            existing, summary_assigned, summary_unassigned,
            buddy_map=buddy_map,
            task_restrictions=task_restrictions,
        )


def auto_assign_range(period_id: int, start: date, end: date,
                      clear_existing: bool = False) -> list[dict]:
    """Auto-assign for a range of days. Returns list of daily summaries."""
    results = []
    current = start
    while current <= end:
        summary = auto_assign_day(period_id, current, clear_existing=clear_existing)
        summary["date"] = current
        results.append(summary)
        current += timedelta(days=1)
    return results


def get_minimum_soldiers_needed(period_id: int) -> dict:
    """Calculate the minimum number of soldiers needed to fill all active tasks.

    Returns dict with:
    - min_needed: minimum unique soldiers needed per day (sum of max shift personnel)
    - total_slots_per_day: total slot-fills across all shifts
    - per_task: [{name, per_shift, shifts, daily_total}]
    """
    tasks = get_period_tasks(period_id, active_only=True)
    total_min = 0
    total_slots = 0
    per_task = []

    for task in tasks:
        slots = get_task_slots(task.id)
        per_shift = sum(s.get("quantity", 1) for s in slots) if slots else task.personnel_per_shift
        daily_total = per_shift * task.shifts_per_day
        # Non-continuous tasks still need the same soldiers, they just don't rotate
        # Minimum unique soldiers = max(per_shift across all tasks in same shift)
        # Simplified: we count per_shift as unique soldiers since shifts rotate
        total_min += per_shift
        total_slots += daily_total
        per_task.append({
            "name": task.name,
            "per_shift": per_shift,
            "shifts": task.shifts_per_day,
            "daily_total": daily_total,
        })

    return {
        "min_needed": total_min,
        "total_slots_per_day": total_slots,
        "per_task": per_task,
    }


def get_available_soldiers_count(period_id: int, day: date) -> dict:
    """Count available soldiers for a specific day, considering constraints and status.

    Returns dict with:
    - total: total soldiers in period
    - available: available for assignment
    - constrained: have some constraints
    - on_leave: fully unavailable due to constraints/departures
    - irrelevant: soldiers marked as irrelevant
    """
    from military_manager.services.soldier_service import get_period_soldiers
    from military_manager.services.constraint_service import get_constraints_for_date

    all_soldiers = get_period_soldiers(period_id, exclude_irrelevant_unit=True)
    date_constraints = get_constraints_for_date(period_id, day)

    # Exclude irrelevant soldiers
    irrelevant_count = sum(1 for s in all_soldiers if s.get("is_irrelevant"))
    relevant_soldiers = [s for s in all_soldiers if not s.get("is_irrelevant")]

    total = len(relevant_soldiers)
    # Fully blocked = blocked in ALL shifts (1,2,3)
    fully_blocked = 0
    partially_blocked = 0
    for sid, blocked in (date_constraints or {}).items():
        if len(blocked) >= 3:  # blocked all shifts
            fully_blocked += 1
        else:
            partially_blocked += 1

    available = total - fully_blocked
    return {
        "total": total,
        "available": available,
        "constrained": partially_blocked,
        "on_leave": fully_blocked,
        "irrelevant": irrelevant_count,
    }


def get_forward_capacity(period_id: int, start: date, num_days: int = 14) -> list[dict]:
    """Look ahead at soldier availability for upcoming days.

    Returns list of dicts per day:
    [{date, available, total, on_leave, deficit (if available < min_needed)}]
    """
    min_info = get_minimum_soldiers_needed(period_id)
    min_needed = min_info["min_needed"]

    results = []
    current = start
    for _ in range(num_days):
        avail = get_available_soldiers_count(period_id, current)
        deficit = max(0, min_needed - avail["available"])
        results.append({
            "date": current,
            "available": avail["available"],
            "total": avail["total"],
            "on_leave": avail["on_leave"],
            "min_needed": min_needed,
            "deficit": deficit,
        })
        current += timedelta(days=1)

    return results


def _clear_day_assignments(period_id: int, day: date) -> int:
    """Remove all shift assignments for a specific day in a period."""
    with get_session() as session:
        stmt = (
            select(ShiftAssignment)
            .join(Task, ShiftAssignment.task_id == Task.id)
            .where(Task.period_id == period_id, ShiftAssignment.date == day)
        )
        rows = session.execute(stmt).scalars().all()
        count = len(rows)
        for r in rows:
            session.delete(r)
        session.commit()
        return count


def _get_shift_counts_map(period_id: int) -> dict[int, int]:
    """Get {soldier_id: total_shift_count} for a period."""
    with get_session() as session:
        stmt = (
            select(ShiftAssignment.soldier_id, func.count().label("cnt"))
            .join(Task, ShiftAssignment.task_id == Task.id)
            .where(Task.period_id == period_id)
            .group_by(ShiftAssignment.soldier_id)
        )
        return {r[0]: r[1] for r in session.execute(stmt).all()}


def _get_night_shift_counts_map(period_id: int) -> dict[int, int]:
    """Get {soldier_id: night_shift_count} for a period."""
    with get_session() as session:
        stmt = (
            select(ShiftAssignment.soldier_id, func.count().label("cnt"))
            .join(Task, ShiftAssignment.task_id == Task.id)
            .where(Task.period_id == period_id, ShiftAssignment.shift_number >= 3)
            .group_by(ShiftAssignment.soldier_id)
        )
        return {r[0]: r[1] for r in session.execute(stmt).all()}


def get_multi_day_schedule(period_id: int, start: date, end: date) -> list[dict]:
    """Get the full schedule for a date range, for visual display.

    Returns a list of dicts, one per day:
    [
        {
            "date": date,
            "tasks": {
                "task_name": {
                    "task_id": int,
                    "shift_times": ["06:00-14:00", ...],
                    "shifts": {
                        1: [{"name": "...", "role": "...", "slot_name": "..."}, ...],
                        2: [...],
                    }
                }
            },
            "duty_officer": "name" or None,
        }
    ]
    """
    with get_session() as session:
        # Get tasks
        tasks_stmt = select(Task).where(
            Task.period_id == period_id, Task.is_active == True
        ).order_by(Task.name)
        tasks = session.execute(tasks_stmt).scalars().all()

        task_info = {}
        for t in tasks:
            try:
                times = json.loads(t.shift_times) if t.shift_times else []
            except (json.JSONDecodeError, TypeError):
                times = []
            task_info[t.id] = {
                "name": t.name,
                "shifts_per_day": t.shifts_per_day,
                "shift_times": times,
            }

        # Get slots
        slot_names: dict[int, str] = {}
        slots_stmt = select(TaskSlot)
        for s in session.execute(slots_stmt).scalars().all():
            slot_names[s.id] = s.slot_name

        # Get all assignments in range
        assign_stmt = (
            select(ShiftAssignment, Soldier)
            .join(Soldier, ShiftAssignment.soldier_id == Soldier.id)
            .join(Task, ShiftAssignment.task_id == Task.id)
            .where(
                Task.period_id == period_id,
                ShiftAssignment.date >= start,
                ShiftAssignment.date <= end,
            )
            .order_by(ShiftAssignment.date, ShiftAssignment.task_id, ShiftAssignment.shift_number)
        )
        assignments = session.execute(assign_stmt).all()

        # Get duty officers in range
        duty_stmt = (
            select(DutyOfficer)
            .where(
                DutyOfficer.period_id == period_id,
                DutyOfficer.date >= start,
                DutyOfficer.date <= end,
            )
        )
        duty_officers = {
            do.date: do.commander_name
            for do in session.execute(duty_stmt).scalars().all()
        }

    # Build per-day structures
    schedule = []
    current = start
    while current <= end:
        day_data = {
            "date": current,
            "period_id": period_id,
            "tasks": {},
            "duty_officer": duty_officers.get(current),
        }
        for tid, info in task_info.items():
            day_data["tasks"][info["name"]] = {
                "task_id": tid,
                "shift_times": info["shift_times"],
                "shifts_per_day": info["shifts_per_day"],
                "shifts": {sn: [] for sn in range(1, info["shifts_per_day"] + 1)},
            }
        schedule.append(day_data)
        current += timedelta(days=1)

    # Fill assignments
    date_to_idx = {s["date"]: i for i, s in enumerate(schedule)}
    for sa, soldier in assignments:
        idx = date_to_idx.get(sa.date)
        if idx is None:
            continue
        tid = sa.task_id
        if tid not in task_info:
            continue
        task_name = task_info[tid]["name"]
        day_entry = schedule[idx]
        if task_name not in day_entry["tasks"]:
            continue
        shift_data = day_entry["tasks"][task_name]["shifts"]
        if sa.shift_number not in shift_data:
            shift_data[sa.shift_number] = []
        shift_data[sa.shift_number].append({
            "soldier_id": soldier.id,
            "name": f"{soldier.first_name} {soldier.last_name}",
            "role": sa.role_in_shift or "",
            "slot_name": slot_names.get(sa.task_slot_id, ""),
            "slot_id": sa.task_slot_id,
        })

    return schedule
