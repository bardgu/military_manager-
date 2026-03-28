"""Drivers page — manage approved drivers per reserve period.

Workflow:
- מ"מ proposes drivers from their squad (or auto-suggest from role)
- סמ"פ reviews and approves/rejects
- Only approved drivers appear in "נהג" task slots on the שבצ"ק page
"""

from __future__ import annotations

import streamlit as st
import pandas as pd

from military_manager.components.navigation import render_page_header
from military_manager.components.filters import period_guard
from military_manager.services.driver_service import (
    propose_driver,
    approve_driver,
    reject_driver,
    bulk_approve,
    remove_driver,
    get_period_drivers,
    get_potential_drivers,
    get_non_driver_soldiers,
)
from military_manager.services.soldier_service import get_sub_units


VEHICLE_TYPES = ["רכב פרטי", "משא", 'חפ"ק', "ג'יפ", "אמבולנס", "סיור", "טיגריס", "אחר"]
STATUS_LABELS = {"pending": "⏳ ממתין לאישור", "approved": "✅ מאושר", "rejected": "❌ נדחה"}
STATUS_COLORS = {"pending": "🟡", "approved": "🟢", "rejected": "🔴"}


def render():
    render_page_header("🚗 נהגים מאושרים", "ניהול רשימת נהגים מאושרים לתקופה — נהג חייב אישור סמ\"פ")

    period = period_guard()
    if not period:
        return

    readonly = st.session_state.get("_company_readonly", False)
    pid = period["id"]

    tab_list, tab_propose, tab_approve = st.tabs([
        "📋 רשימת נהגים",
        "➕ הצע נהג",
        "✅ אישור סמ\"פ",
    ])

    # ── Tab 1: Full driver list ──
    with tab_list:
        _render_driver_list(pid)

    # ── Tab 2: Propose drivers (מ"מ workflow) ──
    with tab_propose:
        _render_propose_tab(pid)

    # ── Tab 3: Approval workflow (סמ"פ) ──
    with tab_approve:
        _render_approval_tab(pid)


def _render_driver_list(pid: int):
    """Show all drivers for this period with status."""
    st.markdown("### 📋 רשימת נהגים לתקופה")

    drivers = get_period_drivers(pid)
    if not drivers:
        st.info("אין נהגים מוגדרים עדיין. הוסף נהגים בלשונית 'הצע נהג'.")
        return

    # Summary metrics
    approved = [d for d in drivers if d["status"] == "approved"]
    pending = [d for d in drivers if d["status"] == "pending"]
    rejected = [d for d in drivers if d["status"] == "rejected"]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("סה\"כ נהגים", len(drivers))
    c2.metric("🟢 מאושרים", len(approved))
    c3.metric("🟡 ממתינים", len(pending))
    c4.metric("🔴 נדחו", len(rejected))

    st.markdown("---")

    # Filter by status
    status_filter = st.selectbox(
        "סנן לפי סטטוס",
        ["הכל", "✅ מאושר", "⏳ ממתין לאישור", "❌ נדחה"],
        key="driver_status_filter",
    )
    filter_map = {"✅ מאושר": "approved", "⏳ ממתין לאישור": "pending", "❌ נדחה": "rejected"}
    filtered = drivers
    if status_filter != "הכל":
        f_status = filter_map.get(status_filter)
        if f_status:
            filtered = [d for d in drivers if d["status"] == f_status]

    # Build table
    if filtered:
        rows = []
        for d in filtered:
            rows.append({
                "שם": d["full_name"],
                "תת יחידה": d["sub_unit"],
                "תפקיד": d["role"],
                "סוג רכב": d["vehicle_type"],
                "סטטוס": STATUS_LABELS.get(d["status"], d["status"]),
                "הוצע ע\"י": d["proposed_by"],
                "אושר ע\"י": d["approved_by"],
                "הערות": d["notes"],
            })
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True,
                     column_config={
                         "שם מלא": st.column_config.TextColumn("שם מלא", pinned=True),
                     })

        # Remove driver option
        st.markdown("---")
        st.markdown("#### 🗑️ הסר נהג מהרשימה")
        remove_options = {d["id"]: f"{d['full_name']} ({STATUS_LABELS.get(d['status'], '')})" for d in filtered}
        sel_remove = st.selectbox("בחר נהג להסרה", ["—"] + list(remove_options.values()), key="remove_driver_sel")
        if sel_remove != "—":
            driver_id = next((did for did, lbl in remove_options.items() if lbl == sel_remove), None)
            if driver_id and st.button("🗑️ הסר", key="do_remove_driver", disabled=readonly):
                remove_driver(driver_id)
                st.success("הנהג הוסר מהרשימה")
                st.rerun()
    else:
        st.info("אין נהגים בסטטוס שנבחר")


