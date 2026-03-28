"""Company CRUD service — manage platoon/company entities."""

from __future__ import annotations

from sqlalchemy import select

from military_manager.database import get_session, Company
from military_manager.logger import log_action

# Default companies (platoons)
DEFAULT_COMPANIES = [
    {"name": "פלוגה א", "code": "A"},
    {"name": "פלוגה ב", "code": "B"},
    {"name": "פלוגה ג", "code": "C"},
    {"name": "פלוגת מפקדה", "code": "HQ"},
]


def ensure_default_companies() -> None:
    """Create default companies if none exist."""
    with get_session() as session:
        existing = session.execute(select(Company)).scalars().first()
        if existing is not None:
            return  # companies already exist

        for c in DEFAULT_COMPANIES:
            session.add(Company(name=c["name"], code=c["code"], is_active=True))
        session.commit()
        log_action("default_companies_created", {"count": len(DEFAULT_COMPANIES)})


def get_all_companies(active_only: bool = True) -> list[dict]:
    """Return list of companies as dicts."""
    with get_session() as session:
        stmt = select(Company).order_by(Company.id)
        if active_only:
            stmt = stmt.where(Company.is_active == True)
        rows = session.execute(stmt).scalars().all()
        return [
            {
                "id": c.id,
                "name": c.name,
                "code": c.code,
                "is_active": c.is_active,
            }
            for c in rows
        ]


def get_company_by_id(company_id: int) -> dict | None:
    """Get a single company by ID."""
    with get_session() as session:
        c = session.get(Company, company_id)
        if not c:
            return None
        return {
            "id": c.id,
            "name": c.name,
            "code": c.code,
            "is_active": c.is_active,
        }


def create_company(name: str, code: str) -> dict:
    """Create a new company."""
    with get_session() as session:
        c = Company(name=name, code=code, is_active=True)
        session.add(c)
        session.commit()
        session.refresh(c)
        log_action("company_created", {"id": c.id, "name": name})
        return {"id": c.id, "name": c.name, "code": c.code, "is_active": c.is_active}


def get_company_name(company_id: int | None) -> str:
    """Get company name by ID, or empty string if None/not found."""
    if company_id is None:
        return ""
    c = get_company_by_id(company_id)
    return c["name"] if c else ""
