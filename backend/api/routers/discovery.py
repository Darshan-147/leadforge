"""Discovery HTTP endpoints."""

from fastapi import APIRouter, Depends

from core.rate_limiter import user_rate_limit
from core.security import AuthenticatedUser, require_user
from models.requests import DiscoverRequest
from services.discovery_service import discover_candidates


router = APIRouter(tags=["discovery"])


@router.post(
    "/api/discover",
    dependencies=[Depends(user_rate_limit("discover", 10, 60))],
)
async def discover_companies(
    request: DiscoverRequest,
    _user: AuthenticatedUser = Depends(require_user),
) -> dict:
    """Discover public company website candidates for an authenticated user."""
    return await discover_candidates(request)