def _render_propose_tab(pid: int):
    """Tab for מ"מ to propose drivers."""
    st.markdown("### ➕ הצע נהג חדש")
    st.caption(
        "כל מ\"מ יכול להציע חיילים מהמחלקה שלו כנהגים. "
        "הנהג ייכנס כ'ממתין לאישור' עד שסמ\"פ מאשר."
    )

    # Auto-suggest: soldiers with נהג in their role
    st.markdown("---")
    st.markdown("#### 🔍 חיילים עם תפקיד נהג (הצעה אוטומטית)")
    potential = get_potential_drivers(pid)
    if potential:
        st.caption(f"נמצאו {len(potential)} חיילים עם תפקיד נהג שטרם הוגדרו ברשימה")
        for sol in potential:
            col1, col2, col3, col4 = st.columns([3, 2, 2, 1])
            with col1:
                st.markdown(f"**{sol['full_name']}** — {sol.get('sub_unit', '')} ({sol.get('role', '')})")
            with col2:
                vtype = st.selectbox(
                    "סוג רכב",
                    VEHICLE_TYPES,
                    key=f"pot_vtype_{sol['soldier_id']}",
                )
            with col3:
                custom_vtype = st.text_input(
                    "הכנס כלי רכב באופן ידני",
                    key=f"pot_cvtype_{sol['soldier_id']}",
                    placeholder="לדוגמא: טיגריס",
                    disabled=(vtype != "אחר"),
                )
            with col4:
                if st.button("➕ הצע", key=f"pot_propose_{sol['soldier_id']}", disabled=readonly):
                    final_vtype = custom_vtype.strip() if custom_vtype and custom_vtype.strip() else vtype
                    propose_driver(
                        pid, sol["soldier_id"],
                        proposed_by=st.session_state.get("commander_name", ""),
                        vehicle_type=final_vtype,
                    )
                    st.success(f"{sol['full_name']} הוצע כנהג")
                    st.rerun()

        # Bulk propose all
        if st.button("📥 הצע את כולם", key="propose_all_potential", disabled=readonly):
            for sol in potential:
                propose_driver(
                    pid, sol["soldier_id"],
                    proposed_by=st.session_state.get("commander_name", ""),
                )
            st.success(f"הוצעו {len(potential)} נהגים")
            st.rerun()
    else:
        st.success("כל החיילים עם תפקיד נהג כבר ברשימה ✅")

    # Manual add: any soldier
    st.markdown("---")
    st.markdown("#### ✏️ הוספה ידנית (חייל שאינו מסומן כנהג)")
    non_drivers = get_non_driver_soldiers(pid)
    if non_drivers:
        options = {s["soldier_id"]: f"{s['full_name']} — {s.get('sub_unit', '')} ({s.get('role', '')})" for s in non_drivers}
        sel = st.selectbox("בחר חייל", ["—"] + list(options.values()), key="manual_driver_sel")
        if sel != "—":
            sid = next((sid for sid, lbl in options.items() if lbl == sel), None)
            c1, c2, c3 = st.columns([2, 2, 2])
            with c1:
                vtype = st.selectbox("סוג רכב", VEHICLE_TYPES, key="manual_vtype")
            with c2:
                custom_vtype = st.text_input(
                    "הכנס כלי רכב באופן ידני",
                    key="manual_cvtype",
                    placeholder="לדוגמא: טיגריס",
                    disabled=(vtype != "אחר"),
                )
            with c3:
                notes = st.text_input("הערות", key="manual_driver_notes")
            if sid and st.button("➕ הצע כנהג", key="do_manual_propose", disabled=readonly):
                final_vtype = custom_vtype.strip() if custom_vtype and custom_vtype.strip() else vtype
                propose_driver(
                    pid, sid,
                    proposed_by=st.session_state.get("commander_name", ""),
                    vehicle_type=final_vtype,
                    notes=notes or None,
                )
                st.success("הנהג הוצע בהצלחה — ממתין לאישור סמ\"פ")
                st.rerun()
    else:
        st.info("כל החיילים כבר ברשימת הנהגים")


def _render_approval_tab(pid: int):
    """Tab for סמ"פ to approve/reject pending drivers."""
    st.markdown("### ✅ אישור נהגים (סמ\"פ)")
    st.caption(
        "נהגים שהוצעו ע\"י מ\"מ צריכים אישור סמ\"פ. "
        "רק נהגים מאושרים יוכלו להיות משובצים לתפקיד נהג במשימות."
    )

    pending = get_period_drivers(pid, status_filter="pending")

    if not pending:
        st.success("אין נהגים ממתינים לאישור 🎉")

        # Show approved list for reference
        approved = get_period_drivers(pid, status_filter="approved")
        if approved:
            st.markdown("---")
            st.markdown(f"#### 🟢 נהגים מאושרים ({len(approved)})")
            for d in approved:
                st.markdown(f"- **{d['full_name']}** ({d['sub_unit']}) — {d['vehicle_type']}")
        return

    st.warning(f"יש {len(pending)} נהגים ממתינים לאישור")

    # Bulk approve all
    if st.button(f"✅ אשר את כל {len(pending)} הנהגים", key="bulk_approve_all",
                  disabled=readonly):
        approver = st.session_state.get("commander_name", "סמ\"פ")
        count = bulk_approve(
            [d["id"] for d in pending],
            approved_by=approver,
        )
        st.success(f"אושרו {count} נהגים!")
        st.rerun()

    st.markdown("---")

    # Individual review
    for d in pending:
        with st.container():
            col_info, col_actions = st.columns([4, 3])

            with col_info:
                st.markdown(
                    f"**{d['full_name']}** — {d['sub_unit']} | "
                    f"תפקיד: {d['role']} | "
                    f"סוג רכב: {d['vehicle_type'] or '—'}"
                )
                if d["proposed_by"]:
                    st.caption(f"הוצע ע\"י: {d['proposed_by']}")
                if d["notes"]:
                    st.caption(f"הערות: {d['notes']}")

            with col_actions:
                c_approve, c_reject = st.columns(2)
                with c_approve:
                    if st.button("✅ אשר", key=f"approve_{d['id']}", disabled=readonly):
                        approver = st.session_state.get("commander_name", "סמ\"פ")
                        approve_driver(d["id"], approved_by=approver)
                        st.success(f"{d['full_name']} אושר!")
                        st.rerun()
                with c_reject:
                    if st.button("❌ דחה", key=f"reject_{d['id']}", disabled=readonly):
                        approver = st.session_state.get("commander_name", "סמ\"פ")
                        reject_driver(d["id"], approved_by=approver)
                        st.warning(f"{d['full_name']} נדחה")
                        st.rerun()

            st.markdown("---")
