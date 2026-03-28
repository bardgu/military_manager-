"""Service for managing equipment assignments."""

from __future__ import annotations

from datetime import date
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from military_manager.database import (
    get_session, EquipmentType, EquipmentAssignment, Soldier,
)
from military_manager.logger import log_action


def create_equipment_type(name: str, requires_form: bool = False,
                          description: str | None = None) -> EquipmentType:
    """Create a new equipment type."""
    with get_session() as session:
        et = EquipmentType(name=name, requires_form=requires_form, description=description)
        session.add(et)
        session.commit()
        session.refresh(et)
        return et


def get_or_create_equipment_type(name: str, **kwargs) -> EquipmentType:
    """Get or create equipment type by name."""
    with get_session() as session:
        stmt = select(EquipmentType).where(EquipmentType.name == name.strip())
        existing = session.execute(stmt).scalar_one_or_none()
        if existing:
            return existing
        et = EquipmentType(name=name.strip(), **{k: v for k, v in kwargs.items() if v is not None})
        session.add(et)
        session.commit()
        session.refresh(et)
        return et


def get_all_equipment_types() -> list[EquipmentType]:
    """Get all equipment types."""
    with get_session() as session:
        stmt = select(EquipmentType).order_by(EquipmentType.name)
        return list(session.execute(stmt).scalars().all())


def assign_equipment(period_id: int, soldier_id: int, equipment_type_id: int,
                     **kwargs) -> EquipmentAssignment:
    """Assign equipment to a soldier."""
    with get_session() as session:
        ea = EquipmentAssignment(
            period_id=period_id,
            soldier_id=soldier_id,
            equipment_type_id=equipment_type_id,
            **{k: v for k, v in kwargs.items() if v is not None}
        )
        session.add(ea)
        session.commit()
        session.refresh(ea)
        log_action("equipment_assigned", {
            "soldier_id": soldier_id,
            "equipment_type_id": equipment_type_id,
        })
        return ea


def return_equipment(assignment_id: int) -> bool:
    """Mark equipment as returned."""
    with get_session() as session:
        ea = session.get(EquipmentAssignment, assignment_id)
        if not ea:
            return False
        ea.returned_date = date.today()
        session.commit()
        return True


def get_soldier_equipment(period_id: int, soldier_id: int) -> list[dict]:
    """Get equipment assigned to a soldier in a period."""
    with get_session() as session:
        stmt = (
            select(EquipmentAssignment, EquipmentType)
            .join(EquipmentType, EquipmentAssignment.equipment_type_id == EquipmentType.id)
            .where(
                EquipmentAssignment.period_id == period_id,
                EquipmentAssignment.soldier_id == soldier_id,
            )
        )
        results = session.execute(stmt).all()
        return [
            {
                "assignment_id": ea.id,
                "type_name": et.name,
                "serial_number": ea.serial_number,
                "form_signed": ea.form_signed,
                "form_type": ea.form_type,
                "assigned_date": ea.assigned_date,
                "returned_date": ea.returned_date,
                "notes": ea.notes,
            }
            for ea, et in results
        ]


def get_period_equipment_report(period_id: int) -> list[dict]:
    """Get full equipment report for a period."""
    with get_session() as session:
        stmt = (
            select(EquipmentAssignment, EquipmentType, Soldier)
            .join(EquipmentType, EquipmentAssignment.equipment_type_id == EquipmentType.id)
            .join(Soldier, EquipmentAssignment.soldier_id == Soldier.id)
            .where(EquipmentAssignment.period_id == period_id)
            .order_by(EquipmentType.name, Soldier.last_name)
        )
        results = session.execute(stmt).all()
        return [
            {
                "assignment_id": ea.id,
                "soldier_name": f"{s.first_name} {s.last_name}",
                "soldier_id": s.id,
                "military_id": s.military_id,
                "equipment_type": et.name,
                "serial_number": ea.serial_number,
                "form_signed": ea.form_signed,
                "form_type": ea.form_type,
                "assigned_date": ea.assigned_date,
                "returned_date": ea.returned_date,
            }
            for ea, et, s in results
        ]
