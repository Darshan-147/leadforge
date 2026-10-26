from fastapi import Depends, Header, HTTPException, status
from pydantic import BaseModel
from supabase import Client, ClientOptions, create_client

from core.config import settings


def get_db() -> Client:
    """Return a service-role Supabase client for privileged server-side operations."""
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise HTTPException(status_code=503, detail="Supabase is not configured. Set backend environment variables.")
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


class AuthenticatedUser(BaseModel):
    """Identity obtained from Supabase after server-side token verification."""

    id: str
    access_token: str


async def require_user(
    authorization: str | None = Header(default=None),
) -> AuthenticatedUser:
    """Verify a Supabase access token; never trust decoded client JWT claims."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="A valid access token is required.")

    access_token = authorization.removeprefix("Bearer ").strip()
    if not access_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="A valid access token is required.")

    try:
        verified = get_db().auth.get_user(access_token)
        user = verified.user
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="The access token is invalid or expired.") from exc

    if not user or not user.id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="The access token is invalid or expired.")
    return AuthenticatedUser(id=str(user.id), access_token=access_token)


def get_user_db(user: AuthenticatedUser) -> Client:
    """Return a request-scoped client so Supabase RLS runs as the signed-in user."""
    if not settings.supabase_url or not settings.supabase_publishable_key:
        raise HTTPException(status_code=503, detail="Supabase user database access is not configured.")
    return create_client(
        settings.supabase_url,
        settings.supabase_publishable_key,
        options=ClientOptions(headers={"Authorization": f"Bearer {user.access_token}"}),
    )


def require_owned_lead(lead_id: str, user: AuthenticatedUser) -> None:
    """Use the private client only to return 403 for cross-user access attempts."""
    result = get_db().table("leads").select("id,user_id").eq("id", lead_id).limit(1).execute()
    rows = result.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Lead not found.")
    if rows[0].get("user_id") != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to access this lead.")
