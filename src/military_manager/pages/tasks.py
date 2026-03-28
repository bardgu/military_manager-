"""Tasks page — manage mission definitions with role-based slots."""

from __future__ import annotations

import json
import streamlit as st

from military_manager.components.navigation import render_page_header
from military_manager.components.filters import period_guard
from military_manager.services.task_service import (
    create_task,
    get_period_tasks,
    update_task,
    delete_task,
    get_fairness_report,
    get_detailed_fairness_report,
    add_task_slot,
    get_task_slots,
    replace_task_slots,
    delete_task_slot,
    KNOWN_ORG_ROLES,
    ALL_KNOWN_ROLES,
    get_all_role_options,
)

# ─── Shift time presets ─────────────────────────────────────
SHIFT_TIME_PRESETS = {
    "3 משמרות (06-14, 14-22, 22-06)": '["06:00-14:00","14:00-22:00","22:00-06:00"]',
    "3 משמרות (08-16, 16-00, 00-08)": '["08:00-16:00","16:00-00:00","00:00-08:00"]',
    "2 משמרות (06-18, 18-06)": '["06:00-18:00","18:00-06:00"]',
    "2 משמרות (07-19, 19-07)": '["07:00-19:00","19:00-07:00"]',
    "4 משמרות (06-12, 12-18, 18-00, 00-06)": '["06:00-12:00","12:00-18:00","18:00-00:00","00:00-06:00"]',
    "24 שעות": '["00:00-00:00"]',
    "✏️ התאמה אישית": "",
}


def _shift_times_selector(key_prefix: str, current_value: str = "", shifts_count: int = 3) -> str:
    """Render a shift-time selector: preset dropdown + optional custom edit.

    Returns the final shift_times string.
    """
    # Try to match current_value to a preset
    matching_preset = None
    for label, val in SHIFT_TIME_PRESETS.items():
        if val and current_value and val.replace(' ', '') == current_value.replace(' ', ''):
            matching_preset = label
            break

    preset_options = list(SHIFT_TIME_PRESETS.keys())
    default_idx = 0
    if matching_preset:
        default_idx = preset_options.index(matching_preset)
    elif current_value and current_value.strip():
        default_idx = preset_options.index("✏️ התאמה אישית")

    selected_preset = st.selectbox(
        "שעות משמרות",
        options=preset_options,
        index=default_idx,
        key=f"{key_prefix}_shift_preset",
    )

    if selected_preset == "✏️ התאמה אישית":
        custom_val = st.text_input(
            "הזן שעות משמרות (JSON)",
            value=current_value or "",
            placeholder='["06:00-14:00","14:00-22:00","22:00-06:00"]',
            key=f"{key_prefix}_shift_custom",
        )
        return custom_val
    else:
        result = SHIFT_TIME_PRESETS[selected_preset]
        # Show the value for reference (read-only)
        if result:
            st.caption(f"🕐 {result}")
        return result


# ─── Task slot templates (quick presets) ──────────────────────

