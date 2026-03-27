"""Constraints page — manage soldier availability restrictions."""

from __future__ import annotations

from datetime import datetime, timedelta

import streamlit as st

from military_manager.components.navigation import render_page_header
from military_manager.components.filters import period_guard
from military_manager.services.constraint_service import (
    add_constraint,
    get_period_constraints,
    get_soldier_constraints,
    delete_constraint,
    get_blocked_shifts,
    apply_pitzul_statuses,
    get_pitzul_constraints,
    PITZUL_THRESHOLD_DAYS,
    TIME_LABELS_HEB,
    SHIFT_LABEL_HEB,
    GUARD_TASK_PATTERNS,
)
from military_manager.services.soldier_service import get_period_soldiers, get_sub_units
from military_manager.services.task_service import get_period_tasks


CONSTRAINT_TYPES = {
    "departure": "🚪 יציאה (שחרור הביתה)",
    "arrival": "📥 הגעה (כניסה לשירות)",
    "unavailable": "🚫 לא זמין",
    "duty_only": "🛡️ תורן בלבד (שמירה/ש\"ג בלבד)",
    "medical": "🏥 הגבלה רפואית",
    "custom": "📝 אילוץ מותאם אישית",
}

CONSTRAINT_TYPES_REV = {v: k for k, v in CONSTRAINT_TYPES.items()}

TIME_OPTIONS = {
    "morning": "☀️ בוקר (06:00-14:00)",
    "afternoon": "🌤️ צהריים (14:00-22:00)",
    "night": "🌙 לילה (22:00-06:00)",
    "all_day": "📅 כל היום",
}

TIME_OPTIONS_REV = {v: k for k, v in TIME_OPTIONS.items()}

# Extended time options for new constraint types (includes "no shift block")
TIME_OPTIONS_EXTENDED = {
    "all_shifts_allowed": "✅ זמין לכל המשמרות (הגבלת משימות בלבד)",
    **TIME_OPTIONS,
}
TIME_OPTIONS_EXTENDED_REV = {v: k for k, v in TIME_OPTIONS_EXTENDED.items()}

# Predefined reasons that auto-require פיצול
PITZUL_REASONS = {"טיסה לחו\"ל", "טיסה לחול", "נסיעה לחו\"ל"}


def render():
    render_page_header(
        "⚠️ אילוצים",
        "ניהול אילוצי זמינות חיילים — יציאות, הגעות, חוסר זמינות, הגבלות רפואיות, תורנות בלבד",
    )

    period = period_guard()
    if not period:
        return

    pid = period["id"]

    try:
        p_start = datetime.strptime(period["start_date"], "%Y-%m-%d").date()
        p_end = datetime.strptime(period["end_date"], "%Y-%m-%d").date()
    except (ValueError, KeyError):
        from datetime import date
        p_start = date.today()
        p_end = date.today() + timedelta(days=21)

    tab_add, tab_view, tab_calendar = st.tabs([
        "➕ הוסף אילוץ",
        "📋 כל האילוצים",
        "📅 תצוגת לוח",
    ])

    # ── Add constraint ──
    with tab_add:
        _render_add_constraint(pid, p_start, p_end)

    # ── View all constraints ──
    with tab_view:
        _render_view_constraints(pid)

    # ── Calendar view ──
    with tab_calendar:
        _render_calendar_view(pid, p_start, p_end)


