"""Public service health endpoint."""

from fastapi import APIRouter


router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Return the stable deployment health contract."""
    return {"status": "ok", "service": "leadforge-api"}
