"""User profile page — change own password."""

from __future__ import annotations

import streamlit as st

from military_manager.components.navigation import render_page_header
from military_manager.components.auth import get_current_user
from military_manager.services.auth_service import change_own_password, ROLE_LABELS


def render():
    render_page_header("👤 הגדרות אישיות", "שינוי סיסמה ופרטים אישיים")

    user = get_current_user()
    if not user:
        st.error("לא מחובר")
        return

    # Show user info
    st.markdown(f"### פרטים אישיים")
    
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**שם משתמש:** {user['username']}")
        st.markdown(f"**שם תצוגה:** {user['display_name']}")
    with col2:
        role_label = ROLE_LABELS.get(user["role"], user["role"])
        st.markdown(f"**תפקיד:** {role_label}")
        if user.get("sub_unit"):
            st.markdown(f"**מחלקה:** {user['sub_unit']}")

    st.markdown("---")

    # Password change form
    st.markdown("### 🔐 שינוי סיסמה")
    
    with st.form("change_password_form"):
        old_password = st.text_input("סיסמה נוכחית", type="password")
        new_password = st.text_input("סיסמה חדשה", type="password")
        confirm_password = st.text_input("אימות סיסמה חדשה", type="password")

        if st.form_submit_button("💾 שמור סיסמה חדשה", type="primary"):
            if not old_password or not new_password or not confirm_password:
                st.error("יש למלא את כל השדות")
            elif new_password != confirm_password:
                st.error("הסיסמאות אינן תואמות")
            elif len(new_password) < 4:
                st.error("הסיסמה חייבת להכיל לפחות 4 תווים")
            else:
                success = change_own_password(user["id"], old_password, new_password)
                if success:
                    st.success("✅ הסיסמה שונתה בהצלחה!")
                    st.balloons()
                else:
                    st.error("❌ הסיסמה הנוכחית שגויה")

    st.markdown("---")
    st.caption("💡 טיפ: בחר סיסמה חזקה עם אותיות, מספרים וסימנים מיוחדים")
