"""Lead validation HTTP endpoint."""

from fastapi import APIRouter, Depends

from core.rate_limiter import user_rate_limit
from core.security import AuthenticatedUser, get_db, get_user_db, require_user
from repositories.lead_repository import (
    get_lead,
    require_owned_lead,
    update_lead_validation,
)
from services.lead_validation import build_lead_validation


router = APIRouter(tags=["validation"])


@router.post(
    "/api/leads/{lead_id}/validate",
    dependencies=[Depends(user_rate_limit("validate", 10, 60))],
)
async def validate_lead(
    lead_id: str,
    user: AuthenticatedUser = Depends(require_user),
) -> dict:
    """Re-run quality validation for an owned lead."""
    require_owned_lead(get_db(), lead_id, user.id)
    db = get_user_db(user)
    lead = get_lead(db, lead_id)
    validation_data = await build_lead_validation(
        lead.get("website_url") or lead.get("source_url") or "",
        list(lead.get("emails") or []),
        list(lead.get("hunter_verification_results") or []),
    )
    saved = update_lead_validation(db, lead_id, validation_data)
    return {
        "lead": saved,
        "validation": {
            "status": validation_data["validation_status"],
            "score": validation_data["data_quality_score"],
            "summary": validation_data["validation_summary"],
            "website": validation_data["validation_details"]["website"],
            "emails": validation_data["validation_details"]["emails"],
        },
    }