TASK_TEMPLATES = {
    "סיור": [
        {"slot_name": "מפקד", "quantity": 1, "allowed_roles": ["מ\"מ", "מ\"כ", "סמל מחלקה", "מ\"פ", "סמ\"פ", "מפקד משימה"]},
        {"slot_name": "נהג", "quantity": 1, "allowed_roles": ["נהג"]},
        {"slot_name": "לוחם", "quantity": 2, "allowed_roles": []},  # any soldier
    ],
    "כרמל": [
        {"slot_name": "מפקד", "quantity": 1, "allowed_roles": ["מ\"מ", "מ\"כ", "סמל מחלקה", "מ\"פ", "סמ\"פ", "מפקד משימה"]},
        {"slot_name": "נהג", "quantity": 1, "allowed_roles": ["נהג"]},
        {"slot_name": "לוחם", "quantity": 2, "allowed_roles": []},  # any soldier
    ],
    "חפ\"ק מ\"פ": [
        {"slot_name": "מפקד חפ\"ק", "quantity": 1, "allowed_roles": ["מ\"פ"]},
        {"slot_name": "נהג חפ\"ק", "quantity": 1, "allowed_roles": ["נהג חפ\"ק מ\"פ"]},
        {"slot_name": "קשר חפ\"ק", "quantity": 1, "allowed_roles": ["קשר חפ\"ק מ\"פ"]},
        {"slot_name": "חובש חפ\"ק", "quantity": 1, "allowed_roles": ["חובש חפ\"ק מ\"פ"]},
    ],
    "חפ\"ק סמ\"פ": [
        {"slot_name": "מפקד חפ\"ק", "quantity": 1, "allowed_roles": ["סמ\"פ"]},
        {"slot_name": "נהג חפ\"ק", "quantity": 1, "allowed_roles": ["נהג חפ\"ק סמ\"פ"]},
        {"slot_name": "קשר חפ\"ק", "quantity": 1, "allowed_roles": ["קשר חפ\"ק סמ\"פ"]},
        {"slot_name": "חובש חפ\"ק", "quantity": 1, "allowed_roles": ["חובש חפ\"ק סמ\"פ"]},
    ],
    "קצין מוצב": [
        {"slot_name": "קצין מוצב", "quantity": 1, "allowed_roles": ["מ\"מ", "קצין מוצב"]},
    ],
    "פילבוקס": [
        {"slot_name": "לוחם", "quantity": 2, "allowed_roles": []},  # any
    ],
    "קצין תורן": [
        {"slot_name": "קצין תורן", "quantity": 1, "allowed_roles": ["מ\"מ", "סמ\"פ", "מ\"פ"]},
    ],
    "שמירת שער": [
        {"slot_name": "שומר", "quantity": 2, "allowed_roles": []},  # any
    ],
    "חילוץ": [
        {"slot_name": "מפקד צוות", "quantity": 1, "allowed_roles": ["מ\"מ", "מ\"כ", "מ\"פ", "סמ\"פ"]},
        {"slot_name": "מחלץ", "quantity": 2, "allowed_roles": ["מחלץ"]},
        {"slot_name": "נהג", "quantity": 1, "allowed_roles": ["נהג"]},
    ],
}


# Hebrew weekday names for rotation config (Monday=0 ... Sunday=6)
HEB_WEEKDAYS = {
    0: "שני", 1: "שלישי", 2: "רביעי", 3: "חמישי",
    4: "שישי", 5: "שבת", 6: "ראשון",
}


def _render_rotation_config(key_prefix: str, task=None) -> tuple[bool, str | None, str | None]:
    """Render non-continuous rotation checkbox + config.
    Returns (non_continuous, rotation_type, rotation_config_json).
    """
    current_nc = getattr(task, 'non_continuous', False) if task else False
    current_type = getattr(task, 'rotation_type', None) if task else None
    current_config = getattr(task, 'rotation_config', None) if task else None

    nc = st.checkbox(
        "🔄 חילוף לא רציף (אותם חיילים נשארים מספר ימים)",
        value=bool(current_nc),
        key=f"{key_prefix}_nc",
    )

    if not nc:
        return False, None, None

    rtype = st.radio(
        "סוג חילוף",
        ["fixed_days", "specific_dates"],
        format_func=lambda x: "ימים קבועים בשבוע" if x == "fixed_days" else "תאריכים ספציפיים",
        index=0 if current_type != "specific_dates" else 1,
        horizontal=True,
        key=f"{key_prefix}_rtype",
    )

    if rtype == "fixed_days":
        current_days = []
        if current_type == "fixed_days" and current_config:
            try:
                current_days = json.loads(current_config)
            except (json.JSONDecodeError, TypeError):
                pass
        selected = st.multiselect(
            "ימי חילוף (ימים שבהם מתחלפים חיילים)",
            options=list(range(7)),
            default=[d for d in current_days if isinstance(d, int)],
            format_func=lambda x: HEB_WEEKDAYS.get(x, str(x)),
            key=f"{key_prefix}_rdays",
        )
        st.caption("💡 בימים שלא נבחרו — אותם חיילים ממשיכים מהיום הקודם")
        config_json = json.dumps(sorted(selected))
    else:
        current_dates = ""
        if current_type == "specific_dates" and current_config:
            try:
                current_dates = ", ".join(json.loads(current_config))
            except (json.JSONDecodeError, TypeError):
                pass
        dates_str = st.text_input(
            "תאריכי חילוף (מופרדים בפסיק)",
            value=current_dates,
            placeholder="2026-02-24, 2026-02-27, 2026-03-01",
            key=f"{key_prefix}_rdates",
        )
        dates_list = [d.strip() for d in dates_str.split(",") if d.strip()]
        config_json = json.dumps(dates_list)

    return True, rtype, config_json


