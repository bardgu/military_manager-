"""Soldiers management page — CRUD, assignment to period, certifications."""

from __future__ import annotations

import pandas as pd
import streamlit as st
from streamlit_sortables import sort_items

from military_manager.components.navigation import render_page_header
from military_manager.components.filters import period_guard, sub_unit_filter
from military_manager.config import IRRELEVANT_UNIT
from military_manager.services.soldier_service import (
    create_soldier,
    get_period_soldiers,
    get_sub_units,
    assign_to_period,
    update_soldier,
    update_period_soldier,
    remove_from_period,
    get_soldier_certifications,
    add_soldier_certification,
    reorder_soldiers,
)


def render():
    render_page_header("👥 חיילים", "ניהול חיילים בתקופת המילואים הנוכחית")

    period = period_guard()
    if not period:
        return

    pid = period["id"]

    tab_list, tab_manage, tab_add, tab_import = st.tabs([
        "📋 רשימת חיילים", "✏️ ניהול כוח אדם", "➕ הוסף חייל", "📥 ייבוא מאקסל"
    ])

    # ── Soldiers list ──
    with tab_list:
        _render_soldiers_list(pid)

    # ── Manage (move / deactivate) ──
    with tab_manage:
        _render_manage_soldiers(pid)

    # ── Add soldier ──
    with tab_add:
        _render_add_soldier(pid)

    # ── Import ──
    with tab_import:
        _render_import(pid)


def _render_soldiers_list(pid: int):
    """Render the soldiers table with filters."""
    # Filters
    col1, col2, col3 = st.columns([2, 2, 3])
    with col1:
        unit_filter = sub_unit_filter(pid, key="soldiers_unit_filter")
    with col2:
        search = st.text_input("🔍 חיפוש", placeholder="שם / מספר אישי", key="soldier_search")
    with col3:
        st.write("")  # spacer

    soldiers = get_period_soldiers(pid)

    # Apply filters
    if unit_filter:
        soldiers = [s for s in soldiers if s.get("sub_unit") == unit_filter]
    if search:
        search_lower = search.lower()
        soldiers = [
            s for s in soldiers
            if search_lower in s.get("full_name", "").lower()
            or search_lower in str(s.get("military_id", ""))
        ]

    if not soldiers:
        st.info("לא נמצאו חיילים" + (" עם הסינון הנוכחי" if unit_filter or search else ""))
        return

    # ── Summary by sub-unit ──
    units = {}
    for s in soldiers:
        u = s.get("sub_unit", "לא משובץ")
        units[u] = units.get(u, 0) + 1

    cols = st.columns(min(len(units) + 1, 6))
    cols[0].metric("סה\"כ", len(soldiers))
    for i, (u, count) in enumerate(sorted(units.items()), 1):
        if i < len(cols):
            cols[i].metric(u, count)

    # ── Role breakdown ──
    st.markdown("#### 📊 פילוח לפי תפקידים")

    # Build role → soldier names mapping
    role_soldiers: dict[str, list[str]] = {}
    for s in soldiers:
        r = s.get("role") or "לא מוגדר"
        role_soldiers.setdefault(r, []).append(s.get("full_name", "?"))

    # Build task_role → soldier names mapping
    task_role_soldiers: dict[str, list[str]] = {}
    for s in soldiers:
        tr = s.get("task_role") or None
        if tr:
            task_role_soldiers.setdefault(tr, []).append(s.get("full_name", "?"))

    col_roles, col_task_roles = st.columns(2)

    with col_roles:
        st.markdown("**תפקידים ארגוניים:**")
        sorted_roles = sorted(role_soldiers.items(), key=lambda x: -len(x[1]))
        # Summary line
        summary = " · ".join(f"{r} ({len(n)})" for r, n in sorted_roles)
        st.caption(summary)
        # Selector to drill into a specific role
        role_options = ["— בחר תפקיד לצפייה ברשימה —"] + [
            f"{r} ({len(n)})" for r, n in sorted_roles
        ]
        selected_role = st.selectbox("בחר תפקיד:", role_options, key="role_drill")
        if selected_role != role_options[0]:
            role_name = selected_role.rsplit(" (", 1)[0]
            names = role_soldiers.get(role_name, [])
            st.info(f"**{role_name}** — {len(names)} חיילים:")
            for j, name in enumerate(sorted(names), 1):
                st.write(f"{j}. {name}")

    with col_task_roles:
        if task_role_soldiers:
            st.markdown("**תפקידים משימתיים:**")
            sorted_task_roles = sorted(task_role_soldiers.items(), key=lambda x: -len(x[1]))
            summary_tr = " · ".join(f"{tr} ({len(n)})" for tr, n in sorted_task_roles)
            st.caption(summary_tr)
            tr_options = ["— בחר תפקיד לצפייה ברשימה —"] + [
                f"{tr} ({len(n)})" for tr, n in sorted_task_roles
            ]
            selected_tr = st.selectbox("בחר תפקיד:", tr_options, key="task_role_drill")
            if selected_tr != tr_options[0]:
                tr_name = selected_tr.rsplit(" (", 1)[0]
                names = task_role_soldiers.get(tr_name, [])
                st.info(f"**{tr_name}** — {len(names)} חיילים:")
                for j, name in enumerate(sorted(names), 1):
                    st.write(f"{j}. {name}")
        else:
            st.markdown("**תפקידים משימתיים:** טרם הוגדרו")

    # ── Certifications breakdown ──
    from military_manager.services.soldier_service import get_soldier_certifications
    cert_soldiers: dict[str, list[str]] = {}
    for s in soldiers:
        certs = get_soldier_certifications(s["soldier_id"])
        for c in certs:
            cn = c.get("name", "")
            if cn:
                cert_soldiers.setdefault(cn, []).append(s.get("full_name", "?"))

    if cert_soldiers:
        st.markdown("**הכשרות:**")
        sorted_certs = sorted(cert_soldiers.items(), key=lambda x: -len(x[1]))
        summary_cert = " · ".join(f"{cn} ({len(n)})" for cn, n in sorted_certs)
        st.caption(summary_cert)
        cert_options = ["— בחר הכשרה לצפייה ברשימה —"] + [
            f"{cn} ({len(n)})" for cn, n in sorted_certs
        ]
        selected_cert = st.selectbox("בחר הכשרה:", cert_options, key="cert_drill")
        if selected_cert != cert_options[0]:
            cert_name = selected_cert.rsplit(" (", 1)[0]
            names = cert_soldiers.get(cert_name, [])
            st.info(f"**{cert_name}** — {len(names)} חיילים:")
            for j, name in enumerate(sorted(names), 1):
                st.write(f"{j}. {name}")

    st.markdown("---")

    # DataFrame display
    df = pd.DataFrame(soldiers)
    # Add מסופח indicator column
    if "is_attached" in df.columns:
        df["attached_label"] = df["is_attached"].apply(lambda x: "📎 מסופח" if x else "")
    else:
        df["attached_label"] = ""
    display_cols = {
        "military_id": "מס' אישי",
        "full_name": "שם מלא",
        "rank": "דרגה",
        "sub_unit": "מחלקה",
        "role": "תפקיד",
        "task_role": "תפקיד משימתי",
        "attached_label": "סוג",
        "assignment_notes": "הערות שיבוץ",
        "phone": "טלפון",
        "city": "ישוב",
    }
    available_cols = [c for c in display_cols if c in df.columns]
    if available_cols:
        display_df = df[available_cols].rename(columns=display_cols)
        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            height=min(35 * len(display_df) + 38, 600),
            column_config={
                "שם מלא": st.column_config.TextColumn("שם מלא", pinned=True),
                "מחלקה": st.column_config.TextColumn("מחלקה", pinned=True),
            },
        )

    # Detail expander for each soldier
    st.markdown("### פרטי חייל")
    selected_name = st.selectbox(
        "בחר חייל לעריכה",
        ["—"] + [s.get("full_name", "") for s in soldiers],
        key="soldier_detail_select",
    )
    if selected_name != "—":
        soldier = next((s for s in soldiers if s.get("full_name") == selected_name), None)
        if soldier:
            _render_soldier_detail(pid, soldier)


