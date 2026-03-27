"""Settings page — configure statuses, default values, import, and system info."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import streamlit as st

from military_manager.components.navigation import render_page_header
from military_manager.components.filters import period_guard
from military_manager.components.auth import require_role, get_current_user
from military_manager.config import DATABASE_PATH, DEFAULT_STATUSES
from military_manager.services.period_service import get_status_options, get_active_period, activate_period, get_all_periods
from military_manager.services.backup_service import (
    create_backup, list_backups, restore_backup, verify_integrity, get_db_stats,
)
from military_manager.services.stats_service import (
    get_status_groups, save_status_group, delete_status_group,
    init_default_groups, get_setting, set_setting,
    get_irrelevant_soldiers, set_soldier_irrelevant,
)
from military_manager.services.soldier_service import get_period_soldiers
from military_manager.database import get_session, StatusOption
from sqlalchemy import select


def render():
    render_page_header("⚙️ הגדרות", "הגדרות מערכת ותצורה")

    tab_status, tab_groups, tab_alerts, tab_irrelevant, tab_import, tab_backup, tab_system = st.tabs([
        "🏷️ סטטוסים", "📊 קבוצות סטטוס", "🚨 ספים והתראות",
        "🚫 לא רלוונטיים", "📥 ייבוא מלא", "💾 גיבויים", "🖥️ מערכת"
    ])

    # ── Status options management ──
    with tab_status:
        _render_status_settings()

    # ── Status groups ──
    with tab_groups:
        _render_status_groups_settings()

    # ── Alerts & thresholds ──
    with tab_alerts:
        _render_alert_settings()

    # ── Irrelevant soldiers ──
    with tab_irrelevant:
        _render_irrelevant_soldiers()

    # ── Full import ──
    with tab_import:
        _render_full_import()

    # ── Backups ──
    with tab_backup:
        _render_backup_management()

    # ── System info ──
    with tab_system:
        _render_system_info()


def _render_status_settings():
    """Manage status options for the active period."""
    period = period_guard()
    if not period:
        return

    pid = period["id"]
    st.markdown("### הגדרת סטטוסים")
    st.caption("סטטוסים זמינים לתקופה הנוכחית. ניתן להוסיף, לערוך ולהסיר.")

    options = get_status_options(pid)

    if options:
        for opt in options:
            col1, col2, col3, col4 = st.columns([3, 2, 1, 1])
            with col1:
                st.markdown(f"**{opt.name}**")
            with col2:
                st.markdown(f"קטגוריה: {opt.category or '—'}")
            with col3:
                if opt.color:
                    st.markdown(
                        f'<span style="background:{opt.color};padding:2px 10px;border-radius:8px;">'
                        f'&nbsp;</span>',
                        unsafe_allow_html=True,
                    )
            with col4:
                if st.button("🗑️", key=f"del_status_{opt.id}"):
                    with get_session() as session:
                        status = session.get(StatusOption, opt.id)
                        if status:
                            session.delete(status)
                            session.commit()
                    st.rerun()

    # Add new status
    st.markdown("---")
    st.markdown("### הוסף סטטוס")
    with st.form("add_status_form"):
        c1, c2, c3 = st.columns(3)
        with c1:
            name = st.text_input("שם הסטטוס")
        with c2:
            category = st.selectbox("קטגוריה", ["present", "away", "special", "absent", "other"])
        with c3:
            color = st.color_picker("צבע", "#4CAF50")

        if st.form_submit_button("➕ הוסף"):
            if name:
                with get_session() as session:
                    existing = [o.name for o in options]
                    if name not in existing:
                        max_order = max((o.sort_order for o in options), default=0)
                        new_status = StatusOption(
                            period_id=pid,
                            name=name,
                            category=category,
                            color=color,
                            sort_order=max_order + 1,
                        )
                        session.add(new_status)
                        session.commit()
                        st.success(f"סטטוס '{name}' נוסף")
                        st.rerun()
                    else:
                        st.warning("סטטוס עם שם זה כבר קיים")
            else:
                st.error("יש להזין שם לסטטוס")

    # Reset to defaults
    if st.button("🔄 אפס לברירות מחדל"):
        with get_session() as session:
            # Delete existing
            for opt in options:
                o = session.get(StatusOption, opt.id)
                if o:
                    session.delete(o)
            # Add defaults
            for i, s in enumerate(DEFAULT_STATUSES):
                session.add(StatusOption(
                    period_id=pid,
                    name=s["name"],
                    category=s.get("category", "other"),
                    color=s.get("color", "#9E9E9E"),
                    sort_order=i,
                ))
            session.commit()
        st.success("הסטטוסים אופסו לברירות מחדל")
        st.rerun()


def _render_status_groups_settings():
    """Manage status groups — user defines which statuses belong to each group."""
    period = period_guard()
    if not period:
        return

    user = get_current_user()
    is_mfkd = user and user.get("role") == "mefaked"

    if not is_mfkd:
        st.info("🔒 ניהול קבוצות סטטוס זמין רק למ\"פ")

    pid = period["id"]

    st.markdown("### 📊 הגדרת קבוצות סטטוס")
    st.caption(
        "הגדר קבוצות סטטוס לצורך חישוב אחוזים. "
        "לדוגמה: 'בבסיס' = התייצב + חוזר מחופש + בבסיס. "
        "הקבוצות ישמשו לחישוב אוטומטי בדף הבית ובדוחות."
    )

    # Init defaults if none exist
    init_default_groups(pid)
    groups = get_status_groups(pid)

    # Get all available status names from this period
    from military_manager.services.period_service import get_status_options
    status_opts = get_status_options(pid)
    all_status_names = [s.name for s in status_opts] if status_opts else []

    # Show existing groups
    if groups:
        for grp in groups:
            with st.expander(f"📁 {grp['name']}  ({len(grp['statuses'])} סטטוסים)", expanded=False):
                st.markdown(f"**סטטוסים בקבוצה:** {', '.join(grp['statuses']) if grp['statuses'] else '—'}")

                if is_mfkd:
                    # Edit form
                    new_statuses = st.multiselect(
                        "ערוך סטטוסים בקבוצה",
                        all_status_names,
                        default=[s for s in grp["statuses"] if s in all_status_names],
                        key=f"grp_edit_{grp['id']}",
                    )
                    new_color = st.color_picker("צבע", grp.get("color", "#9E9E9E"), key=f"grp_color_{grp['id']}")

                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button("💾 שמור", key=f"grp_save_{grp['id']}"):
                            save_status_group(pid, grp["name"], new_statuses, new_color)
                            st.success(f"קבוצה '{grp['name']}' עודכנה")
                            st.rerun()
                    with c2:
                        if st.button("🗑️ מחק", key=f"grp_del_{grp['id']}"):
                            delete_status_group(grp["id"])
                            st.success(f"קבוצה '{grp['name']}' נמחקה")
                            st.rerun()

    # Add new group
    if is_mfkd:
        st.markdown("---")
        st.markdown("### ➕ הוסף קבוצה חדשה")
        with st.form("add_group_form"):
            grp_name = st.text_input("שם הקבוצה")
            grp_statuses = st.multiselect("סטטוסים בקבוצה", all_status_names)
            grp_color = st.color_picker("צבע", "#607D8B")

            if st.form_submit_button("➕ הוסף"):
                if grp_name:
                    existing_names = [g["name"] for g in groups]
                    if grp_name in existing_names:
                        st.warning("קבוצה עם שם זה כבר קיימת")
                    else:
                        save_status_group(pid, grp_name, grp_statuses, grp_color)
                        st.success(f"קבוצה '{grp_name}' נוספה")
                        st.rerun()
                else:
                    st.error("יש להזין שם לקבוצה")

        # Reset groups to defaults
        if st.button("🔄 אפס קבוצות לברירות מחדל", key="reset_groups"):
            # Delete all existing groups
            for g in groups:
                delete_status_group(g["id"])
            # Re-init
            from military_manager.services.stats_service import DEFAULT_STATUS_GROUPS
            for grp_def in DEFAULT_STATUS_GROUPS:
                save_status_group(pid, grp_def["name"], grp_def["statuses"], grp_def.get("color", "#9E9E9E"))
            st.success("הקבוצות אופסו לברירות מחדל")
            st.rerun()


def _render_alert_settings():
    """Configure alert thresholds — only מ\"פ can change."""
    period = period_guard()
    if not period:
        return

    user = get_current_user()
    is_mfkd = user and user.get("role") == "mefaked"

    pid = period["id"]

    st.markdown("### 🚨 הגדרת ספים והתראות")
    st.caption("הגדר ספים לאחוזים — כאשר הסף נחצה, המספר יוצג באדום בדף הבית ובדוחות.")

    current_threshold = get_setting(pid, "home_alert_percent", "25")

    if is_mfkd:
        with st.form("alert_settings_form"):
            st.markdown("#### 🏠 אחוז חיילים בבית (חופש)")
            new_threshold = st.number_input(
                "סף אחוז בבית (מעל ערך זה — התראה אדומה)",
                min_value=5, max_value=100, value=int(float(current_threshold)),
                step=5,
                help="אם אחוז החיילים בחופש (מתוך סה\"כ בשמ\"פ) עולה על ערך זה, יוצג באדום",
            )

            if st.form_submit_button("💾 שמור"):
                set_setting(pid, "home_alert_percent", str(new_threshold))
                st.success(f"הסף עודכן ל-{new_threshold}%")
                st.rerun()
    else:
        st.info("🔒 רק מ\"פ יכול לשנות את ספי ההתראות")

    st.markdown("---")
    st.markdown(f"**סף נוכחי:** {current_threshold}% — אם אחוז החיילים בחופש עולה מעל ערך זה, יופיע באדום.")


