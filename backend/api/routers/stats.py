"""Lead dashboard statistics endpoint."""

from fastapi import APIRouter, Depends

from core.rate_limiter import user_rate_limit
from core.security import AuthenticatedUser, get_user_db, require_user
from repositories.lead_repository import lead_statistics


router = APIRouter(tags=["stats"])


@router.get(
    "/api/stats",
    dependencies=[Depends(user_rate_limit("stats", 60, 60))],
)
def stats(user: AuthenticatedUser = Depends(require_user)) -> dict[str, int]:
    """Return counts calculated from rows visible to the authenticated user."""
    return lead_statistics(get_user_db(user))
