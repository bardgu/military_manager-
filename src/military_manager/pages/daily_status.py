"""Daily Status page — the core status grid (soldier × date matrix)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

from military_manager.components.navigation import render_page_header
from military_manager.components.filters import period_guard, sub_unit_filter
from military_manager.services.status_service import (
    get_daily_status_grid,
    set_status,
    bulk_set_status,
    get_daily_counts,
)
from military_manager.services.soldier_service import get_period_soldiers
from military_manager.services.period_service import get_status_options


def render():
    render_page_header("📊 סטטוס יומי", "ניהול סטטוס חיילים לפי תאריך")

    period = period_guard()
    if not period:
        return

    pid = period["id"]

    tab_grid, tab_bulk, tab_counts = st.tabs(["📅 טבלת סטטוסים", "⚡ עדכון מרוכז", "📈 סיכום יומי"])

    with tab_grid:
        _render_status_grid(pid, period)

    with tab_bulk:
        _render_bulk_update(pid, period)

    with tab_counts:
        _render_daily_counts(pid, period)


def _render_status_grid(pid: int, period: dict):
    """Render the main status grid — editable matrix."""

    # Filters
    col1, col2, col3 = st.columns([2, 2, 2])
    with col1:
        unit_filter = sub_unit_filter(pid, key="status_unit_filter")
    with col2:
        today = date.today()
        try:
            p_start = datetime.strptime(period["start_date"], "%Y-%m-%d").date()
            p_end = datetime.strptime(period["end_date"], "%Y-%m-%d").date()
        except (ValueError, KeyError):
            p_start = today
            p_end = today + timedelta(days=21)

        view_start = st.date_input(
            "מתאריך", value=max(p_start, today - timedelta(days=3)),
            format="DD/MM/YYYY", key="grid_start"
        )
    with col3:
        view_end = st.date_input(
            "עד תאריך", value=min(view_start + timedelta(days=7), p_end),
            format="DD/MM/YYYY", key="grid_end"
        )

    # Get status options
    status_options = get_status_options(pid)
    status_names = [s.name for s in status_options] if status_options else [
        "בבסיס", "חופש", "חופש מיוחד", "פיצול", "ח.צ", "שמירה",
        "סיור", "קורס", "נפקד", "ל.ר", "ל.מ",
    ]

    # Get grid data
    grid = get_daily_status_grid(pid, view_start, view_end, sub_unit=unit_filter)

    if not grid or not grid.get("soldiers"):
        st.info("אין חיילים להצגה" + (" במחלקה זו" if unit_filter else ""))
        return

    dates = grid["dates"]
    soldiers = grid["soldiers"]
    statuses = grid["statuses"]

    # Build DataFrame — include role columns
    rows = []
    for s in soldiers:
        row = {
            "שם": s["full_name"],
            "תפקיד": s.get("role", "") or "",
            "תפקיד משימתי": s.get("task_role", "") or "",
            "מחלקה": s.get("sub_unit", ""),
        }
        for d in dates:
            d_str = d.strftime("%d/%m")
            key = f"{s['soldier_id']}_{d.isoformat()}"
            current_status = statuses.get(key, "")
            row[d_str] = current_status
        rows.append(row)

    df = pd.DataFrame(rows)

    # Display with data editor
    date_cols = [d.strftime("%d/%m") for d in dates]
    column_config = {}
    for dc in date_cols:
        column_config[dc] = st.column_config.SelectboxColumn(
            dc,
            options=status_names,
            width="small",
        )
    column_config["שם"] = st.column_config.TextColumn("שם", disabled=True, width="medium")
    column_config["תפקיד"] = st.column_config.TextColumn("תפקיד", disabled=True, width="small")
    column_config["תפקיד משימתי"] = st.column_config.TextColumn("תפקיד משימתי", disabled=True, width="small")
    column_config["מחלקה"] = st.column_config.TextColumn("מחלקה", disabled=True, width="small")

    edited_df = st.data_editor(
        df,
        column_config=column_config,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        disabled=st.session_state.get("_company_readonly", False),
        key="status_grid_editor",
    )

    # Save changes
    readonly = st.session_state.get("_company_readonly", False)
    if st.button("💾 שמור שינויים", key="save_status_grid", disabled=readonly):
        changes = 0
        for row_idx, soldier in enumerate(soldiers):
            for d in dates:
                d_str = d.strftime("%d/%m")
                old_val = df.iloc[row_idx][d_str]
                new_val = edited_df.iloc[row_idx][d_str]
                if new_val != old_val and new_val:
                    set_status(pid, soldier["soldier_id"], d, new_val)
                    changes += 1
        if changes:
            st.success(f"✅ {changes} סטטוסים עודכנו")
            st.rerun()
        else:
            st.info("לא בוצעו שינויים")

    # Counts summary below the grid
    st.markdown("---")
    st.markdown("**סיכום להיום:**")
    counts = get_daily_counts(pid, today)
    if counts:
        summary_cols = st.columns(min(len(counts), 8))
        for i, (status, count) in enumerate(sorted(counts.items(), key=lambda x: -x[1])):
            summary_cols[i % len(summary_cols)].metric(status, count)


def _render_bulk_update(pid: int, period: dict):
    """Render bulk status update form."""
    st.markdown("### עדכון סטטוס מרוכז")
    st.caption("עדכון סטטוס לכמה חיילים בבת אחת")

    status_options = get_status_options(pid)
    status_names = [s.name for s in status_options] if status_options else [
        "בבסיס", "חופש", "פיצול", "ח.צ", "שמירה", "סיור"
    ]

    # Select date
    today = date.today()
    target_date = st.date_input("תאריך", value=today, format="DD/MM/YYYY", key="bulk_date")

    # Select status
    status = st.selectbox("סטטוס", status_names, key="bulk_status")

    # Select soldiers
    soldiers = get_period_soldiers(pid, exclude_irrelevant_unit=True)
    unit_filter = sub_unit_filter(pid, key="bulk_unit_filter")
    if unit_filter:
        soldiers = [s for s in soldiers if s.get("sub_unit") == unit_filter]

    soldier_names = {s.get("full_name", ""): s["soldier_id"] for s in soldiers}
    selected = st.multiselect("חיילים", list(soldier_names.keys()), key="bulk_soldiers")

    if st.button("⚡ עדכן סטטוס", key="bulk_update_btn",
                  disabled=st.session_state.get("_company_readonly", False)):
        if not selected:
            st.error("יש לבחור לפחות חייל אחד")
        else:
            soldier_ids = [soldier_names[name] for name in selected]
            result = bulk_set_status(pid, soldier_ids, target_date, status)
            st.success(f"✅ עודכנו {result} סטטוסים")
            st.rerun()


def _render_daily_counts(pid: int, period: dict):
    """Render daily status counts chart."""
    st.markdown("### סיכום סטטוסים יומי")

    try:
        p_start = datetime.strptime(period["start_date"], "%Y-%m-%d").date()
        p_end = datetime.strptime(period["end_date"], "%Y-%m-%d").date()
    except (ValueError, KeyError):
        p_start = date.today()
        p_end = date.today() + timedelta(days=21)

    # Gather data for each day
    data = []
    current = p_start
    while current <= p_end:
        counts = get_daily_counts(pid, current)
        for status, count in counts.items():
            data.append({"תאריך": current, "סטטוס": status, "כמות": count})
        current += timedelta(days=1)

    if not data:
        st.info("אין נתוני סטטוס לתקופה זו")
        return

    df = pd.DataFrame(data)

    import plotly.express as px
    fig = px.bar(
        df,
        x="תאריך",
        y="כמות",
        color="סטטוס",
        barmode="stack",
        title="סטטוסים לפי ימים",
    )
    fig.update_layout(
        font=dict(family="Segoe UI, Arial"),
        xaxis_title="תאריך",
        yaxis_title="מספר חיילים",
        height=400,
    )
    st.plotly_chart(fig, use_container_width=True)