def _render_add_constraint(pid: int, p_start, p_end):
    st.markdown("### ➕ הוספת אילוץ חדש")

    soldiers = get_period_soldiers(pid, exclude_irrelevant_unit=True)
    if not soldiers:
        st.info("אין חיילים בתקופה.")
        return

    # Filter by unit
    units = get_sub_units(pid)
    filter_unit = st.selectbox(
        "סנן לפי יחידה",
        ["כל היחידות"] + units,
        key="constraint_filter_unit",
    )

    if filter_unit != "כל היחידות":
        soldiers = [s for s in soldiers if s.get("sub_unit") == filter_unit]

    # Soldier selector
    soldier_options = {
        s["soldier_id"]: f"{s['full_name']} ({s.get('sub_unit', '?')} · {s.get('role', '?')})"
        for s in soldiers
    }

    selected_soldier_label = st.selectbox(
        "בחר חייל",
        options=list(soldier_options.values()),
        key="constraint_soldier",
    )

    selected_sid = next(
        (sid for sid, lbl in soldier_options.items() if lbl == selected_soldier_label),
        None,
    )

    if not selected_sid:
        return

    # Constraint type
    c_type_label = st.selectbox(
        "סוג אילוץ",
        options=list(CONSTRAINT_TYPES.values()),
        key="constraint_type",
    )
    c_type = CONSTRAINT_TYPES_REV[c_type_label]

    # ── Type-specific description ──
    _type_descriptions = {
        "departure": "חייל יוצא הביתה — ייחסם מהמשמרות שאחרי היציאה",
        "arrival": "חייל מגיע לבסיס — ייחסם מהמשמרות שלפני ההגעה",
        "unavailable": "חייל לא זמין למשמרת/ות מסוימת/ות",
        "duty_only": "החייל יכול רק תורנ/שמירה/ש\"ג — לא ישובץ למשימות שטח (סיור, כרמל, חפ\"ק וכו')",
        "medical": "הגבלה רפואית — חסום ממשימות מסוימות, זמין לאחרות",
        "custom": "אילוץ מותאם אישית — בחר משמרות ומשימות לחסום",
    }
    st.info(f"ℹ️ {_type_descriptions.get(c_type, '')}")

    # ── Custom reason (free text) ──
    st.markdown("---")
    if c_type == "medical":
        st.markdown("##### 🏥 תיאור ההגבלה הרפואית")
        st.caption("תאר את ההגבלה — לדוגמא: פציעת ברך, אסור ריצה, פרופיל 45 וכו'")
        custom_reason = st.text_input(
            "תיאור ההגבלה",
            key="constraint_custom_reason",
            placeholder="לדוגמא: פציעת ברך, פרופיל 45, לא יכול לבצע סיורים ברגל...",
        )
    elif c_type == "custom":
        st.markdown("##### 📝 תיאור האילוץ")
        st.caption("כתוב תיאור מפורט של האילוץ — הכלי ישתמש בזה כדי להחליט על שיבוצים")
        custom_reason = st.text_area(
            "תיאור האילוץ",
            key="constraint_custom_reason",
            placeholder="לדוגמא: החייל יכול לעלות רק תורן, לא יכול סיורים ברגל אבל כן יכול נהיגה...",
            height=100,
        )
    else:
        st.markdown("##### 📝 סיבת האילוץ")
        st.caption("ניתן לכתוב סיבה חופשית — לדוגמא: טיסה לחו\"ל, חתונה, מבחן באוניברסיטה וכו'")
        custom_reason = st.text_input(
            "סיבה (אופציונלי)",
            key="constraint_custom_reason",
            placeholder="לדוגמא: טיסה לחו\"ל, חתונה, מבחן...",
        )

    # ── Task blocking (for duty_only, medical, custom) ──
    blocked_tasks: list[str] = []
    if c_type in ("medical", "custom"):
        st.markdown("---")
        st.markdown("##### 🚫 משימות חסומות")
        st.caption(
            "בחר משימות שהחייל **לא** יכול לבצע. "
            "כל השאר — מותר."
        )
        # Load actual tasks for checkboxes
        tasks = get_period_tasks(pid, active_only=True)
        task_names = [t.name for t in tasks]
        if task_names:
            selected_blocked = st.multiselect(
                "בחר משימות לחסום",
                options=task_names,
                key="constraint_blocked_tasks",
            )
            blocked_tasks = selected_blocked if selected_blocked else []
        # Also allow free text for custom patterns not in list
        extra_blocked = st.text_input(
            "משימות נוספות לחסום (טקסט חופשי, מופרד בפסיקים)",
            key="constraint_extra_blocked",
            placeholder="לדוגמא: סיור, כרמל",
        )
        if extra_blocked:
            blocked_tasks.extend([p.strip() for p in extra_blocked.split(",") if p.strip()])

    elif c_type == "duty_only":
        st.markdown("---")
        st.markdown("##### 🛡️ תורן בלבד")
        st.caption(
            "החייל ישובץ **רק** למשימות שמירה/ש\"ג/תורנות. "
            "לא ישובץ לסיור, כרמל, חפ\"ק או כל משימת שטח אחרת."
        )

    # ── Date range ──
    st.markdown("---")
    st.markdown("##### 📅 תאריכים")

    single_day = st.checkbox(
        "אילוץ ליום אחד בלבד",
        value=True,
        key="constraint_single_day",
    )

    if single_day:
        c_start_date = st.date_input(
            "תאריך",
            value=p_start,
            min_value=p_start,
            max_value=p_end,
            key="constraint_date",
        )
        c_end_date = None
    else:
        col_s, col_e = st.columns(2)
        with col_s:
            c_start_date = st.date_input(
                "תאריך התחלה",
                value=p_start,
                min_value=p_start,
                max_value=p_end,
                key="constraint_date_start",
            )
        with col_e:
            c_end_date = st.date_input(
                "תאריך סיום",
                value=min(c_start_date + timedelta(days=3), p_end),
                min_value=c_start_date,
                max_value=p_end,
                key="constraint_date_end",
            )

    # Duration info
    actual_end = c_end_date or c_start_date
    duration = (actual_end - c_start_date).days + 1
    if duration > 1:
        st.info(f"📆 משך האילוץ: **{duration} ימים** ({c_start_date.strftime('%d/%m')} — {actual_end.strftime('%d/%m')})")

    # Time / shift selector — depends on type
    ignore_sleep = False
    if c_type in ("departure", "arrival", "unavailable"):
        # Original time selector
        c_time_label = st.selectbox(
            "זמן",
            options=list(TIME_OPTIONS.values()),
            key="constraint_time",
        )
        c_time = TIME_OPTIONS_REV[c_time_label]

        # Ignore sleep checkbox (only for departure)
        if c_type == "departure" and c_time in ("morning", "all_day"):
            st.markdown("---")
            st.markdown("##### 😴 אפשרות שינה")
            st.caption(
                "ברירת מחדל: חייל שיוצא בבוקר לא ישובץ למשמרת לילה ביום הקודם (כדי שיספיק לישון). "
                "אם תסמן 'התעלם משעות שינה', יהיה אפשרי לשבץ אותו למשמרת לילה למרות שיוצא בבוקר."
            )
            ignore_sleep = st.checkbox(
                "🔓 התעלם משעות שינה (אפשר משמרת לילה לפני יציאה בבוקר)",
                key="constraint_ignore_sleep",
            )
    elif c_type == "duty_only":
        # duty_only: optionally restrict shifts too
        st.markdown("---")
        st.markdown("##### ⏰ הגבלת משמרות (אופציונלי)")
        st.caption("אם החייל זמין לכל המשמרות אבל רק למשימות שמירה — בחר 'זמין לכל המשמרות'")
        c_time_label = st.selectbox(
            "זמינות למשמרות",
            options=list(TIME_OPTIONS_EXTENDED.values()),
            key="constraint_time",
        )
        c_time = TIME_OPTIONS_EXTENDED_REV[c_time_label]
    else:
        # medical / custom: extended time selector
        st.markdown("---")
        st.markdown("##### ⏰ הגבלת משמרות")
        st.caption(
            "אם האילוץ חוסם רק משימות מסוימות (לא משמרות) — בחר 'זמין לכל המשמרות'. "
            "אם גם משמרות מסוימות חסומות — בחר את המשמרת החסומה."
        )
        c_time_label = st.selectbox(
            "זמינות למשמרות",
            options=list(TIME_OPTIONS_EXTENDED.values()),
            key="constraint_time",
        )
        c_time = TIME_OPTIONS_EXTENDED_REV[c_time_label]

    # ── פיצול logic (only for departure/arrival/unavailable) ──
    requires_pitzul = False
    reason_lower = (custom_reason or "").strip()
    auto_pitzul_by_reason = any(r in reason_lower for r in PITZUL_REASONS)
    auto_pitzul_by_duration = duration > PITZUL_THRESHOLD_DAYS and c_type in ("departure", "arrival", "unavailable")

    if c_type in ("departure", "arrival", "unavailable"):
        st.markdown("---")
        st.markdown("##### 🔀 דרישת פיצול")

        if auto_pitzul_by_reason:
            st.warning(f"⚠️ הסיבה \"{custom_reason}\" מחייבת סימון פיצול — הסימון יופעל אוטומטית.")
            requires_pitzul = True
        elif auto_pitzul_by_duration:
            st.warning(
                f"⚠️ משך האילוץ ({duration} ימים) עולה על {PITZUL_THRESHOLD_DAYS} ימים — "
                f"פיצול יסומן אוטומטית החל מהיום הרביעי."
            )
            requires_pitzul = st.checkbox(
                "🔀 דורש פיצול",
                value=True,
                key="constraint_pitzul",
            )
        else:
            st.caption(
                "סמן אם האילוץ דורש לסמן 'פיצול' לחייל בדו\"ח 1. "
                "פיצול יסומן אוטומטית אם ההעדרות עולה על 4 ימים או אם הסיבה מחייבת."
            )
            requires_pitzul = st.checkbox(
                "🔀 דורש פיצול",
                value=False,
                key="constraint_pitzul",
            )

    # Notes
    notes = st.text_input("הערות נוספות (אופציונלי)", key="constraint_notes")

    # Preview what this blocks
    st.markdown("---")
    st.markdown("##### 👁️ תצוגה מקדימה — מה ייחסם?")
    _preview_constraint(c_type, c_start_date, c_end_date, c_time, ignore_sleep,
                        requires_pitzul, duration, blocked_tasks=blocked_tasks)

    # Submit
    if st.button("✅ הוסף אילוץ", type="primary", key="add_constraint_btn"):
        add_constraint(
            period_id=pid,
            soldier_id=selected_sid,
            constraint_type=c_type,
            constraint_date=c_start_date,
            constraint_time=c_time,
            ignore_sleep=ignore_sleep,
            notes=notes or None,
            end_date=c_end_date,
            custom_reason=custom_reason.strip() if custom_reason and custom_reason.strip() else None,
            requires_pitzul=requires_pitzul,
            blocked_tasks=blocked_tasks if blocked_tasks else None,
        )
        # Auto-apply פיצול statuses if needed
        if requires_pitzul or auto_pitzul_by_duration:
            applied = apply_pitzul_statuses(pid)
            if applied:
                st.info(f"🔀 פיצול הוחל על {len(applied)} ימים בדו\"ח 1")
        st.success("✅ אילוץ נוסף בהצלחה!")
        st.rerun()


