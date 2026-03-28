#!/usr/bin/env python3
"""One-time migration: copy all data from local SQLite to Supabase (PostgreSQL).

Usage:
    python migrate_to_supabase.py "postgresql://postgres.xxx:PASSWORD@host:6543/postgres"

Or set the SUPABASE_URL environment variable:
    set SUPABASE_URL=postgresql://...
    python migrate_to_supabase.py

This script:
  1. Reads ALL tables from the local SQLite database
  2. Creates the schema in Supabase (via SQLAlchemy create_all)
  3. Inserts all rows, preserving IDs and foreign keys
  4. Resets PostgreSQL sequences so new inserts get correct auto-IDs
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ── Ensure project modules are importable ──
sys.path.insert(0, str(Path(__file__).parent / "src"))

from sqlalchemy import create_engine, text, inspect as sa_inspect
from sqlalchemy.orm import sessionmaker

from military_manager.database import Base
from military_manager.config import DATA_DIR


# ── All models must be imported so Base.metadata knows about them ──
from military_manager.database import (          # noqa: F401
    Company, ReservePeriod, Soldier, PeriodSoldier,
    Certification, SoldierCertification,
    StatusOption, StatusGroup, AppSetting,
    DailyStatus, Task, TaskSlot, ShiftAssignment,
    DutyOfficer, EquipmentType, EquipmentAssignment,
    Request, Commander, Qualification, PeriodQualification,
    PeriodDriver, SoldierConstraint, AuditLog, User, OrgNode,
)


# ── Tables in dependency order (parents before children) ──
TABLE_ORDER = [
    "companies",
    "reserve_periods",
    "soldiers",
    "users",
    "certifications",
    "equipment_types",
    "qualifications",
    "org_nodes",
    "commanders",
    "period_soldiers",
    "soldier_certifications",
    "status_options",
    "status_groups",
    "app_settings",
    "daily_status",
    "tasks",
    "task_slots",
    "shift_assignments",
    "duty_officers",
    "equipment_assignments",
    "requests",
    "period_qualifications",
    "period_drivers",
    "soldier_constraints",
    "audit_log",
]


def migrate(sqlite_path: str, pg_url: str):
    """Copy all data from SQLite to PostgreSQL."""
    print(f"\n📂 Source: {sqlite_path}")
    print(f"🌐 Target: {pg_url[:60]}...")

    # ── Connect to source (SQLite) ──
    src_engine = create_engine(
        f"sqlite:///{sqlite_path}",
        connect_args={"check_same_thread": False},
    )
    SrcSession = sessionmaker(bind=src_engine)

    # ── Connect to target (PostgreSQL) ──
    dst_engine = create_engine(pg_url, pool_pre_ping=True)
    DstSession = sessionmaker(bind=dst_engine)

    # ── Create all tables in PostgreSQL ──
    print("\n🔧 Creating tables in PostgreSQL...")
    Base.metadata.create_all(dst_engine)
    print("   ✅ Tables created")

    # ── Get available tables from source ──
    src_inspector = sa_inspect(src_engine)
    src_tables = set(src_inspector.get_table_names())

    # ── Migrate each table ──
    total_rows = 0
    for table_name in TABLE_ORDER:
        if table_name not in src_tables:
            print(f"   ⏭️  {table_name} — not in source, skipping")
            continue

        # Read all rows from SQLite
        with SrcSession() as src_sess:
            rows = src_sess.execute(text(f"SELECT * FROM {table_name}")).fetchall()
            if not rows:
                print(f"   ⏭️  {table_name} — empty")
                continue

            # Get column names
            columns = src_sess.execute(text(f"SELECT * FROM {table_name} LIMIT 1")).keys()
            col_names = list(columns)

        # Insert into PostgreSQL
        with DstSession() as dst_sess:
            # Clear existing data (in case of re-run)
            dst_sess.execute(text(f"DELETE FROM {table_name}"))

            # Batch insert
            for row in rows:
                values = dict(zip(col_names, row))
                # Build INSERT statement
                cols = ", ".join(col_names)
                params = ", ".join(f":{c}" for c in col_names)
                dst_sess.execute(
                    text(f"INSERT INTO {table_name} ({cols}) VALUES ({params})"),
                    values,
                )

            dst_sess.commit()

        count = len(rows)
        total_rows += count
        print(f"   ✅ {table_name}: {count} rows")

    # ── Reset PostgreSQL sequences ──
    print("\n🔄 Resetting PostgreSQL sequences...")
    with DstSession() as dst_sess:
        for table_name in TABLE_ORDER:
            if table_name not in src_tables:
                continue
            # Check if table has an 'id' column with a sequence
            try:
                max_id = dst_sess.execute(
                    text(f"SELECT MAX(id) FROM {table_name}")
                ).scalar()
                if max_id is not None:
                    seq_name = f"{table_name}_id_seq"
                    dst_sess.execute(
                        text(f"SELECT setval('{seq_name}', :val)")
                        , {"val": max_id}
                    )
                    print(f"   ✅ {seq_name} → {max_id}")
            except Exception:
                pass  # Table may not have id column or sequence
        dst_sess.commit()

    print(f"\n🎉 Migration complete! {total_rows} total rows transferred.")


def main():
    # ── Get PostgreSQL URL ──
    pg_url = None
    if len(sys.argv) > 1:
        pg_url = sys.argv[1]
    else:
        pg_url = os.environ.get("SUPABASE_URL") or os.environ.get("DATABASE_URL")

    if not pg_url:
        print("❌ Usage: python migrate_to_supabase.py <postgresql_url>")
        print("   Or set SUPABASE_URL environment variable")
        sys.exit(1)

    # ── Find SQLite database ──
    sqlite_path = str(DATA_DIR / "military.db")
    if not Path(sqlite_path).exists():
        print(f"❌ SQLite database not found at: {sqlite_path}")
        sys.exit(1)

    # ── Confirm ──
    print("=" * 60)
    print("  SQLite → Supabase Migration Tool")
    print("=" * 60)
    print(f"\n  Source:  {sqlite_path}")
    print(f"  Target:  {pg_url[:60]}...")
    print(f"\n  This will OVERWRITE all data in the target database!")
    resp = input("\n  Continue? [y/N]: ").strip().lower()
    if resp != "y":
        print("  Cancelled.")
        sys.exit(0)

    migrate(sqlite_path, pg_url)


if __name__ == "__main__":
    main()