def _render_manage_soldiers(pid: int):
    """Render visual soldier management — drag-and-drop + checkbox modes."""
    st.markdown("### ✏️ ניהול כוח אדם")

    soldiers = get_period_soldiers(pid)
    if not soldiers:
        st.info("אין חיילים בתקופה זו.")
        return

    existing_units = get_sub_units(pid)
    all_units = list(dict.fromkeys(
        existing_units + ["מפקדת הפלוגה", "מחלקה 1", "מחלקה 2", "מחלקה 3", "מפלג", "עוזיה", "מסופחים"]
    ))
    # Ensure "לא רלוונטי" unit always appears last
    if IRRELEVANT_UNIT in all_units:
        all_units.remove(IRRELEVANT_UNIT)
    all_units.append(IRRELEVANT_UNIT)

    mode_dnd, mode_reorder, mode_checkbox = st.tabs(["🖐️ גרור ושחרר", "🔢 שינוי סדר", "☑️ בחירה מרובה"])

    with mode_dnd:
        _render_drag_and_drop(pid, soldiers, all_units)

    with mode_reorder:
        _render_reorder_soldiers(pid, soldiers)

    with mode_checkbox:
        _render_checkbox_manage(pid, soldiers, all_units)


def _render_drag_and_drop(pid: int, soldiers: list[dict], all_units: list[str]):
    """Drag-and-drop interface to move soldiers between sub-units."""
    # Force dark text inside the sortable component iframe
    st.markdown("""
    <style>
    iframe[title*="streamlit_sortables"] {
        color-scheme: light !important;
    }
    [data-testid="stCustomComponentV1"] iframe {
        color-scheme: light !important;
    }
    </style>
    """, unsafe_allow_html=True)
    st.caption("גרור חיילים בין המחלקות ולחץ 'שמור שינויים' לעדכון")
    st.info(
        "🚫 **מחלקת 'לא רלוונטי'** — חיילים שמשובצים במחלקה זו לא ייספרו בדוחות, "
        "באחוזי נוכחות ובכל החישובים. ניתן להחזיר אותם למחלקה רגילה בכל עת."
    )

    # Build a fingerprint of the soldier set so the drag-and-drop component
    # resets when soldiers are added/removed/changed.
    _soldier_ids = sorted(s["period_soldier_id"] for s in soldiers)
    _fingerprint = hash(tuple(_soldier_ids))
    dnd_key = f"dnd_soldiers_{_fingerprint}"

    # If the soldier set changed, clear the old cached widget state
    if st.session_state.get("_dnd_soldiers_fp") != _fingerprint:
        old_key = st.session_state.get("_dnd_soldiers_key")
        if old_key and old_key in st.session_state:
            del st.session_state[old_key]
        st.session_state["_dnd_soldiers_fp"] = _fingerprint
        st.session_state["_dnd_soldiers_key"] = dnd_key

    # Group soldiers by sub-unit
    by_unit: dict[str, list[dict]] = {}
    for s in soldiers:
        u = s.get("sub_unit", "לא משובץ")
        by_unit.setdefault(u, [])
        by_unit[u].append(s)

    # Build unique labels: "שם · תפקיד" — use psid as hidden suffix for uniqueness
    label_to_psid: dict[str, int] = {}
    psid_to_label: dict[int, str] = {}
    for s in soldiers:
        psid = s["period_soldier_id"]
        name = s.get("full_name", "?")
        role = s.get("role") or ""
        label = f"{name} · {role}" if role else name
        # Ensure uniqueness
        if label in label_to_psid:
            label = f"{label} #{psid}"
        label_to_psid[label] = psid
        psid_to_label[psid] = label

    # Build sort_items data structure — irrelevant unit always last
    containers = []
    irr_container = None
    for unit_name in sorted(by_unit.keys()):
        unit_soldiers = by_unit[unit_name]
        items = [psid_to_label[s["period_soldier_id"]] for s in unit_soldiers]
        entry = {
            "header": f"🚫 {unit_name} ({len(items)})" if unit_name == IRRELEVANT_UNIT
                     else f"{unit_name} ({len(items)})",
            "items": items,
        }
        if unit_name == IRRELEVANT_UNIT:
            irr_container = entry
        else:
            containers.append(entry)
    # Always show IRRELEVANT_UNIT container (even if empty)
    if irr_container is None:
        irr_container = {"header": f"🚫 {IRRELEVANT_UNIT} (0)", "items": []}
    containers.append(irr_container)

    # Custom CSS for RTL and nicer appearance
    custom_css = """
    .sortable-component { direction: rtl; color: #333 !important; }
    .sortable-component * { color: inherit !important; }
    .sortable-container {
        background: #f8f9fa;
        border: 2px solid #1B5E20;
        border-radius: 12px;
        padding: 8px;
        min-height: 100px;
        min-width: 200px;
        color: #333 !important;
    }
    .sortable-container-header {
        background: #1B5E20;
        color: white !important;
        padding: 8px 12px;
        border-radius: 8px;
        text-align: center;
        font-weight: bold;
        margin-bottom: 8px;
    }
    .sortable-item {
        background: white;
        color: #333 !important;
        border: 1px solid #ddd;
        border-radius: 6px;
        padding: 6px 10px;
        margin: 4px 0;
        cursor: grab;
        font-size: 14px;
        transition: background 0.2s;
        text-align: right;
    }
    .sortable-item * {
        color: #333 !important;
    }
    .sortable-item:hover {
        background: #E8F5E9;
        border-color: #1B5E20;
    }
    .sortable-item:active {
        cursor: grabbing;
        background: #C8E6C9;
    }
    """

    result = sort_items(
        containers,
        multi_containers=True,
        direction="horizontal",
        custom_style=custom_css,
        key=dnd_key,
    )

    # Detect changes (unit transfers + reordering)
    if result:
        # Build original mapping: psid → (original_unit, original_order)
        original_unit: dict[int, str] = {}
        original_order: dict[str, list[int]] = {}  # unit → [psid list in original order]
        for s in soldiers:
            psid = s["period_soldier_id"]
            u = s.get("sub_unit", "לא משובץ")
            original_unit[psid] = u
            original_order.setdefault(u, []).append(psid)

        unit_changes: list[tuple[int, str]] = []  # (psid, new_unit)
        new_full_order: list[int] = []  # all psids in new display order
        order_changed = False

        for container in result:
            header = container.get("header", "") if isinstance(container, dict) else container
            items = container.get("items", []) if isinstance(container, dict) else []
            unit_name = header.rsplit(" (", 1)[0].strip() if " (" in header else header
            # Strip any emoji prefix (e.g. "🚫 לא רלוונטי" → "לא רלוונטי")
            if unit_name.startswith("🚫 "):
                unit_name = unit_name[2:].strip()

            unit_psids_new = []
            for label in items:
                psid = label_to_psid.get(label)
                if psid:
                    unit_psids_new.append(psid)
                    new_full_order.append(psid)
                    if original_unit.get(psid) != unit_name:
                        unit_changes.append((psid, unit_name))

            # Check if order within this unit changed
            orig = original_order.get(unit_name, [])
            if unit_psids_new != orig:
                order_changed = True

        has_changes = bool(unit_changes) or order_changed

        if has_changes:
            change_parts = []
            if unit_changes:
                change_parts.append(f"{len(unit_changes)} העברות בין מחלקות")
            if order_changed:
                change_parts.append("שינוי סדר חיילים")
            change_text = " + ".join(change_parts)

            st.markdown(
                f"<div style='background:#FFF3E0; border:2px solid #E65100; "
                f"border-radius:8px; padding:12px; margin:8px 0; text-align:center;'>"
                f"<b>🔄 {change_text} — ממתין לשמירה</b></div>",
                unsafe_allow_html=True,
            )

            # Show unit transfer details
            if unit_changes:
                psid_to_soldier = {s["period_soldier_id"]: s for s in soldiers}
                for psid, new_unit in unit_changes:
                    s_info = psid_to_soldier.get(psid)
                    if s_info:
                        old_unit = original_unit.get(psid, "?")
                        st.write(f"**{s_info['full_name']}**: {old_unit} → {new_unit}")

            if order_changed and not unit_changes:
                st.caption("סדר החיילים בתוך המחלקות השתנה. הסדר החדש ישתקף בכל העמודים בכלי.")

            if st.button("💾 שמור שינויים", type="primary", key="save_dnd"):
                # Save unit transfers
                move_count = 0
                for psid, new_unit in unit_changes:
                    update_period_soldier(psid, sub_unit=new_unit)
                    move_count += 1
                # Save new order (applies to all soldiers globally)
                if new_full_order:
                    reorder_soldiers(new_full_order)
                parts = []
                if move_count:
                    parts.append(f"{move_count} חיילים הועברו")
                if order_changed:
                    parts.append("הסדר עודכן")
                st.success(f"✅ {' | '.join(parts)}")
                st.rerun()


