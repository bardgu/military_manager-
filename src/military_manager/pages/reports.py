"""Reports page — summary reports, leave stats, export."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

from military_manager.components.navigation import render_page_header
from military_manager.components.filters import period_guard, sub_unit_filter, date_range_filter
from military_manager.services.status_service import (
    get_daily_status_grid,
    calculate_leave_stats,
    get_daily_counts,
)
from military_manager.services.soldier_service import get_period_soldiers, get_sub_units
from military_manager.services.task_service import get_fairness_report, get_detailed_fairness_report


def render():
    render_page_header("📈 דוחות", "דוחות וסיכומים לתקופת המילואים")

    period = period_guard()
    if not period:
        return

    pid = period["id"]

    try:
        p_start = datetime.strptime(period["start_date"], "%Y-%m-%d").date()
        p_end = datetime.strptime(period["end_date"], "%Y-%m-%d").date()
    except (ValueError, KeyError):
        p_start = date.today()
        p_end = date.today() + timedelta(days=21)

    tab_summary, tab_leave, tab_fairness, tab_export = st.tabs([
        "📊 סיכום כללי", "🏠 מאזן יציאות", "⚖️ צדק משמרות", "📥 ייצוא"
    ])

    # ── Summary ──
    with tab_summary:
        _render_summary(pid, p_start, p_end)

    # ── Leave balance ──
    with tab_leave:
        _render_leave_stats(pid, p_start, p_end)

    # ── Fairness ──
    with tab_fairness:
        _render_fairness(pid)

    # ── Export ──
    with tab_export:
        _render_export(pid, p_start, p_end)


def _render_summary(pid: int, p_start: date, p_end: date):
    """סיכום Overall period summary."""
    soldiers = get_period_soldiers(pid, exclude_irrelevant_unit=True)
    total = len(soldiers)

    # Sub-unit breakdown
    units = get_sub_units(pid)
    st.markdown("### סיכום כח אדם")

    c1, c2, c3 = st.columns(3)
    c1.metric("סה\"כ חיילים", total)
    c2.metric("מחלקות", len(units))
    days = (p_end - p_start).days + 1
    c3.metric("ימי תקופה", days)

    st.markdown("---")

    # Daily presence chart
    st.markdown("### 📈 נוכחות יומית")
    data = []
    current = p_start
    while current <= p_end:
        counts = get_daily_counts(pid, current)
        present = sum(v for k, v in counts.items() if k in ("בבסיס", "שמירה", "סיור"))
        away = sum(v for k, v in counts.items() if k in ("חופש", "חופש מיוחד", "פיצול"))
        data.append({
            "תאריך": current,
            "נוכחים": present,
            "ביציאה": away,
            "סה\"כ עם סטטוס": sum(counts.values()),
        })
        current += timedelta(days=1)

    if data:
        df = pd.DataFrame(data)
        import plotly.express as px

        fig = px.line(
            df, x="תאריך", y=["נוכחים", "ביציאה"],
            title="נוכחות יומית לאורך התקופה",
            labels={"value": "מספר חיילים", "variable": "קטגוריה"},
        )
        fig.update_layout(
            font=dict(family="Segoe UI, Arial"),
            height=350,
        )
        st.plotly_chart(fig, use_container_width=True)

    # Per-unit table
    st.markdown("### פירוט לפי מחלקות")
    unit_data = []
    for unit in units:
        unit_soldiers = [s for s in soldiers if s.get("sub_unit") == unit]
        unit_data.append({"מחלקה": unit, "חיילים": len(unit_soldiers)})
    if unit_data:
        st.dataframe(pd.DataFrame(unit_data), use_container_width=True, hide_index=True)


def _render_leave_stats(pid: int, p_start: date, p_end: date):
    """Leave/absence balance per soldier."""
    st.markdown("### 🏠 מאזן יציאות")
    st.caption("ימי חופש ביחס לימי מילואים בפועל (ימי פיצול לא נחשבים ימי מילואים)")

    unit_filter = sub_unit_filter(pid, key="leave_unit_filter")

    stats = calculate_leave_stats(pid, p_start, p_end, sub_unit=unit_filter)

    if not stats:
        st.info("אין נתוני סטטוסים לתקופה זו")
        return

    df = pd.DataFrame(stats)
    display_cols = {
        "full_name": "שם",
        "sub_unit": "מחלקה",
        "days_total": "סה\"כ ימים",
        "days_pitzul": "ימי פיצול",
        "days_reserve": "ימי מילואים בפועל",
        "days_leave": "ימי חופש",
        "days_present": "ימי נוכחות",
        "leave_pct": "% חופש (מתוך מילואים)",
    }
    available = [c for c in display_cols if c in df.columns]
    display_df = df[available].rename(columns=display_cols)

    if "% חופש (מתוך מילואים)" in display_df.columns:
        display_df = display_df.sort_values("% חופש (מתוך מילואים)", ascending=False)

    st.dataframe(display_df, use_container_width=True, hide_index=True,
                 column_config={
                     "שם": st.column_config.TextColumn("שם", pinned=True),
                     "מחלקה": st.column_config.TextColumn("מחלקה", pinned=True),
                     "% חופש (מתוך מילואים)": st.column_config.ProgressColumn(
                         "% חופש (מתוך מילואים)",
                         min_value=0, max_value=100, format="%.1f%%",
                     ),
                 })

    # Chart
    if len(display_df) > 0:
        import plotly.express as px
        fig = px.bar(
            display_df.head(20),
            x="שם",
            y=[c for c in ["ימי נוכחות", "ימי חופש", "ימי פיצול"] if c in display_df.columns],
            barmode="stack",
            title="מאזן נוכחות / חופש / פיצול",
        )
        fig.update_layout(
            font=dict(family="Segoe UI, Arial"),
            height=350,
            xaxis_tickangle=-45,
        )
        st.plotly_chart(fig, use_container_width=True)


def _render_fairness(pid: int):
    """Shift fairness report — detailed breakdown with color coding."""
    st.markdown("### ⚖️ טבלת צדק — חלוקת משמרות")

    try:
        report_data, task_names = get_detailed_fairness_report(pid)
    except Exception:
        report_data, task_names = [], []

    if not report_data:
        st.info("אין נתוני שיבוצים עדיין")
        return

    import numpy as np

    # Summary metrics
    total_shifts = sum(s["total_shifts"] for s in report_data)
    soldiers_with = sum(1 for s in report_data if s["total_shifts"] > 0)
    active_counts = [s["total_shifts"] for s in report_data if s["total_shifts"] > 0]
    avg_shifts = sum(active_counts) / len(active_counts) if active_counts else 0
    max_s = max(active_counts) if active_counts else 0
    min_s = min(active_counts) if active_counts else 0

    mc1, mc2, mc3, mc4, mc5 = st.columns(5)
    mc1.metric("סה\"כ שיבוצים", total_shifts)
    mc2.metric("חיילים ששובצו", f"{soldiers_with}/{len(report_data)}")
    mc3.metric("מקסימום", max_s)
    mc4.metric("מינימום (פעיל)", min_s)
    mc5.metric("ממוצע", f"{avg_shifts:.1f}")

    if max_s > 0 and min_s > 0:
        ratio = min_s / max_s
        st.progress(ratio, text=f"מדד צדק: {ratio:.0%} (מינימום/מקסימום)")

    # Outlier detection
    if active_counts and len(active_counts) >= 3:
        std_dev = np.std(active_counts)
        th_high = avg_shifts + 1.5 * std_dev
        th_low = avg_shifts - 1.5 * std_dev
        outliers_high = [s for s in report_data if s["total_shifts"] > th_high]
        outliers_low = [s for s in report_data if 0 < s["total_shifts"] < max(th_low, 1)]
        if outliers_high:
            st.warning(
                f"⚠️ **חיילים עם עומס חריג ({len(outliers_high)}):** "
                + ", ".join(f"{s['full_name']} ({s['total_shifts']})" for s in outliers_high)
            )
        if outliers_low:
            st.info(
                f"💡 **חיילים עם מעט שיבוצים ({len(outliers_low)}):** "
                + ", ".join(f"{s['full_name']} ({s['total_shifts']})" for s in outliers_low)
            )
    else:
        th_high, th_low = 9999, -1

    st.markdown("---")

    # Build DataFrame
    rows = []
    for s in report_data:
        row = {
            "שם": s["full_name"],
            "מחלקה": s["sub_unit"],
            "תפקיד": s["role"],
            "סה\"כ": s["total_shifts"],
            "☀️ בוקר": s["morning_shifts"],
            "🌤️ צהריים": s["afternoon_shifts"],
            "🌙 לילה": s["night_shifts"],
        }
        for tn in task_names:
            row[f"📋 {tn}"] = s["tasks"].get(tn, 0)
        rows.append(row)

    df = pd.DataFrame(rows).sort_values("סה\"כ", ascending=False)

    def _color_total(val):
        if not isinstance(val, (int, float)):
            return ""
        if val > th_high:
            return "background-color: #FFCDD2; font-weight: bold"
        elif 0 < val < max(th_low, 1):
            return "background-color: #C8E6C9"
        return ""

    styled = df.style.applymap(_color_total, subset=["סה\"כ"] if "סה\"כ" in df.columns else [])
    st.dataframe(styled, use_container_width=True, hide_index=True)

    # Chart
    if not df.empty:
        import plotly.express as px
        fig = px.bar(
            df.head(20),
            x="שם", y="סה\"כ",
            title="מספר משמרות לחייל (מקסימום → מינימום)",
            color="סה\"כ",
            color_continuous_scale=["green", "yellow", "red"],
        )
        fig.update_layout(
            font=dict(family="Segoe UI, Arial"),
            height=400,
            xaxis_tickangle=-45,
        )
        st.plotly_chart(fig, use_container_width=True)


def _render_export(pid: int, p_start: date, p_end: date):
    """Export data to Excel."""
    st.markdown("### 📥 ייצוא לאקסל")

    export_options = st.multiselect(
        "בחר נתונים לייצוא",
        ["רשימת חיילים", "סטטוס יומי", "שיבוצי משמרות", "מאזן יציאות"],
        default=["רשימת חיילים", "סטטוס יומי"],
    )

    if st.button("📥 ייצא לאקסל"):
        import io

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            if "רשימת חיילים" in export_options:
                soldiers = get_period_soldiers(pid, exclude_irrelevant_unit=True)
                df = pd.DataFrame(soldiers)
                if not df.empty:
                    df.to_excel(writer, sheet_name="חיילים", index=False)

            if "סטטוס יומי" in export_options:
                grid = get_daily_status_grid(pid, p_start, p_end)
                if grid and grid.get("soldiers"):
                    rows = []
                    for s in grid["soldiers"]:
                        row = {"שם": s["full_name"], "מחלקה": s.get("sub_unit", "")}
                        for d in grid["dates"]:
                            key = f"{s['soldier_id']}_{d.isoformat()}"
                            row[d.strftime("%d/%m")] = grid["statuses"].get(key, "")
                        rows.append(row)
                    pd.DataFrame(rows).to_excel(writer, sheet_name="סטטוס יומי", index=False)

            if "מאזן יציאות" in export_options:
                stats = calculate_leave_stats(pid, p_start, p_end)
                if stats:
                    pd.DataFrame(stats).to_excel(writer, sheet_name="מאזן יציאות", index=False)

            if "שיבוצי משמרות" in export_options:
                report = get_fairness_report(pid)
                if report:
                    pd.DataFrame(report).to_excel(writer, sheet_name="שיבוצי משמרות", index=False)

        output.seek(0)
        st.download_button(
            "💾 הורד קובץ אקסל",
            data=output.getvalue(),
            file_name=f"report_{date.today().isoformat()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
