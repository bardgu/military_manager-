"""Periods management page — create, edit, activate reserve periods."""

from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from military_manager.components.navigation import render_page_header
from military_manager.components.auth import get_current_user, get_effective_company_id, is_viewing_own_company
from military_manager.services.period_service import (
    create_period,
    get_all_periods,
    activate_period,
    update_period,
    delete_period,
    copy_soldiers_from_period,
)


def render():
    render_page_header("📅 תקופות מילואים", "ניהול תקופות שירות מילואים")

    user = get_current_user()
    company_id = get_effective_company_id()
    readonly = not is_viewing_own_company()
    if readonly:
        st.info("🔒 צפייה בלבד — אתה צופה בפלוגה שאינה שלך. לא ניתן לבצע שינויים.")

    tab_list, tab_new = st.tabs(["📋 תקופות קיימות", "➕ תקופה חדשה"])

    # ── Existing periods ──
    with tab_list:
        periods = get_all_periods(company_id)
        if not periods:
            st.info("אין תקופות מילואים. צור תקופה חדשה כדי להתחיל.")
        else:
            for p in periods:
                with st.expander(
                    f"{'🟢' if p.is_active else '⚪'} {p.name} — {p.start_date} עד {p.end_date}",
                    expanded=p.is_active,
                ):
                    col1, col2, col3 = st.columns([3, 1, 1])

                    with col1:
                        st.markdown(f"**מיקום:** {p.location or '—'}")
                        st.markdown(f"**תאריכים:** {p.start_date} — {p.end_date}")
                        st.markdown(f"**סטטוס:** {'פעיל ✅' if p.is_active else 'לא פעיל'}")

                    with col2:
                        if not p.is_active and not readonly:
                            if st.button("הפעל", key=f"activate_{p.id}"):
                                activate_period(p.id, company_id)
                                # Update session state
                                st.session_state["active_period"] = {
                                    "id": p.id,
                                    "name": p.name,
                                    "location": p.location,
                                    "start_date": str(p.start_date),
                                    "end_date": str(p.end_date),
                                }
                                st.success(f"תקופה '{p.name}' הופעלה!")
                                st.rerun()

                    with col3:
                        if not p.is_active and not readonly:
                            user = get_current_user()
                            if user and user.get("role") == "mefaked":
                                if st.button("🗑️ מחק", key=f"delete_{p.id}"):
                                    from military_manager.services.backup_service import create_backup
                                    create_backup(reason="pre-delete", created_by=user.get("display_name"))
                                    delete_period(p.id)
                                    st.success("התקופה נמחקה (גיבוי נשמר)")
                                    st.rerun()
                            else:
                                st.caption("🔒 מ\"פ בלבד")

                    # Edit form
                    with st.form(key=f"edit_period_{p.id}"):
                        st.markdown("**עריכה:**")
                        new_name = st.text_input("שם", value=p.name)
                        new_location = st.text_input("מיקום", value=p.location or "")
                        c1, c2 = st.columns(2)
                        with c1:
                            new_start = st.date_input("תחילה", value=p.start_date, format="DD/MM/YYYY")
                        with c2:
                            new_end = st.date_input("סיום", value=p.end_date, format="DD/MM/YYYY")

                        if st.form_submit_button("💾 שמור שינויים", disabled=readonly):
                            update_period(p.id, name=new_name, location=new_location,
                                          start_date=new_start, end_date=new_end)
                            st.success("התקופה עודכנה!")
                            st.rerun()

    # ── New period ──
    with tab_new:
        with st.form("new_period_form"):
            st.markdown("### יצירת תקופת מילואים חדשה")

            name = st.text_input("שם התקופה", placeholder="לדוגמה: מילואים מרץ 2025")
            location = st.text_input("מיקום", placeholder="לדוגמה: נחל שלמה")

            c1, c2 = st.columns(2)
            with c1:
                start_date = st.date_input(
                    "תאריך התחלה",
                    value=date.today(),
                    format="DD/MM/YYYY",
                )
            with c2:
                end_date = st.date_input(
                    "תאריך סיום",
                    value=date.today() + timedelta(days=21),
                    format="DD/MM/YYYY",
                )

            # Copy from previous
            existing = get_all_periods(company_id)
            copy_from = None
            copy_quals = True
            copy_drvs = True
            if existing:
                copy_options = ["ללא העתקה"] + [f"{p.name} (ID: {p.id})" for p in existing]
                copy_choice = st.selectbox("העתק חיילים מתקופה קודמת", copy_options)
                if copy_choice != "ללא העתקה":
                    copy_from = int(copy_choice.split("ID: ")[1].rstrip(")"))
                    copy_quals = st.checkbox("העתק גם הסמכות", value=True)
                    copy_drvs = st.checkbox("העתק גם נהגים מאושרים", value=True)

            submitted = st.form_submit_button("✅ צור תקופה", disabled=readonly)

            if submitted:
                if not name:
                    st.error("יש להזין שם לתקופה")
                elif start_date >= end_date:
                    st.error("תאריך התחלה חייב להיות לפני תאריך סיום")
                else:
                    period = create_period(
                        name=name,
                        location=location,
                        start_date=start_date,
                        end_date=end_date,
                        company_id=company_id,
                    )
                    activate_period(period.id, company_id)
                    st.session_state["active_period"] = {
                        "id": period.id,
                        "name": period.name,
                        "location": period.location,
                        "start_date": str(period.start_date),
                        "end_date": str(period.end_date),
                    }

                    if copy_from:
                        result = copy_soldiers_from_period(
                            copy_from, period.id,
                            copy_qualifications=copy_quals,
                            copy_drivers=copy_drvs,
                        )
                        parts = [f"{result['soldiers']} חיילים"]
                        if result["qualifications"]:
                            parts.append(f"{result['qualifications']} הסמכות")
                        if result["drivers"]:
                            parts.append(f"{result['drivers']} נהגים")
                        st.success(f"התקופה נוצרה! הועתקו: {', '.join(parts)}.")
                    else:
                        st.success("התקופה נוצרה והופעלה!")

                    st.rerun()