def _render_irrelevant_soldiers():
    """Manage the 'irrelevant for operations' soldier group — מ\"פ only."""
    period = period_guard()
    if not period:
        return

    user = get_current_user()
    is_mfkd = user and user.get("role") == "mefaked"

    pid = period["id"]

    st.markdown("### 🚫 חיילים לא רלוונטיים בתעסוקה")
    st.caption(
        "חיילים שלא התייצבו או לא רלוונטיים לתעסוקה השוטפת. "
        "הם לא ייספרו בחישובי הנוכחות ולא יידרשו דיווח יומי. "
        "רק מ\"פ יכול להוסיף/להסיר חיילים מהרשימה."
    )

    # Show current irrelevant soldiers
    irr_soldiers = get_irrelevant_soldiers(pid)

    if irr_soldiers:
        st.markdown(f"**{len(irr_soldiers)} חיילים לא רלוונטיים:**")
        for sol in irr_soldiers:
            c1, c2, c3 = st.columns([4, 2, 1])
            with c1:
                st.markdown(f"👤 **{sol['name']}**")
            with c2:
                st.caption(f"{sol['sub_unit']} — {sol['role'] or '—'}")
            with c3:
                if is_mfkd:
                    if st.button("🔄 החזר", key=f"unirr_{sol['period_soldier_id']}"):
                        set_soldier_irrelevant(sol["period_soldier_id"], False)
                        st.success(f"{sol['name']} הוחזר לרשימה הפעילה")
                        st.rerun()
    else:
        st.info("אין חיילים לא רלוונטיים כרגע")

    # Add soldiers to irrelevant
    if is_mfkd:
        st.markdown("---")
        st.markdown("### ➕ הוסף חיילים לרשימת לא רלוונטיים")

        all_soldiers = get_period_soldiers(pid)
        irr_ids = {s["soldier_id"] for s in irr_soldiers}
        available = [s for s in all_soldiers if s["soldier_id"] not in irr_ids]

        if available:
            soldier_options = {
                f"{s.get('full_name', '—')} ({s.get('sub_unit', '—')})": s
                for s in available
            }
            selected_names = st.multiselect(
                "בחר חיילים",
                list(soldier_options.keys()),
                key="add_irrelevant",
            )

            if st.button("➕ הוסף לרשימה", key="confirm_add_irrelevant"):
                if selected_names:
                    for name in selected_names:
                        sol = soldier_options[name]
                        # Need period_soldier_id
                        from military_manager.database import PeriodSoldier
                        with get_session() as session:
                            ps = session.execute(
                                select(PeriodSoldier).where(
                                    PeriodSoldier.period_id == pid,
                                    PeriodSoldier.soldier_id == sol["soldier_id"],
                                )
                            ).scalar_one_or_none()
                            if ps:
                                ps.is_irrelevant = True
                                session.commit()
                    st.success(f"נוספו {len(selected_names)} חיילים לרשימת לא רלוונטיים")
                    st.rerun()
                else:
                    st.warning("בחר חיילים להוספה")
        else:
            st.caption("כל החיילים כבר ברשימה")
    else:
        st.info("🔒 רק מ\"פ יכול לנהל את רשימת החיילים הלא רלוונטיים")


