"""User-scoped lead persistence and privileged ownership checks."""

from typing import Any

from fastapi import HTTPException, status
from supabase import Client


def require_owned_lead(
    service_db: Client,
    lead_id: str,
    user_id: str,
) -> None:
    """Preserve the explicit 404/403 distinction for lead mutations."""
    result = (
        service_db.table("leads")
        .select("id,user_id")
        .eq("id", lead_id)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Lead not found.")
    if rows[0].get("user_id") != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this lead.",
        )


def existing_lead_status(db: Client, domain: str) -> str | None:
    """Read a user's existing pipeline status before a domain upsert."""
    result = (
        db.table("leads")
        .select("id,status")
        .eq("domain", domain)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0].get("status") if rows else None


def upsert_lead(db: Client, lead: dict[str, Any]) -> dict[str, Any]:
    """Upsert a lead using the existing per-user domain conflict key."""
    result = db.table("leads").upsert(
        lead,
        on_conflict="user_id,domain",
    ).execute()
    return result.data[0] if result.data else lead


def list_leads(
    db: Client,
    search: str,
    lead_status: str,
    min_score: int,
    limit: int,
) -> list[dict[str, Any]]:
    """Return leads visible to the request-scoped RLS client."""
    query = (
        db.table("leads")
        .select("*")
        .gte("lead_score", min_score)
        .order("created_at", desc=True)
        .limit(limit)
    )
    if lead_status != "all":
        query = query.eq("status", lead_status)
    if search.strip():
        term = search.strip().replace("%", "").replace(",", " ")
        query = query.or_(
            "company_name.ilike.%{term}%,domain.ilike.%{term}%,"
            "description.ilike.%{term}%".format(term=term)
        )
    result = query.execute()
    return result.data or []


def update_lead_status(db: Client, lead_id: str, lead_status: str) -> dict[str, Any]:
    """Update a lead status through the caller's RLS-scoped client."""
    result = (
        db.table("leads")
        .update({"status": lead_status})
        .eq("id", lead_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Lead not found.")
    return result.data[0]


def get_lead(db: Client, lead_id: str) -> dict[str, Any]:
    """Load one lead visible to the caller, retaining the current 404 response."""
    result = db.table("leads").select("*").eq("id", lead_id).limit(1).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Lead not found.")
    return result.data[0]


def update_lead_validation(
    db: Client,
    lead_id: str,
    validation_data: dict[str, Any],
) -> dict[str, Any]:
    """Persist validation fields and return the complete updated row."""
    result = (
        db.table("leads")
        .update(validation_data)
        .eq("id", lead_id)
        .select("*")
        .execute()
    )
    if not result.data:
        raise HTTPException(
            status_code=500,
            detail="Lead validation could not be persisted.",
        )
    return result.data[0]


def lead_statistics(db: Client) -> dict[str, int]:
    """Calculate dashboard statistics from rows permitted by RLS."""
    result = (
        db.table("leads")
        .select("id,lead_score,status,emails")
        .limit(5000)
        .execute()
    )
    rows = result.data or []
    return {
        "total": len(rows),
        "qualified": sum(1 for row in rows if row.get("status") == "qualified"),
        "with_email": sum(1 for row in rows if row.get("emails")),
        "high_score": sum(
            1 for row in rows if (row.get("lead_score") or 0) >= 70
        ),
    }


def exportable_leads(db: Client) -> list[dict[str, Any]]:
    """Load the bounded export projection through the user's RLS client."""
    result = (
        db.table("leads")
        .select(
            "company_name,domain,website_url,emails,"
            "phones,lead_score,status,created_at"
        )
        .order("created_at", desc=True)
        .limit(500)
        .execute()
    )
    return result.data or []
