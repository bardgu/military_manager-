"""Authentication and user management service."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime

from sqlalchemy import select

from military_manager.database import get_session, User


def _hash_password(password: str, salt: str = "") -> str:
    """Hash a password with SHA-256 + salt."""
    if not salt:
        salt = secrets.token_hex(8)
    hashed = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"{salt}${hashed}"


def _verify_password(password: str, stored_hash: str) -> bool:
    """Verify a password against a stored hash."""
    if "$" not in stored_hash:
        return False
    salt, expected = stored_hash.split("$", 1)
    check = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return check == expected


def create_user(username: str, password: str, display_name: str,
                role: str = "viewer", sub_unit: str | None = None) -> User | None:
    """Create a new user. Returns None if username already exists."""
    normalized_username = username.strip().lower()
    
    with get_session() as session:
        # Check with normalized username to match DB constraint
        existing = session.execute(
            select(User).where(User.username == normalized_username)
        ).scalar_one_or_none()
        if existing:
            return None

        user = User(
            username=normalized_username,
            password_hash=_hash_password(password),
            display_name=display_name.strip(),
            role=role,
            sub_unit=sub_unit,
            is_active=True,
        )
        session.add(user)
        
        try:
            session.commit()
            session.refresh(user)
            return user
        except Exception:
            # Rollback on any error (including UNIQUE constraint)
            session.rollback()
            return None


def authenticate(username: str, password: str) -> dict | None:
    """Authenticate a user. Returns user dict or None."""
    with get_session() as session:
        user = session.execute(
            select(User).where(
                User.username == username.strip().lower(),
                User.is_active == True,
            )
        ).scalar_one_or_none()
        if not user:
            return None
        if not _verify_password(password, user.password_hash):
            return None
        return {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "role": user.role,
            "sub_unit": user.sub_unit,
        }


def change_own_password(user_id: int, old_password: str, new_password: str) -> bool:
    """Change user's own password. Returns True if successful."""
    with get_session() as session:
        user = session.get(User, user_id)
        if not user:
            return False
        
        # Verify old password
        if not _verify_password(old_password, user.password_hash):
            return False
        
        # Update to new password
        user.password_hash = _hash_password(new_password)
        session.commit()
        return True


def get_all_users() -> list[dict]:
    """Get all users."""
    with get_session() as session:
        users = session.execute(
            select(User).order_by(User.role, User.display_name)
        ).scalars().all()
        return [
            {
                "id": u.id,
                "username": u.username,
                "display_name": u.display_name,
                "role": u.role,
                "sub_unit": u.sub_unit,
                "is_active": u.is_active,
            }
            for u in users
        ]


def update_user(user_id: int, **kwargs) -> bool:
    """Update user fields. Handle password separately."""
    with get_session() as session:
        user = session.get(User, user_id)
        if not user:
            return False
        if "password" in kwargs:
            kwargs["password_hash"] = _hash_password(kwargs.pop("password"))
        for key, val in kwargs.items():
            if hasattr(user, key):
                setattr(user, key, val)
        session.commit()
        return True


def delete_user(user_id: int) -> bool:
    """Deactivate a user."""
    return update_user(user_id, is_active=False)


def ensure_default_admin():
    """Create a default admin user if none exists."""
    with get_session() as session:
        any_user = session.execute(select(User)).first()
        if any_user:
            return  # At least one user exists
    # No users — create default מ"פ
    create_user(
        username="mefaked",
        password="1234",
        display_name="מ\"פ ראשי",
        role="mefaked",
    )


ROLE_LABELS = {
    "mefaked": "מ\"פ — גישה מלאה",
    "chopal": "חופ\"ל — רפואה",
    "mm": "מ\"מ — ניהול מחלקה",
    "viewer": "צפייה בלבד",
}


def is_mefaked(user: dict | None) -> bool:
    """Check if user has מ"פ role."""
    return user is not None and user.get("role") == "mefaked"


def is_mm(user: dict | None) -> bool:
    """Check if user has מ"מ role."""
    return user is not None and user.get("role") == "mm"


def is_chopal(user: dict | None) -> bool:
    """Check if user has חופ"ל role."""
    return user is not None and user.get("role") == "chopal"


def can_approve_leave(user: dict | None) -> bool:
    """Only מ"פ can approve leave exceptions."""
    return is_mefaked(user)
