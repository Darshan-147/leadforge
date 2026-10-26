"""Website scanning HTTP endpoint."""

from fastapi import APIRouter, Depends

from core.rate_limiter import user_rate_limit
from core.security import AuthenticatedUser, get_db, get_user_db, require_user
from models.requests import ScanRequest
from services.scanning_service import scan_and_save_lead


router = APIRouter(tags=["scans"])


@router.post(
    "/api/scan",
    dependencies=[Depends(user_rate_limit("scan", 5, 60))],
)
async def scan(
    request: ScanRequest,
    user: AuthenticatedUser = Depends(require_user),
) -> dict:
    """Scan a public website and save its user-owned lead."""
    return await scan_and_save_lead(
        str(request.url),
        user.id,
        user_db_factory=lambda: get_user_db(user),
        cache_db_factory=get_db,
    )
