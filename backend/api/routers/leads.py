"""Lead list and pipeline-status endpoints."""

from fastapi import APIRouter, Depends, Query

from core.rate_limiter import user_rate_limit
from core.security import AuthenticatedUser, get_db, get_user_db, require_user
from models.requests import StatusRequest
from repositories.lead_repository import (
    list_leads as list_user_leads,
    require_owned_lead,
    update_lead_status,
)


router = APIRouter(tags=["leads"])


@router.get(
    "/api/leads",
    dependencies=[Depends(user_rate_limit("leads", 60, 60))],
)
def list_leads(
    search: str = Query(default="", max_length=150),
    status: str = Query(
        default="all",
        pattern="^(all|new|reviewed|qualified|disqualified)$",
    ),
    min_score: int = Query(default=0, ge=0, le=100),
    limit: int = Query(default=100, ge=1, le=500),
    user: AuthenticatedUser = Depends(require_user),
) -> dict:
    """List only the caller's leads through the RLS-scoped client."""
    leads = list_user_leads(
        get_user_db(user),
        search,
        status,
        min_score,
        limit,
    )
    return {"leads": leads}


@router.patch(
    "/api/leads/{lead_id}/status",
    dependencies=[Depends(user_rate_limit("status_update", 30, 60))],
)
def update_status(
    lead_id: str,
    request: StatusRequest,
    user: AuthenticatedUser = Depends(require_user),
) -> dict:
    """Update a user-owned lead's pipeline status."""
    require_owned_lead(get_db(), lead_id, user.id)
    return {"lead": update_lead_status(get_user_db(user), lead_id, request.status)}
