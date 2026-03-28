"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# App
APP_ENV = os.getenv("APP_ENV", "development")
APP_NAME = os.getenv("APP_NAME", "military-manager")
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Database
# In production (Streamlit Cloud) use DATABASE_URL env var pointing to Supabase/PostgreSQL.
# Locally, fall back to SQLite.
_db_url_env = os.getenv("DATABASE_URL", "")

# Streamlit secrets support (secrets.toml or Streamlit Cloud dashboard)
try:
    import streamlit as st
    _db_url_env = _db_url_env or st.secrets.get("DATABASE_URL", "")
except Exception:
    pass

if _db_url_env:
    DATABASE_URL = _db_url_env
    IS_POSTGRES = True
else:
    DATABASE_PATH = os.getenv("DATABASE_PATH", str(DATA_DIR / "military.db"))
    DATABASE_URL = f"sqlite:///{DATABASE_PATH}"
    IS_POSTGRES = False

# AI
AI_SERVICE = os.getenv("AI_SERVICE", "local_llm")
AI_BASE_URL = os.getenv("AI_BASE_URL", "http://localhost:3001/v1")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "gpt-4o")
AI_TIMEOUT = int(os.getenv("AI_TIMEOUT", "30"))
AI_MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS", "2000"))

# Default status options for new reserve periods
DEFAULT_STATUSES = [
    {"name": "בבסיס", "category": "present", "color": "#4CAF50"},
    {"name": "התייצב", "category": "present", "color": "#8BC34A"},
    {"name": "צפוי להתייצב", "category": "arriving", "color": "#FFC107"},
    {"name": "סיפוח מאוחר", "category": "arriving", "color": "#FF9800"},
    {"name": "יוצא לחופש", "category": "leaving", "color": "#2196F3"},
    {"name": "חופש", "category": "away", "color": "#03A9F4"},
    {"name": "חוזר מחופש", "category": "arriving", "color": "#00BCD4"},
    {"name": "יוצא לפיצול", "category": "leaving", "color": "#9C27B0"},
    {"name": "פיצול", "category": "away", "color": "#AB47BC"},
    {"name": "משתחרר", "category": "final", "color": "#607D8B"},
    {"name": "לא בשמפ", "category": "na", "color": "#9E9E9E"},
    {"name": "נפקד", "category": "alert", "color": "#F44336"},
    {"name": "גימלים", "category": "away", "color": "#FF5722"},
    {"name": "יוצא לקורס", "category": "away", "color": "#795548"},
    {"name": "רספ/סרספ", "category": "present", "color": "#3F51B5"},
    {"name": "סמבצים", "category": "present", "color": "#009688"},
    {"name": "סוואנה", "category": "present", "color": "#CDDC39"},
]

# Status categories for aggregation
STATUS_CATEGORIES = {
    "present": "נוכח",
    "away": "לא נוכח",
    "arriving": "בדרך",
    "leaving": "יוצא",
    "final": "סופי",
    "alert": "התראה",
    "na": "לא רלוונטי",
}

# Special sub-unit name for soldiers excluded from all calculations
IRRELEVANT_UNIT = "לא רלוונטי"
