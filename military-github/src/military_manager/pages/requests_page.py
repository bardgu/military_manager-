"""Requests page — leave, discharge, medical, attachment requests."""

from __future__ import annotations

from datetime import date, datetime

import streamlit as st

from military_manager.components.navigation import render_page_header
from military_manager.components.filters import period_guard
from military_manager.components.auth import get_current_user
from military_manager.services.soldier_service import get_period_soldiers
from military_manager.services.auth_service import (
    get_all_users, ROLE_LABELS, is_mefaked, is_chopal,
)
from military_manager.database import get_session, Request, User
from sqlalchemy import select


REQUEST_TYPES = ["חופשה", "שחרור מוקדם", "רפואי", "צימוד", "אחר"]
REQUEST_STATUSES = ["ממתין", "מאושר", "נדחה"]


# ── helpers ──────────────────────────────────────────────────

def _get_requests(period_id: int, *, current_user: dict | None = None) -> list[dict]:
    """Get requests for a period, filtered by user visibility.

    Rules:
    - מ"פ sees ALL requests.
    - חופ"ל sees medical requests assigned to them, plus their own sent requests.
    - Others see only requests assigned to them, plus their own sent requests.
    - Medical requests are hidden from everyone except חופ"ל and מ"פ.
    """
    with get_session() as session:
        stmt = (
            select(Request)
            .where(Request.period_id == period_id)
            .order_by(Request.created_at.desc())
        )
        results = session.execute(stmt).scalars().all()

        out: list[dict] = []
        for r in results:
            # Build dict first
            d = {
                "id": r.id,
                "soldier_id": r.soldier_id,
                "type": r.request_type,
                "status": r.status,
                "start_date": r.start_date,
                "end_date": r.end_date,
                "reason": r.reason,
                "commander_notes": r.commander_notes,
                "decided_by": r.decided_by,
                "decided_at": r.decided_at,
                "created_at": r.created_at,
                "submitted_by_user_id": r.submitted_by_user_id,
                "assigned_to_user_id": r.assigned_to_user_id,
            }

            # resolve display names
            if r.submitted_by_user_id:
                u = session.get(User, r.submitted_by_user_id)
                d["submitted_by_name"] = u.display_name if u else ""
            else:
                d["submitted_by_name"] = ""

            if r.assigned_to_user_id:
                u = session.get(User, r.assigned_to_user_id)
                d["assigned_to_name"] = u.display_name if u else ""
            else:
                d["assigned_to_name"] = ""

            # Visibility rules
            if current_user is None:
                out.append(d)
                continue

            uid = current_user.get("id")

            # מ"פ sees everything
            if is_mefaked(current_user):
                out.append(d)
                continue

            is_medical = (r.request_type == "רפואי")

            # Medical requests — only חופ"ל (or sender) can see
            if is_medical:
                if is_chopal(current_user) or r.submitted_by_user_id == uid:
                    out.append(d)
                continue

            # Non-medical: assigned to me OR I sent it
            if r.assigned_to_user_id == uid or r.submitted_by_user_id == uid:
                out.append(d)

        return out


def _create_request(
    period_id: int,
    soldier_id: int,
    request_type: str,
    start_date: date,
    end_date: date,
    reason: str = "",
    submitted_by_user_id: int | None = None,
    assigned_to_user_id: int | None = None,
):
    """Create a new request."""
    with get_session() as session:
        req = Request(
            period_id=period_id,
            soldier_id=soldier_id,
            request_type=request_type,
            status="ממתין",
            start_date=start_date,
            end_date=end_date,
            reason=reason,
            submitted_by_user_id=submitted_by_user_id,
            assigned_to_user_id=assigned_to_user_id,
        )
        session.add(req)
        session.commit()
        return req


def _update_request_status(request_id: int, status: str, notes: str = "",
                           decided_by_name: str = ""):
    """Update request status (approve/reject)."""
    with get_session() as session:
        req = session.get(Request, request_id)
        if req:
            req.status = status
            req.commander_notes = notes
            req.decided_by = decided_by_name
            req.decided_at = datetime.utcnow()
            session.commit()


