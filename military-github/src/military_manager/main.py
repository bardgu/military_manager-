"""Military Reserve Manager — Main entry point.

Handles page config, session state initialization, navigation routing,
and top-level layout.
"""

from __future__ import annotations

import streamlit as st

from military_manager.config import APP_NAME
from military_manager.database import init_db
from military_manager.logger import setup_logging
from military_manager.components.rtl import inject_rtl_css
from military_manager.components.navigation import render_sidebar_nav, render_mobile_nav, PAGES
from military_manager.components.auth import require_login
from military_manager.services.period_service import get_active_period


def _init_session_state():
    """Initialize session state defaults."""
    defaults = {
        "current_page": "home",
        "active_period": None,
        "commander_name": None,
        "commander_role": None,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val

    # Load active period on first run
    if st.session_state["active_period"] is None:
        period = get_active_period()
        if period:
            st.session_state["active_period"] = {
                "id": period.id,
                "name": period.name,
                "location": period.location,
                "start_date": str(period.start_date),
                "end_date": str(period.end_date),
            }


def main():
    """Application entry point."""
    # --- Page config (must be first Streamlit call) ---
    st.set_page_config(
        page_title=APP_NAME,
        page_icon="🪖",
        layout="wide",
        initial_sidebar_state="auto",
    )

    # --- Infrastructure ---
    setup_logging()
    init_db()

    # --- Auto-backup (once per server session) ---
    if "backup_initialized" not in st.session_state:
        from military_manager.services.backup_service import start_auto_backup
        start_auto_backup()
        st.session_state["backup_initialized"] = True

    # --- Session state ---
    _init_session_state()

    # --- RTL support ---
    inject_rtl_css()

    # --- Keepalive: prevent HF Spaces from sleeping ---
    st.markdown(
        """
        <script>
        // Send a keepalive ping every 5 minutes to prevent container sleep
        setInterval(function() {
            fetch(window.location.href, {method: 'HEAD', cache: 'no-cache'}).catch(()=>{});
        }, 5 * 60 * 1000);
        </script>
        """,
        unsafe_allow_html=True,
    )

    # --- Authentication ---
    user = require_login()
    if not user:
        return  # Show login form, don't render the rest

    # --- Navigation ---
    selected_page = render_sidebar_nav()

    # --- Mobile bottom nav (hidden on desktop via CSS) ---
    # Check if mobile nav triggered a page change via query params
    _valid_pages = {p[0] for p in PAGES}
    _qp = st.query_params
    if "page" in _qp:
        _target = _qp["page"]
        if _target in _valid_pages:
            st.session_state["current_page"] = _target
            selected_page = _target
        del st.query_params["page"]
    render_mobile_nav()

    # --- Page routing ---
    _route_page(selected_page)


def _route_page(page_key: str):
    """Route to the selected page module."""
    # Lazy imports to avoid circular dependencies and speed up startup
    if page_key == "home":
        from military_manager.pages.home import render
    elif page_key == "periods":
        from military_manager.pages.periods import render
    elif page_key == "soldiers":
        from military_manager.pages.soldiers import render
    elif page_key == "daily_status":
        from military_manager.pages.daily_status import render
    elif page_key == "drivers":
        from military_manager.pages.drivers import render
    elif page_key == "qualifications":
        from military_manager.pages.qualifications import render
    elif page_key == "constraints":
        from military_manager.pages.constraints import render
    elif page_key == "tasks":
        from military_manager.pages.tasks import render
    elif page_key == "shifts":
        from military_manager.pages.shifts import render
    elif page_key == "equipment":
        from military_manager.pages.equipment import render
    elif page_key == "requests":
        from military_manager.pages.requests_page import render
    elif page_key == "org_tree":
        from military_manager.pages.org_tree import render
    elif page_key == "availability":
        from military_manager.pages.availability import render
    elif page_key == "report1":
        from military_manager.pages.report1 import render
    elif page_key == "users":
        from military_manager.pages.users import render
    elif page_key == "profile":
        from military_manager.pages.profile import render
    elif page_key == "reports":
        from military_manager.pages.reports import render
    elif page_key == "settings":
        from military_manager.pages.settings import render
    else:
        from military_manager.pages.home import render

    render()


if __name__ == "__main__":
    main()