def _render_reorder_soldiers(pid: int, soldiers: list[dict]):
    """Reorder soldiers within each sub-unit via drag-and-drop.

    Each sub-unit is shown as a separate sortable list. Dragging within
    a unit changes order; dragging between units also moves the soldier.
    The order set here is reflected everywhere in the app (shifts, reports, etc.)
    because all queries sort by PeriodSoldier.sort_order.
    """
    st.caption("גרור חיילים לשינוי סדר בתוך כל מחלקה. לחץ 'שמור סדר' לעדכון. הסדר ישתקף בכל העמודים בכלי.")

    # Group soldiers by sub-unit, preserving current sort_order
    by_unit: dict[str, list[dict]] = {}
    for s in soldiers:
        u = s.get("sub_unit", "לא משובץ")
        by_unit.setdefault(u, [])
        by_unit[u].append(s)

    # Build unique labels
    label_to_psid: dict[str, int] = {}
    psid_to_label: dict[int, str] = {}
    for s in soldiers:
        psid = s["period_soldier_id"]
        name = s.get("full_name", "?")
        role = s.get("role") or ""
        label = f"{name} · {role}" if role else name
        if label in label_to_psid:
            label = f"{label} #{psid}"
        label_to_psid[label] = psid
        psid_to_label[psid] = label

    # Build containers — one per sub-unit (vertical layout for easy reordering)
    containers = []
    for unit_name in sorted(by_unit.keys()):
        unit_soldiers = by_unit[unit_name]
        items = [psid_to_label[s["period_soldier_id"]] for s in unit_soldiers]
        containers.append({
            "header": f"{unit_name} ({len(items)})",
            "items": items,
        })

    # Fingerprint for dynamic key
    _ids = sorted(s["period_soldier_id"] for s in soldiers)
    _fp = hash(tuple(_ids))
    reorder_key = f"reorder_dnd_{_fp}"

    if st.session_state.get("_reorder_fp") != _fp:
        old_key = st.session_state.get("_reorder_key")
        if old_key and old_key in st.session_state:
            del st.session_state[old_key]
        st.session_state["_reorder_fp"] = _fp
        st.session_state["_reorder_key"] = reorder_key

    custom_css = """
    .sortable-component { direction: rtl; color: #333 !important; }
    .sortable-component * { color: inherit !important; }
    .sortable-container {
        background: #f8f9fa;
        border: 2px solid #1565C0;
        border-radius: 12px;
        padding: 8px;
        min-height: 60px;
        color: #333 !important;
    }
    .sortable-container-header {
        background: #1565C0;
        color: white !important;
        padding: 8px 12px;
        border-radius: 8px;
        text-align: center;
        font-weight: bold;
        margin-bottom: 8px;
    }
    .sortable-item {
        background: white;
        color: #333 !important;
        border: 1px solid #ddd;
        border-radius: 6px;
        padding: 8px 12px;
        margin: 4px 0;
        cursor: grab;
        font-size: 14px;
        transition: background 0.2s;
        text-align: right;
    }
    .sortable-item * { color: #333 !important; }
    .sortable-item:hover { background: #E3F2FD; border-color: #1565C0; }
    .sortable-item:active { cursor: grabbing; background: #BBDEFB; }
    """

    result = sort_items(
        containers,
        multi_containers=True,
        direction="vertical",
        custom_style=custom_css,
        key=reorder_key,
    )

    # Detect changes
    if result:
        original_unit: dict[int, str] = {}
        original_order: dict[str, list[int]] = {}
        for s in soldiers:
            psid = s["period_soldier_id"]
            u = s.get("sub_unit", "לא משובץ")
            original_unit[psid] = u
            original_order.setdefault(u, []).append(psid)

        unit_changes: list[tuple[int, str]] = []
        new_full_order: list[int] = []
        order_changed = False

        for container in result:
            header = container.get("header", "") if isinstance(container, dict) else container
            items = container.get("items", []) if isinstance(container, dict) else []
            unit_name = header.rsplit(" (", 1)[0].strip() if " (" in header else header
            # Strip any emoji prefix (e.g. "🚫 לא רלוונטי" → "לא רלוונטי")
            if unit_name.startswith("🚫 "):
                unit_name = unit_name[2:].strip()

            unit_psids_new = []
            for label in items:
                psid = label_to_psid.get(label)
                if psid:
                    unit_psids_new.append(psid)
                    new_full_order.append(psid)
                    if original_unit.get(psid) != unit_name:
                        unit_changes.append((psid, unit_name))

            orig = original_order.get(unit_name, [])
            if unit_psids_new != orig:
                order_changed = True

        has_changes = bool(unit_changes) or order_changed

        if has_changes:
            change_parts = []
            if unit_changes:
                change_parts.append(f"{len(unit_changes)} העברות בין מחלקות")
            if order_changed:
                change_parts.append("שינוי סדר חיילים")
            change_text = " + ".join(change_parts)

            st.markdown(
                f"<div style='background:#FFF3E0; border:2px solid #E65100; "
                f"border-radius:8px; padding:12px; margin:8px 0; text-align:center;'>"
                f"<b>🔄 {change_text} — ממתין לשמירה</b></div>",
                unsafe_allow_html=True,
            )

            if unit_changes:
                psid_to_soldier = {s["period_soldier_id"]: s for s in soldiers}
                for psid, new_unit in unit_changes:
                    s_info = psid_to_soldier.get(psid)
                    if s_info:
                        old = original_unit.get(psid, "?")
                        st.write(f"**{s_info['full_name']}**: {old} → {new_unit}")

            if st.button("💾 שמור סדר", type="primary", key="save_reorder"):
                move_count = 0
                for psid, new_unit in unit_changes:
                    update_period_soldier(psid, sub_unit=new_unit)
                    move_count += 1
                if new_full_order:
                    reorder_soldiers(new_full_order)
                parts = []
                if move_count:
                    parts.append(f"{move_count} חיילים הועברו")
                if order_changed:
                    parts.append("הסדר עודכן")
                st.success(f"✅ {' | '.join(parts)}")
                st.rerun()


