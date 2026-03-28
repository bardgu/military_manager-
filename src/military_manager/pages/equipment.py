"""Equipment management page — track pistols, forms, drivers, etc."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from military_manager.components.navigation import render_page_header
from military_manager.components.filters import period_guard
from military_manager.services.equipment_service import (
    get_or_create_equipment_type,
    get_all_equipment_types,
    assign_equipment,
    return_equipment,
    get_period_equipment_report,
)
from military_manager.services.soldier_service import get_period_soldiers


def render():
    render_page_header("🛡️ ציוד", "מעקב ציוד אישי — אקדחים, טפסים, רישיונות")

    period = period_guard()
    if not period:
        return

    readonly = st.session_state.get("_company_readonly", False)
    pid = period["id"]

    tab_overview, tab_assign, tab_types = st.tabs(["📋 סקירה", "➕ שיוך ציוד", "⚙️ סוגי ציוד"])

    # ── Overview ──
    with tab_overview:
        report = get_period_equipment_report(pid)
        if not report:
            st.info("אין שיוכי ציוד לתקופה זו")
        else:
            df = pd.DataFrame(report)
            display_map = {
                "soldier_name": "שם חייל",
                "equipment_type": "סוג ציוד",
                "serial_number": "מספר סידורי",
                "form_signed": "טופס חתום",
                "assigned_date": "תאריך שיוך",
                "returned_date": "תאריך החזרה",
                "notes": "הערות",
            }
            available = [c for c in display_map if c in df.columns]
            display_df = df[available].rename(columns=display_map)

            # Mark form_signed as checkmark
            if "טופס חתום" in display_df.columns:
                display_df["טופס חתום"] = display_df["טופס חתום"].map(
                    lambda x: "✅" if x else "❌"
                )

            st.dataframe(display_df, use_container_width=True, hide_index=True)

            # Summary by type
            st.markdown("### סיכום לפי סוג ציוד")
            active = df[df.get("returned_date", pd.Series(dtype=str)).isna()] if "returned_date" in df.columns else df
            if not active.empty and "equipment_type" in active.columns:
                summary = active.groupby("equipment_type").size().reset_index(name="כמות")
                summary.columns = ["סוג ציוד", "כמות"]
                st.dataframe(summary, use_container_width=True, hide_index=True)

    # ── Assign equipment ──
    with tab_assign:
        st.markdown("### שיוך ציוד לחייל")

        soldiers = get_period_soldiers(pid, exclude_irrelevant_unit=True)
        soldier_map = {s.get("full_name", ""): s["soldier_id"] for s in soldiers}

        eq_types = get_all_equipment_types()
        type_map = {t.name: t.id for t in eq_types}

        with st.form("assign_equipment_form"):
            soldier_name = st.selectbox("חייל", list(soldier_map.keys()))
            eq_type_name = st.selectbox("סוג ציוד", list(type_map.keys()) if type_map else ["אקדח", "טופס 101", "רישיון נהיגה"])
            serial = st.text_input("מספר סידורי", placeholder="אופציונלי")
            form_signed = st.checkbox("טופס חתום", value=False)
            notes = st.text_area("הערות", key="eq_notes")

            if st.form_submit_button("✅ שייך ציוד",
                                        disabled=readonly):
                sid = soldier_map.get(soldier_name)
                if not sid:
                    st.error("יש לבחור חייל")
                else:
                    # Ensure equipment type exists
                    if eq_type_name not in type_map:
                        et = get_or_create_equipment_type(eq_type_name)
                        type_id = et.id
                    else:
                        type_id = type_map[eq_type_name]

                    assign_equipment(
                        period_id=pid,
                        soldier_id=sid,
                        equipment_type_id=type_id,
                        serial_number=serial or None,
                        form_signed=form_signed,
                        notes=notes or None,
                    )
                    st.success(f"ציוד שויך ל{soldier_name}")
                    st.rerun()

        # Return equipment
        st.markdown("---")
        st.markdown("### החזרת ציוד")
        report = get_period_equipment_report(pid)
        active_eq = [r for r in report if not r.get("returned_date")]
        if active_eq:
            for eq in active_eq:
                label = f"{eq.get('soldier_name', '')} — {eq.get('equipment_type', '')} {eq.get('serial_number', '') or ''}"
                if st.button(f"↩️ החזר: {label}", key=f"return_{eq.get('assignment_id')}", disabled=readonly):
                    return_equipment(eq["assignment_id"])
                    st.success(f"ציוד הוחזר: {label}")
                    st.rerun()
        else:
            st.info("אין ציוד פעיל להחזרה")

    # ── Equipment types ──
    with tab_types:
        st.markdown("### סוגי ציוד")

        types = get_all_equipment_types()
        if types:
            for t in types:
                st.markdown(f"- {t.name}")

        new_type = st.text_input("סוג ציוד חדש", placeholder="לדוגמה: מכשיר קשר")
        if new_type and st.button("➕ הוסף סוג", disabled=readonly):
            get_or_create_equipment_type(new_type)
            st.success(f"סוג ציוד '{new_type}' נוסף")
            st.rerun()
