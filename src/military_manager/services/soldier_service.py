"""Service for managing soldiers and their period assignments."""

from __future__ import annotations

from datetime import date
from sqlalchemy import select, func, and_, or_
from sqlalchemy.orm import Session, joinedload

from military_manager.database import (
    get_session, Soldier, PeriodSoldier, Certification,
    SoldierCertification,
)
from military_manager.logger import log_action


# ─── Soldier CRUD ─────────────────────────────────────────────

def create_soldier(military_id: str, first_name: str, last_name: str,
                   **kwargs) -> Soldier:
    """Create a new soldier."""
    with get_session() as session:
        soldier = Soldier(
            military_id=military_id.strip(),
            first_name=first_name.strip(),
            last_name=last_name.strip(),
            **{k: v for k, v in kwargs.items() if v is not None}
        )
        session.add(soldier)
        session.commit()
        session.refresh(soldier)
        log_action("soldier_created", {
            "soldier_id": soldier.id,
            "military_id": military_id,
        })
        return soldier


def get_or_create_soldier(military_id: str, first_name: str, last_name: str,
                          **kwargs) -> tuple[Soldier, bool]:
    """Get existing soldier by military ID or create new one. Returns (soldier, created)."""
    with get_session() as session:
        stmt = select(Soldier).where(Soldier.military_id == military_id.strip())
        existing = session.execute(stmt).scalar_one_or_none()
        if existing:
            return existing, False
        soldier = Soldier(
            military_id=military_id.strip(),
            first_name=first_name.strip(),
            last_name=last_name.strip(),
            **{k: v for k, v in kwargs.items() if v is not None}
        )
        session.add(soldier)
        session.commit()
        session.refresh(soldier)
        return soldier, True


def get_soldier(soldier_id: int) -> Soldier | None:
    """Get a soldier by ID."""
    with get_session() as session:
        return session.get(Soldier, soldier_id)


def get_soldier_by_military_id(military_id: str) -> Soldier | None:
    """Get a soldier by military ID."""
    with get_session() as session:
        stmt = select(Soldier).where(Soldier.military_id == military_id.strip())
        return session.execute(stmt).scalar_one_or_none()


def get_all_soldiers() -> list[Soldier]:
    """Get all soldiers."""
    with get_session() as session:
        stmt = select(Soldier).order_by(Soldier.last_name, Soldier.first_name)
        return list(session.execute(stmt).scalars().all())


def update_soldier(soldier_id: int, **kwargs) -> Soldier | None:
    """Update a soldier's permanent info."""
    with get_session() as session:
        soldier = session.get(Soldier, soldier_id)
        if not soldier:
            return None
        for key, value in kwargs.items():
            if hasattr(soldier, key):
                setattr(soldier, key, value)
        session.commit()
        session.refresh(soldier)
        return soldier


def delete_soldier(soldier_id: int) -> bool:
    """Delete a soldier (only if no active period assignments)."""
    with get_session() as session:
        soldier = session.get(Soldier, soldier_id)
        if not soldier:
            return False
        # Check for active assignments
        stmt = select(func.count()).select_from(PeriodSoldier).where(
            PeriodSoldier.soldier_id == soldier_id,
            PeriodSoldier.is_active == True,
        )
        active_count = session.execute(stmt).scalar()
        if active_count and active_count > 0:
            raise ValueError(
                f"לא ניתן למחוק חייל עם שיבוצים פעילים ({active_count} שיבוצים). "
                "יש לבטל קודם את השיבוצים."
            )
        session.delete(soldier)
        session.commit()
        log_action("soldier_deleted", {"soldier_id": soldier_id})
        return True


# ─── Period Soldier assignments ───────────────────────────────

def assign_to_period(period_id: int, soldier_id: int, sub_unit: str,
                     **kwargs) -> PeriodSoldier:
    """Assign a soldier to a period with sub-unit and role."""
    with get_session() as session:
        # Check for existing assignment
        stmt = select(PeriodSoldier).where(
            PeriodSoldier.period_id == period_id,
            PeriodSoldier.soldier_id == soldier_id,
        )
        existing = session.execute(stmt).scalar_one_or_none()
        if existing:
            raise ValueError("החייל כבר משובץ לתקופה זו")

        ps = PeriodSoldier(
            period_id=period_id,
            soldier_id=soldier_id,
            sub_unit=sub_unit,
            **{k: v for k, v in kwargs.items() if v is not None}
        )
        session.add(ps)
        session.commit()
        session.refresh(ps)
        return ps


