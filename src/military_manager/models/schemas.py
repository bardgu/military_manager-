"""Pydantic schemas for data validation."""

from __future__ import annotations

import re
from datetime import date, datetime
from pydantic import BaseModel, Field, field_validator
from typing import Optional


# ─── Reserve Period ───────────────────────────────────────────
class PeriodCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    location: Optional[str] = None
    start_date: date
    end_date: date
    notes: Optional[str] = None

    @field_validator("end_date")
    @classmethod
    def end_after_start(cls, v: date, info) -> date:
        start = info.data.get("start_date")
        if start and v <= start:
            raise ValueError("תאריך סיום חייב להיות אחרי תאריך התחלה")
        return v


class PeriodUpdate(BaseModel):
    name: Optional[str] = None
    location: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


# ─── Soldier ──────────────────────────────────────────────────
class SoldierCreate(BaseModel):
    military_id: str = Field(..., min_length=1)
    first_name: str = Field(..., min_length=1)
    last_name: str = Field(..., min_length=1)
    phone: Optional[str] = None
    city: Optional[str] = None
    address: Optional[str] = None
    gender: Optional[str] = None
    profile: Optional[int] = None
    birth_date: Optional[date] = None
    id_number: Optional[str] = None
    is_volunteer: bool = False
    medical_notes: Optional[str] = None
    personal_notes: Optional[str] = None

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str | None) -> str | None:
        if v is None or v.strip() == "":
            return v
        cleaned = v.replace("-", "").replace(" ", "")
        if not re.match(r"^0\d{8,9}$", cleaned):
            raise ValueError("מספר טלפון לא תקין — יש להזין מספר ישראלי (למשל 050-1234567)")
        return cleaned

    @field_validator("military_id")
    @classmethod
    def validate_military_id(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("מספר אישי לא יכול להיות ריק")
        return cleaned


class SoldierUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    city: Optional[str] = None
    address: Optional[str] = None
    gender: Optional[str] = None
    profile: Optional[int] = None
    birth_date: Optional[date] = None
    id_number: Optional[str] = None
    is_volunteer: Optional[bool] = None
    medical_notes: Optional[str] = None
    personal_notes: Optional[str] = None


# ─── Period Soldier (assignment to period) ────────────────────
class PeriodSoldierCreate(BaseModel):
    period_id: int
    soldier_id: int
    sub_unit: str = Field(..., min_length=1)
    role: Optional[str] = None
    task_role: Optional[str] = None
    rank: Optional[str] = None
    rifle_count: int = 0
    arrival_date: Optional[date] = None
    departure_date: Optional[date] = None
    notes: Optional[str] = None


class PeriodSoldierUpdate(BaseModel):
    sub_unit: Optional[str] = None
    role: Optional[str] = None
    task_role: Optional[str] = None
    rank: Optional[str] = None
    rifle_count: Optional[int] = None
    arrival_date: Optional[date] = None
    departure_date: Optional[date] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


# ─── Task ─────────────────────────────────────────────────────
class TaskCreate(BaseModel):
    period_id: int
    name: str = Field(..., min_length=1)
    location: Optional[str] = None
    personnel_per_shift: int = Field(default=1, ge=1)
    shifts_per_day: int = Field(default=1, ge=1, le=4)
    total_daily_personnel: Optional[int] = None
    shift_times: Optional[str] = None
    required_roles: Optional[str] = None
    notes: Optional[str] = None


class TaskUpdate(BaseModel):
    name: Optional[str] = None
    location: Optional[str] = None
    personnel_per_shift: Optional[int] = None
    shifts_per_day: Optional[int] = None
    total_daily_personnel: Optional[int] = None
    shift_times: Optional[str] = None
    required_roles: Optional[str] = None
    is_active: Optional[bool] = None
    notes: Optional[str] = None


# ─── Daily Status ─────────────────────────────────────────────
class DailyStatusUpdate(BaseModel):
    period_id: int
    soldier_id: int
    date: date
    status: str = Field(..., min_length=1)
    updated_by: Optional[str] = None


class BulkStatusUpdate(BaseModel):
    """Update multiple soldiers' status for the same date."""
    period_id: int
    date: date
    updates: list[dict]  # [{"soldier_id": 1, "status": "בבסיס"}, ...]
    updated_by: Optional[str] = None


# ─── Shift Assignment ─────────────────────────────────────────
class ShiftAssignmentCreate(BaseModel):
    date: date
    task_id: int
    shift_number: int = Field(..., ge=1, le=3)
    soldier_id: int
    role_in_shift: Optional[str] = None
    assigned_by: Optional[str] = None


# ─── Equipment ────────────────────────────────────────────────
class EquipmentTypeCreate(BaseModel):
    name: str = Field(..., min_length=1)
    requires_form: bool = False
    description: Optional[str] = None


class EquipmentAssignmentCreate(BaseModel):
    period_id: int
    soldier_id: int
    equipment_type_id: int
    serial_number: Optional[str] = None
    form_signed: bool = False
    form_type: Optional[str] = None
    assigned_date: Optional[date] = None
    notes: Optional[str] = None


# ─── Request ──────────────────────────────────────────────────
class RequestCreate(BaseModel):
    period_id: int
    soldier_id: int
    request_type: str = Field(..., pattern=r"^(leave|discharge|medical|attachment|personal)$")
    subject: Optional[str] = None
    details: Optional[str] = None


class RequestDecision(BaseModel):
    status: str = Field(..., pattern=r"^(approved|denied|resolved)$")
    decided_by: str = Field(..., min_length=1)