def _render_full_import():
    """Render full Excel import UI."""
    period = period_guard()
    if not period:
        return

    pid = period["id"]

    st.markdown("### ייבוא מלא מקובץ אקסל")
    st.info(
        "ייבוא כל הנתונים מקובץ האקסל של הפלוגה: "
        "חיילים, סטטוסים, משימות וציוד."
    )

    uploaded = st.file_uploader("בחר קובץ אקסל", type=["xlsx", "xls"], key="full_import")

    if uploaded:
        import tempfile
        from military_manager.services.excel_import import full_import, get_available_sheets

        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as f:
            f.write(uploaded.getvalue())
            temp_path = f.name

        sheets = get_available_sheets(temp_path)
        st.markdown(f"**גיליונות שנמצאו ({len(sheets)}):** {', '.join(sheets[:10])}")

        if st.button("🚀 התחל ייבוא מלא"):
            with st.spinner("מייבא נתונים..."):
                results = full_import(temp_path, pid)

            # Show results
            for key, label in [
                ("roster", "חיילים"),
                ("status", "סטטוסים"),
                ("tasks", "משימות"),
                ("equipment", "ציוד"),
            ]:
                result = results.get(key)
                if result:
                    st.markdown(f"**{label}:**")
                    if key == "roster":
                        created = result.get("created", 0)
                        updated = result.get("updated", 0)
                        officers = result.get("officers", 0)
                        enlisted = result.get("enlisted", 0)
                        total_imported = created + updated
                        st.success(
                            f"✅ יובאו {total_imported} חיילים "
                            f"({created} חדשים, {updated} עודכנו) — "
                            f"קצינים: {officers}, חוגרים: {enlisted}"
                        )
                        if result.get("errors"):
                            with st.expander(f"שגיאות ({len(result['errors'])})"):
                                for err in result["errors"]:
                                    st.caption(err)
                    else:
                        st.json(result)
                else:
                    st.caption(f"{label}: לא נמצא גיליון מתאים")

        # Cleanup
        try:
            Path(temp_path).unlink()
        except Exception:
            pass


