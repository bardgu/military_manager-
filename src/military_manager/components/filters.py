"""Reusable filter widgets for common patterns."""

import streamlit as st
from datetime import date, timedelta

from military_manager.services.soldier_service import get_sub_units
from military_manager.services.period_service import get_active_period


def period_guard() -> dict | None:
    """Check for active period and warn if missing. Returns period dict or None.

    Also sets ``st.session_state["_company_readonly"]`` to True and shows
    a read-only banner when an admin is viewing a company other than their own.
    """
    from military_manager.components.auth import is_viewing_own_company

    period = st.session_state.get("active_period")
    if not period:
        st.warning("⚠️ לא נבחרה תקופת מילואים פעילה. עבור להגדרות כדי לבחור תקופה.")
        return None

    # Company write-protection
    if not is_viewing_own_company():
        st.session_state["_company_readonly"] = True
        st.info("🔒 צפייה בלבד — אתה צופה בפלוגה שאינה שלך. לא ניתן לבצע שינויים.")
    else:
        st.session_state["_company_readonly"] = False

    return period


def sub_unit_filter(period_id: int, key: str = "sub_unit_filter") -> str | None:
    """Render a sub-unit dropdown filter. Returns selected sub-unit or None for all."""
    from military_manager.config import IRRELEVANT_UNIT
    units = get_sub_units(period_id)
    units = [u for u in units if u != IRRELEVANT_UNIT]
    options = ["הכל"] + units
    selected = st.selectbox("מחלקה", options, key=key)
    return None if selected == "הכל" else selected


def date_range_filter(
    period_start: date | None = None,
    period_end: date | None = None,
    key_prefix: str = "date",
) -> tuple[date, date]:
    """Render a date range picker. Returns (start, end) dates."""
    today = date.today()
    default_start = period_start or today
    default_end = period_end or (today + timedelta(days=30))

    col1, col2 = st.columns(2)
    with col1:
        start = st.date_input(
            "מתאריך",
            value=default_start,
            key=f"{key_prefix}_start",
            format="DD/MM/YYYY",
        )
    with col2:
        end = st.date_input(
            "עד תאריך",
            value=default_end,
            key=f"{key_prefix}_end",
            format="DD/MM/YYYY",
        )
    return start, end


def single_date_selector(
    period_start: date | None = None,
    period_end: date | None = None,
    key: str = "single_date",
) -> date:
    """Render a single date picker within period bounds."""
    today = date.today()
    min_d = period_start or today
    max_d = period_end or (today + timedelta(days=60))
    selected = today if min_d <= today <= max_d else min_d

    return st.date_input(
        "תאריך",
        value=selected,
        min_value=min_d,
        max_value=max_d,
        key=key,
        format="DD/MM/YYYY",
    )