def _render_checkbox_manage(pid: int, soldiers: list[dict], all_units: list[str]):
    """Checkbox-based soldier management — select + move/role/remove."""

    # Group soldiers by sub-unit
    by_unit: dict[str, list[dict]] = {}
    for s in soldiers:
        u = s.get("sub_unit", "לא משובץ")
        by_unit.setdefault(u, [])
        by_unit[u].append(s)

    # Build lookup
    psid_to_soldier = {s["period_soldier_id"]: s for s in soldiers}

    # ── Read ACTUAL checkbox states from session_state widget keys ──
    # This reads the real checkbox values BEFORE the widgets render,
    # so the action panel reflects current selections immediately.
    selected_psids: set[int] = set()
    for s in soldiers:
        psid = s["period_soldier_id"]
        if st.session_state.get(f"chk_{psid}", False):
            selected_psids.add(psid)

    selected_soldiers_info = [psid_to_soldier[p] for p in selected_psids if p in psid_to_soldier]

    # ── ACTION PANEL — only shows when soldiers are selected ──
    if selected_soldiers_info:
        st.markdown(
            f"<div style='background:#E8F5E9; border:2px solid #1B5E20; border-radius:12px; "
            f"padding:16px; margin:8px 0 16px 0;'>"
            f"<h4 style='color:#1B5E20; margin:0 0 8px 0; text-align:center;'>"
            f"🎯 נבחרו {len(selected_soldiers_info)} חיילים — מה לעשות?</h4></div>",
            unsafe_allow_html=True,
        )

        # Show who is selected
        selected_names = [
            f"**{s['full_name']}** ({s.get('sub_unit', '?')})"
            for s in selected_soldiers_info
        ]
        with st.expander(f"👁️ הצג את {len(selected_names)} החיילים הנבחרים", expanded=False):
            for i, name in enumerate(selected_names, 1):
                st.write(f"{i}. {name}")

        # Three clear action tabs
        act_move, act_role, act_remove = st.tabs([
            "🔀 העבר למחלקה אחרת",
            "📝 שנה תפקיד",
            "🗑️ הסר מהתקופה",
        ])

        with act_move:
            st.markdown(f"**העבר את {len(selected_soldiers_info)} החיילים הנבחרים ל:**")
            target_unit = st.selectbox(
                "מחלקת יעד",
                options=all_units,
                key="manage_target_unit",
            )
            if st.button(
                f"🔀 העבר {len(selected_soldiers_info)} חיילים ← {target_unit}",
                type="primary",
                key="do_move",
            ):
                count = 0
                for psid in list(selected_psids):
                    update_period_soldier(psid, sub_unit=target_unit)
                    count += 1
                # Clear all checkboxes
                for s in soldiers:
                    st.session_state[f"chk_{s['period_soldier_id']}"] = False
                st.success(f"✅ {count} חיילים הועברו ל-{target_unit}!")
                st.rerun()

        with act_role:
            st.markdown(f"**שנה תפקיד ל-{len(selected_soldiers_info)} החיילים הנבחרים:**")
            new_role = st.text_input(
                "תפקיד חדש",
                placeholder='לדוגמה: לוחם, מ"כ, מחלץ',
                key="manage_new_role",
            )
            if st.button(
                f"📝 עדכן תפקיד ל-{len(selected_soldiers_info)} חיילים",
                disabled=not new_role.strip(),
                key="do_role",
            ):
                count = 0
                for psid in list(selected_psids):
                    update_period_soldier(psid, role=new_role.strip())
                    count += 1
                for s in soldiers:
                    st.session_state[f"chk_{s['period_soldier_id']}"] = False
                st.success(f"✅ תפקיד עודכן ל-{count} חיילים: {new_role}")
                st.rerun()

        with act_remove:
            st.warning(
                f"⚠️ {len(selected_soldiers_info)} חיילים ייוסרו מהתקופה ולא יוצגו עוד ברשימות!"
            )
            if st.button(
                f"🗑️ הסר {len(selected_soldiers_info)} חיילים מהתקופה",
                type="primary",
                key="do_remove",
            ):
                count = 0
                for psid in list(selected_psids):
                    s_info = psid_to_soldier.get(psid)
                    if s_info:
                        remove_from_period(pid, s_info["soldier_id"])
                        count += 1
                for s in soldiers:
                    st.session_state[f"chk_{s['period_soldier_id']}"] = False
                st.success(f"✅ {count} חיילים הוסרו מהתקופה")
                st.rerun()

        # Clear selection button
        if st.button("🧹 נקה בחירה"):
            for s in soldiers:
                st.session_state[f"chk_{s['period_soldier_id']}"] = False
            st.rerun()

    else:
        st.info("👆 סמן חיילים בצ'קבוקס למטה, ואז יופיעו כאן אפשרויות הפעולה (העברה / תפקיד / הסרה)")

    st.markdown("---")

    # ── Visual unit columns ──
    unit_names = list(by_unit.keys())
    cols_per_row = 3

    for row_start in range(0, len(unit_names), cols_per_row):
        row_units = unit_names[row_start:row_start + cols_per_row]
        cols = st.columns(len(row_units))

        for col, unit_name in zip(cols, row_units):
            unit_soldiers = by_unit[unit_name]
            with col:
                # Unit header
                st.markdown(
                    f"<div style='background:#1B5E20; color:white; padding:8px 12px; "
                    f"border-radius:8px 8px 0 0; text-align:center; font-weight:bold;'>"
                    f"📋 {unit_name} ({len(unit_soldiers)})</div>",
                    unsafe_allow_html=True,
                )

                # Select all / deselect all
                unit_psids = [s["period_soldier_id"] for s in unit_soldiers]
                all_selected = all(
                    st.session_state.get(f"chk_{p}", False) for p in unit_psids
                )

                ca, cb = st.columns(2)
                if ca.button(
                    "☑️ כולם" if all_selected else "✅ כולם",
                    key=f"sel_all_{unit_name}",
                ):
                    new_val = not all_selected
                    for p in unit_psids:
                        st.session_state[f"chk_{p}"] = new_val
                    st.rerun()

                if cb.button("❌ אף אחד", key=f"desel_{unit_name}"):
                    for p in unit_psids:
                        st.session_state[f"chk_{p}"] = False
                    st.rerun()

                # Soldier checkboxes
                with st.container(border=True, height=350):
                    for s in unit_soldiers:
                        psid = s["period_soldier_id"]
                        role_str = s.get("role") or ""
                        label = f"**{s['full_name']}** {'· ' + role_str if role_str else ''}"
                        st.checkbox(label, key=f"chk_{psid}")

        if row_start + cols_per_row < len(unit_names):
            st.markdown("")