def _render_system_info():
    """Show system information and protected admin actions."""
    user = get_current_user()
    is_mefaked = user and user.get("role") == "mefaked"

    st.markdown("### 📊 מידע מערכת")

    # --- Database stats ---
    stats = get_db_stats()
    if stats.get("exists"):
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("גודל מסד נתונים", f"{stats['size_mb']:.2f} MB")
        with col2:
            integrity_ok = stats.get("integrity", False)
            st.metric("תקינות", "✅ תקין" if integrity_ok else "❌ בעיה!")
        with col3:
            st.metric("עדכון אחרון", stats["modified"].strftime("%d/%m/%Y %H:%M"))

        # Record counts
        st.markdown("#### 📈 סטטיסטיקות נתונים")
        table_labels = {
            "count_soldiers": "חיילים",
            "count_reserve_periods": "תקופות",
            "count_period_soldiers": "שיבוצים לתקופות",
            "count_daily_statuses": "סטטוסים יומיים",
            "count_tasks": "משימות",
            "count_task_slots": "עמדות",
            "count_shift_assignments": "שיבוצים למשמרות",
            "count_soldier_constraints": "אילוצים",
            "count_period_drivers": "נהגים",
            "count_audit_log": "רשומות לוג",
        }
        cols = st.columns(5)
        for i, (key, label) in enumerate(table_labels.items()):
            count = stats.get(key, 0)
            with cols[i % 5]:
                st.metric(label, f"{count:,}")
    else:
        st.warning("מסד הנתונים לא נמצא")

    # --- Backup info ---
    st.markdown("---")
    backups = list_backups()
    st.markdown(f"### 💾 מצב גיבויים")
    if backups:
        last = backups[0]
        st.success(f"גיבוי אחרון: {last['created_at'].strftime('%d/%m/%Y %H:%M')} ({last['reason']}) — {last['size_mb']} MB")
        st.caption(f"סה\"כ {len(backups)} גיבויים שמורים")
    else:
        st.warning("אין גיבויים — מומלץ לגבות עכשיו!")

    # --- Active period selector ---
    st.markdown("---")
    st.markdown("### 🔄 החלפת תקופה פעילה")
    periods = get_all_periods()
    if periods:
        options = {f"{p.name} ({p.start_date} — {p.end_date})": p.id for p in periods}
        selected = st.selectbox("בחר תקופה", list(options.keys()))
        if st.button("🔄 הפעל תקופה"):
            period_id = options[selected]
            activate_period(period_id)
            p = next(p for p in periods if p.id == period_id)
            st.session_state["active_period"] = {
                "id": p.id,
                "name": p.name,
                "location": p.location,
                "start_date": str(p.start_date),
                "end_date": str(p.end_date),
            }
            st.success(f"תקופה '{p.name}' הופעלה!")
            st.rerun()

    # --- DANGER ZONE: DB Reset (מ"פ only) ---
    st.markdown("---")
    st.markdown("### ⚠️ אזור מסוכן")

    if not is_mefaked:
        st.info("🔒 פעולות ניהול מסד נתונים זמינות רק למ\"פ ראשי")
        return

    st.warning("⚠️ הפעולות הבאות הן בלתי הפיכות! גיבוי אוטומטי ייוצר לפני כל פעולה.")

    with st.expander("🗑️ איפוס מסד נתונים — מחיקת כל הנתונים", expanded=False):
        st.error(
            "**שימו לב!** פעולה זו תמחק את **כל** הנתונים במערכת:\n"
            "חיילים, סטטוסים, משימות, שיבוצים, אילוצים, נהגים — **הכל.**\n\n"
            "גיבוי אוטומטי ייוצר לפני המחיקה."
        )
        confirm_text = st.text_input(
            "הקלד **מחק הכל** לאישור:",
            key="reset_confirm_text",
        )
        if st.button("🗑️ אישור סופי — מחק את כל הנתונים", type="primary", key="do_reset"):
            if confirm_text.strip() == "מחק הכל":
                # Create safety backup first
                backup = create_backup(reason="pre-reset", created_by=user.get("display_name"))
                if backup:
                    st.info(f"💾 גיבוי בטיחות נוצר: {backup.name}")

                db_path = Path(DATABASE_PATH)
                for ext in ["", "-wal", "-shm"]:
                    p = Path(str(db_path) + ext)
                    if p.exists():
                        p.unlink()
                from military_manager.database import init_db
                init_db()
                st.session_state["active_period"] = None
                st.success("מסד הנתונים אופס. הגיבוי שנוצר זמין בטאב 'גיבויים'.")
                st.rerun()
            else:
                st.error("יש להקליד 'מחק הכל' לאישור הפעולה")