def _preview_constraint(c_type: str, c_start, c_end, c_time: str,
                         ignore_sleep: bool, requires_pitzul: bool, duration: int,
                         blocked_tasks: list[str] | None = None):
    """Show preview of what shifts will be blocked."""
    blocked_info = []
    actual_end = c_end or c_start
    is_range = c_start != actual_end

    if is_range:
        blocked_info.append(
            f"📅 טווח: {c_start.strftime('%d/%m')} — {actual_end.strftime('%d/%m')} ({duration} ימים)"
        )

    if c_type == "departure":
        if is_range:
            blocked_info.append(f"🚪 יום יציאה: {c_start.strftime('%d/%m')}")
            if c_time in ("morning", "all_day"):
                blocked_info.append(f"🚫 {c_start.strftime('%d/%m')} — כל המשמרות (יום יציאה)")
            elif c_time == "afternoon":
                blocked_info.append(f"🚫 {c_start.strftime('%d/%m')} — צהריים, לילה")
            elif c_time == "night":
                blocked_info.append(f"🚫 {c_start.strftime('%d/%m')} — לילה")
            if duration > 1:
                blocked_info.append(
                    f"🚫 {(c_start + timedelta(days=1)).strftime('%d/%m')} — "
                    f"{actual_end.strftime('%d/%m')} — כל המשמרות (החייל בחוץ)"
                )
            if not ignore_sleep and c_time in ("morning", "all_day"):
                prev = c_start - timedelta(days=1)
                blocked_info.append(f"😴 {prev.strftime('%d/%m')} — לילה (שינה לפני יציאה)")
        else:
            if c_time == "morning":
                blocked_info.append(f"🚫 {c_start.strftime('%d/%m')} — בוקר, צהריים, לילה")
                if not ignore_sleep:
                    prev = c_start - timedelta(days=1)
                    blocked_info.append(f"😴 {prev.strftime('%d/%m')} — לילה (שינה לפני יציאה)")
                else:
                    prev = c_start - timedelta(days=1)
                    blocked_info.append(f"✅ {prev.strftime('%d/%m')} — לילה (מותר, התעלמות משינה)")
            elif c_time == "afternoon":
                blocked_info.append(f"🚫 {c_start.strftime('%d/%m')} — צהריים, לילה")
                prev = c_start - timedelta(days=1)
                if not ignore_sleep:
                    blocked_info.append(f"😴 {prev.strftime('%d/%m')} — לילה (שינה)")
            elif c_time == "night":
                blocked_info.append(f"🚫 {c_start.strftime('%d/%m')} — לילה")
            elif c_time == "all_day":
                blocked_info.append(f"🚫 {c_start.strftime('%d/%m')} — כל המשמרות")
                if not ignore_sleep:
                    prev = c_start - timedelta(days=1)
                    blocked_info.append(f"😴 {prev.strftime('%d/%m')} — לילה (שינה)")

    elif c_type == "arrival":
        if is_range:
            blocked_info.append(f"📥 יום הגעה: {actual_end.strftime('%d/%m')}")
            if duration > 1:
                blocked_info.append(
                    f"🚫 {c_start.strftime('%d/%m')} — "
                    f"{(actual_end - timedelta(days=1)).strftime('%d/%m')} — כל המשמרות (טרם הגיע)"
                )
            if c_time == "afternoon":
                blocked_info.append(f"🚫 {actual_end.strftime('%d/%m')} — בוקר (טרם הגיע)")
            elif c_time == "night":
                blocked_info.append(f"🚫 {actual_end.strftime('%d/%m')} — בוקר, צהריים (טרם הגיע)")
            elif c_time == "all_day":
                blocked_info.append(f"🚫 {actual_end.strftime('%d/%m')} — כל המשמרות (מגיע במהלך)")
            elif c_time == "morning":
                blocked_info.append(f"✅ {actual_end.strftime('%d/%m')} — הגעה בבוקר, זמין")
        else:
            if c_time == "afternoon":
                blocked_info.append(f"🚫 {c_start.strftime('%d/%m')} — בוקר (טרם הגיע)")
            elif c_time == "night":
                blocked_info.append(f"🚫 {c_start.strftime('%d/%m')} — בוקר, צהריים (טרם הגיע)")
            elif c_time == "all_day":
                blocked_info.append(f"🚫 {c_start.strftime('%d/%m')} — כל המשמרות (מגיע במהלך)")
            elif c_time == "morning":
                blocked_info.append(f"✅ {c_start.strftime('%d/%m')} — הגעה בבוקר, זמין לכל המשמרות")

    elif c_type == "unavailable":
        time_heb = TIME_LABELS_HEB.get(c_time, c_time)
        if is_range:
            if c_time == "all_day":
                blocked_info.append(
                    f"🚫 {c_start.strftime('%d/%m')} — {actual_end.strftime('%d/%m')} — כל המשמרות"
                )
            else:
                blocked_info.append(
                    f"🚫 {c_start.strftime('%d/%m')} — {actual_end.strftime('%d/%m')} — {time_heb}"
                )
        else:
            if c_time == "all_day":
                blocked_info.append(f"🚫 {c_start.strftime('%d/%m')} — כל המשמרות")
            else:
                blocked_info.append(f"🚫 {c_start.strftime('%d/%m')} — {time_heb}")

    elif c_type == "duty_only":
        date_range = (
            f"{c_start.strftime('%d/%m')} — {actual_end.strftime('%d/%m')}"
            if is_range else c_start.strftime('%d/%m')
        )
        blocked_info.append(f"🛡️ {date_range} — תורן בלבד")
        blocked_info.append("  ↳ החייל ישובץ **רק** למשימות שמירה / ש\"ג / תורנות")
        if c_time == "all_shifts_allowed":
            blocked_info.append("  ↳ ✅ זמין לכל המשמרות (הגבלת משימות בלבד)")
        elif c_time != "all_day":
            time_heb = TIME_LABELS_HEB.get(c_time, c_time)
            blocked_info.append(f"  ↳ 🚫 חסום ממשמרות: {time_heb}")

    elif c_type == "medical":
        date_range = (
            f"{c_start.strftime('%d/%m')} — {actual_end.strftime('%d/%m')}"
            if is_range else c_start.strftime('%d/%m')
        )
        blocked_info.append(f"🏥 {date_range} — הגבלה רפואית")
        if blocked_tasks:
            blocked_info.append("  ↳ 🚫 משימות חסומות:")
            for t in blocked_tasks:
                blocked_info.append(f"    • {t}")
        else:
            blocked_info.append("  ↳ ⚠️ לא נבחרו משימות חסומות")
        if c_time == "all_shifts_allowed":
            blocked_info.append("  ↳ ✅ זמין לכל המשמרות")
        elif c_time != "all_day":
            time_heb = TIME_LABELS_HEB.get(c_time, c_time)
            blocked_info.append(f"  ↳ 🚫 חסום ממשמרות: {time_heb}")

    elif c_type == "custom":
        date_range = (
            f"{c_start.strftime('%d/%m')} — {actual_end.strftime('%d/%m')}"
            if is_range else c_start.strftime('%d/%m')
        )
        blocked_info.append(f"📝 {date_range} — אילוץ מותאם אישית")
        if blocked_tasks:
            blocked_info.append("  ↳ 🚫 משימות חסומות:")
            for t in blocked_tasks:
                blocked_info.append(f"    • {t}")
        if c_time == "all_shifts_allowed":
            blocked_info.append("  ↳ ✅ זמין לכל המשמרות (הגבלת משימות בלבד)")
        elif c_time == "all_day":
            blocked_info.append("  ↳ 🚫 חסום מכל המשמרות")
        else:
            time_heb = TIME_LABELS_HEB.get(c_time, c_time)
            blocked_info.append(f"  ↳ 🚫 חסום ממשמרות: {time_heb}")

    # פיצול info
    if requires_pitzul:
        blocked_info.append(f"🔀 **סימון פיצול בדו\"ח 1** — כל ימי האילוץ")
    elif duration > PITZUL_THRESHOLD_DAYS:
        day4 = c_start + timedelta(days=PITZUL_THRESHOLD_DAYS - 1)
        blocked_info.append(
            f"🔀 **פיצול אוטומטי** — החל מ-{day4.strftime('%d/%m')} (יום {PITZUL_THRESHOLD_DAYS})"
        )

    for line in blocked_info:
        st.markdown(line)