def _can_decide(request_dict: dict, current_user: dict | None) -> bool:
    """Check if current user can approve/reject a given request."""
    if current_user is None:
        return False
    # מ"פ can always decide
    if is_mefaked(current_user):
        return True
    # Otherwise, must be the assigned user
    return request_dict.get("assigned_to_user_id") == current_user.get("id")


def _get_target_users(request_type: str) -> list[dict]:
    """Return list of users eligible to receive a request of the given type.

    Medical → only חופ"ל users.
    Others  → מ"פ + חופ"ל + מ"מ (anyone who can act on requests).
    """
    all_users = get_all_users()
    active = [u for u in all_users if u.get("is_active")]
    if request_type == "רפואי":
        return [u for u in active if u["role"] == "chopal"]
    # Non-medical: any non-viewer
    return [u for u in active if u["role"] != "viewer"]


# ── render ───────────────────────────────────────────────────

def render():
    render_page_header("📨 בקשות", "ניהול בקשות חופשה, שחרור, רפואי וצימוד")

    period = period_guard()
    if not period:
        return

    current_user = get_current_user()
    pid = period["id"]

    tab_pending, tab_all, tab_new = st.tabs(["⏳ ממתינות", "📋 כל הבקשות", "➕ בקשה חדשה"])

    soldiers = get_period_soldiers(pid)
    soldier_map = {s["soldier_id"]: s.get("full_name", "") for s in soldiers}

    # ── Pending requests ──
    with tab_pending:
        requests = _get_requests(pid, current_user=current_user)
        pending = [r for r in requests if r["status"] == "ממתין"]

        if not pending:
            st.info("אין בקשות ממתינות 🎉")
        else:
            st.markdown(f"**{len(pending)} בקשות ממתינות**")

            for req in pending:
                name = soldier_map.get(req["soldier_id"], f"#{req['soldier_id']}")
                with st.expander(f"📨 {name} — {req['type']} ({req['start_date']} — {req['end_date']})"):
                    st.markdown(f"**סוג:** {req['type']}")
                    st.markdown(f"**תאריכים:** {req['start_date']} — {req['end_date']}")
                    if req["reason"]:
                        st.markdown(f"**סיבה:** {req['reason']}")
                    if req["submitted_by_name"]:
                        st.markdown(f"**נשלח ע\"י:** {req['submitted_by_name']}")
                    if req["assigned_to_name"]:
                        st.markdown(f"**יעד:** {req['assigned_to_name']}")

                    if _can_decide(req, current_user):
                        notes = st.text_area("הערות מפקד", key=f"notes_{req['id']}")
                        c1, c2 = st.columns(2)
                        with c1:
                            if st.button("✅ אשר", key=f"approve_{req['id']}"):
                                decided_name = current_user.get("display_name", "") if current_user else ""
                                _update_request_status(req["id"], "מאושר", notes, decided_name)
                                st.success("הבקשה אושרה")
                                st.rerun()
                        with c2:
                            if st.button("❌ דחה", key=f"reject_{req['id']}"):
                                decided_name = current_user.get("display_name", "") if current_user else ""
                                _update_request_status(req["id"], "נדחה", notes, decided_name)
                                st.success("הבקשה נדחתה")
                                st.rerun()
                    else:
                        st.caption("⏳ הבקשה ממתינה לאישור הגורם המוסמך")
                    # Show commander response if exists
                    if req.get("commander_notes"):
                        st.info(f"💬 תגובת מפקד: {req['commander_notes']}")
                    if req.get("decided_by"):
                        decided_at_str = ""
                        if req.get("decided_at"):
                            decided_at_str = f" ({req['decided_at'].strftime('%d/%m/%Y %H:%M') if hasattr(req['decided_at'], 'strftime') else req['decided_at']})"
                        st.caption(f"החלטה ע\"י: {req['decided_by']}{decided_at_str}")

    # ── All requests ──
    with tab_all:
        requests = _get_requests(pid, current_user=current_user)
        if not requests:
            st.info("אין בקשות לתקופה זו")
        else:
            # Filter by status
            status_filter = st.selectbox("סנן לפי סטטוס", ["הכל"] + REQUEST_STATUSES)
            filtered_requests = requests if status_filter == "הכל" else [r for r in requests if r["status"] == status_filter]

            st.markdown(f"**{len(filtered_requests)} בקשות**")

            for req in filtered_requests:
                name = soldier_map.get(req["soldier_id"], f"#{req['soldier_id']}")
                status_icon = {"ממתין": "⏳", "מאושר": "✅", "נדחה": "❌"}.get(req["status"], "📨")
                with st.expander(f"{status_icon} {name} — {req['type']} | {req['status']} ({req['start_date']} — {req['end_date']})"):
                    c1, c2 = st.columns(2)
                    with c1:
                        st.markdown(f"**סוג:** {req['type']}")
                        st.markdown(f"**תאריכים:** {req['start_date']} — {req['end_date']}")
                        if req["reason"]:
                            st.markdown(f"**סיבה:** {req['reason']}")
                    with c2:
                        st.markdown(f"**סטטוס:** {req['status']}")
                        if req["submitted_by_name"]:
                            st.markdown(f"**נשלח ע\"י:** {req['submitted_by_name']}")
                        if req["assigned_to_name"]:
                            st.markdown(f"**יעד:** {req['assigned_to_name']}")
                    # Show commander response
                    if req.get("commander_notes"):
                        st.success(f"💬 תגובת מפקד: {req['commander_notes']}")
                    if req.get("decided_by"):
                        decided_at_str = ""
                        if req.get("decided_at"):
                            decided_at_str = f" ({req['decided_at'].strftime('%d/%m/%Y %H:%M') if hasattr(req['decided_at'], 'strftime') else req['decided_at']})"
                        st.caption(f"החלטה ע\"י: {req['decided_by']}{decided_at_str}")

    # ── New request ──
    with tab_new:
        with st.form("new_request_form"):
            st.markdown("### הגשת בקשה חדשה")

            soldier_name = st.selectbox(
                "חייל",
                [s.get("full_name", "") for s in soldiers],
            )
            request_type = st.selectbox("סוג בקשה", REQUEST_TYPES)

            # Target user selection
            target_users = _get_target_users(request_type)
            if target_users:
                target_options = {
                    f"{u['display_name']} ({ROLE_LABELS.get(u['role'], u['role'])})": u["id"]
                    for u in target_users
                }
                if request_type == "רפואי" and len(target_users) == 1:
                    # Auto-select the single חופ"ל
                    target_label = st.selectbox(
                        "יעד (מקבל הבקשה)",
                        list(target_options.keys()),
                        disabled=True,
                        help="בקשות רפואיות נשלחות לחופ\"ל בלבד",
                    )
                else:
                    target_label = st.selectbox("יעד (מקבל הבקשה)", list(target_options.keys()))
                assigned_user_id = target_options.get(target_label) if target_label else None
            else:
                st.warning("לא נמצאו משתמשים מתאימים לטיפול בבקשה זו")
                assigned_user_id = None

            c1, c2 = st.columns(2)
            with c1:
                start_date = st.date_input("מתאריך", value=date.today(), format="DD/MM/YYYY")
            with c2:
                end_date = st.date_input("עד תאריך", value=date.today(), format="DD/MM/YYYY")

            reason = st.text_area("סיבה / פירוט")

            if st.form_submit_button("📨 שלח בקשה"):
                sid = next((s["soldier_id"] for s in soldiers if s.get("full_name") == soldier_name), None)
                if not sid:
                    st.error("יש לבחור חייל")
                elif start_date > end_date:
                    st.error("תאריך התחלה חייב להיות לפני תאריך סיום")
                elif not assigned_user_id:
                    st.error("יש לבחור יעד לבקשה")
                else:
                    submitter_id = current_user.get("id") if current_user else None
                    _create_request(
                        pid, sid, request_type, start_date, end_date, reason,
                        submitted_by_user_id=submitter_id,
                        assigned_to_user_id=assigned_user_id,
                    )
                    st.success("הבקשה נשלחה בהצלחה!")
                    st.rerun()
