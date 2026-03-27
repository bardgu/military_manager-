"""Backup service — automatic and manual database backups.

Provides:
- Automatic backup on server startup
- Scheduled periodic backups (every N hours)
- Manual backup/restore via settings (מ"פ only)
- Backup rotation (keeps last N backups)
- Integrity verification (PRAGMA integrity_check)
"""

from __future__ import annotations

import shutil
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from military_manager.config import DATABASE_PATH, DATA_DIR
from military_manager.logger import log_action

# ─── Configuration ─────────────────────────────────────────────
BACKUP_DIR = DATA_DIR / "backups"
MAX_BACKUPS = 30            # keep last 30 backups
AUTO_BACKUP_INTERVAL_HOURS = 4  # backup every 4 hours

_backup_thread: threading.Thread | None = None
_stop_event = threading.Event()


# ─── Core functions ────────────────────────────────────────────

def ensure_backup_dir() -> Path:
    """Create backup directory if it doesn't exist."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return BACKUP_DIR


def create_backup(reason: str = "manual", created_by: str | None = None) -> Path | None:
    """Create a timestamped backup of the database.

    Uses SQLite's built-in backup API for a safe, consistent copy
    even while the database is being written to.

    Returns the backup file path, or None on failure.
    """
    db_path = Path(DATABASE_PATH)
    if not db_path.exists():
        return None

    ensure_backup_dir()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"military_{timestamp}_{reason}.db"
    backup_path = BACKUP_DIR / backup_name

    try:
        # Use SQLite online backup API — safe even during writes
        source = sqlite3.connect(str(db_path))
        dest = sqlite3.connect(str(backup_path))
        source.backup(dest)
        dest.close()
        source.close()

        log_action("backup_created", {
            "path": str(backup_path),
            "reason": reason,
            "created_by": created_by or "system",
            "size_mb": round(backup_path.stat().st_size / (1024 * 1024), 2),
        })

        # Rotate old backups
        _rotate_backups()

        return backup_path
    except Exception as e:
        log_action("backup_failed", {"error": str(e), "reason": reason})
        return None


def list_backups() -> list[dict]:
    """List all available backups, newest first."""
    ensure_backup_dir()
    backups = []
    for f in sorted(BACKUP_DIR.glob("military_*.db"), reverse=True):
        stat = f.stat()
        parts = f.stem.split("_")  # military_20260223_143000_reason
        reason = parts[3] if len(parts) >= 4 else "unknown"
        try:
            ts = datetime.strptime(f"{parts[1]}_{parts[2]}", "%Y%m%d_%H%M%S")
        except (ValueError, IndexError):
            ts = datetime.fromtimestamp(stat.st_mtime)
        backups.append({
            "filename": f.name,
            "path": str(f),
            "size_mb": round(stat.st_size / (1024 * 1024), 2),
            "created_at": ts,
            "reason": reason,
        })
    return backups


def restore_backup(backup_path: str) -> bool:
    """Restore database from a backup file.

    1. Creates a safety backup of current DB before restoring.
    2. Verifies the backup file integrity.
    3. Replaces the current DB.

    Returns True on success.
    """
    src = Path(backup_path)
    if not src.exists():
        return False

    # Verify backup integrity first
    if not verify_integrity(str(src)):
        log_action("restore_failed", {"reason": "backup_corrupt", "path": backup_path})
        return False

    db_path = Path(DATABASE_PATH)

    try:
        # Safety backup before restoring
        create_backup(reason="pre-restore")

        # Close any WAL/SHM files
        wal_path = Path(str(db_path) + "-wal")
        shm_path = Path(str(db_path) + "-shm")

        # Use SQLite backup API to restore cleanly
        source = sqlite3.connect(str(src))
        dest = sqlite3.connect(str(db_path))
        source.backup(dest)
        dest.close()
        source.close()

        # Remove WAL/SHM if they exist (fresh start)
        if wal_path.exists():
            wal_path.unlink()
        if shm_path.exists():
            shm_path.unlink()

        log_action("backup_restored", {"from": backup_path})
        return True
    except Exception as e:
        log_action("restore_failed", {"error": str(e), "path": backup_path})
        return False


def verify_integrity(db_path: str | None = None) -> bool:
    """Run SQLite integrity check on a database file.

    Returns True if the database is healthy.
    """
    path = db_path or DATABASE_PATH
    if not Path(path).exists():
        return False

    try:
        conn = sqlite3.connect(str(path))
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check")
        result = cursor.fetchone()
        conn.close()
        return result[0] == "ok"
    except Exception:
        return False


def get_db_stats() -> dict:
    """Get database statistics."""
    db_path = Path(DATABASE_PATH)
    if not db_path.exists():
        return {"exists": False}

    stats = {
        "exists": True,
        "size_mb": round(db_path.stat().st_size / (1024 * 1024), 2),
        "modified": datetime.fromtimestamp(db_path.stat().st_mtime),
        "integrity": verify_integrity(),
    }

    # Count records in main tables
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        for table in [
            "soldiers", "reserve_periods", "period_soldiers",
            "daily_statuses", "tasks", "task_slots",
            "shift_assignments", "soldier_constraints",
            "period_drivers", "audit_log",
        ]:
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                stats[f"count_{table}"] = cursor.fetchone()[0]
            except Exception:
                stats[f"count_{table}"] = 0
        conn.close()
    except Exception:
        pass

    return stats


# ─── Auto-backup ───────────────────────────────────────────────

def start_auto_backup():
    """Start background thread for periodic backups.

    Called once on server startup from main.py.
    """
    global _backup_thread

    # Create startup backup
    create_backup(reason="startup")

    if _backup_thread is not None and _backup_thread.is_alive():
        return  # Already running

    _stop_event.clear()
    _backup_thread = threading.Thread(target=_auto_backup_loop, daemon=True)
    _backup_thread.start()


def stop_auto_backup():
    """Stop the auto-backup thread."""
    _stop_event.set()


def _auto_backup_loop():
    """Background loop — creates a backup every N hours."""
    interval_seconds = AUTO_BACKUP_INTERVAL_HOURS * 3600
    while not _stop_event.is_set():
        _stop_event.wait(interval_seconds)
        if not _stop_event.is_set():
            create_backup(reason="auto")


def _rotate_backups():
    """Keep only the last MAX_BACKUPS backup files."""
    ensure_backup_dir()
    all_backups = sorted(BACKUP_DIR.glob("military_*.db"), reverse=True)
    for old in all_backups[MAX_BACKUPS:]:
        try:
            old.unlink()
            log_action("backup_rotated", {"deleted": old.name})
        except Exception:
            pass
