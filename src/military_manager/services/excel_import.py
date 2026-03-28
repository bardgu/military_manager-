"""Excel import service — handles importing the company Excel file.

Supports the known worksheet formats from the real Excel files:
- סד"כ / תכנון קדימה: Full soldier roster with roles and certifications
- תכנון יציאות: Daily status grid with dates as columns
- משימות פלוגה: Mission/task definitions
- אקדחים ונהגים: Equipment assignments
- מחלקה 1/2/3 / מפלג / עוזיה: Per-squad status grids
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import select

from military_manager.database import get_session, Soldier, PeriodSoldier
from military_manager.services.soldier_service import (
    get_or_create_soldier, assign_to_period, add_soldier_certification,
)
from military_manager.services.status_service import set_status
from military_manager.services.task_service import create_task
from military_manager.services.equipment_service import (
    get_or_create_equipment_type, assign_equipment,
)
from military_manager.logger import log_action


def _clean_str(val) -> str | None:
    """Clean a cell value to string."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    return s if s else None


def _clean_int(val) -> int | None:
    """Clean a cell value to int."""
    if val is None:
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _clean_date(val) -> date | None:
    """Clean a cell value to date."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    try:
        s = str(val).strip()
        # Try common formats
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d.%m.%Y"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
    except Exception:
        pass
    return None


def _is_valid_name(val: str | None) -> bool:
    """Check that a value looks like a real Hebrew name (not a number)."""
    if not val:
        return False
    return bool(re.search(r'[\u0590-\u05FF]', val))


def _is_valid_military_id(val: str | None) -> bool:
    """Check that a military ID is a proper 6-8 digit number."""
    if not val:
        return False
    try:
        digits = str(int(float(val)))
        return len(digits) >= 6
    except (ValueError, TypeError):
        return False


# Words that appear in Excel summary rows — never valid soldier data.
# NOTE: "קצינים" and "חוגרים" are valid values in "תת סוג תקן" column,
# so they must NOT be treated as summary keywords.
_SUMMARY_KEYWORDS = {"סה\"כ", 'סה"כ', "תקן", "מצבה", "שינויים"}


def _is_summary_row(row_values: list) -> bool:
    """Check if a row looks like a summary/totals row rather than a soldier.

    Only flag rows where the FIRST non-empty cell is a summary keyword,
    or where no cell has a valid military ID (6+ digit number).
    """
    for v in row_values:
        s = _clean_str(v)
        if s and s in _SUMMARY_KEYWORDS:
            return True
    return False


def _parse_phone(val) -> str | None:
    """Parse and clean Israeli phone number."""
    s = _clean_str(val)
    if not s:
        return None
    cleaned = s.replace("-", "").replace(" ", "").replace(".", "")
    if cleaned.startswith("972"):
        cleaned = "0" + cleaned[3:]
    # Keep only digits
    digits = re.sub(r"[^\d]", "", cleaned)
    if len(digits) >= 9 and digits.startswith("0"):
        return digits
    return s  # Return original if can't parse


def import_roster_sheet(
    filepath: str | Path,
    period_id: int,
    sheet_name: str = "סד\"כ ללא חובשים ואנו\"ח",
) -> dict:
    """Import soldier roster from the סד"כ or תכנון קדימה sheet.

    Expected columns (by position or name):
    - תת מסגרת (sub-unit)
    - תאור תפקיד (role description)
    - מספר אישי (military ID)
    - דרגה (rank)
    - פרטי (first name)
    - משפחה (last name)
    - נייד (phone)
    - ישוב (city)
    - כתובת (address)
    - מגדר (gender)
    - רובאי (rifle count)
    - מתנדב (volunteer)
    - ת.לידה (birth date)
    - גיל (age)
    - הכשרות וקורסים בצבא (certifications)
    """
    results = {"created": 0, "updated": 0, "errors": [], "total": 0}

    try:
        df = pd.read_excel(filepath, sheet_name=sheet_name, header=None)
    except Exception as e:
        results["errors"].append(f"שגיאה בקריאת הגיליון '{sheet_name}': {e}")
        return results

    # Find header row by looking for "מספר אישי"
    header_row = None
    for idx, row in df.iterrows():
        row_values = [_clean_str(v) for v in row.values]
        if any(v and "מספר אישי" in v for v in row_values if v):
            header_row = idx
            break

    if header_row is None:
        results["errors"].append("לא נמצאה שורת כותרת עם 'מספר אישי'")
        return results

    # Set headers
    headers = [_clean_str(v) or f"col_{i}" for i, v in enumerate(df.iloc[header_row])]
    df.columns = headers

    # Find column indices
    col_map = {}
    for i, h in enumerate(headers):
        if not h:
            continue
        h_lower = h.strip()
        if "מספר אישי" in h_lower:
            col_map["military_id"] = i
        elif "תת מסגרת" in h_lower:
            col_map["sub_unit"] = i
        elif any(x in h_lower for x in ["תאור תפקיד", "תפקיד"]) and "role" not in col_map:
            col_map["role"] = i
        elif h_lower == "עיסוק":
            col_map["task_role"] = i
        elif "דרגה" in h_lower and "בתקן" not in h_lower:
            col_map["rank"] = i
        elif h_lower == "פרטי":
            col_map["first_name"] = i
        elif h_lower == "משפחה":
            col_map["last_name"] = i
        elif "נייד" in h_lower:
            col_map["phone"] = i
        elif "ישוב" in h_lower:
            col_map["city"] = i
        elif "כתובת" in h_lower:
            col_map["address"] = i
        elif "מגדר" in h_lower:
            col_map["gender"] = i
        elif "רובאי" in h_lower:
            col_map["rifle_count"] = i
        elif "מתנדב" in h_lower:
            col_map["volunteer"] = i
        elif "ת.לידה" in h_lower or "תאריך לידה" in h_lower:
            col_map["birth_date"] = i
        elif "פרופיל" in h_lower:
            col_map["profile"] = i
        elif "הכשרות" in h_lower or "קורסים" in h_lower:
            col_map["certifications"] = i
        elif "הערות" in h_lower and "notes" not in col_map:
            col_map["notes"] = i

    # Map "תת סוג תקן" for officer/enlisted classification
    for i, h in enumerate(headers):
        if h and "תת סוג תקן" in h.strip():
            col_map["soldier_type"] = i
            break
    # Map "עיסוק" if not yet found (header detection is positional)
    if "task_role" not in col_map:
        for i, h in enumerate(headers):
            if h and h.strip() == "עיסוק":
                col_map["task_role"] = i
                break

    if "military_id" not in col_map or "first_name" not in col_map:
        results["errors"].append("לא נמצאו עמודות חובה: מספר אישי, פרטי")
        return results

    # Track current sub-unit for rows that inherit it
    current_sub_unit = "מפקדת הפלוגה"
    current_soldier_type = None  # קצינים / חוגרים — propagated like sub_unit
    soldier_order = 0  # preserve Excel row order
    # Track officer/enlisted counts for validation
    officer_count = 0
    enlisted_count = 0

    for idx in range(header_row + 1, len(df)):
        row = df.iloc[idx]
        results["total"] += 1
        soldier_order += 1

        # Quick check: skip rows that contain summary keywords anywhere
        row_vals = [_clean_str(row.iloc[i]) for i in range(min(len(row), 10))]
        if _is_summary_row(row_vals):
            continue

        mil_id = _clean_str(row.iloc[col_map["military_id"]])
        first_name = _clean_str(row.iloc[col_map["first_name"]])
        last_name = _clean_str(row.iloc[col_map.get("last_name", col_map["first_name"])])

        if not mil_id or not first_name:
            continue

        # Skip summary / numeric rows — a real soldier has a Hebrew name
        if not _is_valid_name(first_name):
            continue

        # Skip rows with short / non-numeric IDs (summary counts etc.)
        if not _is_valid_military_id(mil_id):
            continue

        # Clean military ID — could be float
        try:
            mil_id = str(int(float(mil_id)))
        except (ValueError, TypeError):
            mil_id = str(mil_id).strip()

        # Update sub-unit tracking
        if "sub_unit" in col_map:
            sub = _clean_str(row.iloc[col_map["sub_unit"]])
            if sub:
                current_sub_unit = sub

        # Update soldier type tracking (merged cells — carry forward)
        if "soldier_type" in col_map:
            stype = _clean_str(row.iloc[col_map["soldier_type"]])
            if stype:
                current_soldier_type = stype

        try:
            soldier, created = get_or_create_soldier(
                military_id=mil_id,
                first_name=first_name,
                last_name=last_name or "",
                phone=_parse_phone(row.iloc[col_map["phone"]]) if "phone" in col_map else None,
                city=_clean_str(row.iloc[col_map["city"]]) if "city" in col_map else None,
                address=_clean_str(row.iloc[col_map["address"]]) if "address" in col_map else None,
                gender=_clean_str(row.iloc[col_map["gender"]]) if "gender" in col_map else None,
                profile=_clean_int(row.iloc[col_map["profile"]]) if "profile" in col_map else None,
                birth_date=_clean_date(row.iloc[col_map["birth_date"]]) if "birth_date" in col_map else None,
                is_volunteer=_clean_str(row.iloc[col_map["volunteer"]]) == "כן" if "volunteer" in col_map else False,
            )

            if created:
                results["created"] += 1
            else:
                results["updated"] += 1

            # Track officer/enlisted for validation (using carried-forward type)
            if current_soldier_type:
                if "קצינים" in current_soldier_type:
                    officer_count += 1
                elif "חוגרים" in current_soldier_type:
                    enlisted_count += 1

            # Assign to period
            role = _clean_str(row.iloc[col_map["role"]]) if "role" in col_map else None
            task_role = _clean_str(row.iloc[col_map["task_role"]]) if "task_role" in col_map else None
            rank = _clean_str(row.iloc[col_map["rank"]]) if "rank" in col_map else None
            rifle = _clean_int(row.iloc[col_map["rifle_count"]]) if "rifle_count" in col_map else 0
            notes = _clean_str(row.iloc[col_map["notes"]]) if "notes" in col_map else None

            try:
                assign_to_period(
                    period_id=period_id,
                    soldier_id=soldier.id,
                    sub_unit=current_sub_unit,
                    role=role,
                    task_role=task_role,
                    rank=rank,
                    rifle_count=rifle or 0,
                    sort_order=soldier_order,
                    notes=notes,
                )
            except ValueError:
                pass  # Already assigned — skip

            # Import certifications
            if "certifications" in col_map:
                cert_str = _clean_str(row.iloc[col_map["certifications"]])
                if cert_str:
                    cert_names = [c.strip() for c in cert_str.split(",") if c.strip()]
                    for cert_name in cert_names:
                        try:
                            add_soldier_certification(soldier.id, cert_name)
                        except Exception:
                            pass  # Skip duplicate certs

        except Exception as e:
            results["errors"].append(f"שורה {idx + 1}: {e}")

    # Add officer/enlisted validation to results
    results["officers"] = officer_count
    results["enlisted"] = enlisted_count
    results["skipped_summary"] = results["total"] - (results["created"] + results["updated"]) - len(results["errors"])

    log_action("roster_imported", {
        "period_id": period_id,
        "created": results["created"],
        "updated": results["updated"],
        "officers": officer_count,
        "enlisted": enlisted_count,
        "errors": len(results["errors"]),
    })
    return results


def import_status_sheet(
    filepath: str | Path,
    period_id: int,
    sheet_name: str = "תכנון יציאות",
    updated_by: str | None = None,
) -> dict:
    """Import daily status data from a status grid sheet.

    Expected format:
    - Row headers: תאריכים as column headers (dates)
    - Left columns: מספר אישי, מחלקה, תפקיד, שם, שם משפחה
    - Grid cells: status values (בבסיס, חופש, etc.)
    """
    results = {"updated": 0, "errors": [], "total": 0}

    try:
        df = pd.read_excel(filepath, sheet_name=sheet_name, header=None)
    except Exception as e:
        results["errors"].append(f"שגיאה בקריאת הגיליון '{sheet_name}': {e}")
        return results

    # Find the header row with dates
    date_row_idx = None
    for idx, row in df.iterrows():
        date_count = sum(1 for v in row.values if isinstance(v, datetime))
        if date_count > 5:
            date_row_idx = idx
            break

    if date_row_idx is None:
        results["errors"].append("לא נמצאה שורת תאריכים")
        return results

    # Find the data header row (with מספר אישי)
    data_start = None
    mil_id_col = None
    name_col = None
    family_col = None

    for idx in range(max(0, date_row_idx - 3), date_row_idx + 3):
        row_vals = [_clean_str(v) for v in df.iloc[idx].values]
        for ci, v in enumerate(row_vals):
            if v and "מספר אישי" in v:
                mil_id_col = ci
            if v and v == "שם":
                name_col = ci
            if v and "משפחה" in v:
                family_col = ci
        if mil_id_col is not None:
            data_start = idx + 1
            break

    if mil_id_col is None:
        # Try matching by first name / last name columns
        results["errors"].append("לא נמצאה עמודת מספר אישי")
        return results

    # Get date columns
    date_cols: dict[int, date] = {}
    for ci, val in enumerate(df.iloc[date_row_idx].values):
        d = _clean_date(val)
        if d:
            date_cols[ci] = d

    if not date_cols:
        results["errors"].append("לא נמצאו תאריכים בשורת הכותרת")
        return results

    # Process data rows
    for idx in range(data_start, len(df)):
        row = df.iloc[idx]
        mil_id = _clean_str(row.iloc[mil_id_col])
        if not mil_id:
            continue

        try:
            mil_id = str(int(float(mil_id)))
        except (ValueError, TypeError):
            continue

        # Find soldier
        with get_session() as session:
            stmt = select(Soldier).where(Soldier.military_id == mil_id)
            soldier = session.execute(stmt).scalar_one_or_none()
            if not soldier:
                continue

        results["total"] += 1

        # Update statuses for each date
        for ci, d in date_cols.items():
            status = _clean_str(row.iloc[ci])
            if status and status not in ("#DIV/0!", "#REF!"):
                try:
                    set_status(period_id, soldier.id, d, status, updated_by)
                    results["updated"] += 1
                except Exception as e:
                    results["errors"].append(f"חייל {mil_id}, תאריך {d}: {e}")

    log_action("status_imported", {
        "period_id": period_id,
        "updated": results["updated"],
        "errors": len(results["errors"]),
    })
    return results


def import_tasks_sheet(
    filepath: str | Path,
    period_id: int,
    sheet_name: str = "משימות פלוגה",
) -> dict:
    """Import tasks/missions from the missions sheet."""
    results = {"created": 0, "errors": [], "total": 0}

    try:
        df = pd.read_excel(filepath, sheet_name=sheet_name, header=None)
    except Exception as e:
        results["errors"].append(f"שגיאה בקריאת הגיליון '{sheet_name}': {e}")
        return results

    # Find task rows (look for known pattern: משימה, כ"א דרוש, etc.)
    header_row = None
    for idx, row in df.iterrows():
        vals = [_clean_str(v) for v in row.values]
        if any(v and "משימה" in v for v in vals if v):
            header_row = idx
            break

    if header_row is None:
        results["errors"].append("לא נמצאה שורת כותרת משימות")
        return results

    # Find column positions
    task_col = None
    personnel_col = None
    shifts_col = None
    total_col = None

    for ci, v in enumerate(df.iloc[header_row].values):
        s = _clean_str(v)
        if not s:
            continue
        if s == "משימה":
            task_col = ci
        elif "כ\"א" in s or "כ\"א" in s.replace("'", '"'):
            personnel_col = ci
        elif "משמרות" in s:
            shifts_col = ci
        elif "סה\"כ" in s or "סה\"כ" in s.replace("'", '"'):
            total_col = ci

    if task_col is None:
        results["errors"].append("לא נמצאה עמודת משימה")
        return results

    # Read tasks
    for idx in range(header_row + 1, min(header_row + 20, len(df))):
        row = df.iloc[idx]
        task_name = _clean_str(row.iloc[task_col])
        if not task_name or "סה\"כ" in task_name:
            continue

        results["total"] += 1
        personnel = _clean_int(row.iloc[personnel_col]) if personnel_col else 1
        shifts = _clean_int(row.iloc[shifts_col]) if shifts_col else 1
        total = _clean_int(row.iloc[total_col]) if total_col else None

        try:
            create_task(
                period_id=period_id,
                name=task_name,
                personnel_per_shift=personnel or 1,
                shifts_per_day=shifts or 1,
                total_daily_personnel=total,
            )
            results["created"] += 1
        except Exception as e:
            results["errors"].append(f"משימה '{task_name}': {e}")

    log_action("tasks_imported", {
        "period_id": period_id,
        "created": results["created"],
    })
    return results


def import_equipment_sheet(
    filepath: str | Path,
    period_id: int,
    sheet_name: str = "אקדחים ונהגים",
) -> dict:
    """Import equipment data from the equipment sheet."""
    results = {"assigned": 0, "errors": [], "total": 0}

    try:
        df = pd.read_excel(filepath, sheet_name=sheet_name, header=None)
    except Exception as e:
        results["errors"].append(f"שגיאה בקריאת הגיליון '{sheet_name}': {e}")
        return results

    # First row is headers
    headers = [_clean_str(v) or f"col_{i}" for i, v in enumerate(df.iloc[0].values)]

    # Map equipment columns
    name_col = 0  # שם
    family_col = 1  # שם משפחה
    equip_cols: dict[int, str] = {}

    for ci, h in enumerate(headers):
        if ci <= 1:
            continue
        if h and h not in (f"col_{ci}",):
            equip_cols[ci] = h

    for idx in range(1, len(df)):
        row = df.iloc[idx]
        first_name = _clean_str(row.iloc[name_col])
        last_name = _clean_str(row.iloc[family_col])
        if not first_name or not last_name:
            continue

        results["total"] += 1

        # Find soldier by name
        with get_session() as session:
            stmt = select(Soldier).where(
                Soldier.first_name == first_name,
                Soldier.last_name == last_name,
            )
            soldier = session.execute(stmt).scalar_one_or_none()
            if not soldier:
                continue

        for ci, equip_name in equip_cols.items():
            val = _clean_str(row.iloc[ci])
            if val and val not in ("0", "None"):
                try:
                    et = get_or_create_equipment_type(equip_name)
                    has_form = val in ("1", "1.0", "True")
                    assign_equipment(
                        period_id=period_id,
                        soldier_id=soldier.id,
                        equipment_type_id=et.id,
                        form_signed=has_form,
                        notes=val if not has_form else None,
                    )
                    results["assigned"] += 1
                except Exception as e:
                    results["errors"].append(f"{first_name} {last_name} - {equip_name}: {e}")

    log_action("equipment_imported", {
        "period_id": period_id,
        "assigned": results["assigned"],
    })
    return results


def get_available_sheets(filepath: str | Path) -> list[str]:
    """Get list of sheet names in an Excel file."""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(filepath, read_only=True)
        names = wb.sheetnames
        wb.close()
        return names
    except Exception:
        return []


def full_import(filepath: str | Path, period_id: int,
                updated_by: str | None = None) -> dict:
    """Run full import from Excel file — roster, statuses, tasks, equipment.

    Tries known sheet names and imports what's available.
    """
    results = {
        "roster": None,
        "status": None,
        "tasks": None,
        "equipment": None,
        "available_sheets": [],
    }

    sheets = get_available_sheets(filepath)
    results["available_sheets"] = sheets

    # Import roster
    roster_sheets = [
        "סד\"כ ללא חובשים ואנו\"ח",
        "פלוגה ב - תכנון קדימה",
    ]
    for sheet in roster_sheets:
        if sheet in sheets:
            results["roster"] = import_roster_sheet(filepath, period_id, sheet)
            break

    # Import statuses
    if "תכנון יציאות" in sheets:
        results["status"] = import_status_sheet(
            filepath, period_id, "תכנון יציאות", updated_by
        )

    # Import tasks
    if "משימות פלוגה" in sheets:
        results["tasks"] = import_tasks_sheet(filepath, period_id, "משימות פלוגה")

    # Import equipment
    if "אקדחים ונהגים" in sheets:
        results["equipment"] = import_equipment_sheet(
            filepath, period_id, "אקדחים ונהגים"
        )

    return results
