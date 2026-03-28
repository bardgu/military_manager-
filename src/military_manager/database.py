"""Database setup with SQLAlchemy 2.0 and full schema definition."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, date
from sqlalchemy import (
    create_engine, Column, Integer, Text, Boolean, Date, DateTime,
    Float, ForeignKey, UniqueConstraint, Index, CheckConstraint, JSON,
    event,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session

from military_manager.config import DATABASE_URL, IS_POSTGRES

Base = declarative_base()


# ─── Companies (multi-platoon support) ────────────────────────
class Company(Base):
    """Company/platoon — top-level tenant for multi-platoon support."""
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, unique=True, nullable=False)   # פלוגה א / פלוגה ב / ...
    code = Column(Text, unique=True, nullable=False)    # short code: a, b, c, mafkada
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    periods = relationship("ReservePeriod", back_populates="company")
    users = relationship("User", back_populates="company")


# ─── Reserve Periods ──────────────────────────────────────────
class ReservePeriod(Base):
    __tablename__ = "reserve_periods"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True)  # nullable for backward compat
    name = Column(Text, nullable=False)
    location = Column(Text)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    is_active = Column(Boolean, default=False)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    # relationships
    company = relationship("Company", back_populates="periods")
    period_soldiers = relationship("PeriodSoldier", back_populates="period", cascade="all, delete-orphan")
    status_options = relationship("StatusOption", back_populates="period", cascade="all, delete-orphan")
    tasks = relationship("Task", back_populates="period", cascade="all, delete-orphan")
    daily_statuses = relationship("DailyStatus", back_populates="period", cascade="all, delete-orphan")


# ─── Soldiers (permanent info) ────────────────────────────────
class Soldier(Base):
    __tablename__ = "soldiers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    military_id = Column(Text, unique=True, nullable=False)
    first_name = Column(Text, nullable=False)
    last_name = Column(Text, nullable=False)
    phone = Column(Text)
    city = Column(Text)
    address = Column(Text)
    gender = Column(Text)
    profile = Column(Integer)
    birth_date = Column(Date)
    id_number = Column(Text)
    is_volunteer = Column(Boolean, default=False)
    medical_notes = Column(Text)
    personal_notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    # relationships
    period_assignments = relationship("PeriodSoldier", back_populates="soldier", cascade="all, delete-orphan")
    certifications = relationship("SoldierCertification", back_populates="soldier", cascade="all, delete-orphan")


# ─── Period-Soldier link (role/unit per period) ───────────────
class PeriodSoldier(Base):
    __tablename__ = "period_soldiers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    period_id = Column(Integer, ForeignKey("reserve_periods.id"), nullable=False)
    soldier_id = Column(Integer, ForeignKey("soldiers.id"), nullable=False)
    sub_unit = Column(Text, nullable=False)  # מחלקה 1/2/3/מפלג/מסופחים
    role = Column(Text)  # organizational: מ"פ, סמ"פ, מ"מ, לוחם...
    task_role = Column(Text)  # operational: מחלץ, נהג, קשר, חובש...
    rank = Column(Text)
    rifle_count = Column(Integer, default=0)
    sort_order = Column(Integer, default=999)  # preserve Excel row order / role hierarchy
    arrival_date = Column(Date)
    departure_date = Column(Date)
    notes = Column(Text)
    assignment_notes = Column(Text)  # structured assignment conditions: לילה בלבד, נהיגה בלבד, etc.
    preferred_buddies = Column(Text)  # JSON list of soldier_ids this soldier prefers to be with
    is_active = Column(Boolean, default=True)
    is_irrelevant = Column(Boolean, default=False)  # חיילים לא רלוונטיים בתעסוקה
    is_attached = Column(Boolean, default=False)  # חייל מסופח (לא חייל קבוע של הפלוגה)
    is_student = Column(Boolean, default=False)  # סטודנט — זכאי לקיצור שירות
    student_short_service = Column(Boolean, default=False)  # האם סטודנט מעוניין לקצר שירות

    __table_args__ = (
        UniqueConstraint("period_id", "soldier_id", name="uq_period_soldier"),
    )

    period = relationship("ReservePeriod", back_populates="period_soldiers")
    soldier = relationship("Soldier", back_populates="period_assignments")


# ─── Certifications ───────────────────────────────────────────
class Certification(Base):
    __tablename__ = "certifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, unique=True, nullable=False)
    category = Column(Text)  # חילוץ, נהיגה, רפואה, לחימה...
    description = Column(Text)


class SoldierCertification(Base):
    __tablename__ = "soldier_certifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    soldier_id = Column(Integer, ForeignKey("soldiers.id"), nullable=False)
    certification_id = Column(Integer, ForeignKey("certifications.id"), nullable=False)
    granted_date = Column(Date)
    expiry_date = Column(Date)
    notes = Column(Text)

    __table_args__ = (
        UniqueConstraint("soldier_id", "certification_id", name="uq_soldier_cert"),
    )

    soldier = relationship("Soldier", back_populates="certifications")
    certification = relationship("Certification")


# ─── Status Options (configurable per period) ─────────────────
class StatusOption(Base):
    __tablename__ = "status_options"

    id = Column(Integer, primary_key=True, autoincrement=True)
    period_id = Column(Integer, ForeignKey("reserve_periods.id"), nullable=False)
    name = Column(Text, nullable=False)
    category = Column(Text, nullable=False)  # present/away/arriving/leaving/final/alert/na
    color = Column(Text)
    sort_order = Column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("period_id", "name", name="uq_period_status"),
    )

    period = relationship("ReservePeriod", back_populates="status_options")


# ─── Status Groups (user-defined aggregation groups) ──────────
class StatusGroup(Base):
    __tablename__ = "status_groups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    period_id = Column(Integer, ForeignKey("reserve_periods.id"), nullable=False)
    name = Column(Text, nullable=False)  # e.g. "בבסיס", "בחופש", "בשמ\"פ"
    statuses_json = Column(Text, nullable=False)  # JSON list of status names
    color = Column(Text)  # display color
    sort_order = Column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("period_id", "name", name="uq_period_status_group"),
    )

    period = relationship("ReservePeriod")


# ─── App Settings (key-value per period) ──────────────────────
class AppSetting(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    period_id = Column(Integer, ForeignKey("reserve_periods.id"), nullable=False)
    key = Column(Text, nullable=False)
    value = Column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("period_id", "key", name="uq_period_setting"),
    )


# ─── Daily Status (THE core grid) ─────────────────────────────
class DailyStatus(Base):
    __tablename__ = "daily_status"

    id = Column(Integer, primary_key=True, autoincrement=True)
    period_id = Column(Integer, ForeignKey("reserve_periods.id"), nullable=False)
    soldier_id = Column(Integer, ForeignKey("soldiers.id"), nullable=False)
    date = Column(Date, nullable=False)
    status = Column(Text, nullable=False)
    notes = Column(Text)  # free-text note for this status (e.g. absence reason)
    updated_by = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("period_id", "soldier_id", "date", name="uq_daily_status"),
        Index("idx_daily_status_date", "period_id", "date"),
        Index("idx_daily_status_soldier", "soldier_id"),
    )

    period = relationship("ReservePeriod", back_populates="daily_statuses")
    soldier = relationship("Soldier")


# ─── Tasks (missions per period) ──────────────────────────────
class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    period_id = Column(Integer, ForeignKey("reserve_periods.id"), nullable=False)
    name = Column(Text, nullable=False)
    location = Column(Text)
    personnel_per_shift = Column(Integer, default=1)
    shifts_per_day = Column(Integer, default=1)
    total_daily_personnel = Column(Integer)
    shift_times = Column(Text)  # JSON string: ["05:30-13:30","13:30-21:30","21:30-05:30"]
    required_roles = Column(Text)  # JSON string: ["מפקד","נהג","קשר","לוחם"]
    is_active = Column(Boolean, default=True)
    notes = Column(Text)
    # Non-continuous rotation: task doesn't change soldiers every day
    non_continuous = Column(Boolean, default=False)
    # 'fixed_days' = rotate on specific weekdays, 'specific_dates' = rotate on given dates
    rotation_type = Column(Text)  # 'fixed_days' or 'specific_dates'
    # JSON: weekday numbers [0=Mon..6=Sun] for fixed_days, or ["2026-02-24",...] for specific_dates
    rotation_config = Column(Text)
    # Carmel (כיתת כוננות) linking: points to the paired task (e.g. כרמל→סיור)
    linked_task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True)
    # 'auto' = system decides, 'shared' = shared soldiers (approach 1), 'separate' = separate (approach 2)
    carmel_mode = Column(Text, default="auto")
    created_at = Column(DateTime, default=datetime.utcnow)

    period = relationship("ReservePeriod", back_populates="tasks")
    linked_task = relationship("Task", remote_side=[id], foreign_keys=[linked_task_id])
    slots = relationship("TaskSlot", back_populates="task", cascade="all, delete-orphan", order_by="TaskSlot.slot_order")
    shift_assignments = relationship("ShiftAssignment", back_populates="task", cascade="all, delete-orphan")


# ─── Task Slots (role-based positions per task) ───────────────
class TaskSlot(Base):
    """Each slot defines a named position within a task and which roles can fill it."""
    __tablename__ = "task_slots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False)
    slot_name = Column(Text, nullable=False)     # display name: "נהג", "מפקד משימה", "לוחם"
    slot_order = Column(Integer, default=0)       # display order
    quantity = Column(Integer, default=1)          # how many soldiers needed for this slot
    allowed_roles = Column(Text)                   # JSON: ["מ\"פ", "סמ\"פ", "מ\"מ"] - matches role OR task_role

    task = relationship("Task", back_populates="slots")


# ─── Shift Assignments (daily duty roster) ────────────────────
class ShiftAssignment(Base):
    __tablename__ = "shift_assignments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False)
    shift_number = Column(Integer, nullable=False)  # 1=morning, 2=afternoon, 3=night
    soldier_id = Column(Integer, ForeignKey("soldiers.id"), nullable=False)
    task_slot_id = Column(Integer, ForeignKey("task_slots.id"), nullable=True)  # which role-slot this fills
    role_in_shift = Column(Text)  # מפקד, נהג, קשר, לוחם — auto-filled from slot_name
    assigned_by = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("date", "task_id", "shift_number", "soldier_id", name="uq_shift_assign"),
        Index("idx_shift_date", "date"),
    )

    task = relationship("Task", back_populates="shift_assignments")
    task_slot = relationship("TaskSlot")
    soldier = relationship("Soldier")


# ─── Duty Officers ────────────────────────────────────────────
class DutyOfficer(Base):
    __tablename__ = "duty_officers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    period_id = Column(Integer, ForeignKey("reserve_periods.id"), nullable=False)
    date = Column(Date, nullable=False)
    soldier_id = Column(Integer, ForeignKey("soldiers.id"))
    commander_name = Column(Text, nullable=False)
    notes = Column(Text)

    __table_args__ = (
        UniqueConstraint("period_id", "date", name="uq_duty_officer"),
    )

    soldier = relationship("Soldier")


# ─── Equipment ────────────────────────────────────────────────
class EquipmentType(Base):
    __tablename__ = "equipment_types"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, unique=True, nullable=False)
    requires_form = Column(Boolean, default=False)
    description = Column(Text)


class EquipmentAssignment(Base):
    __tablename__ = "equipment_assignments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    period_id = Column(Integer, ForeignKey("reserve_periods.id"), nullable=False)
    soldier_id = Column(Integer, ForeignKey("soldiers.id"), nullable=False)
    equipment_type_id = Column(Integer, ForeignKey("equipment_types.id"), nullable=False)
    serial_number = Column(Text)
    form_signed = Column(Boolean, default=False)
    form_type = Column(Text)
    assigned_date = Column(Date)
    returned_date = Column(Date)
    notes = Column(Text)

    soldier = relationship("Soldier")
    equipment_type = relationship("EquipmentType")


# ─── Requests (leave/discharge/attachment) ────────────────────
class Request(Base):
    __tablename__ = "requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    period_id = Column(Integer, ForeignKey("reserve_periods.id"), nullable=False)
    soldier_id = Column(Integer, ForeignKey("soldiers.id"), nullable=False)
    request_type = Column(Text, nullable=False)  # leave/discharge/medical/attachment/personal
    subject = Column(Text)
    details = Column(Text)
    start_date = Column(Date)
    end_date = Column(Date)
    reason = Column(Text)
    status = Column(Text, default="pending")  # pending/approved/denied/resolved
    commander_notes = Column(Text)
    decided_by = Column(Text)
    decided_at = Column(DateTime)
    submitted_by_user_id = Column(Integer, ForeignKey("users.id"))  # user who created the request
    assigned_to_user_id = Column(Integer, ForeignKey("users.id"))   # target user who can approve/reject
    created_at = Column(DateTime, default=datetime.utcnow)

    soldier = relationship("Soldier")
    submitted_by_user = relationship("User", foreign_keys=[submitted_by_user_id])
    assigned_to_user = relationship("User", foreign_keys=[assigned_to_user_id])


# ─── Commanders ───────────────────────────────────────────────
class Commander(Base):
    __tablename__ = "commanders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False)
    role = Column(Text)  # מ"פ, סמ"פ, מ"מ
    sub_unit = Column(Text)  # מחלקה 1/2/3 etc
    phone = Column(Text)
    is_active = Column(Boolean, default=True)


# ─── Qualifications (operational capabilities per soldier) ───
class Qualification(Base):
    """Operational qualification types — e.g. מפקד משימה, חובש קרבי.

    These are NOT training certifications (Certification table handles those).
    A qualification means a soldier is authorized to serve in a specific
    operational capacity during a reserve period.
    """
    __tablename__ = "qualifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, unique=True, nullable=False)   # e.g. "מפקד משימה"
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class PeriodQualification(Base):
    """Per-period soldier qualification assignment.

    Similar to PeriodDriver — soldiers must be explicitly qualified
    for each reserve period.
    """
    __tablename__ = "period_qualifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    period_id = Column(Integer, ForeignKey("reserve_periods.id"), nullable=False)
    soldier_id = Column(Integer, ForeignKey("soldiers.id"), nullable=False)
    qualification_id = Column(Integer, ForeignKey("qualifications.id"), nullable=False)
    granted_by = Column(Text)          # who granted it
    granted_at = Column(DateTime, default=datetime.utcnow)
    notes = Column(Text)

    __table_args__ = (
        UniqueConstraint("period_id", "soldier_id", "qualification_id",
                         name="uq_period_qual"),
    )

    soldier = relationship("Soldier")
    qualification = relationship("Qualification")


# ─── Period Drivers (approved drivers per period) ────────────
class PeriodDriver(Base):
    """Approved drivers for a reserve period.
    
    Not every soldier marked as \"נהג\" from previous periods can drive this time.
    מ\"מ proposes drivers from their squad, סמ\"פ approves.
    Only approved drivers can be assigned to \"נהג\" task slots.
    """
    __tablename__ = "period_drivers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    period_id = Column(Integer, ForeignKey("reserve_periods.id"), nullable=False)
    soldier_id = Column(Integer, ForeignKey("soldiers.id"), nullable=False)
    proposed_by = Column(Text)          # name of מ"מ who proposed
    proposed_at = Column(DateTime, default=datetime.utcnow)
    status = Column(Text, default="pending")  # pending / approved / rejected
    approved_by = Column(Text)          # name of סמ"פ who approved
    approved_at = Column(DateTime)
    vehicle_type = Column(Text)         # רכב פרטי / משא / חפ"ק / ...
    license_valid = Column(Boolean, default=True)   # refresher completed?
    notes = Column(Text)

    __table_args__ = (
        UniqueConstraint("period_id", "soldier_id", name="uq_period_driver"),
    )

    soldier = relationship("Soldier")


# ─── Soldier Constraints (availability restrictions) ──────────
class SoldierConstraint(Base):
    """Per-period availability constraints for soldiers.

    Examples:
    - Soldier must leave on Tuesday morning → cannot be assigned to morning shift Tuesday
    - The last available shift is the afternoon before (to allow sleep)
    - With ignore_sleep=True, allow night shift even before departure
    """
    __tablename__ = "soldier_constraints"

    id = Column(Integer, primary_key=True, autoincrement=True)
    period_id = Column(Integer, ForeignKey("reserve_periods.id"), nullable=False)
    soldier_id = Column(Integer, ForeignKey("soldiers.id"), nullable=False)
    constraint_type = Column(Text, nullable=False)  # "departure"/"arrival"/"unavailable"/"duty_only"/"medical"/"custom"
    constraint_date = Column(Date, nullable=False)   # the start date of the constraint
    end_date = Column(Date)                           # end date (nullable = single day)
    constraint_time = Column(Text)                    # "morning"/"afternoon"/"night"/"all_day"
    ignore_sleep = Column(Boolean, default=False)     # if True, allow night shift before morning departure
    custom_reason = Column(Text)                      # free-text reason (e.g. "טיסה לחו"ל")
    requires_pitzul = Column(Boolean, default=False)  # if True, auto-set פיצול status
    blocked_tasks = Column(Text)                      # JSON list of task name patterns to block (e.g. '["סיור","כרמל"]')
    notes = Column(Text)
    created_by = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_constraint_period_soldier", "period_id", "soldier_id"),
    )

    soldier = relationship("Soldier")


# ─── Audit Log ────────────────────────────────────────────────
class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    commander_name = Column(Text)
    action = Column(Text, nullable=False)
    entity_type = Column(Text)  # soldier, task, assignment, status, equipment
    entity_id = Column(Integer)
    details = Column(Text)  # JSON
    correlation_id = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


# ─── Users (authentication & roles) ──────────────────────────
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True)  # nullable for backward compat
    username = Column(Text, unique=True, nullable=False)
    password_hash = Column(Text, nullable=False)
    display_name = Column(Text, nullable=False)
    role = Column(Text, nullable=False, default="viewer")
    # roles: "mefaked" (מ"פ — full access), "mm" (מ"מ — squad-level), "viewer" (צפייה בלבד)
    sub_unit = Column(Text)  # which squad the user manages (for מ"מ)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    company = relationship("Company", back_populates="users")


# ─── Organization Tree ───────────────────────────────────────
class OrgNode(Base):
    """Hierarchical org-chart node."""
    __tablename__ = "org_nodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    parent_id = Column(Integer, ForeignKey("org_nodes.id"), nullable=True)
    title = Column(Text, nullable=False)          # תפקיד / שם תפוקה
    holder_name = Column(Text)                    # שם הממלא
    phone = Column(Text)
    notes = Column(Text)
    sort_order = Column(Integer, default=0)       # סידור בתוך אותו הורה
    icon = Column(Text, default="👤")
    created_at = Column(DateTime, default=datetime.utcnow)

    children = relationship("OrgNode", backref="parent", remote_side=[id],
                            cascade="all, delete-orphan", single_parent=True,
                            order_by="OrgNode.sort_order")


# ─── Engine & Session ─────────────────────────────────────────

_engine = None
_SessionLocal = None


def get_engine():
    """Get or create the SQLAlchemy engine (PostgreSQL or SQLite)."""
    global _engine
    if _engine is None:
        engine_kwargs: dict = {"pool_pre_ping": True, "echo": False}

        if IS_POSTGRES:
            # PostgreSQL (Supabase) settings
            engine_kwargs["pool_size"] = 5
            engine_kwargs["max_overflow"] = 10
        else:
            # SQLite settings
            engine_kwargs["connect_args"] = {"check_same_thread": False}

        _engine = create_engine(DATABASE_URL, **engine_kwargs)

        if not IS_POSTGRES:
            # SQLite-only optimizations
            @event.listens_for(_engine, "connect")
            def set_sqlite_pragma(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA cache_size=-64000")
                cursor.close()

    return _engine


def get_session_factory():
    """Get or create the session factory."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