def get_period_soldiers(period_id: int, sub_unit: str | None = None,
                        active_only: bool = True,
                        exclude_irrelevant_unit: bool = False) -> list[dict]:
    """Get soldiers assigned to a period with their details.

    If exclude_irrelevant_unit is True, soldiers in the special "לא רלוונטי"
    sub-unit are excluded from results.
    """
    from military_manager.config import IRRELEVANT_UNIT

    with get_session() as session:
        stmt = (
            select(PeriodSoldier, Soldier)
            .join(Soldier, PeriodSoldier.soldier_id == Soldier.id)
            .where(PeriodSoldier.period_id == period_id)
        )
        if active_only:
            stmt = stmt.where(PeriodSoldier.is_active == True)
        if sub_unit:
            stmt = stmt.where(PeriodSoldier.sub_unit == sub_unit)
        if exclude_irrelevant_unit:
            stmt = stmt.where(PeriodSoldier.sub_unit != IRRELEVANT_UNIT)

        stmt = stmt.order_by(PeriodSoldier.sub_unit, PeriodSoldier.sort_order, Soldier.last_name)

        results = session.execute(stmt).all()
        soldiers = []
        for ps, soldier in results:
            soldiers.append({
                "period_soldier_id": ps.id,
                "soldier_id": soldier.id,
                "military_id": soldier.military_id,
                "first_name": soldier.first_name,
                "last_name": soldier.last_name,
                "full_name": f"{soldier.first_name} {soldier.last_name}",
                "phone": soldier.phone,
                "city": soldier.city,
                "gender": soldier.gender,
                "profile": soldier.profile,
                "birth_date": soldier.birth_date,
                "sub_unit": ps.sub_unit,
                "role": ps.role,
                "task_role": ps.task_role,
                "rank": ps.rank,
                "sort_order": ps.sort_order,
                "rifle_count": ps.rifle_count,
                "arrival_date": ps.arrival_date,
                "departure_date": ps.departure_date,
                "notes": ps.notes,
                "assignment_notes": ps.assignment_notes,
                "preferred_buddies": ps.preferred_buddies,
                "is_active": ps.is_active,
                "is_irrelevant": getattr(ps, "is_irrelevant", False) or False,
                "is_attached": getattr(ps, "is_attached", False) or False,
                "is_student": getattr(ps, "is_student", False) or False,
                "student_short_service": getattr(ps, "student_short_service", False) or False,
            })
        return soldiers


def update_period_soldier(period_soldier_id: int, **kwargs) -> PeriodSoldier | None:
    """Update a soldier's period-specific info."""
    with get_session() as session:
        ps = session.get(PeriodSoldier, period_soldier_id)
        if not ps:
            return None
        for key, value in kwargs.items():
            if hasattr(ps, key):
                setattr(ps, key, value)
        session.commit()
        session.refresh(ps)
        return ps


def get_sub_units(period_id: int) -> list[str]:
    """Get distinct sub-units for a period."""
    with get_session() as session:
        stmt = (
            select(PeriodSoldier.sub_unit)
            .where(PeriodSoldier.period_id == period_id)
            .distinct()
            .order_by(PeriodSoldier.sub_unit)
        )
        return [row[0] for row in session.execute(stmt).all()]


def reorder_soldiers(ordered_psids: list[int]) -> int:
    """Update sort_order for a list of PeriodSoldier IDs.

    The position in the list determines the new sort_order (0-based).
    This ensures the order set here is reflected everywhere in the app
    because all queries order by PeriodSoldier.sort_order.

    Returns the number of soldiers updated.
    """
    if not ordered_psids:
        return 0
    with get_session() as session:
        count = 0
        for new_order, psid in enumerate(ordered_psids):
            ps = session.get(PeriodSoldier, psid)
            if ps and ps.sort_order != new_order:
                ps.sort_order = new_order
                count += 1
        session.commit()
        if count:
            log_action("soldiers_reordered", {"updated": count, "total": len(ordered_psids)})
        return count


def remove_from_period(period_id: int, soldier_id: int) -> bool:
    """Remove a soldier from a period (soft-delete)."""
    with get_session() as session:
        stmt = select(PeriodSoldier).where(
            PeriodSoldier.period_id == period_id,
            PeriodSoldier.soldier_id == soldier_id,
        )
        ps = session.execute(stmt).scalar_one_or_none()
        if not ps:
            return False
        ps.is_active = False
        session.commit()
        log_action("soldier_removed_from_period", {
            "period_id": period_id,
            "soldier_id": soldier_id,
        })
        return True


# ─── Certifications ───────────────────────────────────────────

def get_or_create_certification(name: str, category: str | None = None) -> Certification:
    """Get or create a certification type."""
    with get_session() as session:
        stmt = select(Certification).where(Certification.name == name.strip())
        existing = session.execute(stmt).scalar_one_or_none()
        if existing:
            return existing
        cert = Certification(name=name.strip(), category=category)
        session.add(cert)
        session.commit()
        session.refresh(cert)
        return cert


def add_soldier_certification(soldier_id: int, certification_name: str,
                              granted_date: date | None = None,
                              expiry_date: date | None = None) -> SoldierCertification:
    """Add a certification to a soldier."""
    cert = get_or_create_certification(certification_name)
    with get_session() as session:
        sc = SoldierCertification(
            soldier_id=soldier_id,
            certification_id=cert.id,
            granted_date=granted_date,
            expiry_date=expiry_date,
        )
        session.add(sc)
        session.commit()
        session.refresh(sc)
        return sc


def get_soldier_certifications(soldier_id: int) -> list[dict]:
    """Get all certifications for a soldier."""
    with get_session() as session:
        stmt = (
            select(SoldierCertification, Certification)
            .join(Certification, SoldierCertification.certification_id == Certification.id)
            .where(SoldierCertification.soldier_id == soldier_id)
        )
        results = session.execute(stmt).all()
        return [
            {
                "id": sc.id,
                "name": cert.name,
                "category": cert.category,
                "granted_date": sc.granted_date,
                "expiry_date": sc.expiry_date,
            }
            for sc, cert in results
        ]


def get_all_certifications() -> list[Certification]:
    """Get all certification types."""
    with get_session() as session:
        stmt = select(Certification).order_by(Certification.name)
        return list(session.execute(stmt).scalars().all())
