"""Navigation component using streamlit-option-menu."""

import streamlit as st
from streamlit_option_menu import option_menu


# Page definitions: (key, icon, label)
PAGES = [
    ("home", "house", "דף הבית"),
    ("periods", "calendar-range", "תקופות מילואים"),
    ("soldiers", "people", "חיילים"),
    ("daily_status", "grid-3x3-gap", "סטטוס יומי"),
    ("drivers", "truck", "נהגים מאושרים"),
    ("qualifications", "award", "הסמכות"),
    ("constraints", "exclamation-triangle", "אילוצים"),
    ("tasks", "clipboard-check", "משימות"),
    ("shifts", "table", "שבצ\"ק"),
    ("equipment", "shield-check", "ציוד"),
    ("requests", "envelope", "בקשות"),
    ("org_tree", "diagram-3", "מבנה ארגוני"),
    ("availability", "calendar-check", "דו\"ח זמינות"),
    ("report1", "file-earmark-text", "דו\"ח 1"),
    ("reports", "bar-chart", "דוחות"),
    ("users", "person-badge", "ניהול משתמשים"),
    ("profile", "person-circle", "הגדרות אישיות"),
    ("settings", "gear", "הגדרות"),
]

# Quick-access pages shown in the mobile bottom nav bar.
# Keep this short (max 5) for usability.
MOBILE_NAV_PAGES = [
    ("home", "🏠", "בית"),
    ("shifts", "📋", "שבצ\"ק"),
    ("report1", "📄", "דו\"ח 1"),
    ("constraints", "⚠️", "אילוצים"),
    ("soldiers", "👥", "חיילים"),
]


def render_sidebar_nav() -> str:
    """Render navigation in sidebar. Returns selected page key."""
    with st.sidebar:
        # Company header + switcher
        from military_manager.components.auth import get_current_user
        from military_manager.services.company_service import get_all_companies
        user = get_current_user()
        companies = get_all_companies() if user else []
        selected_cid = st.session_state.get("selected_company_id")
        is_admin = user.get("role") == "mefaked" if user else False

        if is_admin and companies:
            # Admin can switch between companies
            comp_names = [c["name"] for c in companies]
            comp_ids = [c["id"] for c in companies]
            current_idx = comp_ids.index(selected_cid) if selected_cid in comp_ids else 0
            chosen = st.selectbox(
                "🪖 פלוגה פעילה",
                comp_names,
                index=current_idx,
                key="_company_selector",
            )
            chosen_id = comp_ids[comp_names.index(chosen)]
            if chosen_id != selected_cid:
                st.session_state["selected_company_id"] = chosen_id
                st.session_state["active_period"] = None  # force reload
                st.session_state["_period_company_id"] = None
                st.rerun()
            # Show company name + read-only badge if viewing another company
            own_cid = user.get("company_id")
            if own_cid and chosen_id != own_cid:
                st.markdown(f"### 🪖 {chosen}")
                st.caption("🔒 צפייה בלבד — לא הפלוגה שלך")
            else:
                st.markdown(f"### 🪖 {chosen}")
        elif companies and selected_cid:
            comp = next((c for c in companies if c["id"] == selected_cid), None)
            st.markdown(f"### 🪖 {comp['name'] if comp else 'ניהול מילואים'}")
        else:
            st.markdown("### 🪖 ניהול מילואים")
        st.markdown("---")

        # Build options for the menu
        labels = [p[2] for p in PAGES]
        icons = [p[1] for p in PAGES]

        # Get current page index
        current_key = st.session_state.get("current_page", "home")
        default_idx = next(
            (i for i, p in enumerate(PAGES) if p[0] == current_key), 0
        )

        selected = option_menu(
            menu_title=None,
            options=labels,
            icons=icons,
            default_index=default_idx,
            orientation="vertical",
            styles={
                "container": {"padding": "0", "direction": "rtl"},
                "icon": {"font-size": "16px"},
                "nav-link": {
                    "font-size": "14px",
                    "text-align": "right",
                    "direction": "rtl",
                    "--hover-color": "#E8F5E9",
                    "padding": "8px 12px",
                },
                "nav-link-selected": {
                    "background-color": "#1B5E20",
                    "color": "white",
                    "font-weight": "600",
                },
            },
        )

        # Map label back to key
        selected_key = next(
            (p[0] for p in PAGES if p[2] == selected), "home"
        )
        st.session_state["current_page"] = selected_key

        st.markdown("---")

        # Show active period info
        period = st.session_state.get("active_period")
        if period:
            st.markdown(f"**תקופה:** {period.get('name', '—')}")
            start = period.get("start_date", "")
            end = period.get("end_date", "")
            if start and end:
                st.caption(f"{start} — {end}")
        else:
            st.warning("לא נבחרה תקופת מילואים")

        # Show user info if logged in
        from military_manager.components.auth import render_user_info
        render_user_info()

        return selected_key