def get_session():
    """Get a new database session as context manager."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

get_session = contextmanager(get_session)


def init_db() -> None:
    """Create all database tables."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    _run_migrations(engine)


def _run_migrations(engine) -> None:
    """Run all pending ALTER TABLE migrations for existing databases.
    
    Works on both SQLite and PostgreSQL.
    """
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(engine)

    def _table_columns(table_name: str) -> set[str]:
        """Get column names for a table using SQLAlchemy inspector (DB-agnostic)."""
        try:
            return {c["name"] for c in inspector.get_columns(table_name)}
        except Exception:
            return set()

    def _add_column(table: str, col: str, col_type: str, default=None):
        """Add a column if it doesn't exist (DB-agnostic)."""
        cols = _table_columns(table)
        if col not in cols:
            default_clause = f" DEFAULT {default}" if default is not None else ""
            with engine.begin() as conn:
                conn.execute(
                    __import__("sqlalchemy").text(
                        f"ALTER TABLE {table} ADD COLUMN {col} {col_type}{default_clause}"
                    )
                )

    # ── requests table ──
    _add_column("requests", "submitted_by_user_id", "INTEGER")
    _add_column("requests", "assigned_to_user_id", "INTEGER")

    # ── period_soldiers table ──
    bool_type = "BOOLEAN" if IS_POSTGRES else "BOOLEAN"
    bool_default = "FALSE" if IS_POSTGRES else "0"
    _add_column("period_soldiers", "is_irrelevant", bool_type, bool_default)
    _add_column("period_soldiers", "is_attached", bool_type, bool_default)
    _add_column("period_soldiers", "is_student", bool_type, bool_default)
    _add_column("period_soldiers", "student_short_service", bool_type, bool_default)

    # ── soldier_constraints table ──
    _add_column("soldier_constraints", "blocked_tasks", "TEXT")

    # ── daily_status table ──
    _add_column("daily_status", "notes", "TEXT")

    # ── companies / multi-platoon support ──
    _add_column("reserve_periods", "company_id", "INTEGER")
    _add_column("users", "company_id", "INTEGER")