def _render_soldier_detail(pid: int, soldier: dict):
    """Render detail / edit form for a single soldier."""
    sid = soldier["soldier_id"]
    psid = soldier["period_soldier_id"]

    # Get available sub-units for selectbox
    existing_units = get_sub_units(pid)
    all_units = list(dict.fromkeys(
        existing_units + ["מפקדת הפלוגה", "מחלקה 1", "מחלקה 2", "מחלקה 3", "מפלג", "עוזיה", "מסופחים"]
    ))
    # Ensure "לא רלוונטי" unit is available
    if IRRELEVANT_UNIT not in all_units:
        all_units.append(IRRELEVANT_UNIT)
    current_unit = soldier.get("sub_unit", "")
    if current_unit and current_unit not in all_units:
        all_units.insert(0, current_unit)
    unit_idx = all_units.index(current_unit) if current_unit in all_units else 0

    with st.form(f"edit_soldier_{sid}"):
        st.markdown("#### פרטים אישיים")
        c1, c2 = st.columns(2)
        with c1:
            first_name = st.text_input("שם פרטי", value=soldier.get("first_name", ""))
            phone = st.text_input("טלפון", value=soldier.get("phone", "") or "")
            city = st.text_input("ישוב", value=soldier.get("city", "") or "")
        with c2:
            last_name = st.text_input("שם משפחה", value=soldier.get("last_name", ""))
            rank = st.text_input("דרגה", value=soldier.get("rank", "") or "")
            sub_unit = st.selectbox("מחלקה", options=all_units, index=unit_idx)

        st.markdown("#### תפקידים")
        role = st.text_input("תפקיד ארגוני", value=soldier.get("role", "") or "")
        task_role = st.text_input("תפקיד משימתי", value=soldier.get("task_role", "") or "")
        notes = st.text_area("הערות", value=soldier.get("notes", "") or "")

        st.markdown("#### 📝 הערות שיבוץ")
        st.caption(
            "הערות שמשפיעות על השיבוץ האוטומטי. "
            "בחר מהאפשרויות או כתוב הערה חופשית."
        )
        assignment_notes_presets = [
            "לילה בלבד",
            "ללא לילה",
            "נהיגה בלבד",
            "חפ\"ק בלבד",
            "שמירה בלבד",
            "לא לשבץ",
        ]
        current_assignment_notes = soldier.get("assignment_notes", "") or ""
        # Show preset chips + free text
        selected_presets = []
        preset_cols = st.columns(3)
        for idx_p, preset in enumerate(assignment_notes_presets):
            with preset_cols[idx_p % 3]:
                is_active = preset in current_assignment_notes
                if st.checkbox(preset, value=is_active, key=f"anp_{sid}_{idx_p}"):
                    selected_presets.append(preset)

        free_text_notes = st.text_input(
            "הערה חופשית נוספת",
            value=_extract_free_text(current_assignment_notes, assignment_notes_presets),
            key=f"an_free_{sid}",
        )
        # Combine into a single string
        assignment_notes_parts = selected_presets[:]
        if free_text_notes.strip():
            assignment_notes_parts.append(free_text_notes.strip())
        assignment_notes = " | ".join(assignment_notes_parts)

        st.markdown("#### סוג חייל")
        is_attached = st.checkbox(
            "חייל מסופח",
            value=soldier.get("is_attached", False),
            key=f"attached_{sid}",
            help="חייל מסופח — לא חייל קבוע של הפלוגה. יוצג בצבע שונה בדוח 1",
        )

        st.markdown("#### סטודנט")
        is_student = st.checkbox(
            "החייל סטודנט",
            value=soldier.get("is_student", False),
            key=f"student_{sid}",
            help="סטודנטים זכאים לקיצור שירות מילואים ב-25%",
        )
        student_short_service = False
        if is_student:
            student_short_service = st.checkbox(
                "מעוניין לקצר שירות",
                value=soldier.get("student_short_service", False),
                key=f"student_short_{sid}",
                help="האם החייל מעוניין לממש את הזכאות לקיצור 25% מאורך הצו",
            )

        if st.form_submit_button("💾 שמור שינויים"):
            # Save permanent soldier info
            update_soldier(
                sid,
                first_name=first_name,
                last_name=last_name,
                phone=phone or None,
                city=city or None,
            )
            # Save period-specific info (sub_unit, role, task_role, rank, notes, assignment_notes)
            update_period_soldier(
                psid,
                sub_unit=sub_unit,
                role=role or None,
                task_role=task_role or None,
                rank=rank or None,
                notes=notes or None,
                assignment_notes=assignment_notes or None,
                is_attached=is_attached,
                is_student=is_student,
                student_short_service=student_short_service if is_student else False,
            )
            st.success("כל הפרטים עודכנו בהצלחה!")
            st.rerun()

    # Remove from period (outside form)
    st.markdown("---")
    if st.button(f"🗑️ הסר את החייל מהתקופה (לא יוצג עוד)", key=f"remove_{sid}"):
        remove_from_period(pid, sid)
        st.success("החייל הוסר מהתקופה ולא יוצג עוד")
        st.rerun()

    # Certifications
    st.markdown("**הכשרות:**")
    certs = get_soldier_certifications(sid)
    if certs:
        for c in certs:
            st.markdown(f"- {c.get('name', '')} {'(פג תוקף)' if c.get('expired') else '✅'}")
    else:
        st.caption("אין הכשרות")

    new_cert = st.text_input("הוסף הכשרה", key=f"new_cert_{sid}")
    if new_cert and st.button("➕ הוסף", key=f"add_cert_{sid}"):
        add_soldier_certification(sid, new_cert)
        st.success(f"הכשרה '{new_cert}' נוספה")
        st.rerun()

    # ── Qualifications (הסמכות) ──
    st.markdown("**הסמכות:**")
    from military_manager.services.qualification_service import get_soldier_qualification_names
    sol_quals = get_soldier_qualification_names(pid, sid)
    if sol_quals:
        for qname in sol_quals:
            st.markdown(f"- 🎖️ {qname}")
    else:
        st.caption("אין הסמכות")

    # ── Soldier Dossier (תיק חייל) ──
    with st.expander("📂 תיק חייל — מידע נוסף", expanded=False):
        # Read the permanent soldier record for medical/personal notes
        from military_manager.services.soldier_service import get_soldier
        full_soldier = get_soldier(sid)

        st.markdown("##### 🏥 הערות רפואיות")
        medical = (full_soldier.medical_notes if full_soldier else None) or ""
        new_medical = st.text_area("הערות רפואיות", value=medical, key=f"med_{sid}")

        st.markdown("##### 📝 הערות אישיות")
        personal = (full_soldier.personal_notes if full_soldier else None) or ""
        new_personal = st.text_area("הערות אישיות", value=personal, key=f"pers_{sid}")

        # ── Preferred buddies ──
        st.markdown("##### 👥 חברים מועדפים למשימות")
        st.caption(
            "חיילים שמעדיפים לעלות יחד למשימות. "
            "השיבוץ האוטומטי ינסה לשבץ אותם יחד כשאפשר, "
            "בלי לפגוע בצדק (לא ידלג על אחרים ולא ישבץ יותר מדי)."
        )
        import json as _json
        all_period_soldiers = get_period_soldiers(pid)
        other_soldiers = [
            s for s in all_period_soldiers
            if s["soldier_id"] != sid
        ]
        buddy_options = {s["soldier_id"]: s["full_name"] for s in other_soldiers}

        current_buddies_json = soldier.get("preferred_buddies", "") or "[]"
        try:
            current_buddy_ids = _json.loads(current_buddies_json)
        except (_json.JSONDecodeError, TypeError):
            current_buddy_ids = []

        # Show current buddies
        buddy_names = [buddy_options.get(bid, f"ID {bid}") for bid in current_buddy_ids if bid in buddy_options]
        if buddy_names:
            st.markdown("**חברים נוכחיים:** " + ", ".join(f"👥 {n}" for n in buddy_names))

        # Multi-select to add/remove buddies
        new_buddy_ids = st.multiselect(
            "בחר חברים מועדפים",
            options=list(buddy_options.keys()),
            default=[bid for bid in current_buddy_ids if bid in buddy_options],
            format_func=lambda x: buddy_options.get(x, str(x)),
            key=f"buddies_{sid}",
        )

        # Show assignment notes read-only summary
        a_notes = soldier.get("assignment_notes", "") or ""
        if a_notes:
            st.markdown(f"##### 📋 הערות שיבוץ (מוגדרות למעלה)")
            for note_part in a_notes.split("|"):
                note_part = note_part.strip()
                if note_part:
                    st.markdown(f"- 🔹 {note_part}")

        # Additional info
        st.markdown("##### ℹ️ פרטים נוספים")
        ic1, ic2 = st.columns(2)
        ic1.markdown(f"**מספר אישי:** {soldier.get('military_id', 'לא ידוע')}")
        ic2.markdown(f"**ישוב:** {soldier.get('city', '') or 'לא ידוע'}")
        if soldier.get("arrival_date"):
            ic1.markdown(f"**תאריך הגעה:** {soldier['arrival_date']}")
        if soldier.get("departure_date"):
            ic2.markdown(f"**תאריך עזיבה:** {soldier['departure_date']}")

        if st.button("💾 שמור תיק חייל", key=f"save_dossier_{sid}"):
            update_soldier(sid, medical_notes=new_medical or None, personal_notes=new_personal or None)
            # Save buddies
            buddies_json = _json.dumps(new_buddy_ids) if new_buddy_ids else None
            update_period_soldier(psid, preferred_buddies=buddies_json)
            st.success("תיק החייל עודכן!")
            st.rerun()