def _render_view_constraints(pid: int):
    st.markdown("### 📋 כל האילוצים")

    constraints = get_period_constraints(pid)
    if not constraints:
        st.info("אין אילוצים מוגדרים.")
        return

    st.markdown(f"**סה\"כ {len(constraints)} אילוצים**")

    # Check for פיצול constraints
    pitzul_list = get_pitzul_constraints(pid)
    if pitzul_list:
        with st.expander(f"🔀 אילוצים הדורשים פיצול ({len(pitzul_list)})", expanded=False):
            for p in pitzul_list:
                st.markdown(
                    f"- **{p['full_name']}** — {p['pitzul_reason']} "
                    f"({p['constraint_date'].strftime('%d/%m')} — "
                    f"{(p.get('end_date') or p['constraint_date']).strftime('%d/%m')})"
                )
            if st.button("🔀 החל פיצול בדו\"ח 1", key="apply_pitzul_btn"):
                applied = apply_pitzul_statuses(pid)
                if applied:
                    st.success(f"✅ פיצול הוחל על {len(applied)} ימים")
                    st.rerun()
                else:
                    st.info("אין שינויים להחיל.")

    st.markdown("---")

    for c in constraints:
        type_label = CONSTRAINT_TYPES.get(c["constraint_type"], c["constraint_type"])
        time_label = TIME_LABELS_HEB.get(c["constraint_time"], c["constraint_time"])
        date_str = c["constraint_date"].strftime("%d/%m/%Y") if c["constraint_date"] else "?"

        # Build date display with range
        if c.get("end_date") and c["end_date"] != c["constraint_date"]:
            end_str = c["end_date"].strftime("%d/%m/%Y")
            duration = (c["end_date"] - c["constraint_date"]).days + 1
            date_display = f"{date_str} — {end_str} ({duration} ימים)"
        else:
            date_display = date_str

        col1, col2, col3 = st.columns([6, 1, 1])
        with col1:
            sleep_badge = " 🔓" if c.get("ignore_sleep") else ""
            pitzul_badge = " 🔀" if c.get("requires_pitzul") else ""
            reason_str = f" · 📝 {c['custom_reason']}" if c.get("custom_reason") else ""
            notes_str = f" — {c['notes']}" if c.get("notes") else ""
            bt = c.get("blocked_tasks") or []
            blocked_str = f" · 🚫 משימות: {', '.join(bt)}" if bt else ""
            st.markdown(
                f"**{c['full_name']}** · {type_label} · {date_display} · {time_label}"
                f"{sleep_badge}{pitzul_badge}{reason_str}{blocked_str}{notes_str}"
            )
        with col2:
            st.caption(f"ID: {c['id']}")
        with col3:
            if st.button("🗑️", key=f"del_constraint_{c['id']}"):
                delete_constraint(c["id"])
                st.rerun()


