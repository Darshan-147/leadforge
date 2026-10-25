"""Private S3 CSV export endpoint."""

from fastapi import APIRouter, Depends

from core.rate_limiter import user_rate_limit
from core.security import AuthenticatedUser, get_user_db, require_user
from services.export_service import export_leads_csv_to_s3


router = APIRouter(tags=["exports"])


@router.get(
    "/api/exports/leads",
    dependencies=[Depends(user_rate_limit("export", 3, 60))],
)
def export_leads_to_s3(
    user: AuthenticatedUser = Depends(require_user),
) -> dict:
    """Build a private user-scoped CSV export and return a temporary URL."""
    return export_leads_csv_to_s3(get_user_db(user), user.id)