def _render_add_soldier(pid: int):
    """Render form to add a new soldier."""
    with st.form("add_soldier_form"):
        st.markdown("### הוספת חייל חדש")

        c1, c2 = st.columns(2)
        with c1:
            military_id = st.text_input("מספר אישי *")
            first_name = st.text_input("שם פרטי *")
            phone = st.text_input("טלפון", placeholder="05X-XXXXXXX")
        with c2:
            rank = st.text_input("דרגה")
            last_name = st.text_input("שם משפחה *")
            city = st.text_input("ישוב")

        sub_unit = st.selectbox(
            "מחלקה",
            ["מפקדת הפלוגה", "מחלקה 1", "מחלקה 2", "מחלקה 3", "מפלג", "עוזיה"],
        )
        role = st.text_input("תפקיד ארגוני")
        task_role = st.text_input("תפקיד משימתי")
        is_attached = st.checkbox("חייל מסופח", value=False,
                                  help="סמן אם החייל מסופח לפלוגה ולא חייל קבוע שלנו")
        is_student = st.checkbox("סטודנט", value=False,
                                help="סטודנטים זכאים לקיצור שירות ב-25%")

        if st.form_submit_button("✅ הוסף חייל"):
            if not military_id or not first_name or not last_name:
                st.error("יש למלא שדות חובה: מספר אישי, שם פרטי, שם משפחה")
            else:
                from military_manager.services.soldier_service import get_or_create_soldier
                soldier, created = get_or_create_soldier(
                    military_id=military_id,
                    first_name=first_name,
                    last_name=last_name,
                    phone=phone or None,
                    city=city or None,
                )
                try:
                    assign_to_period(
                        period_id=pid,
                        soldier_id=soldier.id,
                        sub_unit=sub_unit,
                        role=role or None,
                        task_role=task_role or None,
                        rank=rank or None,
                        is_attached=is_attached,
                        is_student=is_student,
                    )
                    st.success(f"החייל {first_name} {last_name} נוסף בהצלחה!")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))


