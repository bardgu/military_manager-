"""Shifts page — daily shift assignment board (שבצ"ק) with role-based slot filtering,
auto-assignment, and visual Gantt-like schedule view."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import streamlit as st
from streamlit_sortables import sort_items

from military_manager.components.navigation import render_page_header
from military_manager.components.filters import period_guard, single_date_selector
from military_manager.services.task_service import (
    get_period_tasks,
    get_daily_assignments,
    get_task_slots,
    assign_shift,
    remove_shift_assignment,
    set_duty_officer,
    get_duty_officer_eligible,
    get_eligible_soldiers_for_roles,
    _soldier_matches_roles,
    _slot_requires_driver,
    auto_assign_day,
    auto_assign_range,
    get_multi_day_schedule,
    get_minimum_soldiers_needed,
    get_available_soldiers_count,
    get_forward_capacity,
    DUTY_OFFICER_ROLES,
    get_carmel_recommendation,
    link_carmel_to_patrol,
    set_carmel_mode,
    get_linked_task,
)
from military_manager.services.soldier_service import get_period_soldiers
from military_manager.services.status_service import get_daily_counts
from military_manager.services.constraint_service import (
    get_constraints_for_date,
    is_soldier_available,
    SHIFT_LABEL_HEB,
)

# Hebrew day names
HEB_DAYS = {
    0: "שני", 1: "שלישי", 2: "רביעי", 3: "חמישי",
    4: "שישי", 5: "שבת", 6: "ראשון",
}


def render():
    render_page_header("📋 שבצ\"ק", "שיבוץ חיילים למשמרות — ידני / אוטומטי / תצוגה ויזואלית")

    period = period_guard()
    if not period:
        return

    pid = period["id"]

    try:
        p_start = datetime.strptime(period["start_date"], "%Y-%m-%d").date()
        p_end = datetime.strptime(period["end_date"], "%Y-%m-%d").date()
    except (ValueError, KeyError):
        p_start = date.today()
        p_end = date.today() + timedelta(days=21)

    tab_manual, tab_auto, tab_visual, tab_capacity, tab_weekly = st.tabs([
        "✋ שיבוץ ידני",
        "🤖 שיבוץ אוטומטי",
        "📊 תצוגה ויזואלית",
        "📈 סד\"כ וזמינות",
        "📅 סיכום שבועי",
    ])

    # ── Manpower summary bar (always visible) ──
    _render_manpower_bar(pid, p_start)

    with tab_manual:
        _render_manual_assignment(pid, p_start, p_end)

    with tab_auto:
        _render_auto_assignment(pid, p_start, p_end)

    with tab_visual:
        _render_visual_schedule(pid, p_start, p_end)

    with tab_capacity:
        _render_capacity_view(pid, p_start, p_end)

    with tab_weekly:
        _render_weekly_summary(pid, p_start, p_end)


def _render_manpower_bar(pid: int, today: date):
    """Always-visible bar: min soldiers needed vs available."""
    try:
        min_info = get_minimum_soldiers_needed(pid)
        avail_info = get_available_soldiers_count(pid, today)
    except Exception:
        return

    min_needed = min_info["min_needed"]
    total_daily_slots = min_info["total_slots_per_day"]
    available = avail_info["available"]
    total = avail_info["total"]
    deficit = max(0, min_needed - available)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("📋 מינימום נדרש (בו-זמנית)", min_needed)
    c2.metric("📋 סה\"כ שיבוצים יומי", total_daily_slots,
              help="סה\"כ כמה שיבוצי-חייל-למשמרת נדרשים ביום (כולל כל המשמרות)")
    if deficit > 0:
        c3.metric("👤 זמינים היום", available, delta=f"-{deficit} חוסר!", delta_color="inverse")
    else:
        c3.metric("👤 זמינים היום", available, delta=f"+{available - min_needed} עודף", delta_color="normal")
    c4.metric("📊 סה\"כ חיילים (רלוונטיים)", total)
    c5.metric("🚫 לא זמינים", avail_info["on_leave"])

    irrelevant = avail_info.get("irrelevant", 0)
    if irrelevant > 0:
        st.caption(f"ℹ️ {irrelevant} חיילים סומנו כ'לא רלוונטיים' ולא נספרים.")

    if deficit > 0:
        st.error(f"⚠️ **חריגה! חסרים {deficit} חיילים** — לא ניתן למלא את כל המשימות. "
                 f"בדוק שלא שוחררו יותר מדי חיילים.")
    st.markdown("---")


# ╔══════════════════════════════════════════════════════════════╗
# ║                    MANUAL ASSIGNMENT                         ║
# ╚══════════════════════════════════════════════════════════════╝

def _render_manual_assignment(pid: int, p_start, p_end):
    """Original manual shift assignment UI."""
    col1, col2 = st.columns([2, 4])
    with col1:
        selected_date = single_date_selector(p_start, p_end, key="shift_date")
    with col2:
        st.write("")

    st.markdown("---")

    all_soldiers = get_period_soldiers(pid)
    soldier_map = {s["soldier_id"]: s for s in all_soldiers}

    tasks = get_period_tasks(pid)
    active_tasks = [t for t in tasks if t.is_active]
    assignments = get_daily_assignments(pid, selected_date)

    assigned_ids = set()
    for task_name, task_data in assignments.items():
        for key, value in task_data.items():
            if isinstance(key, int) and isinstance(value, list):
                for s in value:
                    assigned_ids.add(s.get("soldier_id"))

    date_constraints = get_constraints_for_date(pid, selected_date)
    if date_constraints:
        with st.expander(f"⚠️ {len(date_constraints)} חיילים עם אילוצים ביום זה", expanded=False):
            for sid, blocked_shifts in date_constraints.items():
                s_info = soldier_map.get(sid)
                if s_info:
                    blocked_names = ", ".join(
                        SHIFT_LABEL_HEB.get(sn, str(sn)) for sn in sorted(blocked_shifts)
                    )
                    st.markdown(f"- **{s_info['full_name']}** — חסום: {blocked_names}")

    # Duty Officer
    st.markdown("### 🎖️ קצין תורן")
    st.caption("רק מ\"פ, סמ\"פ, מ\"מ, רס\"פ או ע.מ\"פ")

    eligible_officers = get_duty_officer_eligible(pid)
    if not eligible_officers:
        st.warning("אין קצינים מתאימים לתפקיד קצין תורן")
    else:
        officer_options = {s["soldier_id"]: f"{s['full_name']} ({s.get('role', '')})" for s in eligible_officers}
        duty_options = ["—"] + list(officer_options.values())
        duty_selection = st.selectbox("בחר קצין תורן ליום", duty_options, key="duty_officer")

        if duty_selection != "—":
            duty_sid = next(
                (sid for sid, name in officer_options.items() if name == duty_selection), None,
            )
            if duty_sid and st.button("✅ קבע קצין תורן", key="set_duty"):
                set_duty_officer(pid, selected_date, duty_sid)
                st.success(f"קצין תורן: {duty_selection}")
                st.rerun()

    st.markdown("---")

    if not active_tasks:
        st.info("אין משימות פעילות. הגדר משימות בעמוד 'משימות'.")
        return

    st.markdown(f"### 📋 שיבוצים ליום {selected_date.strftime('%d/%m/%Y')}")

    # ── Soldier availability bank — summary metrics + full table ──
    import pandas as pd

    available_soldiers = [s for s in all_soldiers if s["soldier_id"] not in assigned_ids]
    available_count = len(available_soldiers)
    constrained_count = len(date_constraints) if date_constraints else 0
    assigned_count = len(assigned_ids)

    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("סה\"כ חיילים", len(all_soldiers))
    mc2.metric("🟢 זמינים", available_count)
    mc3.metric("🔴 משובצים", assigned_count)
    mc4.metric("🟡 אילוצים", constrained_count)

    bank_rows = []
    for s in all_soldiers:
        sid = s["soldier_id"]
        is_assigned = sid in assigned_ids
        blocked = date_constraints.get(sid, set()) if date_constraints else set()
        blocked_labels = ", ".join(
            SHIFT_LABEL_HEB.get(sn, str(sn)) for sn in sorted(blocked)
        ) if blocked else ""

        if is_assigned:
            status = "🔴 משובץ"
        elif blocked:
            status = "🟡 אילוץ חלקי"
        else:
            status = "🟢 זמין"

        bank_rows.append({
            "סטטוס": status,
            "שם מלא": s.get("full_name", ""),
            "תפקיד": s.get("role", "") or "",
            "תפקיד משימתי": s.get("task_role", "") or "",
            "מחלקה": s.get("sub_unit", "") or "",
            "משמרות חסומות": blocked_labels,
        })

    sort_order = {"🟢 זמין": 0, "🟡 אילוץ חלקי": 1, "🔴 משובץ": 2}
    bank_rows.sort(key=lambda r: (sort_order.get(r["סטטוס"], 9), r["שם מלא"]))
    df_bank = pd.DataFrame(bank_rows)

    # Show available-only by default, with toggle to see all
    show_all = st.checkbox("הצג את כל החיילים (כולל משובצים)", value=False, key="bank_show_all")
    if not show_all:
        df_bank = df_bank[df_bank["סטטוס"] != "🔴 משובץ"]

    st.dataframe(df_bank, use_container_width=True, hide_index=True, height=300,
                 column_config={
                     "שם מלא": st.column_config.TextColumn("שם מלא", pinned=True),
                     "מחלקה": st.column_config.TextColumn("מחלקה", pinned=True),
                 })

    st.markdown("---")

    for task in active_tasks:
        task_data = assignments.get(task.name, {"task_id": task.id, "shifts_per_day": task.shifts_per_day, "slots": []})
        task_slots = task_data.get("slots", [])

        if task_slots:
            slot_desc = " | ".join(
                f"{s['slot_name']}×{s['quantity']}" if s['quantity'] > 1 else s['slot_name']
                for s in task_slots
            )
        else:
            slot_desc = f"{task.personnel_per_shift} חיילים"

        st.markdown(f"#### 🎯 {task.name}")
        st.caption(f"תפקידים: {slot_desc} · {task.shifts_per_day} משמרות")

        for shift_num in range(1, task.shifts_per_day + 1):
            shift_soldiers = task_data.get(shift_num, []) if isinstance(task_data.get(shift_num), list) else []

            time_label = ""
            if task.shift_times:
                try:
                    times = json.loads(task.shift_times) if isinstance(task.shift_times, str) else task.shift_times
                    if isinstance(times, list) and shift_num <= len(times):
                        time_label = f" ({times[shift_num - 1]})"
                except (json.JSONDecodeError, TypeError):
                    time_label = ""

            st.markdown(f"**משמרת {shift_num}{time_label}**")

            if task_slots:
                _render_slot_based_shift(
                    task, task_slots, shift_num, shift_soldiers,
                    all_soldiers, assigned_ids, pid, selected_date,
                    date_constraints,
                )
            else:
                _render_legacy_shift(
                    task, shift_num, shift_soldiers,
                    all_soldiers, assigned_ids, pid, selected_date,
                )

        st.markdown("---")

    st.markdown("### 📊 סיכום יומי")
    counts = get_daily_counts(pid, selected_date)
    if counts:
        cols = st.columns(min(len(counts), 6))
        for i, (status, count) in enumerate(sorted(counts.items(), key=lambda x: -x[1])):
            cols[i % len(cols)].metric(status, count)

    # ── Group-based summary ──
    from military_manager.services.stats_service import compute_percentages, get_setting
    day_stats = compute_percentages(pid, selected_date)
    if day_stats and day_stats["groups"]:
        st.markdown("### 📊 סיכום לפי קבוצות")
        threshold = float(get_setting(pid, "home_alert_percent", "25"))
        grp_cols = st.columns(min(len(day_stats["groups"]), 5))
        for i, (grp_name, grp_data) in enumerate(day_stats["groups"].items()):
            with grp_cols[i % len(grp_cols)]:
                pct = grp_data["percent"]
                is_alert = grp_name == "בחופש" and pct > threshold
                if is_alert:
                    st.markdown(
                        f'<div style="background:#FFCDD2;padding:10px;border-radius:8px;text-align:center;">'
                        f'<b style="color:#B71C1C;font-size:1.3em;">{pct}%</b><br>'
                        f'<span style="color:#B71C1C;">{grp_name}: {grp_data["count"]}</span></div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.metric(f"{grp_name}", f"{grp_data['count']}  ({pct}%)")


# ╔══════════════════════════════════════════════════════════════╗
# ║                 CARMEL-PATROL INFO                           ║
# ╚══════════════════════════════════════════════════════════════╝

def _show_carmel_info(pid: int, ref_date):
    """Smart Carmel-Patrol panel: auto-detects tasks, auto-links, shows
    recommendation, and lets user pick mode — all inside the auto-assignment page.

    If no כרמל or סיור tasks exist, shows nothing.
    """
    tasks = get_period_tasks(pid, active_only=True)

    # Auto-detect: find tasks with כרמל and סיור in their name
    carmel_task = next((t for t in tasks if "כרמל" in t.name), None)
    patrol_task = next((t for t in tasks if "סיור" in t.name), None)

    if not carmel_task or not patrol_task:
        return  # No carmel+patrol pair — nothing to show

    # Auto-link if not already linked
    if not carmel_task.linked_task_id or carmel_task.linked_task_id != patrol_task.id:
        link_carmel_to_patrol(carmel_task.id, patrol_task.id, mode="auto")

    # Get recommendation
    rec = get_carmel_recommendation(pid, ref_date)
    if not rec:
        return

    mode_labels = {
        "auto": "🤖 אוטומטי — המערכת מחליטה לפי סד\"כ זמין",
        "shared": "🔄 סד\"כ מצומצם — אותם חיילים מתחלפים כרמל↔סיור",
        "separate": "👥 סד\"כ מלא — חיילים שונים לכל משימה",
    }
    rec_display = {
        "shared": "🔄 **סד\"כ מצומצם** — אותם חיילים יתחלפו בין כרמל לסיור",
        "separate": "👥 **סד\"כ מלא** — יש מספיק חיילים להפריד",
    }

    with st.expander(
        f"🔗 כרמל-סיור: {rec['carmel_task']} ↔ {rec['patrol_task']}",
        expanded=True,
    ):
        # Metrics
        c1, c2, c3 = st.columns(3)
        c1.metric("👤 חיילים זמינים למשימות", rec["available_soldiers"])
        c2.metric(
            f"📋 נדרש (מצומצם)",
            rec["needed_shared"],
            help="אם אותם חיילים עושים גם כרמל וגם סיור",
        )
        c3.metric(
            f"📋 נדרש (מלא)",
            rec["needed_separate"],
            help="אם חיילים שונים לכל משימה",
        )

        # Recommendation
        st.info(f"💡 המלצת המערכת: {rec_display.get(rec['recommended'], rec['recommended'])}")

        # Mode selector
        current_mode = rec["mode"]
        mode_options = ["auto", "shared", "separate"]
        current_idx = mode_options.index(current_mode) if current_mode in mode_options else 0

        new_mode = st.radio(
            "גישת שיבוץ כרמל-סיור:",
            mode_options,
            format_func=lambda x: mode_labels[x],
            index=current_idx,
            horizontal=False,
            key="carmel_mode_shift",
        )

        if new_mode != current_mode:
            set_carmel_mode(carmel_task.id, new_mode)
            st.rerun()

        # Task details side by side
        carmel_slots = get_task_slots(carmel_task.id)
        patrol_slots = get_task_slots(patrol_task.id)
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**{carmel_task.name}** — {rec['carmel_per_shift']} חיילים/משמרת")
            for s in carmel_slots:
                st.caption(f"• {s['slot_name']} ×{s['quantity']}")
        with c2:
            st.markdown(f"**{patrol_task.name}** — {rec['patrol_per_shift']} חיילים/משמרת")
            for s in patrol_slots:
                st.caption(f"• {s['slot_name']} ×{s['quantity']}")

        st.caption("💡 ניתן להתעלם מהגדרה זו — רלוונטי רק לשיבוץ אוטומטי. בשיבוץ ידני אתה בונה בעצמך.")


# ╔══════════════════════════════════════════════════════════════╗
# ║                    AUTO ASSIGNMENT                           ║
# ╚══════════════════════════════════════════════════════════════╝

def _render_auto_assignment(pid: int, p_start, p_end):
    """Auto-assignment UI — single day or range."""
    st.markdown("### 🤖 שיבוץ אוטומטי")
    st.markdown(
        "המערכת תשבץ אוטומטית חיילים למשמרות לפי:\n"
        "- **התאמת תפקיד** — כל חייל לסלוט מתאים\n"
        "- **אילוצי זמינות** — לא ישובץ מי שלא זמין\n"
        "- **טבלת צדק** — חיילים עם פחות משמרות יקבלו עדיפות\n"
        "- **משמרות לילה** — פיזור שוויוני של לילות\n"
        "- **🔗 כרמל-סיור** — רוטציה חכמה בין כיתת כוננות לסיור\n\n"
        "**אחרי השיבוץ האוטומטי, אפשר לערוך ידנית בלשונית \'שיבוץ ידני\'.**"
    )

    # Show Carmel-Patrol recommendation if a link exists
    _show_carmel_info(pid, p_start)

    st.markdown("---")

    mode = st.radio(
        "בחר מצב",
        ["📅 יום בודד", "📆 טווח ימים"],
        horizontal=True,
        key="auto_mode",
    )

    if mode == "📅 יום בודד":
        auto_date = st.date_input(
            "תאריך לשיבוץ",
            value=p_start,
            min_value=p_start,
            max_value=p_end,
            key="auto_single_date",
        )

        clear_existing = st.checkbox(
            "🗑️ מחק שיבוצים קיימים לפני השיבוץ האוטומטי",
            value=False,
            key="auto_clear_single",
        )

        st.markdown("")

        if st.button("🚀 הפעל שיבוץ אוטומטי", type="primary", key="run_auto_single"):
            with st.spinner("משבץ..."):
                result = auto_assign_day(pid, auto_date, clear_existing=clear_existing)
            st.session_state["auto_result"] = result
            st.session_state["auto_result_date"] = str(auto_date)
            st.rerun()

        if st.session_state.get("auto_result") and st.session_state.get("auto_result_date") == str(auto_date):
            _show_auto_result(st.session_state["auto_result"])

    else:
        c1, c2 = st.columns(2)
        with c1:
            range_start = st.date_input(
                "מתאריך",
                value=p_start,
                min_value=p_start,
                max_value=p_end,
                key="auto_range_start",
            )
        with c2:
            range_end = st.date_input(
                "עד תאריך",
                value=min(p_start + timedelta(days=6), p_end),
                min_value=p_start,
                max_value=p_end,
                key="auto_range_end",
            )

        clear_existing = st.checkbox(
            "🗑️ מחק שיבוצים קיימים לפני השיבוץ האוטומטי",
            value=False,
            key="auto_clear_range",
        )

        num_days = (range_end - range_start).days + 1
        st.caption(f"סה\"כ {num_days} ימים")

        if st.button(f"🚀 שבץ אוטומטית {num_days} ימים", type="primary", key="run_auto_range"):
            progress = st.progress(0, text="מתחיל שיבוץ...")
            all_results = []
            current = range_start
            day_num = 0
            total = num_days
            while current <= range_end:
                day_num += 1
                progress.progress(
                    day_num / total,
                    text=f"משבץ יום {day_num}/{total} — {current.strftime('%d/%m')}",
                )
                result = auto_assign_day(pid, current, clear_existing=clear_existing)
                result["date"] = current
                all_results.append(result)
                current += timedelta(days=1)

            progress.progress(1.0, text="✅ השיבוץ הושלם!")
            st.session_state["auto_range_results"] = all_results
            st.rerun()

        if "auto_range_results" in st.session_state:
            results = st.session_state["auto_range_results"]
            total_assigned = sum(r["total_assigned"] for r in results)
            total_unassigned = sum(r["total_unassigned"] for r in results)

            mc1, mc2, mc3 = st.columns(3)
            mc1.metric("✅ שובצו", total_assigned)
            mc2.metric("⚠️ לא שובצו", total_unassigned)
            mc3.metric("📅 ימים", len(results))

            for r in results:
                d = r.get("date", "?")
                date_str = d.strftime('%d/%m') if hasattr(d, 'strftime') else str(d)
                icon = "✅" if r["total_unassigned"] == 0 else "⚠️"
                with st.expander(
                    f"{icon} {date_str} — {r['total_assigned']} שובצו, {r['total_unassigned']} חסרים",
                    expanded=r["total_unassigned"] > 0,
                ):
                    _show_auto_result(r)


def _show_auto_result(result: dict):
    """Display auto-assignment results."""
    mc1, mc2 = st.columns(2)
    mc1.metric("✅ שובצו", result["total_assigned"])
    mc2.metric("⚠️ לא שובצו", result["total_unassigned"])

    if result["assigned"]:
        with st.expander(f"✅ {result['total_assigned']} שיבוצים:", expanded=False):
            for name, task_name, shift_num, slot_name in result["assigned"]:
                shift_heb = SHIFT_LABEL_HEB.get(shift_num, str(shift_num))
                st.markdown(f"- **{name}** → {task_name} · משמרת {shift_heb} · {slot_name}")

    if result["unassigned"]:
        st.markdown("**⚠️ לא ניתן למלא:**")
        for task_name, shift_num, slot_name, reason in result["unassigned"]:
            shift_heb = SHIFT_LABEL_HEB.get(shift_num, str(shift_num))
            st.warning(f"🎯 {task_name} · משמרת {shift_heb} · {slot_name} — {reason}")


# ╔══════════════════════════════════════════════════════════════╗
# ║                  VISUAL SCHEDULE VIEW                        ║
# ╚══════════════════════════════════════════════════════════════╝

def _render_visual_schedule(pid: int, p_start, p_end):
    """Visual Gantt-like schedule view — daily/weekly table."""
    st.markdown("### 📊 תצוגה ויזואלית — לוח שיבוצים")

    view_mode = st.radio(
        "טווח תצוגה",
        ["📅 יום בודד", "📆 שבוע", "🗓️ טווח מותאם"],
        horizontal=True,
        key="visual_mode",
    )

    if view_mode == "📅 יום בודד":
        vis_start = st.date_input(
            "תאריך", value=p_start, min_value=p_start, max_value=p_end, key="vis_date",
        )
        vis_end = vis_start
    elif view_mode == "📆 שבוע":
        vis_start = st.date_input(
            "תחילת שבוע", value=p_start, min_value=p_start, max_value=p_end, key="vis_week_start",
        )
        vis_end = min(vis_start + timedelta(days=6), p_end)
    else:
        c1, c2 = st.columns(2)
        with c1:
            vis_start = st.date_input(
                "מתאריך", value=p_start, min_value=p_start, max_value=p_end, key="vis_range_start",
            )
        with c2:
            vis_end = st.date_input(
                "עד", value=min(p_start + timedelta(days=6), p_end),
                min_value=p_start, max_value=p_end, key="vis_range_end",
            )

    st.markdown("---")

    schedule = get_multi_day_schedule(pid, vis_start, vis_end)

    if not schedule:
        st.info("אין נתונים לטווח זה.")
        return

    num_days = len(schedule)

    if num_days == 1:
        _render_single_day_visual(schedule[0])
    else:
        _render_multi_day_visual(schedule)


def _render_single_day_visual(day_data: dict):
    """Detailed single-day visual schedule — colored cards per shift with inline editing."""
    d = day_data["date"]
    day_heb = HEB_DAYS.get(d.weekday(), "")
    st.markdown(f"#### 📅 יום {day_heb} — {d.strftime('%d/%m/%Y')}")

    if day_data.get("duty_officer"):
        st.markdown(f"🎖️ **קצין תורן:** {day_data['duty_officer']}")

    edit_col1, edit_col2, edit_col3 = st.columns([1, 1, 1])
    with edit_col1:
        view_choice = st.radio(
            "מצב תצוגה",
            ["👁️ צפייה", "✏️ עריכה", "🖐️ גרור ושחרר"],
            horizontal=True,
            key="visual_view_choice",
        )
    edit_mode = view_choice == "✏️ עריכה"
    dnd_mode = view_choice == "🖐️ גרור ושחרר"

    tasks = day_data.get("tasks", {})
    if not tasks:
        st.info("אין משימות.")
        return

    if dnd_mode:
        _render_dnd_daily(day_data)
        return

    # If edit mode, load soldiers for eligibility checks
    pid = day_data.get("period_id")
    all_soldiers_map = {}
    if edit_mode and pid:
        all_soldiers_list = get_period_soldiers(pid)
        all_soldiers_map = {s["soldier_id"]: s for s in all_soldiers_list}

    for task_name, task_info in tasks.items():
        shift_times = task_info.get("shift_times", [])
        shifts = task_info.get("shifts", {})
        shifts_per_day = task_info.get("shifts_per_day", 1)
        task_id = task_info.get("task_id")

        st.markdown(f"##### 🎯 {task_name}")

        cols = st.columns(shifts_per_day)

        for sn in range(1, shifts_per_day + 1):
            with cols[sn - 1]:
                time_str = shift_times[sn - 1] if sn <= len(shift_times) else ""
                shift_heb = SHIFT_LABEL_HEB.get(sn, f"משמרת {sn}")

                if sn == 1:
                    color, border = "#FFF3E0", "#FF9800"
                elif sn == 2:
                    color, border = "#E3F2FD", "#2196F3"
                else:
                    color, border = "#EDE7F6", "#673AB7"

                soldiers = shifts.get(sn, [])

                header = f"{shift_heb}"
                if time_str:
                    header += f" ({time_str})"

                if not edit_mode:
                    # Read-only display (original)
                    soldier_html = ""
                    if soldiers:
                        for sol in soldiers:
                            slot = sol.get("slot_name", "")
                            badge = f" · {slot}" if slot else ""
                            soldier_html += f"<div style='padding:2px 0;'>👤 <b>{sol['name']}</b>{badge}</div>"
                    else:
                        soldier_html = "<div style='color:#999; padding:4px;'>ריק</div>"

                    st.markdown(
                        f"<div style='background:{color}; border:2px solid {border}; "
                        f"border-radius:10px; padding:12px; margin:4px 0; min-height:100px;'>"
                        f"<div style='text-align:center; font-weight:bold; color:{border}; "
                        f"margin-bottom:8px; font-size:14px;'>{header}</div>"
                        f"{soldier_html}</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    # Edit mode: show selectboxes for each soldier slot
                    st.markdown(
                        f"<div style='background:{color}; border:2px solid {border}; "
                        f"border-radius:10px; padding:8px; margin:4px 0;'>"
                        f"<div style='text-align:center; font-weight:bold; color:{border}; "
                        f"font-size:13px;'>{header}</div></div>",
                        unsafe_allow_html=True,
                    )
                    _render_visual_edit_shift(
                        task_id, task_name, sn, soldiers, d, pid
                    )

        st.markdown("---")


def _render_visual_edit_shift(task_id, task_name, shift_num, soldiers, day, pid,
                              key_prefix: str = ""):
    """Render inline editing controls for a single shift in the visual view.
    
    Shows each assigned soldier with a selectbox to swap them with an eligible replacement.
    Role validation ensures only qualified soldiers appear in the dropdown.
    key_prefix: optional prefix for widget keys to avoid collisions in multi-day edit mode.
    """
    if not task_id or not pid:
        for sol in soldiers:
            st.caption(f"👤 {sol['name']} · {sol.get('slot_name', '')}")
        return

    task_slots = get_task_slots(task_id)
    slot_map = {s["id"]: s for s in task_slots}

    # Load eligible soldiers for this task
    all_soldiers_list = get_period_soldiers(pid)

    # Get all currently assigned soldier IDs for this day (to avoid double-assignment)
    from military_manager.services.constraint_service import get_constraints_for_date
    date_constraints = get_constraints_for_date(pid, day)

    # Pre-load qualifications and approved drivers once (outside the loop)
    from military_manager.services.task_service import _load_soldier_qualifications_map
    qual_map = _load_soldier_qualifications_map(pid)
    from military_manager.services.driver_service import get_approved_driver_ids
    approved_drivers = get_approved_driver_ids(pid)

    for i, sol in enumerate(soldiers):
        slot_id = sol.get("slot_id")
        slot_name = sol.get("slot_name", "")
        slot_info = slot_map.get(slot_id, {})
        allowed_roles = slot_info.get("allowed_roles", [])
        is_driver = _slot_requires_driver(allowed_roles) if allowed_roles else False

        eligible = []
        for s in all_soldiers_list:
            sid = s["soldier_id"]
            if sid == sol["soldier_id"]:
                continue  # skip self (will be shown as current)
            if sid in date_constraints and shift_num in date_constraints.get(sid, set()):
                continue
            if is_driver:
                if sid not in approved_drivers:
                    continue
            elif allowed_roles:
                soldier_quals = qual_map.get(sid, [])
                if not _soldier_matches_roles(s, allowed_roles, soldier_quals):
                    continue
            eligible.append(s)

        options = [f"👤 {sol['name']} (נוכחי)"] + [
            f"{s['full_name']} ({s.get('role', '')})" for s in eligible
        ]

        key = f"{key_prefix}_vis_edit_{task_id}_{shift_num}_{i}_{day}"
        selected = st.selectbox(
            f"{slot_name}" if slot_name else f"חייל {i+1}",
            options=options,
            index=0,
            key=key,
            label_visibility="collapsed",
        )

        if selected != options[0]:
            # User selected a replacement
            new_soldier = next(
                (s for s in eligible if f"{s['full_name']} ({s.get('role', '')})" == selected),
                None
            )
            if new_soldier:
                btn_key = f"{key_prefix}_vis_swap_{task_id}_{shift_num}_{i}_{day}"
                if st.button(f"🔄 החלף → {new_soldier['full_name']}", key=btn_key):
                    # Remove old assignment and add new
                    remove_shift_assignment(task_id, day, shift_num, sol["soldier_id"])
                    try:
                        assign_shift(
                            task_id, day, shift_num, new_soldier["soldier_id"],
                            task_slot_id=slot_id,
                            assigned_by="visual-edit",
                        )
                        st.success(f"✅ {sol['name']} → {new_soldier['full_name']}")
                        st.rerun()
                    except ValueError as e:
                        # Revert: re-assign original
                        try:
                            assign_shift(
                                task_id, day, shift_num, sol["soldier_id"],
                                task_slot_id=slot_id,
                                assigned_by="visual-revert",
                            )
                        except ValueError:
                            pass
                        st.error(f"שגיאה: {e}")

    # Show empty slots
    if task_slots:
        slot_fill_count = {}
        for sol in soldiers:
            sid = sol.get("slot_id")
            slot_fill_count[sid] = slot_fill_count.get(sid, 0) + 1
        
        for slot in task_slots:
            filled = slot_fill_count.get(slot["id"], 0)
            needed = slot.get("quantity", 1) - filled
            if needed > 0:
                st.caption(f"⚠️ {slot['slot_name']} — חסרים {needed}")


def _render_multi_day_visual(schedule: list[dict]):
    """Multi-day Gantt-like visual — HTML table with days as columns."""
    st.markdown("#### 📆 לוח שיבוצים")

    all_tasks = set()
    for day in schedule:
        all_tasks.update(day.get("tasks", {}).keys())
    all_tasks = sorted(all_tasks)

    if not all_tasks:
        st.info("אין משימות מוגדרות.")
        return

    ec1, ec2 = st.columns([3, 1])
    with ec2:
        edit_choice = st.radio(
            "מצב",
            ["👁️ צפייה", "✏️ עריכה", "🖐️ גרור ושחרר"],
            horizontal=True,
            key="multi_edit_choice",
        )
    edit_mode = edit_choice == "✏️ עריכה"
    dnd_mode = edit_choice == "🖐️ גרור ושחרר"

    if dnd_mode:
        st.info("💡 בחר יום לעריכת שיבוצים בגרירה")

    if not edit_mode and not dnd_mode:
        # Standard Gantt HTML view
        html = _build_schedule_html(schedule, all_tasks)
        st.markdown(html, unsafe_allow_html=True)

        # Per-task detail expanders
        st.markdown("---")
        st.markdown("##### 📋 פירוט לפי משימה")

        for task_name in all_tasks:
            with st.expander(f"🎯 {task_name}", expanded=False):
                _render_task_detail_table(schedule, task_name)
    else:
        # Edit/DnD mode: render each day as an expandable section
        for i, day_data in enumerate(schedule):
            d = day_data["date"]
            day_heb = HEB_DAYS.get(d.weekday(), "")
            tasks = day_data.get("tasks", {})
            total_assigned = sum(
                len(sol_list)
                for t_info in tasks.values()
                for sn, sol_list in t_info.get("shifts", {}).items()
                if isinstance(sol_list, list)
            )
            with st.expander(
                f"📅 יום {day_heb} — {d.strftime('%d/%m/%Y')}  ({total_assigned} שיבוצים)",
                expanded=(i == 0),
            ):
                if dnd_mode:
                    _render_dnd_daily(day_data, key_prefix=f"mde_{i}")
                else:
                    _render_single_day_visual_inline(day_data, f"mde_{i}")


# ╔══════════════════════════════════════════════════════════════╗
# ║               DRAG-AND-DROP SHIFT EDITING                    ║
# ╚══════════════════════════════════════════════════════════════╝

_DND_SHIFT_CSS = """
.sortable-component { direction: rtl; color: #333 !important; }
.sortable-component * { color: inherit !important; }
.sortable-container {
    background: #f8f9fa;
    border: 2px solid #1B5E20;
    border-radius: 12px;
    padding: 8px;
    min-height: 80px;
    min-width: 150px;
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
    font-size: 13px;
}
.sortable-item {
    background: white;
    color: #333 !important;
    border: 1px solid #ddd;
    border-radius: 6px;
    padding: 5px 8px;
    margin: 3px 0;
    cursor: grab;
    font-size: 13px;
    transition: background 0.2s;
    text-align: right;
}
.sortable-item * { color: #333 !important; }
.sortable-item:hover { background: #E8F5E9; border-color: #1B5E20; }
.sortable-item:active { cursor: grabbing; background: #C8E6C9; }
"""


def _render_dnd_daily(day_data: dict, key_prefix: str = ""):
    """Drag-and-drop shift editing for a single day.

    Shows a shift selector, then containers per task + a pool of unassigned soldiers.
    Users drag soldiers between tasks and the pool, then click save.
    """
    d = day_data["date"]
    pid = day_data.get("period_id")
    tasks = day_data.get("tasks", {})

    if not pid:
        st.warning("חסר מזהה תקופה")
        return
    if not tasks:
        st.info("אין משימות.")
        return

    # Force light color scheme for the sortable iframe
    st.markdown("""
    <style>
    iframe[title*="streamlit_sortables"] { color-scheme: light !important; }
    [data-testid="stCustomComponentV1"] iframe { color-scheme: light !important; }
    </style>
    """, unsafe_allow_html=True)

    # Determine available shift numbers
    max_shifts = max(
        t_info.get("shifts_per_day", 1) for t_info in tasks.values()
    )
    shift_options = [SHIFT_LABEL_HEB.get(sn, f"משמרת {sn}") for sn in range(1, max_shifts + 1)]

    selected_shift_label = st.radio(
        "בחר משמרת לעריכה",
        shift_options,
        horizontal=True,
        key=f"{key_prefix}_dnd_shift_radio_{d}",
    )
    selected_shift = shift_options.index(selected_shift_label) + 1

    # Load all soldiers and constraints
    all_soldiers = get_period_soldiers(pid)
    soldier_map = {s["soldier_id"]: s for s in all_soldiers}
    date_constraints = get_constraints_for_date(pid, d) or {}

    # Collect currently assigned soldier IDs for this shift across all tasks
    # Also build task→soldiers map for the selected shift
    assigned_ids_this_shift: set[int] = set()
    task_assigned: dict[str, list[dict]] = {}  # task_name → [soldier_dicts]

    for task_name, task_info in tasks.items():
        shifts = task_info.get("shifts", {})
        soldiers = shifts.get(selected_shift, [])
        task_assigned[task_name] = soldiers
        for sol in soldiers:
            assigned_ids_this_shift.add(sol["soldier_id"])

    # Pool: available soldiers not assigned to this shift and not blocked
    blocked_for_shift = {
        sid for sid, blocked in date_constraints.items()
        if selected_shift in blocked
    }
    pool_soldiers = [
        s for s in all_soldiers
        if s["soldier_id"] not in assigned_ids_this_shift
        and s["soldier_id"] not in blocked_for_shift
    ]

    # Build labels: "שם · תפקיד"
    label_to_sid: dict[str, int] = {}
    sid_to_label: dict[int, str] = {}
    # Helper to create unique labels
    used_labels: set[str] = set()

    def _make_label(sid: int) -> str:
        info = soldier_map.get(sid, {})
        name = info.get("full_name", f"חייל #{sid}")
        role = info.get("role", "") or ""
        label = f"{name} · {role}" if role else name
        if label in used_labels:
            label = f"{label} #{sid}"
        used_labels.add(label)
        label_to_sid[label] = sid
        sid_to_label[sid] = label
        return label

    # Build container data for sort_items
    containers = []

    # Pool container
    pool_items = [_make_label(s["soldier_id"]) for s in pool_soldiers]
    containers.append({
        "header": f"👥 זמינים ({len(pool_items)})",
        "items": pool_items,
    })

    # Task containers (only tasks that have this shift)
    task_order = []  # keep track of task names in order
    for task_name, task_info in tasks.items():
        shifts_per_day = task_info.get("shifts_per_day", 1)
        if selected_shift > shifts_per_day:
            continue  # this task doesn't have this shift
        task_order.append(task_name)
        soldiers = task_assigned.get(task_name, [])
        items = [_make_label(sol["soldier_id"]) for sol in soldiers]
        containers.append({
            "header": f"🎯 {task_name} ({len(items)})",
            "items": items,
        })

    # Record original assignment for change detection
    original_map: dict[int, str | None] = {}  # sid → task_name or None (pool)
    for s in pool_soldiers:
        original_map[s["soldier_id"]] = None
    for task_name in task_order:
        for sol in task_assigned.get(task_name, []):
            original_map[sol["soldier_id"]] = task_name

    # Render drag-and-drop
    result = sort_items(
        containers,
        multi_containers=True,
        direction="horizontal",
        custom_style=_DND_SHIFT_CSS,
        key=f"{key_prefix}_dnd_shifts_{d}_{selected_shift}",
    )

    # Parse result and detect changes
    if not result:
        return

    # Map result containers back to task names
    # Result: list of lists of labels
    # Container 0 = pool, containers 1..N = tasks in task_order
    new_map: dict[int, str | None] = {}  # sid → task_name or None

    for ci, container_items in enumerate(result):
        if ci == 0:
            # Pool
            for label in container_items:
                sid = label_to_sid.get(label)
                if sid:
                    new_map[sid] = None
        else:
            task_idx = ci - 1
            if task_idx < len(task_order):
                task_name = task_order[task_idx]
                for label in container_items:
                    sid = label_to_sid.get(label)
                    if sid:
                        new_map[sid] = task_name

    # Find changes
    changes = []
    for sid, new_task in new_map.items():
        old_task = original_map.get(sid)
        if old_task != new_task:
            name = soldier_map.get(sid, {}).get("full_name", f"#{sid}")
            from_label = old_task or "זמינים"
            to_label = new_task or "זמינים"
            changes.append({
                "sid": sid,
                "name": name,
                "from": old_task,
                "to": new_task,
                "from_label": from_label,
                "to_label": to_label,
            })

    if changes:
        st.markdown(f"#### 📝 {len(changes)} שינויים ממתינים")
        for ch in changes:
            st.markdown(f"- **{ch['name']}**: {ch['from_label']} → {ch['to_label']}")

        if st.button("💾 שמור שינויים", type="primary",
                      key=f"{key_prefix}_dnd_save_{d}_{selected_shift}"):
            errors = []
            success_count = 0

            for ch in changes:
                sid = ch["sid"]
                old_task = ch["from"]
                new_task = ch["to"]

                # Remove from old task (if not pool)
                if old_task:
                    old_task_id = tasks[old_task].get("task_id")
                    if old_task_id:
                        try:
                            remove_shift_assignment(old_task_id, d, selected_shift, sid)
                        except Exception as e:
                            errors.append(f"שגיאה בהסרה מ-{old_task}: {e}")

                # Add to new task (if not pool)
                if new_task:
                    new_task_id = tasks[new_task].get("task_id")
                    if new_task_id:
                        # Find the best matching slot
                        slot_id = _find_best_slot(new_task_id, sid, pid)
                        try:
                            assign_shift(
                                new_task_id, d, selected_shift, sid,
                                task_slot_id=slot_id,
                                assigned_by="drag-and-drop",
                            )
                            success_count += 1
                        except ValueError as e:
                            errors.append(f"שגיאה בשיבוץ {ch['name']} ל-{new_task}: {e}")
                else:
                    success_count += 1  # moved to pool = removed successfully

            if success_count:
                st.success(f"✅ {success_count} שינויים נשמרו בהצלחה")
            if errors:
                for err in errors:
                    st.error(err)
            st.rerun()
    else:
        st.caption("גרור חיילים בין המשימות ולחץ שמור")


def _find_best_slot(task_id: int, soldier_id: int, period_id: int) -> int | None:
    """Find the best matching task slot for a soldier based on role/qualifications."""
    task_slots = get_task_slots(task_id)
    if not task_slots:
        return None

    all_soldiers = get_period_soldiers(period_id)
    soldier_info = next((s for s in all_soldiers if s["soldier_id"] == soldier_id), None)
    if not soldier_info:
        return task_slots[0]["id"] if task_slots else None

    from military_manager.services.task_service import _load_soldier_qualifications_map
    qual_map = _load_soldier_qualifications_map(period_id)
    soldier_quals = qual_map.get(soldier_id, [])

    # Try to find a slot whose allowed_roles match this soldier
    for slot in task_slots:
        allowed = slot.get("allowed_roles", [])
        if not allowed:
            continue
        if _soldier_matches_roles(soldier_info, allowed, soldier_quals):
            return slot["id"]

    # Fallback: first slot
    return task_slots[0]["id"] if task_slots else None


def _build_schedule_html(schedule: list[dict], all_tasks: list[str]) -> str:
    """Build an HTML Gantt-like table for the schedule."""
    shift_colors = {1: "#FF9800", 2: "#2196F3", 3: "#673AB7"}
    shift_bg = {1: "#FFF3E0", 2: "#E3F2FD", 3: "#EDE7F6"}

    html = """
    <style>
    .sched-table { width:100%; border-collapse:separate; border-spacing:0;
        direction:rtl; font-size:12px; }
    .sched-table th { background:#1B5E20; color:white; padding:6px 4px; text-align:center;
        border:1px solid #ddd; position:sticky; top:0; z-index:3; }
    .sched-table th:first-child { position:sticky; right:0; z-index:4;
        background:#1B5E20; }
    .sched-table td { padding:4px; border:1px solid #eee; vertical-align:top;
        text-align:center; min-width:90px; }
    .sched-task { font-weight:bold; background:#F5F5F5; text-align:right !important;
        padding-right:8px !important; white-space:nowrap;
        position:sticky; right:0; z-index:2; min-width:120px; }
    .sched-soldier { font-size:11px; display:block; margin:1px 0; }
    .sched-empty { color:#ccc; font-size:10px; }
    .sched-duty { background:#FFFDE7; font-size:10px; font-weight:bold; }
    </style>
    <div style="overflow-x:auto; max-height:600px; overflow-y:auto; position:relative;">
    <table class="sched-table">
    """

    # Header: dates
    html += "<tr><th>משימה / משמרת</th>"
    for day in schedule:
        d = day["date"]
        day_heb = HEB_DAYS.get(d.weekday(), "")
        html += f"<th>{day_heb}<br>{d.strftime('%d/%m')}</th>"
    html += "</tr>"

    # Duty officer row
    html += '<tr><td class="sched-task">🎖️ קצין תורן</td>'
    for day in schedule:
        do = day.get("duty_officer", "")
        if do:
            html += f'<td class="sched-duty">{do}</td>'
        else:
            html += '<td class="sched-empty">—</td>'
    html += "</tr>"

    # Per task+shift rows
    for task_name in all_tasks:
        max_shifts = 1
        for day in schedule:
            task_info = day.get("tasks", {}).get(task_name, {})
            max_shifts = max(max_shifts, task_info.get("shifts_per_day", 1))

        for sn in range(1, max_shifts + 1):
            shift_heb = SHIFT_LABEL_HEB.get(sn, f"מ{sn}")
            bg = shift_bg.get(sn, "#fff")
            color = shift_colors.get(sn, "#333")

            row_label = f"{task_name} · {shift_heb}" if max_shifts > 1 else task_name
            html += f'<tr><td class="sched-task" style="background:{bg};">'
            html += f'<span style="color:{color};">{row_label}</span></td>'

            for day in schedule:
                task_info = day.get("tasks", {}).get(task_name, {})
                soldiers = task_info.get("shifts", {}).get(sn, [])

                html += f'<td style="background:{bg};">'
                if soldiers:
                    for sol in soldiers:
                        slot = sol.get("slot_name", "")
                        name = sol.get("name", "?")
                        # Show first name for compactness
                        short = name.split()[0] if " " in name else name
                        badge = f"<small>({slot})</small>" if slot else ""
                        html += f'<span class="sched-soldier">{short} {badge}</span>'
                else:
                    html += '<span class="sched-empty">—</span>'
                html += "</td>"

            html += "</tr>"

    html += "</table></div>"
    return html


def _render_task_detail_table(schedule: list[dict], task_name: str):
    """Per-task detailed pandas table."""
    import pandas as pd

    rows = []
    for day in schedule:
        d = day["date"]
        day_heb = HEB_DAYS.get(d.weekday(), "")
        task_info = day.get("tasks", {}).get(task_name, {})
        shifts = task_info.get("shifts", {})
        shift_times = task_info.get("shift_times", [])
        shifts_per_day = task_info.get("shifts_per_day", 1)

        for sn in range(1, shifts_per_day + 1):
            soldiers = shifts.get(sn, [])
            time_str = shift_times[sn - 1] if sn <= len(shift_times) else ""
            shift_heb = SHIFT_LABEL_HEB.get(sn, f"משמרת {sn}")
            names = ", ".join(
                f"{s['name']} ({s.get('slot_name', '')})" if s.get("slot_name") else s["name"]
                for s in soldiers
            ) if soldiers else "—"
            rows.append({
                "תאריך": f"{d.strftime('%d/%m')} {day_heb}",
                "משמרת": f"{shift_heb} {time_str}",
                "חיילים": names,
                "מס'": len(soldiers),
            })

    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.caption("אין נתונים")


def _render_single_day_visual_inline(day_data: dict, key_prefix: str):
    """Render a single day with edit mode always ON — used inside multi-day edit mode.
    
    Uses key_prefix to ensure unique Streamlit widget keys when multiple days are shown.
    """
    d = day_data["date"]

    if day_data.get("duty_officer"):
        st.markdown(f"🎖️ **קצין תורן:** {day_data['duty_officer']}")

    tasks = day_data.get("tasks", {})
    if not tasks:
        st.info("אין משימות.")
        return

    pid = day_data.get("period_id")
    if not pid:
        st.warning("חסר מזהה תקופה — לא ניתן לערוך")
        return

    for task_name, task_info in tasks.items():
        shift_times = task_info.get("shift_times", [])
        shifts = task_info.get("shifts", {})
        shifts_per_day = task_info.get("shifts_per_day", 1)
        task_id = task_info.get("task_id")

        st.markdown(f"##### 🎯 {task_name}")

        cols = st.columns(shifts_per_day)

        for sn in range(1, shifts_per_day + 1):
            with cols[sn - 1]:
                time_str = shift_times[sn - 1] if sn <= len(shift_times) else ""
                shift_heb = SHIFT_LABEL_HEB.get(sn, f"משמרת {sn}")

                if sn == 1:
                    color, border = "#FFF3E0", "#FF9800"
                elif sn == 2:
                    color, border = "#E3F2FD", "#2196F3"
                else:
                    color, border = "#EDE7F6", "#673AB7"

                soldiers = shifts.get(sn, [])
                header = f"{shift_heb}"
                if time_str:
                    header += f" ({time_str})"

                st.markdown(
                    f"<div style='background:{color}; border:2px solid {border}; "
                    f"border-radius:10px; padding:8px; margin:4px 0;'>"
                    f"<div style='text-align:center; font-weight:bold; color:{border}; "
                    f"font-size:13px;'>{header}</div></div>",
                    unsafe_allow_html=True,
                )
                _render_visual_edit_shift(
                    task_id, task_name, sn, soldiers, d, pid,
                    key_prefix=key_prefix,
                )

        st.markdown("---")


# ╔══════════════════════════════════════════════════════════════╗
# ║                    SLOT-BASED SHIFT (helpers)                ║
# ╚══════════════════════════════════════════════════════════════╝

def _render_slot_based_shift(
    task, task_slots, shift_num, shift_soldiers,
    all_soldiers, assigned_ids, pid, selected_date,
    date_constraints=None,
):
    """Render per-slot assignment UI for a shift."""
    if date_constraints is None:
        date_constraints = {}

    assigned_by_slot: dict[int | None, list] = {}
    for s in shift_soldiers:
        slot_id = s.get("slot_id")
        assigned_by_slot.setdefault(slot_id, []).append(s)

    shift_assigned_ids = {s["soldier_id"] for s in shift_soldiers}

    for slot in task_slots:
        slot_id = slot["id"]
        slot_name = slot["slot_name"]
        allowed_roles = slot.get("allowed_roles", [])
        quantity = slot.get("quantity", 1)

        slot_assignments = assigned_by_slot.get(slot_id, [])
        filled = len(slot_assignments)
        needed = quantity - filled

        role_desc = ", ".join(allowed_roles) if allowed_roles else "כל חייל"

        col_info, col_assign = st.columns([4, 4])

        with col_info:
            status_icon = "✅" if needed <= 0 else f"⚠️ חסרים {needed}"
            assigned_names = ", ".join(s.get("name", "") for s in slot_assignments)
            st.markdown(
                f"&nbsp;&nbsp;🎭 **{slot_name}** ({role_desc}) — "
                f"{assigned_names if assigned_names else 'טרם שובץ'} "
                f"{status_icon}"
            )

            for s in slot_assignments:
                if st.button(
                    f"❌ הסר {s.get('name', '')}",
                    key=f"rm_{task.id}_{shift_num}_{slot_id}_{s.get('soldier_id')}",
                ):
                    remove_shift_assignment(task.id, selected_date, shift_num, s["soldier_id"])
                    st.rerun()

        with col_assign:
            if needed > 0:
                is_driver_slot = _slot_requires_driver(allowed_roles)
                if is_driver_slot:
                    from military_manager.services.driver_service import get_approved_driver_ids
                    approved_driver_ids = get_approved_driver_ids(pid)

                eligible = []
                for sol in all_soldiers:
                    sid = sol["soldier_id"]
                    if sid in shift_assigned_ids or sid in assigned_ids:
                        continue
                    if sid in date_constraints and shift_num in date_constraints[sid]:
                        continue
                    if is_driver_slot:
                        if sid not in approved_driver_ids:
                            continue
                    else:
                        if not _soldier_matches_roles(sol, allowed_roles):
                            continue
                    label = f"{sol['full_name']}"
                    if sol.get("role"):
                        label += f" ({sol['role']})"
                    eligible.append((sid, label))

                if eligible:
                    options = ["—"] + [lbl for _, lbl in eligible]
                    sel = st.selectbox(
                        f"שבץ {slot_name}",
                        options,
                        key=f"sel_{task.id}_{shift_num}_{slot_id}",
                    )
                    if sel != "—":
                        sid = next(
                            (sid for sid, lbl in eligible if lbl == sel), None
                        )
                        if sid and st.button(
                            f"➕ שבץ",
                            key=f"do_{task.id}_{shift_num}_{slot_id}",
                        ):
                            try:
                                assign_shift(
                                    task.id, selected_date, shift_num, sid,
                                    task_slot_id=slot_id,
                                )
                                st.success(f"{sel} שובץ כ{slot_name}!")
                                st.rerun()
                            except ValueError as e:
                                st.error(str(e))
                else:
                    st.caption(f"אין חיילים זמינים מתאימים ל{slot_name}")


def _render_legacy_shift(
    task, shift_num, shift_soldiers,
    all_soldiers, assigned_ids, pid, selected_date,
):
    """Fallback for tasks without defined slots."""
    current_names = [s.get("name", "") for s in shift_soldiers]
    needed = task.personnel_per_shift - len(shift_soldiers)

    col_info, col_assign = st.columns([3, 3])

    with col_info:
        status_emoji = "✅" if needed <= 0 else f"⚠️ חסרים {needed}"
        st.markdown(
            f"{', '.join(current_names) if current_names else 'טרם שובץ'} "
            f"{status_emoji}"
        )
        for s in shift_soldiers:
            if st.button(
                f"❌ הסר {s.get('name', '')}",
                key=f"remove_legacy_{task.id}_{shift_num}_{s.get('soldier_id')}",
            ):
                remove_shift_assignment(task.id, selected_date, shift_num, s["soldier_id"])
                st.rerun()

    with col_assign:
        if needed > 0:
            available = {
                s["soldier_id"]: s["full_name"]
                for s in all_soldiers
                if s["soldier_id"] not in assigned_ids
            }
            if available:
                sel = st.selectbox(
                    "שבץ חייל",
                    ["—"] + list(available.values()),
                    key=f"legacy_{task.id}_{shift_num}",
                )
                if sel != "—":
                    sid = next((sid for sid, name in available.items() if name == sel), None)
                    if sid and st.button("➕ שבץ", key=f"do_legacy_{task.id}_{shift_num}"):
                        try:
                            assign_shift(task.id, selected_date, shift_num, sid)
                            st.success(f"{sel} שובץ!")
                            st.rerun()
                        except ValueError as e:
                            st.error(str(e))


# ╔══════════════════════════════════════════════════════════════╗
# ║                 CAPACITY / FORWARD LOOK                      ║
# ╚══════════════════════════════════════════════════════════════╝

def _render_capacity_view(pid: int, p_start, p_end):
    """Forward-looking capacity view — shows soldier availability vs. requirements."""
    import pandas as pd

    st.markdown("### 📈 סד\"כ וזמינות — מבט קדימה")
    st.caption("בדיקת כמות חיילים זמינים מול דרישות המשימות, לפי לוח יציאות ואילוצים")

    # Task breakdown
    min_info = get_minimum_soldiers_needed(pid)
    st.markdown("#### 📋 דרישות כ\"א למשימות")
    task_rows = []
    for t in min_info["per_task"]:
        task_rows.append({
            "משימה": t["name"],
            "כ\"א למשמרת": t["per_shift"],
            "משמרות ביום": t["shifts"],
            "סה\"כ שיבוצים יומי": t["daily_total"],
        })
    if task_rows:
        st.dataframe(pd.DataFrame(task_rows), use_container_width=True, hide_index=True)

    mc1, mc2 = st.columns(2)
    mc1.metric("📋 כמה חיילים בכל זמן נתון במשימות", min_info["min_needed"])
    mc2.metric("📊 סה\"כ שיבוצי-משמרת ביום", min_info["total_slots_per_day"])

    st.markdown("---")

    # Forward look
    st.markdown("#### 📆 זמינות קדימה")
    num_days = min((p_end - p_start).days + 1, 21)
    forward = get_forward_capacity(pid, p_start, num_days)

    if not forward:
        st.info("אין נתונים.")
        return

    chart_rows = []
    for day_info in forward:
        d = day_info["date"]
        day_heb = HEB_DAYS.get(d.weekday(), "")
        deficit = day_info["deficit"]
        chart_rows.append({
            "תאריך": f"{d.strftime('%d/%m')} {day_heb}",
            "זמינים": day_info["available"],
            "מינימום נדרש": day_info["min_needed"],
            "חוסר": deficit,
            "סה\"כ": day_info["total"],
            "יוצאים": day_info["on_leave"],
        })

    df = pd.DataFrame(chart_rows)

    # Highlight deficit days
    def _style_deficit(val):
        if isinstance(val, (int, float)) and val > 0:
            return "background-color: #FFCDD2; font-weight: bold; color: #B71C1C"
        return ""

    def _style_available(row):
        styles = [""] * len(row)
        avail_idx = df.columns.get_loc("זמינים") if "זמינים" in df.columns else -1
        min_idx = df.columns.get_loc("מינימום נדרש") if "מינימום נדרש" in df.columns else -1
        if avail_idx >= 0 and min_idx >= 0:
            if row.iloc[avail_idx] < row.iloc[min_idx]:
                styles[avail_idx] = "background-color: #FFCDD2; font-weight: bold"
        return styles

    styled = df.style.apply(_style_available, axis=1).map(
        _style_deficit, subset=["חוסר"]
    )
    st.dataframe(styled, use_container_width=True, hide_index=True)

    # Chart
    st.markdown("---")
    chart_df = pd.DataFrame({
        "תאריך": [r["תאריך"] for r in chart_rows],
        "זמינים": [r["זמינים"] for r in chart_rows],
        "מינימום נדרש": [r["מינימום נדרש"] for r in chart_rows],
    }).set_index("תאריך")
    st.line_chart(chart_df)

    # Deficit warnings
    deficit_days = [r for r in chart_rows if r["חוסר"] > 0]
    if deficit_days:
        st.error(
            f"⚠️ **{len(deficit_days)} ימים עם חוסר בכ\"א!** "
            "בדקו שלא ניתנו יותר מדי חופשות/יציאות."
        )
        for r in deficit_days:
            st.markdown(f"- 🔴 **{r['תאריך']}** — חוסר {r['חוסר']} חיילים ({r['זמינים']} זמינים / {r['מינימום נדרש']} נדרש)")


def _render_weekly_summary(pid: int, p_start, p_end):
    """Weekly forward-looking summary with status group breakdowns."""
    import pandas as pd
    from military_manager.services.stats_service import (
        compute_weekly_summary, get_status_groups, init_default_groups, get_setting,
    )

    st.markdown("### 📅 סיכום שבועי — שבוע קדימה")
    st.caption("פילוח סטטוסים לפי קבוצות מוגדרות, עם אחוזים יומיים")

    today = date.today()
    safe_start = max(today, p_start)
    safe_end = min(safe_start + timedelta(days=6), p_end)
    num_days = (safe_end - safe_start).days + 1

    if num_days <= 0:
        st.info("אין ימים להציג בטווח הנבחר")
        return

    init_default_groups(pid)
    groups = get_status_groups(pid)
    group_names = [g["name"] for g in groups]
    threshold = float(get_setting(pid, "home_alert_percent", "25"))

    weekly = compute_weekly_summary(pid, safe_start, num_days)

    # Build table
    rows = []
    for day_info in weekly:
        d = day_info["date"]
        day_heb = HEB_DAYS.get(d.weekday(), "")
        row = {"תאריך": f"{d.strftime('%d/%m')} {day_heb}"}
        stats = day_info["stats"]
        for gname in group_names:
            gdata = stats["groups"].get(gname, {"count": 0, "percent": 0})
            row[gname] = f"{gdata['count']}  ({gdata['percent']}%)"
        row["סה\"כ בשמ\"פ"] = stats["total_in_shmap"]
        rows.append(row)

    if rows:
        df = pd.DataFrame(rows)

        # Style: red highlight for high leave percentage
        def _highlight_leave(val):
            if isinstance(val, str) and "%" in val:
                try:
                    pct = float(val.split("(")[1].replace("%)", "").strip())
                    if pct > threshold:
                        return "background-color: #FFCDD2; color: #B71C1C; font-weight: bold"
                except (IndexError, ValueError):
                    pass
            return ""

        leave_col = "בחופש"
        if leave_col in df.columns:
            styled = df.style.map(_highlight_leave, subset=[leave_col])
        else:
            styled = df.style

        st.dataframe(styled, use_container_width=True, hide_index=True)

        # Alerts
        for day_info in weekly:
            for alert in day_info["stats"].get("alerts", []):
                d = day_info["date"]
                day_heb = HEB_DAYS.get(d.weekday(), "")
                st.error(f"🔴 {d.strftime('%d/%m')} {day_heb} — {alert['message']}")
    else:
        st.info("אין נתונים לשבוע הקרוב")

    # Chart
    if weekly:
        st.markdown("---")
        chart_data = {}
        for gname in group_names:
            chart_data[gname] = []
        dates = []
        for day_info in weekly:
            d = day_info["date"]
            dates.append(f"{d.strftime('%d/%m')} {HEB_DAYS.get(d.weekday(), '')}")
            for gname in group_names:
                gdata = day_info["stats"]["groups"].get(gname, {"count": 0})
                chart_data[gname].append(gdata["count"])

        chart_df = pd.DataFrame(chart_data, index=dates)
        st.bar_chart(chart_df)
