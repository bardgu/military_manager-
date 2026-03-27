"""User management page — accessible only by מ"פ."""

from __future__ import annotations

import streamlit as st

from military_manager.components.navigation import render_page_header
from military_manager.components.auth import require_role, get_current_user
from military_manager.services.auth_service import (
    get_all_users, create_user, update_user, delete_user,
    ROLE_LABELS,
)
from military_manager.services.soldier_service import get_sub_units
from military_manager.components.filters import period_guard


def render():
    render_page_header("👥 ניהול משתמשים", "הוספה ועריכת משתמשים והרשאות")

    if not require_role(["mefaked"]):
        st.warning("עמוד זה נגיש רק למ\"פ")
        return

    period = period_guard()
    pid = period["id"] if period else None
    sub_units = get_sub_units(pid) if pid else []

    tab_list, tab_add = st.tabs(["📋 רשימת משתמשים", "➕ הוסף משתמש"])

    with tab_list:
        _render_user_list(sub_units)

    with tab_add:
        _render_add_user(sub_units)


def _render_user_list(sub_units: list[str]):
    """Show list of all users with edit/delete options."""
    users = get_all_users()

    if not users:
        st.info("אין משתמשים במערכת")
        return

    for u in users:
        active_tag = "✅" if u["is_active"] else "❌"
        role_label = ROLE_LABELS.get(u["role"], u["role"])
        sub = f" | {u['sub_unit']}" if u.get("sub_unit") else ""

        with st.expander(f"{active_tag} {u['display_name']} — {role_label}{sub}"):
            st.markdown(f"**שם משתמש:** {u['username']}")
            st.markdown(f"**תפקיד:** {role_label}")
            if u.get("sub_unit"):
                st.markdown(f"**מחלקה:** {u['sub_unit']}")

            col1, col2, col3 = st.columns(3)

            with col1:
                new_role = st.selectbox(
                    "שנה תפקיד",
                    list(ROLE_LABELS.keys()),
                    index=list(ROLE_LABELS.keys()).index(u["role"]) if u["role"] in ROLE_LABELS else 0,
                    format_func=lambda x: ROLE_LABELS.get(x, x),
                    key=f"role_{u['id']}",
                )
            with col2:
                new_sub = st.selectbox(
                    "מחלקה",
                    [""] + sub_units,
                    index=(sub_units.index(u["sub_unit"]) + 1) if u.get("sub_unit") and u["sub_unit"] in sub_units else 0,
                    key=f"sub_{u['id']}",
                )
            with col3:
                new_pass = st.text_input(
                    "סיסמה חדשה (ריק = ללא שינוי)",
                    type="password",
                    key=f"pass_{u['id']}",
                )

            bcol1, bcol2 = st.columns(2)
            with bcol1:
                if st.button("💾 עדכן", key=f"update_{u['id']}"):
                    kwargs = {"role": new_role, "sub_unit": new_sub or None}
                    if new_pass:
                        kwargs["password"] = new_pass
                    update_user(u["id"], **kwargs)
                    st.success("✅ עודכן")
                    st.rerun()
            with bcol2:
                if u["is_active"]:
                    if st.button("🗑️ השבת", key=f"del_{u['id']}"):
                        delete_user(u["id"])
                        st.success("משתמש הושבת")
                        st.rerun()
                else:
                    if st.button("♻️ שחזר", key=f"restore_{u['id']}"):
                        update_user(u["id"], is_active=True)
                        st.success("משתמש שוחזר")
                        st.rerun()


def _render_add_user(sub_units: list[str]):
    """Add new user form."""
    with st.form("add_user_form"):
        st.markdown("### הוסף משתמש חדש")

        username = st.text_input("שם משתמש (באנגלית)")
        display_name = st.text_input("שם תצוגה")
        password = st.text_input("סיסמה", type="password")
        role = st.selectbox(
            "תפקיד",
            list(ROLE_LABELS.keys()),
            format_func=lambda x: ROLE_LABELS.get(x, x),
        )
        sub_unit = st.selectbox("מחלקה (למ\"מ)", [""] + sub_units)

        if st.form_submit_button("➕ צור משתמש", type="primary"):
            if not username or not password or not display_name:
                st.error("יש למלא את כל השדות")
            else:
                user = create_user(
                    username=username,
                    password=password,
                    display_name=display_name,
                    role=role,
                    sub_unit=sub_unit or None,
                )
                if user:
                    st.success(f"✅ משתמש '{display_name}' נוצר בהצלחה!")
                    st.rerun()
                else:
                    st.error("שם משתמש כבר קיים")