def _render_backup_management():
    """Backup management tab — create, restore, download backups."""
    user = get_current_user()
    is_mefaked = user and user.get("role") == "mefaked"

    st.markdown("### 💾 ניהול גיבויים")
    st.info(
        "המערכת מגבה אוטומטית את מסד הנתונים:\n"
        "- **בכל הפעלה** של השרת\n"
        "- **כל 4 שעות** באופן אוטומטי\n"
        "- **לפני כל פעולה מסוכנת** (מחיקה, שחזור)\n\n"
        "נשמרים עד 30 גיבויים אחרונים."
    )

    # Manual backup (any user)
    st.markdown("#### ➕ גיבוי ידני")
    if st.button("💾 צור גיבוי עכשיו", type="primary", key="manual_backup"):
        backup = create_backup(
            reason="manual",
            created_by=user.get("display_name") if user else None,
        )
        if backup:
            st.success(f"✅ גיבוי נוצר: {backup.name}")
            st.rerun()
        else:
            st.error("❌ שגיאה ביצירת גיבוי")

    # Integrity check
    st.markdown("---")
    st.markdown("#### 🔍 בדיקת תקינות")
    if st.button("🔍 בדוק תקינות מסד הנתונים", key="check_integrity"):
        ok = verify_integrity()
        if ok:
            st.success("✅ מסד הנתונים תקין — אין בעיות שלמות נתונים")
        else:
            st.error("❌ נמצאה בעיה בשלמות מסד הנתונים! מומלץ לשחזר מגיבוי.")

    # List existing backups
    st.markdown("---")
    st.markdown("#### 📋 גיבויים קיימים")
    backups = list_backups()

    if not backups:
        st.caption("אין גיבויים עדיין")
        return

    for i, bk in enumerate(backups):
        reason_labels = {
            "startup": "🟢 הפעלת שרת",
            "auto": "🔄 אוטומטי",
            "manual": "👤 ידני",
            "pre-restore": "🔙 לפני שחזור",
            "pre-reset": "🗑️ לפני איפוס",
        }
        reason_display = reason_labels.get(bk["reason"], bk["reason"])
        ts = bk["created_at"].strftime("%d/%m/%Y %H:%M")

        col1, col2, col3, col4 = st.columns([3, 2, 1.5, 1.5])
        with col1:
            st.markdown(f"**{bk['filename']}**")
        with col2:
            st.caption(f"{ts} — {reason_display}")
        with col3:
            st.caption(f"{bk['size_mb']} MB")
        with col4:
            # Download
            try:
                with open(bk["path"], "rb") as f:
                    st.download_button(
                        "⬇️ הורד",
                        data=f.read(),
                        file_name=bk["filename"],
                        key=f"dl_bk_{i}",
                    )
            except Exception:
                st.caption("—")

    # Restore (מ"פ only)
    if not is_mefaked:
        st.markdown("---")
        st.info("🔒 שחזור מגיבוי זמין רק למ\"פ ראשי")
        return

    st.markdown("---")
    st.markdown("#### 🔙 שחזור מגיבוי (מ\"פ בלבד)")
    st.warning("שחזור יחליף את **כל** הנתונים הנוכחיים בנתונים מהגיבוי. גיבוי בטיחות ייוצר אוטומטית לפני השחזור.")

    backup_options = {
        f"{bk['created_at'].strftime('%d/%m/%Y %H:%M')} — {bk['reason']} ({bk['size_mb']} MB)": bk["path"]
        for bk in backups
    }
    selected_restore = st.selectbox("בחר גיבוי לשחזור", ["—"] + list(backup_options.keys()), key="restore_select")

    if selected_restore != "—":
        confirm_restore = st.text_input("הקלד **שחזור** לאישור:", key="restore_confirm")
        if st.button("🔙 שחזר מגיבוי", type="primary", key="do_restore"):
            if confirm_restore.strip() == "שחזור":
                restore_path = backup_options[selected_restore]
                ok = restore_backup(restore_path)
                if ok:
                    st.session_state["active_period"] = None
                    st.success("✅ מסד הנתונים שוחזר בהצלחה!")
                    st.rerun()
                else:
                    st.error("❌ שגיאה בשחזור — ייתכן שהגיבוי פגום")
            else:
                st.error("יש להקליד 'שחזור' לאישור הפעולה")
