"""Import Report-1 Excel — parse the platoon Excel and sync to DB.

Expected Excel format (per sheet = sub-unit):
  Row 0: dates (datetime)  in columns D onward
  Row 1: מין | תפקיד | שמות | day-letters (ה, ו, ש, ...)
  Row 2: optional holidays row
  Row 3+: either a sub-unit header like "מפקדת הפלוגה (11 חיילים)"
          or soldier data: gender | role | name | status1 | status2 | ...

The summary sheet ("סיכום פלוגתי") contains ALL soldiers across all units.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

import pandas as pd
from sqlalchemy import select

from military_manager.database import get_session, Soldier, PeriodSoldier
from military_manager.services.soldier_service import get_period_soldiers
from military_manager.services.status_service import set_status
from military_manager.config import IRRELEVANT_UNIT


# ── Google Sheets helpers ───────────────────────────────────

# Path to service-account key JSON (fallback to env var)
_SA_KEY_PATH: Path | None = None


def _find_sa_key() -> Path | None:
    """Locate the service-account JSON key file."""
    global _SA_KEY_PATH
    if _SA_KEY_PATH and _SA_KEY_PATH.exists():
        return _SA_KEY_PATH
    import os
    # 1) env var
    env = os.environ.get("GOOGLE_SA_KEY_PATH")
    if env:
        p = Path(env)
        if p.exists():
            _SA_KEY_PATH = p
            return p
    # 2) project root
    candidates = [
        Path(__file__).resolve().parents[3] / "service_account.json",  # repo root
        Path("/app/service_account.json"),  # Docker / HF
        Path("service_account.json"),
    ]
    for c in candidates:
        if c.exists():
            _SA_KEY_PATH = c
            return c
    return None


def extract_sheet_id(url: str) -> str | None:
    """Extract the spreadsheet ID from a Google Sheets URL."""
    m = re.search(r'/spreadsheets/d/([a-zA-Z0-9-_]+)', url)
    return m.group(1) if m else None


def extract_gid(url: str) -> str:
    """Extract the gid (sheet tab) from a Google Sheets URL."""
    m = re.search(r'gid=(\d+)', url)
    return m.group(1) if m else "0"


def fetch_google_sheet_as_excel(url: str) -> BytesIO:
    """Download a Google Sheet as xlsx.

    Uses a Service Account key if available (private sheets).
    Falls back to anonymous export (public sheets).
    """
    sheet_id = extract_sheet_id(url)
    if not sheet_id:
        raise ValueError("לא ניתן לחלץ מזהה גיליון מהקישור. ודא שהוא קישור Google Sheets תקין.")

    sa_key = _find_sa_key()

    # ── Method 1: Service Account (private sheets) ──
    if sa_key:
        try:
            from google.oauth2.service_account import Credentials
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaIoBaseDownload
            import httplib2
            import ssl

            SCOPES = [
                "https://www.googleapis.com/auth/spreadsheets.readonly",
                "https://www.googleapis.com/auth/drive.readonly",
            ]
            creds = Credentials.from_service_account_file(str(sa_key), scopes=SCOPES)

            # Disable SSL verification for corporate proxies / firewalls
            http = httplib2.Http(disable_ssl_certificate_validation=True)
            from google_auth_httplib2 import AuthorizedHttp
            authed_http = AuthorizedHttp(creds, http=http)

            drive = build("drive", "v3", http=authed_http)
            request = drive.files().export_media(
                fileId=sheet_id,
                mimeType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            buf = BytesIO()
            downloader = MediaIoBaseDownload(buf, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            buf.seek(0)
            return buf
        except Exception as e:
            err_str = str(e)
            if "404" in err_str or "not found" in err_str.lower():
                raise ValueError("הגיליון לא נמצא. ודא שהקישור תקין.")
            if "403" in err_str or "permission" in err_str.lower():
                raise PermissionError(
                    "אין הרשאה לגיליון. ודא שהגיליון משותף עם:\n"
                    f"`{_get_sa_email(sa_key)}`\nכ-צופה (Viewer)."
                )
            raise ConnectionError(f"שגיאה בהורדת הגיליון: {e}")

    # ── Method 2: Anonymous export (public sheets) ──
    import urllib.request
    import urllib.error

    export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"
    req = urllib.request.Request(export_url)
    req.add_header("User-Agent", "Mozilla/5.0")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise PermissionError(
                "אין גישה לגיליון. שתף את הגיליון כ'כל מי שיש לו את הקישור' "
                "או העלה קובץ service_account.json בהגדרות."
            )
        raise ConnectionError(f"שגיאת חיבור: HTTP {e.code}")
    except Exception as e:
        raise ConnectionError(f"שגיאת חיבור ל-Google Sheets: {e}")

    buf = BytesIO(data)
    buf.seek(0)
    return buf


def _get_sa_email(sa_key_path: Path) -> str:
    """Read the client_email from the service account JSON."""
    import json
    try:
        with open(sa_key_path, "r") as f:
            return json.load(f).get("client_email", "(לא ידוע)")
    except Exception:
        return "(לא ידוע)"


def get_service_account_email() -> str | None:
    """Return the SA email if a key file exists, else None."""
    sa = _find_sa_key()
    return _get_sa_email(sa) if sa else None


# ── Status value normalization ──────────────────────────────
_STATUS_ALIASES: dict[str, str] = {
    "בבסיס": "בבסיס",
    "חופשה": "חופש",
    "חופש": "חופש",
    "יוצא לחופשה": "יוצא לחופש",
    "יוצא לחופש": "יוצא לחופש",
    "בדרך": "חוזר מחופש",
    "חוזר מחופש": "חוזר מחופש",
    "חוזר מחופשה": "חוזר מחופש",
    "פיצול": "פיצול",
    "יוצא לפיצול": "יוצא לפיצול",
    "גימלים": "גימלים",
    "נפקד": "נפקד",
    "משתחרר": "משתחרר",
    "לא בשמפ": "לא בשמפ",
    "לא בשמ\"פ": "לא בשמפ",
    "רספ": "רספ/סרספ",
    "סרספ": "רספ/סרספ",
    "רספ/סרספ": "רספ/סרספ",
    "סמבצים": "סמבצים",
    "סוואנה": "סוואנה",
    "התייצב": "התייצב",
    "צפוי להתייצב": "צפוי להתייצב",
    "סיפוח מאוחר": "סיפוח מאוחר",
    "יוצא לקורס": "יוצא לקורס",
}


def _normalize_status(raw: str | None) -> str | None:
    """Normalize a status value from Excel to a known status."""
    if not raw or str(raw).strip() == "" or str(raw) == "nan":
        return None
    s = str(raw).strip()
    return _STATUS_ALIASES.get(s, s)


def _clean(val) -> str | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    return s if s else None


def _is_unit_header(name_val: str) -> tuple[bool, str]:
    """Check if a row is a sub-unit header like 'מפקדת הפלוגה (11 חיילים)'.
    Returns (is_header, unit_name).
    """
    if not name_val:
        return False, ""
    # Match patterns like "מחלקה 1 (15 חיילים)" or "מפקדת הפלוגה (11 חיילים)"
    m = re.match(r'^(.+?)\s*\(\d+\s*חיילים\)\s*$', name_val)
    if m:
        return True, m.group(1).strip()
    return False, ""


def parse_report1_excel(
    source: str | Path | BinaryIO,
    sheet_name: str = "סיכום פלוגתי",
) -> dict:
    """Parse the Report-1 Excel file and extract soldier statuses.

    Returns:
        {
            "soldiers": [
                {
                    "name": "גיא ברדה",
                    "role": "מפ",
                    "gender": "ז",
                    "sub_unit": "מפקדת הפלוגה",
                    "statuses": {date(2026,3,5): "בבסיס", ...},
                },
                ...
            ],
            "dates": [date(2026,3,5), date(2026,3,6), ...],
            "errors": [...],
        }
    """
    result = {"soldiers": [], "dates": [], "errors": []}

    try:
        df = pd.read_excel(source, sheet_name=sheet_name, header=None)
    except Exception as e:
        result["errors"].append(f"שגיאה בקריאת הגיליון '{sheet_name}': {e}")
        return result

    # ── Find dates row (row 0 typically has datetime values) ──
    date_cols: dict[int, date] = {}
    for row_idx in range(min(3, len(df))):
        for col_idx in range(3, df.shape[1]):
            val = df.iloc[row_idx, col_idx]
            if isinstance(val, (datetime, pd.Timestamp)):
                d = val.date() if hasattr(val, 'date') else val
                if isinstance(d, date):
                    date_cols[col_idx] = d

        if len(date_cols) > 5:
            break

    if not date_cols:
        result["errors"].append("לא נמצאו תאריכים בגיליון")
        return result

    result["dates"] = sorted(set(date_cols.values()))

    # ── Parse soldier rows ──
    current_unit = "ללא מחלקה"

    # Find the first data row (skip header rows)
    # Row 0 = dates, Row 1 = day letters (ה, ו, ש), Row 2 = holidays/notes
    data_start = 2  # start scanning from row 2

    for row_idx in range(data_start, len(df)):
        gender = _clean(df.iloc[row_idx, 0])
        role = _clean(df.iloc[row_idx, 1])
        name = _clean(df.iloc[row_idx, 2])

        if not name:
            continue

        # Check if this is a unit header
        is_header, unit_name = _is_unit_header(name)
        if is_header:
            current_unit = unit_name
            continue

        # Check if this is a summary row (מספר חיילים, אחוז, etc.)
        if any(kw in name for kw in [
            "מספר חיילים", "אחוז", "סה\"כ", "בנים", "בנות",
            "חובשים", "ממים", "סמלים", "מכים", "נהגי", "מהנדסים",
            "מחלצים", "אנוח", "קשרים",
        ]):
            continue

        # This is a soldier row
        if not gender or gender == "חגים":
            continue

        statuses: dict[date, str] = {}
        for col_idx, d in date_cols.items():
            raw = _clean(df.iloc[row_idx, col_idx])
            status = _normalize_status(raw)
            if status:
                statuses[d] = status

        result["soldiers"].append({
            "name": name,
            "role": role or "",
            "gender": gender,
            "sub_unit": current_unit,
            "statuses": statuses,
        })

    return result


def match_soldiers_to_db(
    parsed_soldiers: list[dict],
    period_id: int,
) -> dict:
    """Match parsed Excel soldiers to DB soldiers by name.

    Returns:
        {
            "matched": [(excel_soldier, db_soldier_dict), ...],
            "excel_only": [excel_soldier, ...],      # in Excel but not in DB
            "db_only": [db_soldier_dict, ...],        # in DB but not in Excel
        }
    """
    db_soldiers = get_period_soldiers(period_id, exclude_irrelevant_unit=False)

    # Build name lookup (full name → db soldier)
    db_by_name: dict[str, dict] = {}
    for s in db_soldiers:
        full = s["full_name"].strip()
        db_by_name[full] = s
        # Also try first_name + last_name reversed
        rev = f"{s['last_name']} {s['first_name']}".strip()
        if rev not in db_by_name:
            db_by_name[rev] = s

    matched = []
    excel_only = []
    matched_db_ids = set()

    for es in parsed_soldiers:
        excel_name = es["name"].strip()
        db_match = db_by_name.get(excel_name)

        if db_match:
            matched.append((es, db_match))
            matched_db_ids.add(db_match["soldier_id"])
        else:
            # Try fuzzy: first word match
            excel_parts = excel_name.split()
            found = False
            for db_name, db_s in db_by_name.items():
                db_parts = db_name.split()
                # Match if first and last name appear (in any order)
                if (len(excel_parts) >= 2 and len(db_parts) >= 2 and
                    set(excel_parts) & set(db_parts) == set(excel_parts)):
                    matched.append((es, db_s))
                    matched_db_ids.add(db_s["soldier_id"])
                    found = True
                    break
            if not found:
                excel_only.append(es)

    # DB soldiers not found in Excel (excluding irrelevant unit)
    db_only = [
        s for s in db_soldiers
        if s["soldier_id"] not in matched_db_ids
        and s.get("sub_unit") != IRRELEVANT_UNIT
    ]

    return {
        "matched": matched,
        "excel_only": excel_only,
        "db_only": db_only,
    }


def import_statuses_to_db(
    matched: list[tuple[dict, dict]],
    period_id: int,
    date_range: tuple[date, date] | None = None,
) -> dict:
    """Import matched soldier statuses into the database.

    Args:
        matched: List of (excel_soldier, db_soldier) tuples
        period_id: Target period ID
        date_range: Optional (start, end) to limit import to specific dates

    Returns:
        {"imported": count, "skipped": count, "errors": [...]}
    """
    result = {"imported": 0, "skipped": 0, "errors": []}

    for excel_s, db_s in matched:
        sid = db_s["soldier_id"]
        for d, status in excel_s["statuses"].items():
            if date_range:
                if d < date_range[0] or d > date_range[1]:
                    result["skipped"] += 1
                    continue
            try:
                set_status(period_id, sid, d, status)
                result["imported"] += 1
            except Exception as e:
                result["errors"].append(f"{db_s['full_name']} {d}: {e}")

    return result


# ── Quick Sync (one-click) ──────────────────────────────────

def quick_sync_from_gsheet(
    url: str,
    period_id: int,
    sheet_name: str = "סיכום פלוגתי",
    date_range: tuple[date, date] | None = None,
) -> dict:
    """One-click sync: fetch Google Sheet → parse → match → import.

    Returns:
        {
            "success": bool,
            "imported": int,
            "matched": int,
            "excel_only": int,
            "db_only": int,
            "dates_found": int,
            "errors": [str, ...],
        }
    """
    result = {
        "success": False, "imported": 0, "matched": 0,
        "excel_only": 0, "db_only": 0, "dates_found": 0, "errors": [],
    }

    # 1) Fetch
    try:
        buf = fetch_google_sheet_as_excel(url)
    except Exception as e:
        result["errors"].append(f"שגיאת הורדה: {e}")
        return result

    # 2) Parse
    parsed = parse_report1_excel(buf, sheet_name=sheet_name)
    if parsed["errors"]:
        result["errors"].extend(parsed["errors"])
        return result
    if not parsed["soldiers"]:
        result["errors"].append("לא נמצאו חיילים בגיליון")
        return result

    result["dates_found"] = len(parsed["dates"])

    # 3) Match
    match_result = match_soldiers_to_db(parsed["soldiers"], period_id)
    matched = match_result["matched"]
    result["matched"] = len(matched)
    result["excel_only"] = len(match_result["excel_only"])
    result["db_only"] = len(match_result["db_only"])

    if not matched:
        result["errors"].append("אין חיילים מותאמים לייבוא")
        return result

    # 4) Import
    import_result = import_statuses_to_db(matched, period_id, date_range=date_range)
    result["imported"] = import_result["imported"]
    result["errors"].extend(import_result["errors"])
    result["success"] = True

    return result
