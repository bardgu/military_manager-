"""Login component — handles authentication UI."""

from __future__ import annotations

import streamlit as st

from military_manager.services.auth_service import authenticate, ensure_default_admin


def get_current_user() -> dict | None:
    """Get current logged-in user from session state."""
    return st.session_state.get("current_user")


def require_login() -> dict | None:
    """Show login form if not authenticated. Returns user dict or None."""
    ensure_default_admin()

    if "current_user" in st.session_state and st.session_state["current_user"]:
        return st.session_state["current_user"]

    # Show login form
    _render_login()
    return None


def _render_login():
    """Render the login form."""
    st.markdown(
        """
        <style>
        .login-container {
            max-width: 400px;
            margin: 4rem auto;
            padding: 2rem;
            border-radius: 12px;
            background: white;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("## 🪖 כניסה למערכת")
        st.markdown("מערכת ניהול מילואים")

        with st.form("login_form"):
            username = st.text_input("שם משתמש", placeholder="שם משתמש")
            password = st.text_input("סיסמה", type="password", placeholder="סיסמה")

            if st.form_submit_button("🔑 התחבר", use_container_width=True, type="primary"):
                if not username or not password:
                    st.error("יש להזין שם משתמש וסיסמה")
                else:
                    user = authenticate(username, password)
                    if user:
                        st.session_state["current_user"] = user
                        st.session_state["commander_name"] = user["display_name"]
                        st.session_state["commander_role"] = user["role"]
                        st.rerun()
                    else:
                        st.error("שם משתמש או סיסמה שגויים")

        st.caption("ברירת מחדל: שם משתמש `mefaked` סיסמה `1234`")


def render_user_info():
    """Render user info in sidebar."""
    user = get_current_user()
    if not user:
        return

    from military_manager.services.auth_service import ROLE_LABELS

    role_label = ROLE_LABELS.get(user["role"], user["role"])
    st.sidebar.markdown(f"👤 **{user['display_name']}**")
    st.sidebar.caption(f"תפקיד: {role_label}")
    if user.get("sub_unit"):
        st.sidebar.caption(f"מחלקה: {user['sub_unit']}")

    if st.sidebar.button("🚪 התנתק", use_container_width=True):
        st.session_state["current_user"] = None
        st.session_state["commander_name"] = None
        st.session_state["commander_role"] = None
        st.rerun()


def require_role(allowed_roles: list[str]) -> bool:
    """Check if current user has one of the allowed roles.
    Returns True if allowed, shows error if not.
    """
    user = get_current_user()
    if not user:
        return False
    if user["role"] in allowed_roles:
        return True
    st.error("אין לך הרשאה לבצע פעולה זו")
    return False
