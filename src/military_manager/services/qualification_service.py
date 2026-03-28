"""Service for managing operational qualifications (הסמכות).

Qualifications are distinct from training certifications:
- Certification = a course/training a soldier completed (e.g. חילוץ course)
- Qualification = an operational capability a soldier is authorized to
  perform during a specific reserve period (e.g. מפקד משימה)

Qualifications are period-scoped: a soldier must be explicitly qualified
for each reserve period.  Task slots can require specific qualifications
in their allowed_roles list, and the matching logic in task_service will
check both role AND qualifications.
"""

from __future__ import annotations

from datetime import datetime
from sqlalchemy import select, and_

from military_manager.database import (
    get_session, Qualification, PeriodQualification, Soldier, PeriodSoldier,
)
from military_manager.logger import log_action


# ─── Qualification type CRUD ──────────────────────────────────

def get_all_qualifications() -> list[Qualification]:
    """Get all defined qualification types."""
    with get_session() as session:
        stmt = select(Qualification).order_by(Qualification.name)
        return list(session.execute(stmt).scalars().all())


def get_qualification_names() -> list[str]:
    """Get just the names of all qualifications (for UI dropdowns)."""
    return [q.name for q in get_all_qualifications()]


def create_qualification(name: str, description: str | None = None) -> Qualification:
    """Create a new qualification type."""
    with get_session() as session:
        existing = session.execute(
            select(Qualification).where(Qualification.name == name)
        ).scalar_one_or_none()
        if existing:
            return existing

        q = Qualification(name=name, description=description)
        session.add(q)
        session.commit()
        session.refresh(q)
        log_action("qualification_created", {"name": name})
        return q


def delete_qualification(qualification_id: int) -> bool:
    """Delete a qualification type and all period assignments."""
    with get_session() as session:
        q = session.get(Qualification, qualification_id)
        if not q:
            return False
        # Delete period assignments first
        stmt = select(PeriodQualification).where(
            PeriodQualification.qualification_id == qualification_id
        )
        for pq in session.execute(stmt).scalars().all():
            session.delete(pq)
        session.delete(q)
        session.commit()
        log_action("qualification_deleted", {"name": q.name})
        return True


# ─── Period Qualification assignments ─────────────────────────

def get_period_qualifications(period_id: int,
                               qualification_id: int | None = None) -> list[dict]:
    """Get all qualification assignments for a period.

    Returns list of dicts with soldier info + qualification info.
    """
    with get_session() as session:
        stmt = (
            select(PeriodQualification, Soldier, Qualification)
            .join(Soldier, PeriodQualification.soldier_id == Soldier.id)
            .join(Qualification, PeriodQualification.qualification_id == Qualification.id)
            .where(PeriodQualification.period_id == period_id)
        )
        if qualification_id:
            stmt = stmt.where(PeriodQualification.qualification_id == qualification_id)
        stmt = stmt.order_by(Qualification.name, Soldier.last_name)

        rows = session.execute(stmt).all()
        return [
            {
                "id": pq.id,
                "soldier_id": sol.id,
                "soldier_name": f"{sol.first_name} {sol.last_name}",
                "military_id": sol.military_id,
                "qualification_id": qual.id,
                "qualification_name": qual.name,
                "granted_by": pq.granted_by,
                "granted_at": pq.granted_at,
                "notes": pq.notes,
            }
            for pq, sol, qual in rows
        ]


def assign_qualification(period_id: int, soldier_id: int,
                          qualification_id: int,
                          granted_by: str = "",
                          notes: str | None = None) -> PeriodQualification:
    """Assign a qualification to a soldier for this period."""
    with get_session() as session:
        # Check if already exists
        stmt = select(PeriodQualification).where(
            PeriodQualification.period_id == period_id,
            PeriodQualification.soldier_id == soldier_id,
            PeriodQualification.qualification_id == qualification_id,
        )
        existing = session.execute(stmt).scalar_one_or_none()
        if existing:
            return existing

        pq = PeriodQualification(
            period_id=period_id,
            soldier_id=soldier_id,
            qualification_id=qualification_id,
            granted_by=granted_by,
            notes=notes,
        )
        session.add(pq)
        session.commit()
        session.refresh(pq)
        log_action("qualification_assigned", {
            "period_id": period_id,
            "soldier_id": soldier_id,
            "qualification_id": qualification_id,
        })
        return pq


def remove_qualification(period_qualification_id: int) -> bool:
    """Remove a qualification assignment."""
    with get_session() as session:
        pq = session.get(PeriodQualification, period_qualification_id)
        if not pq:
            return False
        session.delete(pq)
        session.commit()
        log_action("qualification_removed", {"id": period_qualification_id})
        return True


def bulk_assign_qualification(period_id: int, soldier_ids: list[int],
                               qualification_id: int,
                               granted_by: str = "") -> int:
    """Assign a qualification to multiple soldiers at once. Returns count added."""
    count = 0
    for sid in soldier_ids:
        with get_session() as session:
            stmt = select(PeriodQualification).where(
                PeriodQualification.period_id == period_id,
                PeriodQualification.soldier_id == sid,
                PeriodQualification.qualification_id == qualification_id,
            )
            existing = session.execute(stmt).scalar_one_or_none()
            if not existing:
                pq = PeriodQualification(
                    period_id=period_id,
                    soldier_id=sid,
                    qualification_id=qualification_id,
                    granted_by=granted_by,
                )
                session.add(pq)
                session.commit()
                count += 1
    return count


def get_soldier_qualification_names(period_id: int, soldier_id: int) -> list[str]:
    """Get list of qualification names a soldier holds for this period."""
    with get_session() as session:
        stmt = (
            select(Qualification.name)
            .join(PeriodQualification, PeriodQualification.qualification_id == Qualification.id)
            .where(
                PeriodQualification.period_id == period_id,
                PeriodQualification.soldier_id == soldier_id,
            )
        )
        return list(session.execute(stmt).scalars().all())


def get_qualified_soldier_ids(period_id: int, qualification_name: str) -> set[int]:
    """Get set of soldier IDs that hold a specific qualification for this period."""
    with get_session() as session:
        stmt = (
            select(PeriodQualification.soldier_id)
            .join(Qualification, PeriodQualification.qualification_id == Qualification.id)
            .where(
                PeriodQualification.period_id == period_id,
                Qualification.name == qualification_name,
            )
        )
        return set(session.execute(stmt).scalars().all())