def _render_calendar_view(pid: int, p_start, p_end):
    st.markdown("### 📅 תצוגת לוח — אילוצים לפי יום")

    constraints = get_period_constraints(pid)
    if not constraints:
        st.info("אין אילוצים.")
        return

    # Group by date — expand ranges
    by_date: dict = {}
    for c in constraints:
        d_start = c["constraint_date"]
        d_end = c.get("end_date") or d_start
        if d_start and d_end:
            current = d_start
            while current <= d_end:
                by_date.setdefault(current, []).append(c)
                current += timedelta(days=1)

    # Show each date with constraints
    heb_days = {
        "Sunday": "ראשון", "Monday": "שני", "Tuesday": "שלישי",
        "Wednesday": "רביעי", "Thursday": "חמישי", "Friday": "שישי",
        "Saturday": "שבת",
    }

    current = p_start
    while current <= p_end:
        date_constraints = by_date.get(current, [])
        if date_constraints:
            day_name = current.strftime("%A")
            day_heb = heb_days.get(day_name, day_name)

            with st.expander(
                f"📅 {current.strftime('%d/%m')} יום {day_heb} — {len(date_constraints)} אילוצים",
                expanded=False,
            ):
                for c in date_constraints:
                    type_label = CONSTRAINT_TYPES.get(c["constraint_type"], c["constraint_type"])
                    time_label = TIME_LABELS_HEB.get(c["constraint_time"], c["constraint_time"])
                    sleep_icon = " 🔓" if c.get("ignore_sleep") else ""
                    pitzul_icon = " 🔀" if c.get("requires_pitzul") else ""
                    reason_str = f" · {c['custom_reason']}" if c.get("custom_reason") else ""
                    bt = c.get("blocked_tasks") or []
                    blocked_str = f" · 🚫 {', '.join(bt)}" if bt else ""
                    st.markdown(
                        f"- **{c['full_name']}** · {type_label} · {time_label}"
                        f"{sleep_icon}{pitzul_icon}{reason_str}{blocked_str}"
                    )
        current += timedelta(days=1)
