"""Persistence for backend-only Hunter verification cache records."""

from datetime import datetime, timezone
from typing import Any

from supabase import Client


async def read_hunter_cache(
    db: Client,
    email: str,
    cache_ttl_hours: int,
) -> dict[str, Any] | None:
    """Return a non-expired Hunter cache record, without surfacing cache failures."""
    try:
        result = (
            db.table("email_verification_cache")
            .select("*", count="exact")
            .eq("email", email)
            .limit(1)
            .execute()
        )
    except Exception:
        return None

    rows = result.data or []
    if not rows:
        return None

    row = rows[0]
    checked_at = row.get("checked_at")
    if not checked_at:
        return row

    try:
        checked_at_value = datetime.fromisoformat(
            str(checked_at).replace("Z", "+00:00")
        )
    except ValueError:
        return row

    ttl_seconds = max(1, int(cache_ttl_hours)) * 3600
    if (datetime.now(timezone.utc) - checked_at_value).total_seconds() > ttl_seconds:
        return None
    return row


async def persist_hunter_cache(
    db: Client,
    email: str,
    status: str,
    score: int | None,
    details: dict[str, Any],
    checked_at: datetime | None = None,
) -> None:
    """Upsert a Hunter result. Cache failures must not fail a lead scan."""
    payload = {
        "email": email,
        "status": status,
        "score": score,
        "details": details or {},
        "checked_at": (checked_at or datetime.now(timezone.utc)).isoformat(),
    }
    try:
        db.table("email_verification_cache").upsert(
            payload,
            on_conflict="email",
        ).execute()
    except Exception:
        pass