def _render_slot_editor(slots_key: str, initial_slots: list[dict] | None = None):
    """Render a dynamic slot editor in session state.
    
    Returns list of slot dicts from current session state.
    """
    if slots_key not in st.session_state:
        st.session_state[slots_key] = initial_slots or []

    slots = st.session_state[slots_key]

    st.markdown("#### 🎭 תפקידים (סלוטים)")
    st.caption("הגדר את התפקידים הנדרשים למשימה. לכל תפקיד ניתן לבחור אילו תפקידים ארגוניים מתאימים.")

    # Template selector
    template_names = ["—"] + list(TASK_TEMPLATES.keys())
    chosen_template = st.selectbox(
        "📋 טען תבנית מוכנה",
        template_names,
        key=f"{slots_key}_template",
    )
    if chosen_template != "—":
        if st.button("🔄 טען תבנית", key=f"{slots_key}_load_tmpl", disabled=readonly):
            st.session_state[slots_key] = [
                dict(s) for s in TASK_TEMPLATES[chosen_template]
            ]
            st.rerun()

    st.markdown("---")

    # Show current slots
    to_remove = None
    for i, slot in enumerate(slots):
        col_name, col_qty, col_roles, col_del = st.columns([2, 1, 4, 1])
        with col_name:
            slot["slot_name"] = st.text_input(
                f"שם תפקיד",
                value=slot.get("slot_name", ""),
                key=f"{slots_key}_name_{i}",
            )
        with col_qty:
            slot["quantity"] = st.number_input(
                "כמות",
                value=slot.get("quantity", 1),
                min_value=1,
                max_value=20,
                key=f"{slots_key}_qty_{i}",
            )
        with col_roles:
            current_roles = slot.get("allowed_roles", [])
            available_roles = get_all_role_options()
            slot["allowed_roles"] = st.multiselect(
                "תפקידים / הסמכות (ריק = כולם)",
                options=available_roles,
                default=[r for r in current_roles if r in available_roles],
                key=f"{slots_key}_roles_{i}",
            )
        with col_del:
            st.write("")
            st.write("")
            if st.button("🗑️", key=f"{slots_key}_del_{i}", disabled=readonly):
                to_remove = i

    if to_remove is not None:
        st.session_state[slots_key].pop(to_remove)
        st.rerun()

    # Add slot button
    if st.button("➕ הוסף תפקיד", key=f"{slots_key}_add", disabled=readonly):
        st.session_state[slots_key].append(
            {"slot_name": "", "quantity": 1, "allowed_roles": []}
        )
        st.rerun()

    return st.session_state[slots_key]


