"""Service for managing approved drivers per reserve period.

Workflow:
1. מ"מ (squad commander) proposes soldiers from their squad as drivers
2. סמ"פ (deputy commander) approves/rejects proposed drivers
3. Only approved drivers can be assigned to "נהג" task slots
"""

from __future__ import annotations

from datetime import datetime
from sqlalchemy import select, and_

from military_manager.database import get_session, PeriodDriver, Soldier, PeriodSoldier
from military_manager.logger import log_action


def propose_driver(period_id: int, soldier_id: int,
                   proposed_by: str = "",
                   vehicle_type: str | None = None,
                   notes: str | None = None) -> PeriodDriver:
    """Propose a soldier as an approved driver for this period.
    
    Typically called by a מ"מ for soldiers in their squad.
    Status starts as 'pending' until סמ"פ approves.
    """
    with get_session() as session:
        # Check if already exists
        stmt = select(PeriodDriver).where(
            PeriodDriver.period_id == period_id,
            PeriodDriver.soldier_id == soldier_id,
        )
        existing = session.execute(stmt).scalar_one_or_none()
        if existing:
            # Re-propose (update info, reset to pending)
            existing.proposed_by = proposed_by
            existing.proposed_at = datetime.utcnow()
            existing.vehicle_type = vehicle_type
            existing.notes = notes
            existing.status = "pending"
            existing.approved_by = None
            existing.approved_at = None
            session.commit()
            session.refresh(existing)
            return existing

        pd = PeriodDriver(
            period_id=period_id,
            soldier_id=soldier_id,
            proposed_by=proposed_by,
            vehicle_type=vehicle_type,
            notes=notes,
            status="pending",
        )
        session.add(pd)
        session.commit()
        session.refresh(pd)
        log_action("driver_proposed", {
            "period_id": period_id,
            "soldier_id": soldier_id,
            "proposed_by": proposed_by,
        })
        return pd


def approve_driver(driver_id: int, approved_by: str = "") -> PeriodDriver | None:
    """Approve a proposed driver. Typically called by סמ"פ."""
    with get_session() as session:
        pd = session.get(PeriodDriver, driver_id)
        if not pd:
            return None
        pd.status = "approved"
        pd.approved_by = approved_by
        pd.approved_at = datetime.utcnow()
        session.commit()
        session.refresh(pd)
        log_action("driver_approved", {
            "driver_id": driver_id,
            "soldier_id": pd.soldier_id,
            "approved_by": approved_by,
        })
        return pd


def reject_driver(driver_id: int, approved_by: str = "",
                  notes: str | None = None) -> PeriodDriver | None:
    """Reject a proposed driver. Typically called by סמ"פ."""
    with get_session() as session:
        pd = session.get(PeriodDriver, driver_id)
        if not pd:
            return None
        pd.status = "rejected"
        pd.approved_by = approved_by
        pd.approved_at = datetime.utcnow()
        if notes:
            pd.notes = (pd.notes or "") + f" | סיבת דחייה: {notes}"
        session.commit()
        session.refresh(pd)
        return pd


def bulk_approve(driver_ids: list[int], approved_by: str = "") -> int:
    """Approve multiple drivers at once. Returns count approved."""
    count = 0
    for did in driver_ids:
        result = approve_driver(did, approved_by)
        if result:
            count += 1
    return count


def remove_driver(driver_id: int) -> bool:
    """Remove a driver entry entirely."""
    with get_session() as session:
        pd = session.get(PeriodDriver, driver_id)
        if not pd:
            return False
        session.delete(pd)
        session.commit()
        return True


def get_period_drivers(period_id: int,
                       status_filter: str | None = None) -> list[dict]:
    """Get all driver entries for a period.
    
    status_filter: None=all, 'pending', 'approved', 'rejected'
    """
    with get_session() as session:
        stmt = (
            select(PeriodDriver, Soldier, PeriodSoldier)
            .join(Soldier, PeriodDriver.soldier_id == Soldier.id)
            .outerjoin(PeriodSoldier, and_(
                PeriodSoldier.soldier_id == Soldier.id,
                PeriodSoldier.period_id == period_id,
            ))
            .where(PeriodDriver.period_id == period_id)
        )
        if status_filter:
            stmt = stmt.where(PeriodDriver.status == status_filter)
        
        stmt = stmt.order_by(PeriodDriver.status, Soldier.last_name)
        results = session.execute(stmt).all()
        
        drivers = []
        for pd, soldier, ps in results:
            drivers.append({
                "id": pd.id,
                "soldier_id": soldier.id,
                "military_id": soldier.military_id,
                "full_name": f"{soldier.first_name} {soldier.last_name}",
                "sub_unit": ps.sub_unit if ps else "—",
                "role": ps.role if ps else "",
                "task_role": ps.task_role if ps else "",
                "rank": ps.rank if ps else "",
                "status": pd.status,
                "proposed_by": pd.proposed_by or "",
                "proposed_at": pd.proposed_at,
                "approved_by": pd.approved_by or "",
                "approved_at": pd.approved_at,
                "vehicle_type": pd.vehicle_type or "",
                "license_valid": pd.license_valid,
                "notes": pd.notes or "",
            })
        return drivers


def get_approved_driver_ids(period_id: int) -> set[int]:
    """Get set of soldier IDs who are approved drivers for this period.
    
    This is the key function used by task slot filtering.
    """
    with get_session() as session:
        stmt = (
            select(PeriodDriver.soldier_id)
            .where(
                PeriodDriver.period_id == period_id,
                PeriodDriver.status == "approved",
            )
        )
        return {row[0] for row in session.execute(stmt).all()}


def get_potential_drivers(period_id: int) -> list[dict]:
    """Get soldiers who *could* be drivers (marked as נהג in role/task_role)
    but are not yet in the driver list for this period.
    
    Helps מ"מ find candidates to propose.
    """
    from military_manager.services.soldier_service import get_period_soldiers

    existing_ids = set()
    with get_session() as session:
        stmt = (
            select(PeriodDriver.soldier_id)
            .where(PeriodDriver.period_id == period_id)
        )
        existing_ids = {row[0] for row in session.execute(stmt).all()}

    all_soldiers = get_period_soldiers(period_id)
    
    potential = []
    for s in all_soldiers:
        if s["soldier_id"] in existing_ids:
            continue
        role = (s.get("role") or "").strip()
        task_role = (s.get("task_role") or "").strip()
        # Check if their role/task_role suggests driving capability
        if "נהג" in role or "נהג" in task_role:
            potential.append(s)
    return potential


def get_non_driver_soldiers(period_id: int) -> list[dict]:
    """Get ALL soldiers not yet in the driver list (for manual add).
    
    Unlike get_potential_drivers, this returns everyone - for cases
    where a soldier not marked as נהג can still be proposed.
    """
    from military_manager.services.soldier_service import get_period_soldiers

    existing_ids = set()
    with get_session() as session:
        stmt = (
            select(PeriodDriver.soldier_id)
            .where(PeriodDriver.period_id == period_id)
        )
        existing_ids = {row[0] for row in session.execute(stmt).all()}

    all_soldiers = get_period_soldiers(period_id)
    return [s for s in all_soldiers if s["soldier_id"] not in existing_ids]
