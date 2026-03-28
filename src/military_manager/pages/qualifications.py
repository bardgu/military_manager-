"""Qualifications page — manage operational qualifications (הסמכות) per period.

Qualifications define what operational roles a soldier is authorized to fill.
For example: "מפקד משימה" is a qualification — only soldiers who have been
qualified can be assigned to task slots that require it.
"""

from __future__ import annotations

import streamlit as st

from military_manager.components.navigation import render_page_header
from military_manager.components.filters import period_guard
from military_manager.services.qualification_service import (
    get_all_qualifications,
    create_qualification,
    delete_qualification,
    get_period_qualifications,
    assign_qualification,
    remove_qualification,
    bulk_assign_qualification,
)
from military_manager.services.soldier_service import get_period_soldiers


def render():
    render_page_header("🎖️ הסמכות", "ניהול הסמכות תפעוליות — מפקד משימה, חובש קרבי וכו׳")

    period = period_guard()
    if not period:
        return

    pid = period["id"]

    tab_types, tab_assign, tab_view = st.tabs([
        "📋 סוגי הסמכות",
        "➕ הסמכת חיילים",
        "👁️ צפייה בהסמכות",
    ])

    # ── Tab 1: Manage qualification types ──
    with tab_types:
        st.markdown("### הגדרת סוגי הסמכות")
        st.caption("הגדר הסמכות תפעוליות שניתן לשייך לחיילים. ההסמכות ישמשו לסינון חיילים בשיבוץ למשימות.")

        qualifications = get_all_qualifications()

        # Show existing
        if qualifications:
            for q in qualifications:
                col_name, col_desc, col_del = st.columns([3, 4, 1])
                col_name.markdown(f"**{q.name}**")
                col_desc.write(q.description or "—")
                if col_del.button("🗑️", key=f"del_qual_{q.id}"):
                    delete_qualification(q.id)
                    st.success(f"ההסמכה '{q.name}' נמחקה")
                    st.rerun()
        else:
            st.info("אין הסמכות מוגדרות. הוסף הסמכה חדשה למטה.")

        st.markdown("---")

        # Add new
        st.markdown("#### ➕ הסמכה חדשה")
        c1, c2 = st.columns([2, 3])
        new_name = c1.text_input("שם ההסמכה", placeholder="לדוגמה: מפקד משימה")
        new_desc = c2.text_input("תיאור (אופציונלי)", placeholder="חייל שהוסמך לפקד על משימות")

        if st.button("✅ הוסף הסמכה", disabled=not new_name.strip()):
            create_qualification(new_name.strip(), new_desc.strip() or None)
            st.success(f"ההסמכה '{new_name}' נוספה!")
            st.rerun()

        # Quick-add common qualifications
        st.markdown("---")
        st.markdown("#### 📋 הסמכות נפוצות (הוספה מהירה)")
        common_quals = ["מפקד משימה", "חובש קרבי", "מפעיל מל\"ט", "ק׳ אש", "מפקד חילוץ"]
        existing_names = {q.name for q in qualifications}
        available_common = [cq for cq in common_quals if cq not in existing_names]
        
        if available_common:
            cols = st.columns(min(len(available_common), 5))
            for i, cq in enumerate(available_common):
                if cols[i].button(f"➕ {cq}", key=f"quick_qual_{i}"):
                    create_qualification(cq)
                    st.success(f"ההסמכה '{cq}' נוספה!")
                    st.rerun()
        else:
            st.caption("כל ההסמכות הנפוצות כבר הוגדרו.")

    # ── Tab 2: Assign qualifications to soldiers ──
    with tab_assign:
        st.markdown("### הסמכת חיילים")

        qualifications = get_all_qualifications()
        if not qualifications:
            st.warning("יש להגדיר סוגי הסמכות תחילה (בלשונית 'סוגי הסמכות').")
            return

        qual_options = {q.name: q.id for q in qualifications}
        selected_qual_name = st.selectbox(
            "בחר הסמכה",
            options=list(qual_options.keys()),
            key="assign_qual_select",
        )
        selected_qual_id = qual_options[selected_qual_name]

        # Load current assignments for this qualification
        current_assignments = get_period_qualifications(pid, selected_qual_id)
        assigned_soldier_ids = {a["soldier_id"] for a in current_assignments}

        # Show currently qualified soldiers
        if current_assignments:
            st.markdown(f"#### חיילים מוסמכים — {selected_qual_name} ({len(current_assignments)})")
            for a in current_assignments:
                col_name, col_info, col_rem = st.columns([3, 4, 1])
                col_name.write(f"**{a['soldier_name']}**")
                col_info.caption(f"מ.א: {a['military_id']} | נוסף ע\"י: {a['granted_by'] or '—'}")
                if col_rem.button("❌", key=f"rem_qual_{a['id']}"):
                    remove_qualification(a["id"])
                    st.success(f"ההסמכה הוסרה מ-{a['soldier_name']}")
                    st.rerun()
        else:
            st.info(f"אין חיילים מוסמכים כ-'{selected_qual_name}' בתקופה זו.")

        st.markdown("---")

        # Add soldiers
        st.markdown(f"#### ➕ הוסף חיילים להסמכה '{selected_qual_name}'")

        all_soldiers = get_period_soldiers(pid, exclude_irrelevant_unit=True)
        unqualified = [
            s for s in all_soldiers
            if s["soldier_id"] not in assigned_soldier_ids
        ]

        if not unqualified:
            st.caption("כל חיילי התקופה כבר מוסמכים להסמכה זו.")
        else:
            # Filter by sub-unit
            sub_units = sorted(set(s["sub_unit"] for s in unqualified))
            filter_unit = st.selectbox(
                "סנן לפי מחלקה",
                options=["הכל"] + sub_units,
                key="qual_filter_unit",
            )

            if filter_unit != "הכל":
                unqualified = [s for s in unqualified if s["sub_unit"] == filter_unit]

            soldier_options = {
                f"{s['full_name']} ({s['role'] or '—'}) [{s['sub_unit']}]": s["soldier_id"]
                for s in unqualified
            }

            selected_soldiers = st.multiselect(
                "בחר חיילים להסמכה",
                options=list(soldier_options.keys()),
                key="qual_soldier_multiselect",
            )

            granted_by = st.text_input("מוסמך ע\"י", placeholder="שם המפקד", key="qual_granted_by")

            if st.button("✅ הסמך חיילים", disabled=not selected_soldiers):
                soldier_ids = [soldier_options[s] for s in selected_soldiers]
                count = bulk_assign_qualification(
                    pid, soldier_ids, selected_qual_id, granted_by=granted_by
                )
                st.success(f"הוסמכו {count} חיילים כ-'{selected_qual_name}'!")
                st.rerun()

    # ── Tab 3: View all qualifications for this period ──
    with tab_view:
        st.markdown("### סיכום הסמכות לתקופה")

        qualifications = get_all_qualifications()
        all_assignments = get_period_qualifications(pid)

        if not all_assignments:
            st.info("אין הסמכות מוגדרות לחיילים בתקופה זו.")
            return

        # Group by qualification
        by_qual: dict[str, list[dict]] = {}
        for a in all_assignments:
            by_qual.setdefault(a["qualification_name"], []).append(a)

        # Summary metrics
        cols = st.columns(min(len(by_qual), 5))
        for i, (qname, soldiers) in enumerate(by_qual.items()):
            cols[i % len(cols)].metric(qname, len(soldiers))

        st.markdown("---")

        # Details per qualification
        for qname, soldiers in by_qual.items():
            with st.expander(f"🎖️ {qname} — {len(soldiers)} חיילים", expanded=True):
                for j, s in enumerate(soldiers, 1):
                    st.write(f"{j}. **{s['soldier_name']}** — {s.get('military_id', '')} "
                             f"({s.get('granted_by') or '—'})")