def _render_import(pid: int):
    """Render Excel import UI."""
    st.markdown("### ייבוא חיילים מקובץ אקסל")
    st.info(
        "העלה את קובץ האקסל של הפלוגה. המערכת תזהה אוטומטית את סד\"כ החיילים "
        "מהגיליון המתאים."
    )

    uploaded = st.file_uploader("בחר קובץ אקסל", type=["xlsx", "xls"], key="import_soldiers")

    if uploaded:
        import tempfile
        from pathlib import Path
        from military_manager.services.excel_import import get_available_sheets, import_roster_sheet

        # Save to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as f:
            f.write(uploaded.getvalue())
            temp_path = f.name

        sheets = get_available_sheets(temp_path)
        st.markdown(f"נמצאו {len(sheets)} גיליונות: {', '.join(sheets[:8])}")

        # Auto-detect roster sheet
        roster_sheets = [s for s in sheets if any(
            kw in s for kw in ["סד\"כ", "תכנון קדימה", "חיילים"]
        )]
        default_sheet = roster_sheets[0] if roster_sheets else (sheets[0] if sheets else "")

        selected_sheet = st.selectbox("בחר גיליון לייבוא", sheets, index=sheets.index(default_sheet) if default_sheet in sheets else 0)

        if st.button("🚀 התחל ייבוא"):
            with st.spinner("מייבא חיילים..."):
                results = import_roster_sheet(temp_path, pid, selected_sheet)

            st.success(f"✅ הייבוא הושלם: {results['created']} חיילים חדשים, {results['updated']} עודכנו")
            if results["errors"]:
                with st.expander(f"⚠️ {len(results['errors'])} שגיאות"):
                    for err in results["errors"][:20]:
                        st.warning(err)

        # Cleanup
        try:
            Path(temp_path).unlink()
        except Exception:
            pass


def _extract_free_text(assignment_notes: str, presets: list[str]) -> str:
    """Extract the free-text portion from assignment_notes by removing known presets."""
    if not assignment_notes:
        return ""
    parts = [p.strip() for p in assignment_notes.split("|")]
    free_parts = [p for p in parts if p and p not in presets]
    return " | ".join(free_parts)
