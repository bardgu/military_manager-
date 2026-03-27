"""RTL (Right-to-Left) CSS support for Hebrew UI."""

import streamlit as st

RTL_CSS = """
<style>
/* ===== Global RTL ===== */
html, body, [data-testid="stAppViewContainer"] {
    direction: rtl;
    text-align: right;
    font-family: 'Segoe UI', Tahoma, Arial, sans-serif;
}

/* ===== Sidebar RTL ===== */
[data-testid="stSidebar"] {
    direction: rtl;
    text-align: right;
}
[data-testid="stSidebar"] .element-container {
    direction: rtl;
}

/* ===== Input fields RTL ===== */
.stTextInput input,
.stTextArea textarea,
.stSelectbox > div > div,
.stMultiselect > div > div,
.stNumberInput input,
.stDateInput input {
    direction: rtl;
    text-align: right;
}

/* ===== Column layout fix ===== */
[data-testid="column"] {
    direction: rtl;
}

/* ===== Data editor and dataframe ===== */
[data-testid="stDataFrame"],
[data-testid="stDataEditor"] {
    direction: rtl;
}
.dvn-scroller {
    direction: rtl;
}

/* ===== Tabs alignment ===== */
.stTabs [data-baseweb="tab-list"] {
    direction: rtl;
    gap: 8px;
}
.stTabs [data-baseweb="tab"] {
    direction: rtl;
}

/* ===== Labels alignment ===== */
.stSelectbox label,
.stMultiselect label,
.stTextInput label,
.stTextArea label,
.stNumberInput label,
.stDateInput label,
.stSlider label,
.stRadio label,
.stCheckbox label {
    direction: rtl;
    text-align: right;
    width: 100%;
}

/* ===== Metric cards RTL ===== */
[data-testid="stMetricValue"],
[data-testid="stMetricLabel"],
[data-testid="stMetricDelta"] {
    direction: rtl;
    text-align: center;
}

/* ===== Button alignment ===== */
.stButton > button {
    direction: rtl;
}

/* ===== Expander RTL ===== */
details[data-testid="stExpander"] summary {
    direction: rtl;
    text-align: right;
}

/* ===== Toast / alerts RTL ===== */
.stAlert {
    direction: rtl;
    text-align: right;
}

/* ===== Navigation menu ===== */
.nav-link {
    direction: rtl !important;
    text-align: right !important;
}

/* ===== Table improvements ===== */
table {
    direction: rtl;
}
table th, table td {
    text-align: right;
}

/* ===== Frozen columns/rows for custom HTML tables ===== */
/* Ensure sticky cells render properly inside overflow containers */
div[style*="overflow-x:auto"] table {
    border-collapse: separate;
    border-spacing: 0;
}
/* Add subtle shadow to sticky columns for visual separation */
td[style*="position:sticky"], .r1-name, .r1-role, .r1s-cat, .sched-task {
    box-shadow: -2px 0 4px rgba(0,0,0,0.08);
}
/* Shadow on sticky headers */
th[style*="position:sticky"] {
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

/* ===== Form submit button ===== */
[data-testid="stFormSubmitButton"] {
    direction: rtl;
}

/* ===== Markdown headers ===== */
.element-container h1,
.element-container h2,
.element-container h3,
.element-container h4,
.element-container p {
    direction: rtl;
    text-align: right;
}

/* ===== Status badge styling ===== */
.status-badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 12px;
    font-size: 0.85em;
    font-weight: 500;
    margin: 1px;
}
.status-present {
    background-color: #C8E6C9;
    color: #1B5E20;
}
.status-away {
    background-color: #FFECB3;
    color: #E65100;
}
.status-special {
    background-color: #B3E5FC;
    color: #01579B;
}
.status-absent {
    background-color: #FFCDD2;
    color: #B71C1C;
}

/* ===== Card containers ===== */
.card {
    background: white;
    border-radius: 10px;
    padding: 1rem;
    box-shadow: 0 1px 3px rgba(0,0,0,0.12);
    margin-bottom: 0.5rem;
}

/* ===== Responsive tweaks for mobile ===== */
@media (max-width: 768px) {
    /* Sidebar: overlay mode on mobile — don't push content */
    [data-testid="stSidebar"] {
        min-width: 260px !important;
        width: 270px !important;
        position: fixed !important;
        top: 0;
        right: 0;
        height: 100vh !important;
        z-index: 9999 !important;
        box-shadow: -4px 0 12px rgba(0,0,0,0.25) !important;
        transition: transform 0.3s ease !important;
    }
    [data-testid="stSidebar"][aria-expanded="false"] {
        transform: translateX(100%) !important;
    }

    /* Main content — full width, no sidebar offset */
    .main .block-container {
        padding: 0.5rem 0.5rem !important;
        max-width: 100% !important;
    }
    [data-testid="stAppViewContainer"] > .main {
        margin-right: 0 !important;
    }

    /* Header area — reduce padding */
    header[data-testid="stHeader"] {
        padding: 0 !important;
    }

    /* Metrics — smaller on mobile */
    [data-testid="stMetricValue"] {
        font-size: 1.3rem !important;
    }
    [data-testid="stMetricLabel"] {
        font-size: 0.75rem !important;
    }

    /* Make tabs scrollable */
    [data-testid="stTabs"] [role="tablist"] {
        overflow-x: auto !important;
        flex-wrap: nowrap !important;
        -webkit-overflow-scrolling: touch;
    }
    [data-testid="stTabs"] button[role="tab"] {
        font-size: 0.8rem !important;
        padding: 0.4rem 0.6rem !important;
        white-space: nowrap !important;
    }

    /* Tables — horizontal scroll */
    [data-testid="stDataFrame"],
    [data-testid="stDataEditor"] {
        overflow-x: auto !important;
    }

    /* Columns — stack vertically on mobile */
    [data-testid="column"] {
        min-width: 100% !important;
    }

    /* Forms — full width inputs */
    .stTextInput, .stSelectbox, .stDateInput, .stMultiSelect {
        width: 100% !important;
    }

    /* Buttons — larger tap target (44px min for accessibility) */
    .stButton > button {
        min-height: 44px !important;
        font-size: 0.9rem !important;
    }

    /* Expanders — slightly smaller text */
    [data-testid="stExpander"] {
        font-size: 0.9rem !important;
    }

    /* Make bottom nav bar visible */
    .mobile-bottom-nav {
        display: flex !important;
    }
    /* Add bottom padding so content isn't hidden behind nav bar */
    .main .block-container {
        padding-bottom: 70px !important;
    }
}

/* Mobile bottom nav — hidden on desktop */
.mobile-bottom-nav {
    display: none !important;
}

/* ===== Viewport meta for mobile zoom ===== */
</style>
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
"""


def inject_rtl_css():
    """Inject RTL CSS into the Streamlit page."""
    st.markdown(RTL_CSS, unsafe_allow_html=True)


def status_badge(status: str, category: str = "present") -> str:
    """Return HTML for a colored status badge."""
    css_class = f"status-{category}"
    return f'<span class="status-badge {css_class}">{status}</span>'


def card_container(content: str) -> str:
    """Return HTML for a card container."""
    return f'<div class="card">{content}</div>'