def render():
    render_page_header("🎯 משימות", "ניהול משימות ושמירות — כולל הגדרת תפקידים")

    period = period_guard()
    if not period:
        return

    readonly = st.session_state.get("_company_readonly", False)
    pid = period["id"]

    tab_list, tab_add, tab_fairness = st.tabs(["📋 משימות", "➕ משימה חדשה", "⚖️ טבלת צדק"])

    # ── Tasks list ──
    with tab_list:
        tasks = get_period_tasks(pid, active_only=False)
        if not tasks:
            st.info("אין משימות מוגדרות לתקופה זו. הוסף משימה חדשה.")
        else:
            # ── ChaPaK toggle: only one active at a time ──
            chapak_tasks = [t for t in tasks if "חפ\"ק" in t.name and ("מ\"פ" in t.name or "סמ\"פ" in t.name)]
            if len(chapak_tasks) == 2:
                chapak_mf = next((t for t in chapak_tasks if "מ\"פ" in t.name and "סמ\"פ" not in t.name), None)
                chapak_smf = next((t for t in chapak_tasks if "סמ\"פ" in t.name), None)
                if chapak_mf and chapak_smf:
                    st.markdown("### 🔀 בחירת חפ\"ק פעיל")
                    # Determine current active
                    if chapak_mf.is_active and not chapak_smf.is_active:
                        current_idx = 0
                    elif chapak_smf.is_active and not chapak_mf.is_active:
                        current_idx = 1
                    else:
                        current_idx = 0  # default to מ"פ

                    choice = st.radio(
                        "איזה חפ\"ק פעיל?",
                        options=[chapak_mf.name, chapak_smf.name],
                        index=current_idx,
                        horizontal=True,
                        key="chapak_toggle",
                    )

                    want_mf = choice == chapak_mf.name
                    need_update = (want_mf and (not chapak_mf.is_active or chapak_smf.is_active)) or \
                                  (not want_mf and (chapak_mf.is_active or not chapak_smf.is_active))
                    if need_update:
                        update_task(chapak_mf.id, is_active=want_mf)
                        update_task(chapak_smf.id, is_active=not want_mf)
                        st.rerun()

                    st.markdown("---")

            for task in tasks:
                task_slots = get_task_slots(task.id)
                total_per_shift = sum(s["quantity"] for s in task_slots) if task_slots else task.personnel_per_shift
                slot_summary = ", ".join(
                    f"{s['slot_name']}×{s['quantity']}" if s['quantity'] > 1 else s['slot_name']
                    for s in task_slots
                ) if task_slots else f"{task.personnel_per_shift} חיילים"

                with st.expander(
                    f"{'🟢' if task.is_active else '⚪'} {task.name} "
                    f"— {slot_summary} × {task.shifts_per_day} משמרות",
                    expanded=task.is_active,
                ):
                    # Task details
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("כ\"א למשמרת", total_per_shift)
                    c2.metric("משמרות ביום", task.shifts_per_day)
                    total = total_per_shift * task.shifts_per_day
                    c3.metric("סה\"כ יומי", total)
                    c4.metric("סטטוס", "פעיל" if task.is_active else "מושבת")

                    # Show slot details
                    if task_slots:
                        st.markdown("**תפקידים מוגדרים:**")
                        for s in task_slots:
                            role_desc = ", ".join(s["allowed_roles"]) if s["allowed_roles"] else "כל חייל"
                            st.markdown(
                                f"- **{s['slot_name']}** × {s['quantity']} "
                                f"(מתאים ל: {role_desc})"
                            )
                    else:
                        st.info("⚠️ משימה ללא תפקידים מוגדרים — כל חייל ישובץ ללא סינון")

                    if task.shift_times:
                        st.markdown(f"**שעות משמרות:** {task.shift_times}")
                    if getattr(task, 'non_continuous', False):
                        rtype = getattr(task, 'rotation_type', '')
                        rconf = getattr(task, 'rotation_config', '')
                        if rtype == 'fixed_days' and rconf:
                            try:
                                days = json.loads(rconf)
                                day_names = ", ".join(HEB_WEEKDAYS.get(d, str(d)) for d in days)
                                st.markdown(f"🔄 **חילוף לא רציף:** ימים קבועים — {day_names}")
                            except (json.JSONDecodeError, TypeError):
                                st.markdown("🔄 **חילוף לא רציף**")
                        elif rtype == 'specific_dates' and rconf:
                            st.markdown(f"🔄 **חילוף לא רציף:** תאריכים ספציפיים")
                        else:
                            st.markdown("🔄 **חילוף לא רציף**")
                    if task.notes:
                        st.markdown(f"**הערות:** {task.notes}")

                    # Edit form
                    with st.form(f"edit_task_{task.id}"):
                        new_name = st.text_input("שם המשימה", value=task.name)
                        c1, c2 = st.columns(2)
                        with c2:
                            new_shifts = st.number_input(
                                "משמרות ביום", value=task.shifts_per_day, min_value=1
                            )
                        new_times = _shift_times_selector(
                            f"edit_{task.id}",
                            current_value=task.shift_times or "",
                            shifts_count=task.shifts_per_day,
                        )
                        new_notes = st.text_area("הערות", value=task.notes or "")

                        col_save, _, _ = st.columns([2, 1, 1])
                        with col_save:
                            if st.form_submit_button("💾 שמור פרטי משימה",
                                                        disabled=readonly):
                                update_task(
                                    task.id,
                                    name=new_name,
                                    shifts_per_day=new_shifts,
                                    shift_times=new_times or None,
                                    notes=new_notes or None,
                                )
                                st.success("המשימה עודכנה!")
                                st.rerun()

                    # Non-continuous rotation config (outside form for dynamic UI)
                    st.markdown("---")
                    nc, rtype, rconf = _render_rotation_config(f"edit_rot_{task.id}", task)
                    current_nc = getattr(task, 'non_continuous', False)
                    current_rtype = getattr(task, 'rotation_type', None)
                    current_rconf = getattr(task, 'rotation_config', None)
                    if (nc != bool(current_nc) or rtype != current_rtype or rconf != current_rconf):
                        if st.button("💾 שמור הגדרות חילוף", key=f"save_rot_{task.id}", disabled=readonly):
                            update_task(
                                task.id,
                                non_continuous=nc,
                                rotation_type=rtype,
                                rotation_config=rconf,
                            )
                            st.success("הגדרות חילוף עודכנו!")
                            st.rerun()

                    # Slot editor for existing task
                    st.markdown("---")
                    slots_key = f"edit_slots_{task.id}"
                    if slots_key not in st.session_state:
                        st.session_state[slots_key] = task_slots

                    edited_slots = _render_slot_editor(slots_key, task_slots)

                    if st.button("💾 שמור תפקידים", key=f"save_slots_{task.id}", disabled=readonly):
                        replace_task_slots(task.id, edited_slots)
                        # Update personnel_per_shift to match
                        new_total = sum(s.get("quantity", 1) for s in edited_slots)
                        update_task(task.id, personnel_per_shift=new_total)
                        # Clear cache
                        if slots_key in st.session_state:
                            del st.session_state[slots_key]
                        st.success("התפקידים עודכנו!")
                        st.rerun()

                    st.markdown("---")

                    # Actions
                    c_toggle, c_del = st.columns([1, 1])
                    with c_toggle:
                        label = "השבת" if task.is_active else "הפעל"
                        if st.button(f"🔄 {label}", key=f"toggle_{task.id}", disabled=readonly):
                            update_task(task.id, is_active=not task.is_active)
                            st.rerun()
                    with c_del:
                        if st.button(f"🗑️ מחק", key=f"del_task_{task.id}",
                                     disabled=readonly):
                            delete_task(task.id)
                            st.success("המשימה נמחקה")
                            st.rerun()

    # ── Add task ──
    with tab_add:
        st.markdown("### הוספת משימה חדשה")

        name = st.text_input("שם המשימה *", placeholder="לדוגמה: סיור, חפ\"ק, שמירת שער")
        c1, c2 = st.columns(2)
        with c2:
            shifts = st.number_input("משמרות ביום", value=3, min_value=1, key="new_task_shifts")

        shift_times = _shift_times_selector("new_task", shifts_count=shifts)
        notes = st.text_area("הערות", key="new_task_notes")

        st.markdown("---")
        nc, rtype, rconf = _render_rotation_config("new_task")

        st.markdown("---")

        # Slot editor for new task
        new_slots = _render_slot_editor("new_task_slots")

        st.markdown("---")

        if st.button("✅ צור משימה", key="create_task_btn", disabled=readonly):
            if not name:
                st.error("יש להזין שם למשימה")
            elif not new_slots or not any(s.get("slot_name") for s in new_slots):
                st.error("יש להגדיר לפחות תפקיד אחד למשימה")
            else:
                total_personnel = sum(s.get("quantity", 1) for s in new_slots)
                task = create_task(
                    period_id=pid,
                    name=name,
                    personnel_per_shift=total_personnel,
                    shifts_per_day=shifts,
                    shift_times=shift_times or None,
                    notes=notes or None,
                    non_continuous=nc,
                    rotation_type=rtype,
                    rotation_config=rconf,
                )
                # Add slots
                for i, sd in enumerate(new_slots):
                    if sd.get("slot_name"):
                        add_task_slot(
                            task_id=task.id,
                            slot_name=sd["slot_name"],
                            quantity=sd.get("quantity", 1),
                            allowed_roles=sd.get("allowed_roles", []),
                            slot_order=i,
                        )
                # Clear session state
                if "new_task_slots" in st.session_state:
                    del st.session_state["new_task_slots"]
                st.success(f"המשימה '{name}' נוצרה עם {len(new_slots)} תפקידים!")
                st.rerun()

    # ── Fairness report ──
    with tab_fairness:
        st.markdown("### ⚖️ טבלת צדק — חלוקת שמירות ומשימות")
        st.caption("פירוט מלא: כמות משמרות, סוג (בוקר/צהריים/לילה), לפי משימה, ותפקיד. חיילים חריגים מודגשים.")

        try:
            report_data, task_names = get_detailed_fairness_report(pid)
        except Exception:
            report_data, task_names = [], []

        if report_data:
            import pandas as pd
            import numpy as np

            # Summary metrics
            total_shifts = sum(s["total_shifts"] for s in report_data)
            soldiers_with_shifts = sum(1 for s in report_data if s["total_shifts"] > 0)
            max_shifts = max(s["total_shifts"] for s in report_data) if report_data else 0
            min_shifts = min(s["total_shifts"] for s in report_data if s["total_shifts"] > 0) if soldiers_with_shifts else 0
            active_counts = [s["total_shifts"] for s in report_data if s["total_shifts"] > 0]
            avg_shifts = sum(active_counts) / len(active_counts) if active_counts else 0

            mc1, mc2, mc3, mc4, mc5 = st.columns(5)
            mc1.metric("סה\"כ שיבוצים", total_shifts)
            mc2.metric("חיילים ששובצו", f"{soldiers_with_shifts}/{len(report_data)}")
            mc3.metric("מקסימום לחייל", max_shifts)
            mc4.metric("מינימום לחייל (פעיל)", min_shifts)
            mc5.metric("ממוצע", f"{avg_shifts:.1f}")

            if max_shifts > 0 and min_shifts > 0:
                fairness_ratio = min_shifts / max_shifts
                st.progress(fairness_ratio, text=f"מדד צדק: {fairness_ratio:.0%} (מינימום/מקסימום)")

            # Outlier detection
            if active_counts and len(active_counts) >= 3:
                std_dev = np.std(active_counts)
                threshold_high = avg_shifts + 1.5 * std_dev
                threshold_low = avg_shifts - 1.5 * std_dev
                outliers_high = [s for s in report_data if s["total_shifts"] > threshold_high]
                outliers_low = [s for s in report_data if 0 < s["total_shifts"] < max(threshold_low, 1)]
                if outliers_high:
                    st.warning(
                        f"⚠️ **חיילים עם עומס חריג ({len(outliers_high)}):** "
                        + ", ".join(f"{s['full_name']} ({s['total_shifts']})" for s in outliers_high)
                    )
                if outliers_low:
                    st.info(
                        f"💡 **חיילים עם מעט שיבוצים ({len(outliers_low)}):** "
                        + ", ".join(f"{s['full_name']} ({s['total_shifts']})" for s in outliers_low)
                    )

            st.markdown("---")

            # Build main DataFrame
            rows = []
            for s in report_data:
                row = {
                    "שם מלא": s["full_name"],
                    "יחידה": s["sub_unit"],
                    "תפקיד": s["role"],
                    "סה\"כ": s["total_shifts"],
                    "☀️ בוקר": s["morning_shifts"],
                    "🌤️ צהריים": s["afternoon_shifts"],
                    "🌙 לילה": s["night_shifts"],
                    "🎖️ קצין תורן": s["duty_officer"],
                }
                # Add per-task columns
                for tn in task_names:
                    row[f"📋 {tn}"] = s["tasks"].get(tn, 0)
                rows.append(row)

            df = pd.DataFrame(rows)
            df = df.sort_values("סה\"כ", ascending=False)

            def _highlight_outliers(val, col_name, avg, high_th, low_th):
                """Return background color for outlier cells."""
                if col_name != "סה\"כ" or not isinstance(val, (int, float)):
                    return ""
                if val > high_th:
                    return "background-color: #FFCDD2; font-weight: bold"  # red
                elif 0 < val < max(low_th, 1):
                    return "background-color: #C8E6C9"  # green — doing less
                return ""

            # View options
            view_mode = st.radio(
                "תצוגה",
                ["📊 טבלה מלאה", "📈 לפי סוג משמרת", "📋 לפי משימה", "🏷️ לפי תפקיד במשמרת"],
                horizontal=True,
                key="fairness_view",
            )

            # Compute thresholds for styling
            if active_counts and len(active_counts) >= 3:
                _std = np.std(active_counts)
                _high = avg_shifts + 1.5 * _std
                _low = avg_shifts - 1.5 * _std
            else:
                _high, _low = 9999, -1

            def _style_df(frame):
                return frame.style.applymap(
                    lambda v: _highlight_outliers(v, "סה\"כ", avg_shifts, _high, _low),
                    subset=["סה\"כ"] if "סה\"כ" in frame.columns else [],
                )

            if view_mode == "📊 טבלה מלאה":
                st.dataframe(_style_df(df), use_container_width=True, hide_index=True)

            elif view_mode == "📈 לפי סוג משמרת":
                cols_to_show = ["שם מלא", "יחידה", "סה\"כ", "☀️ בוקר", "🌤️ צהריים", "🌙 לילה", "🎖️ קצין תורן"]
                st.dataframe(_style_df(df[cols_to_show]), use_container_width=True, hide_index=True)

            elif view_mode == "📋 לפי משימה":
                task_cols = ["שם מלא", "יחידה", "סה\"כ"] + [f"📋 {tn}" for tn in task_names]
                available_cols = [c for c in task_cols if c in df.columns]
                st.dataframe(_style_df(df[available_cols]), use_container_width=True, hide_index=True)

            elif view_mode == "🏷️ לפי תפקיד במשמרת":
                # Build role breakdown table
                all_roles = set()
                for s in report_data:
                    all_roles.update(s["roles"].keys())
                all_roles = sorted(all_roles)

                if all_roles:
                    role_rows = []
                    for s in report_data:
                        rrow = {
                            "שם מלא": s["full_name"],
                            "יחידה": s["sub_unit"],
                            "סה\"כ": s["total_shifts"],
                        }
                        for r in all_roles:
                            rrow[f"🎭 {r}"] = s["roles"].get(r, 0)
                        role_rows.append(rrow)
                    role_df = pd.DataFrame(role_rows).sort_values("סה\"כ", ascending=False)
                    st.dataframe(_style_df(role_df), use_container_width=True, hide_index=True)
                else:
                    st.info("אין נתוני תפקידים במשמרות")

            # Filter by unit
            st.markdown("---")
            st.markdown("##### 🔍 סינון לפי יחידה")
            units_in_report = sorted(df["יחידה"].unique())
            selected_unit = st.selectbox(
                "יחידה",
                ["הכל"] + list(units_in_report),
                key="fairness_unit_filter",
            )
            if selected_unit != "הכל":
                filtered = df[df["יחידה"] == selected_unit]
                st.dataframe(_style_df(filtered), use_container_width=True, hide_index=True)

        else:
            st.info("אין נתוני שיבוצים עדיין")
