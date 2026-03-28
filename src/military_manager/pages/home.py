"""Home / Dashboard page — overview of current reserve period."""

from __future__ import annotations

from datetime import date, datetime

import streamlit as st

from military_manager.components.navigation import render_page_header
from military_manager.components.filters import period_guard
from military_manager.config import IRRELEVANT_UNIT
from military_manager.services.status_service import (
    get_daily_counts, calculate_leave_stats, get_daily_status_grid,
    count_na_soldiers,
)
from military_manager.services.soldier_service import get_period_soldiers
from military_manager.services.task_service import get_daily_assignments, get_minimum_soldiers_needed
from military_manager.services.stats_service import (
    compute_percentages, init_default_groups, get_setting, set_setting,
    get_irrelevant_soldiers,
)
from military_manager.services.briefing_service import generate_briefing


def render():
    render_page_header("🪖 דף הבית", "סיכום תקופת מילואים נוכחית")

    period = period_guard()
    if not period:
        st.info("👈 התחל בהגדרת תקופת מילואים חדשה דרך תפריט 'תקופות מילואים'")
        return

    pid = period["id"]
    today = date.today()

    # ── Date picker ──
    p_start = period.get("start_date", "")
    p_end = period.get("end_date", "")
    try:
        min_date = datetime.strptime(p_start, "%Y-%m-%d").date() if p_start else today
    except (ValueError, TypeError):
        min_date = today
    try:
        max_date = datetime.strptime(p_end, "%Y-%m-%d").date() if p_end else today
    except (ValueError, TypeError):
        max_date = today

    selected_date = st.date_input(
        "📅 בחר תאריך",
        value=today if min_date <= today <= max_date else min_date,
        min_value=min_date,
        max_value=max_date,
        format="DD/MM/YYYY",
        key="home_date_picker",
    )
    is_today = selected_date == today
    day_label = "היום" if is_today else selected_date.strftime("%d/%m/%Y")

    HEB_DAYS = {0: "שני", 1: "שלישי", 2: "רביעי", 3: "חמישי", 4: "שישי", 5: "שבת", 6: "ראשון"}
    heb_day = HEB_DAYS.get(selected_date.weekday(), "")
    st.markdown(
        f'<div style="text-align:center; font-size:1.1em; color:#666; margin-bottom:8px;">'
        f'📆 יום {heb_day} — {selected_date.strftime("%d/%m/%Y")}'
        f'{" (היום)" if is_today else ""}'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── KPI row ──
    soldiers = get_period_soldiers(pid, exclude_irrelevant_unit=True)
    total = len(soldiers)
    counts = get_daily_counts(pid, selected_date)

    PRESENT_SET = {"בבסיס", "התייצב", "חוזר מחופש"}
    NA_SET = {"לא בשמפ", 'לא בשמ"פ'}

    present = sum(v for k, v in counts.items() if k in PRESENT_SET)
    not_present = sum(v for k, v in counts.items() if k not in PRESENT_SET and k not in NA_SET)
    # Count NA soldiers using the robust function (checks most recent status)
    na_count = count_na_soldiers(pid, selected_date)
    active_total = total - na_count  # total without "לא בשמפ"
    unset = active_total - sum(v for k, v in counts.items() if k not in NA_SET)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("סה\"כ בשמ\"פ", active_total)
    c2.metric("נוכחים", f"{present} / {active_total}")
    c3.metric("לא נוכחים", not_present)
    c4.metric("ללא סטטוס", max(unset, 0))

    if na_count > 0:
        st.caption(f"🔹 לא בשמ\"פ: {na_count} חיילים (לא נכללים בספירה)")

    # ── Prominent presence banner ──
    pct = round(present / active_total * 100) if active_total else 0
    bar_color = "#4CAF50" if pct >= 70 else ("#FF9800" if pct >= 50 else "#F44336")
    st.markdown(
        f'<div style="background:{bar_color}18; border:2px solid {bar_color}; border-radius:12px; '
        f'padding:12px 20px; text-align:center; margin:8px 0 4px 0;">'
        f'<span style="font-size:1.4em; font-weight:bold; color:{bar_color};">'
        f'🏕️ בבסיס {day_label}: {present} מתוך {active_total} ({pct}%)'
        f'</span></div>',
        unsafe_allow_html=True,
    )

    # ── WhatsApp daily briefing ──
    _render_whatsapp_briefing(pid, selected_date, period, day_label)

    # ── Google Sheets quick sync ──
    _render_gsheet_sync(pid, period)

    # ── Breakdown of non-present soldiers ──
    non_present_counts = {k: v for k, v in counts.items()
                          if k not in PRESENT_SET and k not in NA_SET and v > 0}
    if non_present_counts or na_count:
        parts = []
        STATUS_EMOJI = {
            "חופש": "🏖️", "יוצא לחופש": "🚪", "גימלים": "🎖️",
            "פיצול": "✂️", "יוצא לפיצול": "✂️", "נפקד": "🚨",
            "משתחרר": "👋", "יוצא לקורס": "📚",
            "צפוי להתייצב": "⏳", "סיפוח מאוחר": "⏳",
        }
        for status, cnt in sorted(non_present_counts.items(), key=lambda x: -x[1]):
            emoji = STATUS_EMOJI.get(status, "•")
            parts.append(f"{emoji} {status}: <b>{cnt}</b>")
        if na_count:
            parts.append(f"🔇 לא בשמ\"פ: <b>{na_count}</b>")
        breakdown_html = " &nbsp;|&nbsp; ".join(parts)
        st.markdown(
            f'<div style="text-align:center; font-size:13px; color:#555; '
            f'margin:0 0 12px 0; direction:rtl;">{breakdown_html}</div>',
            unsafe_allow_html=True,
        )

    st.markdown("---")

    # ── Dynamic group stats with percentages ──
    _render_group_stats(pid, selected_date, day_label)

    st.markdown("---")

    # ── Status breakdown ──
    col_left, col_right = st.columns(2)

    with col_right:
        st.markdown(f"### 📊 פילוח סטטוסים — {day_label}")
        if counts:
            import plotly.express as px
            import pandas as pd

            df = pd.DataFrame(
                [{"סטטוס": k, "כמות": v} for k, v in sorted(counts.items(), key=lambda x: -x[1])]
            )
            fig = px.pie(df, values="כמות", names="סטטוס", hole=0.4)
            fig.update_layout(
                font=dict(family="Segoe UI, Arial"),
                margin=dict(t=20, b=20, l=20, r=20),
                height=300,
            )
            fig.update_traces(textposition="inside", textinfo="label+value")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info(f"אין נתוני סטטוס ל{day_label}")

    with col_left:
        st.markdown(f"### 📋 משימות — {day_label}")
        # Show daily minimum info
        try:
            min_info = get_minimum_soldiers_needed(pid)
            mc1, mc2 = st.columns(2)
            mc1.metric("מינימום בו-זמנית", min_info["min_needed"])
            mc2.metric("סה\"כ שיבוצים יומי", min_info["total_slots_per_day"])
        except Exception:
            pass

        assignments = get_daily_assignments(pid, selected_date)
        if assignments:
            for task_name, shifts in assignments.items():
                with st.expander(f"🎯 {task_name}", expanded=True):
                    for shift_num, soldiers_list in sorted(
                        ((k, v) for k, v in shifts.items() if isinstance(k, int) and isinstance(v, list))
                    ):
                        names = ", ".join(
                            s.get("name", "—") for s in soldiers_list
                        ) if soldiers_list else "טרם שובץ"
                        st.markdown(f"**משמרת {shift_num}:** {names}")
        else:
            st.info(f"אין שיבוצים ל{day_label}")

    st.markdown("---")

    # ── Per-squad summary ──
    st.markdown(f"### 🏘️ סיכום לפי מחלקות — {day_label}")
    from military_manager.services.soldier_service import get_sub_units
    units = get_sub_units(pid)

    if units:
        # Exclude the special irrelevant unit from display
        units = [u for u in units if u != IRRELEVANT_UNIT]

    if units:
        # Build per-soldier status lookup for selected date
        _grid = get_daily_status_grid(pid, selected_date, selected_date)
        _status_map = _grid.get("statuses", {}) if _grid else {}
        _dk = selected_date.isoformat()

        cols = st.columns(min(len(units), 5))
        for i, unit in enumerate(units):
            unit_soldiers = [s for s in soldiers if s.get("sub_unit") == unit]
            unit_total = len(unit_soldiers)
            unit_present = sum(
                1 for s in unit_soldiers
                if _status_map.get(f"{s['soldier_id']}_{_dk}", "") in PRESENT_SET
            )
            unit_away = unit_total - unit_present
            unit_pct = round(unit_present / unit_total * 100) if unit_total else 0
            bar_c = "#4CAF50" if unit_pct >= 70 else ("#FF9800" if unit_pct >= 50 else "#F44336")

            with cols[i % len(cols)]:
                st.markdown(
                    f'<div style="background:{bar_c}15; border:1px solid {bar_c}; '
                    f'border-radius:10px; padding:10px; text-align:center; margin-bottom:6px;">'
                    f'<b style="font-size:1.15em;">{unit}</b><br>'
                    f'<span style="color:{bar_c}; font-size:1.4em; font-weight:bold;">{unit_pct}%</span><br>'
                    f'<span style="font-size:0.9em;">🏕️ בבסיס: <b>{unit_present}</b> / {unit_total}</span><br>'
                    f'<span style="font-size:0.9em;">🏠 בבית: <b>{unit_away}</b></span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
    else:
        st.info("טרם הוגדרו מחלקות")

    # ── Professional roles breakdown ──
    # Reuse/build status map for roles breakdown
    if '_status_map' not in dir() or _status_map is None:
        _grid = get_daily_status_grid(pid, selected_date, selected_date)
        _status_map = _grid.get("statuses", {}) if _grid else {}
        _dk = selected_date.isoformat()
    _render_roles_breakdown(soldiers, _status_map, _dk, day_label)

    # ── Irrelevant soldiers notice ──
    irr = get_irrelevant_soldiers(pid)
    if irr:
        st.markdown("---")
        with st.expander(f"🚫 {len(irr)} חיילים לא רלוונטיים בתעסוקה"):
            for s in irr:
                st.caption(f"• {s['name']} ({s['sub_unit']})")

    # ── Status breakdown — who is where ──
    _render_status_breakdown(pid, selected_date, soldiers, day_label)

    # ── Notes summary ──
    _render_home_notes_summary(pid, period, selected_date, day_label)

    # ── Export to Excel ──
    _render_export_excel(pid, soldiers, _status_map, _dk, day_label)

    # ── Period info footer ──
    st.markdown("---")
    st.caption(
        f"תקופה: {period.get('name', '—')} | "
        f"מיקום: {period.get('location', '—')} | "
        f"{period.get('start_date', '')} — {period.get('end_date', '')}"
    )


# ── Professional Roles Breakdown ──────────────────────────────

# Categories: (display_name, icon, color, match_fn)
# match_fn receives (role, task_role) and returns True if soldier belongs.
_PROF_CATEGORIES: list[tuple[str, str, str]] = [
    ("מפקדי מחלקה", "🪖", "#5C6BC0"),
    ("מ\"כ / סמל מחלקה", "🎖️", "#7E57C2"),
    ("מחלצים", "🛡️", "#E65100"),
    ("חובשים", "🏥", "#C62828"),
    ("אנו\"ח", "📋", "#00838F"),
    ("מהנדסים", "🔧", "#4E342E"),
    ("קשר עורף", "📡", "#1565C0"),
    ("נהגים", "🚗", "#2E7D32"),
    ('מש"ק אוכלוסיה', "🏷️", "#6A1B9A"),
    ("לוחמים", "👤", "#546E7A"),
]


def _classify_soldier(role: str, task_role: str) -> str | None:
    """Return the professional category for a soldier, or None if unclassified."""
    r = role or ""
    tr = task_role or ""
    if r.startswith('מ"מ'):
        return "מפקדי מחלקה"
    if 'מ"כ' in r or "סמל מחלקה" in r:
        return 'מ"כ / סמל מחלקה'
    if "חובש" in r or "חובש" in tr:
        return "חובשים"
    if 'אנו"ח' in r or "אנוח" in tr:
        return 'אנו"ח'
    if "מהנדס" in r or "מהנדס" in tr:
        return "מהנדסים"
    if "קשר" in r or "קשר" in tr:
        return "קשר עורף"
    if "נהג" in r or "נהג" in tr:
        return "נהגים"
    if 'מש"ק' in r or "משק" in r:
        return 'מש"ק אוכלוסיה'
    if "מחלץ" in r or "חילוץ" in tr or "מחלץ" in tr:
        return "מחלצים"
    if "לוחם" in r or "לוחם" in tr:
        return "לוחמים"
    return None


def _render_roles_breakdown(soldiers: list[dict], status_map: dict, date_key: str, day_label: str = "היום"):
    """Render a graphical professional-roles breakdown showing present/total per category."""
    if not soldiers:
        return

    import pandas as pd
    from collections import defaultdict

    PRESENT_SET_LOCAL = {"בבסיס", "התייצב", "חוזר מחופש"}
    NA_SET_LOCAL = {"לא בשמפ", 'לא בשמ"פ'}

    def _is_present(s: dict) -> bool:
        sid = s["soldier_id"]
        status = status_map.get(f"{sid}_{date_key}", "")
        return status in PRESENT_SET_LOCAL

    def _is_na(s: dict) -> bool:
        sid = s["soldier_id"]
        status = status_map.get(f"{sid}_{date_key}", "")
        return status in NA_SET_LOCAL

    def _get_status(s: dict) -> str:
        return status_map.get(f"{s['soldier_id']}_{date_key}", "")

    st.markdown("---")
    st.markdown(f"### 🎯 פילוח בעלי מקצוע — {day_label}")

    # Classify
    cat_soldiers: dict[str, list[dict]] = defaultdict(list)
    unclassified: list[dict] = []

    for s in soldiers:
        if _is_na(s):
            continue  # skip לא בשמ"פ
        role = s.get("role") or ""
        task_role = s.get("task_role") or ""
        cat = _classify_soldier(role, task_role)
        if cat:
            cat_soldiers[cat].append(s)
        else:
            if role not in ('מ"פ', 'סמ"פ', 'רס"פ', "מחסנאי"):
                unclassified.append(s)

    cat_meta = {name: (icon, color) for name, icon, color in _PROF_CATEGORIES}

    # Build data
    chart_data = []
    ordered_cats = [name for name, _, _ in _PROF_CATEGORIES if name in cat_soldiers]
    for cat_name in ordered_cats:
        s_list = cat_soldiers[cat_name]
        icon, color = cat_meta[cat_name]
        total_cat = len(s_list)
        present_cat = sum(1 for s in s_list if _is_present(s))
        chart_data.append({
            "cat": cat_name,
            "label": f"{icon} {cat_name}",
            "total": total_cat,
            "present": present_cat,
            "absent": total_cat - present_cat,
            "color": color,
        })

    if not chart_data:
        st.info("אין נתוני תפקידים")
        return

    # ── Visual progress-bar cards via components.html (no CSS sanitisation) ──
    import streamlit.components.v1 as components

    n_cards = len(chart_data)
    row_height = 165  # px per row of cards
    cols_per_row = 3
    n_rows = (n_cards + cols_per_row - 1) // cols_per_row
    iframe_h = n_rows * row_height + 20

    cards_body = ""
    for d in chart_data:
        pct = round(d["present"] / d["total"] * 100) if d["total"] else 0
        bar_color = "#4CAF50" if pct >= 70 else ("#FF9800" if pct >= 40 else "#F44336")
        icon, _ = cat_meta[d["cat"]]
        sublabel = "&#x2705; כולם נוכחים!" if pct == 100 else f"חסרים {d['absent']}"

        cards_body += f'''
        <div style="border-radius:14px;padding:14px 16px;background:#fafafa;
                    border:2px solid {bar_color};overflow:hidden;">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
            <span style="font-size:1.8em;">{icon}</span>
            <span style="font-size:1.05em;font-weight:600;color:#333;">{d["cat"]}</span>
          </div>
          <div style="font-size:1.6em;font-weight:800;text-align:center;margin:2px 0 6px 0;color:{d["color"]};">
            {d["present"]} <span style="font-size:0.55em;color:#999;">מתוך</span> {d["total"]}
          </div>
          <div style="font-size:0.82em;color:#888;text-align:center;margin-bottom:8px;">
            {sublabel}
          </div>
          <div style="width:100%;height:22px;background:#e9ecef;border-radius:12px;overflow:hidden;">
            <div style="width:{max(pct, 4)}%;height:100%;border-radius:12px;
                        background:linear-gradient(90deg,{bar_color}cc,{bar_color});
                        display:flex;align-items:center;justify-content:center;
                        font-size:0.78em;font-weight:700;color:#fff;min-width:28px;">
              {pct}%
            </div>
          </div>
        </div>'''

    full_html = f'''<!DOCTYPE html>
<html lang="he" dir="rtl">
<head><meta charset="utf-8">
<style>
  *{{margin:0;padding:0;box-sizing:border-box;}}
  body{{font-family:'Segoe UI',Arial,sans-serif;background:transparent;direction:rtl;}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px;padding:4px;}}
</style></head>
<body><div class="grid">{cards_body}</div></body></html>'''

    components.html(full_html, height=iframe_h, scrolling=False)

    # ── Expandable soldier lists per category ──
    for cat_name in ordered_cats:
        s_list = cat_soldiers[cat_name]
        icon, color = cat_meta[cat_name]
        present_c = sum(1 for s in s_list if _is_present(s))
        total_c = len(s_list)

        with st.expander(f"{icon} {cat_name} — {present_c}/{total_c} נוכחים"):
            rows = []
            for s in sorted(s_list, key=lambda x: (not _is_present(x), x.get("full_name", ""))):
                status = _get_status(s)
                is_here = _is_present(s)
                rows.append({
                    "נוכחות": "✅" if is_here else "❌",
                    "שם": s.get("full_name", ""),
                    "תפקיד": s.get("role") or "",
                    "תפקיד מבצעי": s.get("task_role") or "",
                    "מחלקה": s.get("sub_unit") or "",
                    "סטטוס": status or "ללא סטטוס",
                })
            sdf = pd.DataFrame(rows)
            st.dataframe(sdf, use_container_width=True, hide_index=True, column_config={
                "נוכחות": st.column_config.TextColumn("נוכחות", width="small"),
                "שם": st.column_config.TextColumn("שם", width="medium"),
                "תפקיד": st.column_config.TextColumn("תפקיד", width="small"),
                "תפקיד מבצעי": st.column_config.TextColumn("תפקיד מבצעי", width="medium"),
                "מחלקה": st.column_config.TextColumn("מחלקה", width="small"),
                "סטטוס": st.column_config.TextColumn("סטטוס", width="small"),
            })

    if unclassified:
        with st.expander(f"❓ ללא סיווג — {len(unclassified)} חיילים"):
            rows = []
            for s in sorted(unclassified, key=lambda x: x.get("full_name", "")):
                rows.append({
                    "שם": s.get("full_name", ""),
                    "תפקיד": s.get("role") or "",
                    "תפקיד מבצעי": s.get("task_role") or "",
                    "מחלקה": s.get("sub_unit") or "",
                })
            sdf = pd.DataFrame(rows)
            st.dataframe(sdf, use_container_width=True, hide_index=True)


def _render_export_excel(pid: int, soldiers: list[dict],
                         status_map: dict, date_key: str, day_label: str):
    """Render an export-to-Excel button that downloads all current soldier data."""
    import io
    import pandas as pd

    st.markdown("---")
    st.markdown("### 📥 ייצוא לאקסל")

    PRESENT_SET_LOCAL = {"בבסיס", "התייצב", "חוזר מחופש"}

    def _get_status(s: dict) -> str:
        return status_map.get(f"{s['soldier_id']}_{date_key}", "")

    # Also fetch irrelevant soldiers so they appear at the bottom
    from military_manager.services.soldier_service import get_period_soldiers as _get_ps
    all_period = _get_ps(pid, exclude_irrelevant_unit=False)
    irrelevant = [s for s in all_period if s.get("sub_unit") == IRRELEVANT_UNIT]

    # Build rows with all relevant fields
    rows = []
    for s in soldiers:
        status = _get_status(s)
        is_present = status in PRESENT_SET_LOCAL
        cat = _classify_soldier(s.get("role") or "", s.get("task_role") or "")
        rows.append({
            "שם מלא": s.get("full_name", ""),
            "מספר אישי": s.get("military_id", ""),
            "דרגה": s.get("rank") or "",
            "מחלקה": s.get("sub_unit") or "",
            "תפקיד ארגוני": s.get("role") or "",
            "תפקיד מבצעי": s.get("task_role") or "",
            "קטגוריה מקצועית": cat or "ללא סיווג",
            "טלפון": s.get("phone") or "",
            "עיר": s.get("city") or "",
            f"סטטוס ({day_label})": status or "ללא סטטוס",
            f"נוכח ({day_label})": "כן" if is_present else "לא",
            "הערות": s.get("notes") or "",
            "הערות שיבוץ": s.get("assignment_notes") or "",
            "חייל מסופח": "כן" if s.get("is_attached") else "",
        })

    # Add irrelevant soldiers at the bottom
    for s in irrelevant:
        status = _get_status(s)
        rows.append({
            "שם מלא": s.get("full_name", ""),
            "מספר אישי": s.get("military_id", ""),
            "דרגה": s.get("rank") or "",
            "מחלקה": s.get("sub_unit") or "",
            "תפקיד ארגוני": s.get("role") or "",
            "תפקיד מבצעי": s.get("task_role") or "",
            "קטגוריה מקצועית": "לא רלוונטי",
            "טלפון": s.get("phone") or "",
            "עיר": s.get("city") or "",
            f"סטטוס ({day_label})": status or "לא רלוונטי",
            f"נוכח ({day_label})": "לא רלוונטי",
            "הערות": s.get("notes") or "",
            "הערות שיבוץ": s.get("assignment_notes") or "",
            "חייל מסופח": "כן" if s.get("is_attached") else "",
        })

    df = pd.DataFrame(rows)

    # Write to Excel in memory with openpyxl
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="חיילים")

        # Auto-adjust column widths
        ws = writer.sheets["חיילים"]
        for col_idx, col_name in enumerate(df.columns, 1):
            max_len = max(
                len(str(col_name)),
                df[col_name].astype(str).str.len().max() if len(df) > 0 else 0,
            )
            ws.column_dimensions[chr(64 + col_idx) if col_idx <= 26
                                  else f"{chr(64 + (col_idx - 1) // 26)}{chr(65 + (col_idx - 1) % 26)}"
                                  ].width = min(max_len + 3, 40)

        # Set RTL for the sheet
        ws.sheet_view.rightToLeft = True

    buf.seek(0)

    from datetime import date as _date
    filename = f"soldiers_export_{_date.today().isoformat()}.xlsx"

    st.download_button(
        label="📥 הורד קובץ אקסל — כל החיילים",
        data=buf.getvalue(),
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.xml",
        use_container_width=True,
    )
    st.caption(f"📋 {len(rows)} חיילים ({len(soldiers)} רלוונטיים + {len(irrelevant)} לא רלוונטיים) | נתונים עדכניים מהמערכת + סטטוס {day_label}")


def _render_group_stats(pid: int, target_date: date, day_label: str = "היום"):
    """Render dynamic status group stats with percentages and red alerts."""
    init_default_groups(pid)
    day_stats = compute_percentages(pid, target_date)

    if not day_stats or not day_stats["groups"]:
        return

    threshold = float(get_setting(pid, "home_alert_percent", "25"))
    groups = day_stats["groups"]

    st.markdown(f"### 📊 סיכום קבוצות — {day_label}")
    st.caption(f"סה\"כ בשמ\"פ: {day_stats['total_in_shmap']} | סה\"כ רלוונטיים: {day_stats['total_relevant']}")

    grp_cols = st.columns(min(len(groups), 5))
    for i, (grp_name, grp_data) in enumerate(groups.items()):
        pct = grp_data["percent"]
        cnt = grp_data["count"]
        color = grp_data.get("color", "#9E9E9E")
        is_alert = grp_name == "בחופש" and pct > threshold

        with grp_cols[i % len(grp_cols)]:
            if is_alert:
                st.markdown(
                    f'<div style="background:#FFCDD2;padding:12px;border-radius:10px;text-align:center;'
                    f'border:2px solid #B71C1C;">'
                    f'<b style="color:#B71C1C;font-size:1.5em;">{pct}%</b><br>'
                    f'<span style="color:#B71C1C;font-size:1.1em;font-weight:bold;">{grp_name}: {cnt}</span><br>'
                    f'<span style="color:#B71C1C;font-size:0.8em;">⚠️ מעל הסף ({threshold}%)</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f'<div style="background:{color}22;padding:12px;border-radius:10px;text-align:center;'
                    f'border:1px solid {color};">'
                    f'<b style="color:{color};font-size:1.5em;">{pct}%</b><br>'
                    f'<span style="font-size:1.1em;">{grp_name}: {cnt}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    # Show alerts
    for alert in day_stats.get("alerts", []):
        st.error(alert["message"])


def _render_status_breakdown(pid: int, target_date: date, soldiers: list[dict], day_label: str = "היום"):
    """Show per-status soldier lists for non-present statuses."""
    from military_manager.services.status_service import get_daily_status_grid

    if not soldiers:
        return

    today = target_date
    grid = get_daily_status_grid(pid, today, today)
    status_map = grid.get("statuses", {}) if grid else {}
    notes_map = grid.get("notes", {}) if grid else {}

    # Build ID → soldier info
    id_to_info: dict[int, dict] = {}
    for s in soldiers:
        id_to_info[s["soldier_id"]] = s

    # Group by status
    from collections import defaultdict
    status_groups: dict[str, list[dict]] = defaultdict(list)
    no_status: list[dict] = []

    for s in soldiers:
        sid = s["soldier_id"]
        key = f"{sid}_{today.isoformat()}"
        status = status_map.get(key, "")
        note = notes_map.get(key, "")
        info = {
            "name": f"{s.get('first_name', '')} {s.get('last_name', '')}".strip(),
            "sub_unit": s.get("sub_unit", ""),
            "role": s.get("role", ""),
            "note": note,
        }
        if status:
            status_groups[status].append(info)
        else:
            no_status.append(info)

    # Only show non-present statuses (present soldiers are the majority, not interesting)
    PRESENT_SET = {"בבסיס", "התייצב", "חוזר מחופש"}
    RETURNING_SET = {"חוזר מחופש", "צפוי להתייצב", "סיפוח מאוחר"}
    non_present = {k: v for k, v in status_groups.items() if k not in PRESENT_SET}
    returning = {k: v for k, v in status_groups.items() if k in RETURNING_SET}

    if not non_present and not no_status:
        return

    st.markdown("---")
    st.markdown(f"### 📋 פירוט סטטוסים — {day_label}")

    # ── Returning soldiers (en route) ──
    if returning:
        total_returning = sum(len(v) for v in returning.values())
        with st.expander(f"🚌 חוזרים (בדרך) — {total_returning} חיילים"):
            import pandas as _pd_ret
            rows_ret = []
            for status_name, info_list in returning.items():
                for info in sorted(info_list, key=lambda x: x["name"]):
                    row = {"שם": info["name"], "סטטוס": status_name, "מחלקה": info["sub_unit"]}
                    if info.get("note"):
                        row["הערה"] = info["note"]
                    rows_ret.append(row)
            if rows_ret:
                df_ret = _pd_ret.DataFrame(rows_ret)
                st.dataframe(df_ret, use_container_width=True, hide_index=True)

    # ── Non-present statuses ──

    # Status colors for badges
    STATUS_BADGE_COLORS = {
        "גימלים": "#FF5722",
        "חופש": "#03A9F4",
        "יוצא לחופש": "#2196F3",
        "פיצול": "#AB47BC",
        "יוצא לפיצול": "#9C27B0",
        "משתחרר": "#607D8B",
        "נפקד": "#F44336",
        "לא בשמפ": "#9E9E9E",
        "יוצא לקורס": "#795548",
        "צפוי להתייצב": "#FFC107",
        "סיפוח מאוחר": "#FF9800",
    }

    # Sort: important statuses first
    priority = ["גימלים", "חופש", "יוצא לחופש", "נפקד", "פיצול",
                "יוצא לפיצול", "משתחרר", "יוצא לקורס", "לא בשמפ"]
    sorted_statuses = sorted(
        non_present.keys(),
        key=lambda s: priority.index(s) if s in priority else 999,
    )

    for status in sorted_statuses:
        soldiers_list = non_present[status]
        color = STATUS_BADGE_COLORS.get(status, "#555")
        badge = (
            f'<span style="background:{color}; color:white; padding:2px 10px; '
            f'border-radius:8px; font-size:13px; font-weight:bold;">'
            f'{status}</span>'
        )
        with st.expander(f"{status} — {len(soldiers_list)} חיילים"):
            st.markdown(badge, unsafe_allow_html=True)
            import pandas as _pd
            rows = []
            for info in sorted(soldiers_list, key=lambda x: x["name"]):
                row = {"שם": info["name"], "מחלקה": info["sub_unit"]}
                if info.get("note"):
                    row["הערה"] = info["note"]
                rows.append(row)
            df = _pd.DataFrame(rows)
            cols_config = {
                "שם": st.column_config.TextColumn("שם", width="medium"),
                "מחלקה": st.column_config.TextColumn("מחלקה", width="small"),
            }
            if "הערה" in df.columns:
                cols_config["הערה"] = st.column_config.TextColumn("הערה", width="medium")
            st.dataframe(df, use_container_width=True, hide_index=True,
                         column_config=cols_config)


def _render_home_notes_summary(pid: int, period: dict, target_date: date | None = None, day_label: str = "היום"):
    """Show notes summary on home page for selected date."""
    from collections import defaultdict

    today = target_date or date.today()

    soldiers = get_period_soldiers(pid, exclude_irrelevant_unit=True)
    if not soldiers:
        return

    grid = get_daily_status_grid(pid, today, today)
    notes_map = grid.get("notes", {}) if grid else {}

    if not notes_map:
        return

    # Build soldier ID → name lookup
    id_to_name: dict[int, str] = {}
    for s in soldiers:
        sid = s["soldier_id"]
        id_to_name[sid] = f"{s.get('first_name', '')} {s.get('last_name', '')}".strip()

    soldier_ids = set(id_to_name.keys())

    # Group: note_text → list of soldier names
    note_groups: dict[str, list[str]] = defaultdict(list)

    for key, note_text in notes_map.items():
        if not note_text or not note_text.strip():
            continue
        parts = key.split("_", 1)
        if len(parts) != 2:
            continue
        try:
            sid = int(parts[0])
        except ValueError:
            continue
        if sid not in soldier_ids:
            continue
        normalized = note_text.strip()
        name = id_to_name.get(sid, str(sid))
        note_groups[normalized].append(name)

    if not note_groups:
        return

    st.markdown("---")
    st.markdown(f"### 📝 סיכום הערות — {day_label} ({today.strftime('%d/%m')})")

    sorted_notes = sorted(
        note_groups.items(),
        key=lambda x: len(x[1]),
        reverse=True,
    )

    for note_text, names_list in sorted_notes:
        count = len(names_list)
        with st.expander(f"📌 {note_text} — {count} חיילים"):
            import pandas as _pd
            rows = [{"שם": name} for name in sorted(names_list)]
            df = _pd.DataFrame(rows)
            st.dataframe(
                df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "שם": st.column_config.TextColumn("שם", width="large"),
                },
            )


# ── WhatsApp Daily Briefing ──────────────────────────────────

def _render_whatsapp_briefing(pid: int, selected_date, period: dict, day_label: str):
    """Render a button that generates a WhatsApp-ready daily briefing text."""
    with st.expander(f"📱 תדריך בוקר ל-WhatsApp — {day_label}", expanded=False):
        if st.button("📋 צור תדריך", key="gen_briefing", type="primary", use_container_width=True):
            period_name = period.get("name", "")
            location = period.get("location", "")
            full_name = f"{period_name} — {location}" if location else period_name
            text = generate_briefing(pid, selected_date, full_name)
            st.session_state["_briefing_text"] = text

        if st.session_state.get("_briefing_text"):
            text = st.session_state["_briefing_text"]
            # Show the text in a text area for easy copy
            st.text_area(
                "📋 העתק את הטקסט והדבק ב-WhatsApp:",
                value=text,
                height=400,
                key="briefing_output",
            )
            # Copy-friendly tip
            st.caption("💡 לחץ בתוך התיבה → Ctrl+A (בחר הכל) → Ctrl+C (העתק) → הדבק ב-WhatsApp")

            # WhatsApp direct link (URL-encoded text)
            import urllib.parse
            wa_text = urllib.parse.quote(text)
            wa_url = f"https://api.whatsapp.com/send?text={wa_text}"
            st.markdown(
                f'<a href="{wa_url}" target="_blank" style="'
                f'display:inline-block; background:#25D366; color:white; '
                f'padding:10px 24px; border-radius:8px; text-decoration:none; '
                f'font-weight:bold; font-size:1em; margin-top:4px;">'
                f'📲 שלח ישירות ב-WhatsApp</a>',
                unsafe_allow_html=True,
            )


# ── Google Sheets Quick Sync ─────────────────────────────────

def _render_gsheet_sync(pid: int, period: dict):
    """Render a quick-sync section for Google Sheets."""
    from military_manager.services.report1_import import (
        quick_sync_from_gsheet, get_service_account_email, extract_sheet_id,
    )

    sa_email = get_service_account_email()
    if not sa_email:
        return  # No service account configured — skip

    saved_url = get_setting(pid, "gsheet_url", "")

    with st.expander("🔄 סנכרון מ-Google Sheets", expanded=False):
        url = st.text_input(
            "קישור Google Sheets",
            value=saved_url,
            placeholder="https://docs.google.com/spreadsheets/d/...",
            key="home_gsheet_url",
        )

        if url and url != saved_url and extract_sheet_id(url):
            set_setting(pid, "gsheet_url", url)

        if not url or not extract_sheet_id(url):
            st.caption(
                f"📌 הדבק קישור Google Sheets ששותף עם:\n`{sa_email}`"
            )
            return

        p_start = date.fromisoformat(period["start_date"])
        p_end = date.fromisoformat(period["end_date"])

        if st.button("🔄 סנכרן עכשיו", type="primary", key="home_quick_sync",
                      disabled=st.session_state.get("_company_readonly", False)):
            with st.spinner("מסנכרן מ-Google Sheets..."):
                result = quick_sync_from_gsheet(
                    url, pid,
                    sheet_name="סיכום פלוגתי",
                    date_range=(p_start, p_end),
                )

            if result["success"]:
                st.success(
                    f"✅ סנכרון הושלם!\n\n"
                    f"- **{result['imported']}** סטטוסים עודכנו\n"
                    f"- **{result['matched']}** חיילים מותאמים\n"
                    f"- **{result['dates_found']}** ימים בגיליון"
                )
                if result["excel_only"] > 0:
                    st.caption(f"📄 {result['excel_only']} חיילים באקסל לא נמצאו במערכת")
                st.rerun()
            else:
                for err in result["errors"]:
                    st.error(err)
