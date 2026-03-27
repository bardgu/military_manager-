"""Service for managing reserve periods."""

from __future__ import annotations

from datetime import date
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from military_manager.database import (
    get_session, ReservePeriod, StatusOption, PeriodSoldier,
    Task, DailyStatus, PeriodQualification, PeriodDriver,
)
from military_manager.config import DEFAULT_STATUSES
from military_manager.logger import log_action


def create_period(name: str, start_date: date, end_date: date,
                  location: str | None = None, notes: str | None = None) -> ReservePeriod:
    """Create a new reserve period with default status options."""
    with get_session() as session:
        period = ReservePeriod(
            name=name,
            location=location,
            start_date=start_date,
            end_date=end_date,
            is_active=False,
            notes=notes,
        )
        session.add(period)
        session.flush()

        # Create default status options
        for i, status_def in enumerate(DEFAULT_STATUSES):
            opt = StatusOption(
                period_id=period.id,
                name=status_def["name"],
                category=status_def["category"],
                color=status_def["color"],
                sort_order=i,
            )
            session.add(opt)

        session.commit()
        session.refresh(period)
        log_action("period_created", {"period_id": period.id, "name": name})
        return period


def get_active_period() -> ReservePeriod | None:
    """Get the currently active reserve period (most recent if multiple)."""
    with get_session() as session:
        stmt = (
            select(ReservePeriod)
            .where(ReservePeriod.is_active == True)
            .order_by(ReservePeriod.start_date.desc())
            .limit(1)
        )
        return session.execute(stmt).scalar_one_or_none()


def get_all_periods() -> list[ReservePeriod]:
    """Get all reserve periods ordered by start date desc."""
    with get_session() as session:
        stmt = select(ReservePeriod).order_by(ReservePeriod.start_date.desc())
        return list(session.execute(stmt).scalars().all())


def get_period_by_id(period_id: int) -> ReservePeriod | None:
    """Get a period by ID."""
    with get_session() as session:
        return session.get(ReservePeriod, period_id)


def activate_period(period_id: int) -> None:
    """Set a period as active (deactivate all others)."""
    with get_session() as session:
        # Deactivate all
        session.execute(
            update(ReservePeriod).values(is_active=False)
        )
        # Activate selected
        session.execute(
            update(ReservePeriod)
            .where(ReservePeriod.id == period_id)
            .values(is_active=True)
        )
        session.commit()
        log_action("period_activated", {"period_id": period_id})


def update_period(period_id: int, **kwargs) -> ReservePeriod | None:
    """Update period fields."""
    with get_session() as session:
        period = session.get(ReservePeriod, period_id)
        if not period:
            return None
        for key, value in kwargs.items():
            if value is not None and hasattr(period, key):
                setattr(period, key, value)
        session.commit()
        session.refresh(period)
        return period


def delete_period(period_id: int) -> bool:
    """Delete a period and all related data."""
    with get_session() as session:
        period = session.get(ReservePeriod, period_id)
        if not period:
            return False
        session.delete(period)
        session.commit()
        log_action("period_deleted", {"period_id": period_id})
        return True


def copy_soldiers_from_period(source_period_id: int, target_period_id: int,
                              copy_qualifications: bool = True,
                              copy_drivers: bool = True) -> dict:
    """Copy soldier assignments, qualifications and drivers from one period to another.

    Returns dict with counts: {"soldiers": N, "qualifications": N, "drivers": N}
    """
    result = {"soldiers": 0, "qualifications": 0, "drivers": 0}

    with get_session() as session:
        # ── Copy soldiers ──
        stmt = select(PeriodSoldier).where(
            PeriodSoldier.period_id == source_period_id,
            PeriodSoldier.is_active == True,
        )
        source_soldiers = session.execute(stmt).scalars().all()
        for ps in source_soldiers:
            new_ps = PeriodSoldier(
                period_id=target_period_id,
                soldier_id=ps.soldier_id,
                sub_unit=ps.sub_unit,
                role=ps.role,
                task_role=ps.task_role,
                rank=ps.rank,
                rifle_count=ps.rifle_count,
                notes=ps.notes,
                is_active=True,
            )
            session.add(new_ps)
            result["soldiers"] += 1

        # ── Copy qualifications ──
        if copy_qualifications:
            qual_stmt = select(PeriodQualification).where(
                PeriodQualification.period_id == source_period_id,
            )
            source_quals = session.execute(qual_stmt).scalars().all()
            for pq in source_quals:
                new_pq = PeriodQualification(
                    period_id=target_period_id,
                    soldier_id=pq.soldier_id,
                    qualification_id=pq.qualification_id,
                    granted_by=pq.granted_by,
                    notes=pq.notes,
                )
                session.add(new_pq)
                result["qualifications"] += 1

        # ── Copy drivers ──
        if copy_drivers:
            drv_stmt = select(PeriodDriver).where(
                PeriodDriver.period_id == source_period_id,
            )
            source_drivers = session.execute(drv_stmt).scalars().all()
            for pd in source_drivers:
                new_pd = PeriodDriver(
                    period_id=target_period_id,
                    soldier_id=pd.soldier_id,
                    proposed_by=pd.proposed_by,
                    status=pd.status,
                    approved_by=pd.approved_by,
                    approved_at=pd.approved_at,
                    vehicle_type=pd.vehicle_type,
                    license_valid=pd.license_valid,
                    notes=pd.notes,
                )
                session.add(new_pd)
                result["drivers"] += 1

        session.commit()
        log_action("soldiers_copied", {
            "source": source_period_id,
            "target": target_period_id,
            **result,
        })
        return result


def get_status_options(period_id: int) -> list[StatusOption]:
    """Get status options for a period."""
    with get_session() as session:
        stmt = (
            select(StatusOption)
            .where(StatusOption.period_id == period_id)
            .order_by(StatusOption.sort_order)
        )
        return list(session.execute(stmt).scalars().all())