def render_page_header(title: str, subtitle: str = ""):
    """Render a consistent page header."""
    st.markdown(f"## {title}")
    if subtitle:
        st.caption(subtitle)
    st.markdown("---")


def render_mobile_nav():
    """Render a fixed bottom navigation bar visible only on mobile.

    Uses pure HTML/CSS/JS so it works without extra Streamlit widgets.
    The bar is hidden on screens wider than 768px via the
    .mobile-bottom-nav CSS class defined in rtl.py.
    """
    current = st.session_state.get("current_page", "home")

    buttons_html = ""
    for key, icon, label in MOBILE_NAV_PAGES:
        active_cls = "mbn-active" if key == current else ""
        buttons_html += (
            f'<button class="mbn-btn {active_cls}" '
            f'onclick="mbnNavigate(\'{key}\')">'
            f'<span class="mbn-icon">{icon}</span>'
            f'<span class="mbn-label">{label}</span>'
            f'</button>'
        )

    # The "more" button opens the sidebar
    buttons_html += (
        '<button class="mbn-btn" onclick="mbnOpenSidebar()">'
        '<span class="mbn-icon">☰</span>'
        '<span class="mbn-label">עוד</span>'
        '</button>'
    )

    html = f"""
    <div class="mobile-bottom-nav" id="mobileBottomNav">
        {buttons_html}
    </div>
    <style>
        .mobile-bottom-nav {{
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            height: 60px;
            background: #ffffff;
            border-top: 1px solid #e0e0e0;
            box-shadow: 0 -2px 10px rgba(0,0,0,0.1);
            z-index: 9998;
            justify-content: space-around;
            align-items: center;
            direction: rtl;
            padding: 0 4px;
        }}
        .mbn-btn {{
            flex: 1;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            background: none;
            border: none;
            cursor: pointer;
            color: #666;
            padding: 4px 2px;
            min-height: 56px;
            -webkit-tap-highlight-color: transparent;
            transition: color 0.15s;
        }}
        .mbn-btn:active {{
            background: #f0f0f0;
            border-radius: 8px;
        }}
        .mbn-btn.mbn-active {{
            color: #1B5E20;
        }}
        .mbn-btn.mbn-active .mbn-icon {{
            transform: scale(1.15);
        }}
        .mbn-icon {{
            font-size: 22px;
            line-height: 1;
            margin-bottom: 2px;
            transition: transform 0.15s;
        }}
        .mbn-label {{
            font-size: 10px;
            font-weight: 500;
            line-height: 1.1;
            white-space: nowrap;
        }}
    </style>
    <script>
    function mbnNavigate(pageKey) {{
        // Navigate using query params — Streamlit picks this up on rerun
        const url = new URL(window.parent.location);
        url.searchParams.set('page', pageKey);
        window.parent.location.href = url.toString();
    }}
    function mbnOpenSidebar() {{
        // Click the Streamlit sidebar toggle button
        const btns = window.parent.document.querySelectorAll(
            'button[kind="header"], [data-testid="stSidebarCollapsedControl"] button'
        );
        for (const btn of btns) {{
            if (btn.offsetParent !== null) {{ btn.click(); return; }}
        }}
        // Fallback: try the hamburger in the header
        const headerBtns = window.parent.document.querySelectorAll('header button');
        for (const btn of headerBtns) {{
            if (btn.offsetParent !== null) {{ btn.click(); return; }}
        }}
    }}
    </script>
    """
    st.markdown(html, unsafe_allow_html=True)
