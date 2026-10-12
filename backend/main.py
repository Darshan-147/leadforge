import ipaddress
import json
import os
import re
import socket
from collections import defaultdict, deque
from math import ceil
from threading import Lock
from time import monotonic
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urljoin, urlparse, urlunparse

import dns.exception
import dns.resolver
import httpx
from bs4 import BeautifulSoup
from email_validator import EmailNotValidError, validate_email
from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict
from supabase import Client, ClientOptions, create_client

from ddgs import DDGS
from starlette.concurrency import run_in_threadpool

import csv
import io
import uuid
import boto3
import logging


class Settings(BaseSettings):
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_publishable_key: str = ""
    frontend_origin: str = "http://localhost:3000"
    max_pages_per_scan: int = 5
    request_timeout_seconds: float = 8.0
    hunter_api_key: str = ""
    hunter_api_base_url: str = "https://api.hunter.io/v2"
    hunter_verify_limit_per_scan: int = 5
    hunter_max_emails_per_scan: int | None = None
    hunter_cache_ttl_hours: int = 24
    hunter_timeout_seconds: float = 6.0
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)


settings = Settings()
logger = logging.getLogger(__name__)
app = FastAPI(title="LeadForge API", version="1.0.0",
              description="Bounded public-website lead discovery API.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[x.strip() for x in settings.frontend_origin.split(",") if x.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

def get_db() -> Client:
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


# In-memory rate limiter for a single backend process.
# Keys are (operation name, authenticated user ID).
_RATE_LIMIT_BUCKETS: dict[
    tuple[str, str], tuple[int, deque[float]]
] = {}

_RATE_LIMIT_LOCK = Lock()
_RATE_LIMIT_REQUEST_COUNT = 0


def user_rate_limit(
    scope: str,
    limit: int,
    window_seconds: int,
):
    async def enforce_limit(
        user: AuthenticatedUser = Depends(require_user),
    ) -> None:
        global _RATE_LIMIT_REQUEST_COUNT

        now = monotonic()
        key = (scope, user.id)

        with _RATE_LIMIT_LOCK:
            bucket = _RATE_LIMIT_BUCKETS.get(key)

            if bucket is None or bucket[0] != window_seconds:
                timestamps: deque[float] = deque()
                _RATE_LIMIT_BUCKETS[key] = (
                    window_seconds,
                    timestamps,
                )
            else:
                timestamps = bucket[1]

            # Remove requests outside the current time window.
            while (
                timestamps
                and now - timestamps[0] >= window_seconds
            ):
                timestamps.popleft()

            # Reject the request if the user has reached the limit.
            if len(timestamps) >= limit:
                retry_after = max(
                    1,
                    ceil(
                        window_seconds
                        - (now - timestamps[0])
                    ),
                )

                raise HTTPException(
                    status_code=429,
                    detail=(
                        f"Rate limit exceeded for {scope}. "
                        f"Try again in {retry_after} seconds."
                    ),
                    headers={
                        "Retry-After": str(retry_after)
                    },
                )

            timestamps.append(now)
            _RATE_LIMIT_REQUEST_COUNT += 1

            # Periodically remove expired buckets for inactive users.
            if _RATE_LIMIT_REQUEST_COUNT % 100 == 0:
                for stale_key, (
                    stale_window,
                    stale_timestamps,
                ) in list(_RATE_LIMIT_BUCKETS.items()):
                    while (
                        stale_timestamps
                        and now - stale_timestamps[0]
                        >= stale_window
                    ):
                        stale_timestamps.popleft()

                    if not stale_timestamps:
                        _RATE_LIMIT_BUCKETS.pop(
                            stale_key, None
                        )

    return enforce_limit


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


class ScanRequest(BaseModel):
    url: HttpUrl


class StatusRequest(BaseModel):
    status: str = Field(pattern="^(new|reviewed|qualified|disqualified)$")

class DiscoverRequest(BaseModel):
    industry: str = Field(min_length=2, max_length=100)
    location: str = Field(default="", max_length=100)
    keywords: str = Field(default="", max_length=100)
    country: str = Field(default="IN", pattern="^[A-Z]{2}$")
    limit: int = Field(default=10, ge=1, le=20)

def normalized_url(raw: str) -> str:
    p = urlparse(raw)
    if p.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Only http and https URLs are allowed.")
    host = (p.hostname or "").lower().rstrip(".")
    if not host or host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
        raise HTTPException(status_code=400, detail="This hostname is not allowed.")
    try:
        answers = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM)
        ips = {ipaddress.ip_address(item[4][0]) for item in answers}
    except (socket.gaierror, ValueError, OSError):
        raise HTTPException(status_code=400, detail="Hostname could not be resolved.")
    if not ips or any(not ip.is_global for ip in ips):
        raise HTTPException(status_code=400, detail="Private or non-public network destinations are not allowed.")
    clean_path = p.path or "/"
    return urlunparse((p.scheme, p.netloc, clean_path, "", p.query, ""))


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


_DISCOVER_CACHE_TTL_SECONDS = 300
_DISCOVER_CACHE_MAX_ENTRIES = 200
_DISCOVER_CACHE: dict[
    tuple[str, str, int],
    tuple[float, list[dict[str, Any]]],
] = {}

_DISCOVER_CACHE_LOCK = Lock()
_SEARCH_BACKOFF_UNTIL = 0.0
_SEARCH_BACKOFF_SECONDS = 30


def search_web(query: str, limit: int, country: str):
    """Search the web with a short cache and provider-failure cooldown."""
    global _SEARCH_BACKOFF_UNTIL

    region = f"{country.lower()}-en"
    cache_key = (query.strip().casefold(), region, int(limit))
    now = monotonic()

    with _DISCOVER_CACHE_LOCK:
        # Avoid calling the provider while it's temporarily failing.
        if now < _SEARCH_BACKOFF_UNTIL:
            retry_after = max(
                1, ceil(_SEARCH_BACKOFF_UNTIL - now)
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "The free search provider is temporarily "
                    f"unavailable. Try again in {retry_after} seconds."
                ),
                headers={"Retry-After": str(retry_after)},
            )

        # Return cached results for identical recent searches.
        cached = _DISCOVER_CACHE.get(cache_key)

        if cached and now - cached[0] < _DISCOVER_CACHE_TTL_SECONDS:
            return [dict(item) for item in cached[1]]

        if cached:
            _DISCOVER_CACHE.pop(cache_key, None)

    try:
        with DDGS(timeout=10) as ddgs:
            results = list(
                ddgs.text(
                    query,
                    region=region,
                    safesearch="moderate",
                    max_results=limit,
                )
            )
    except Exception:
        # Log the real exception on the backend; don't expose
        # internal stack traces or provider details to the client.
        logger.exception("Free web search provider failed")

        with _DISCOVER_CACHE_LOCK:
            _SEARCH_BACKOFF_UNTIL = max(
                _SEARCH_BACKOFF_UNTIL,
                monotonic() + _SEARCH_BACKOFF_SECONDS,
            )

        raise

    with _DISCOVER_CACHE_LOCK:
        _DISCOVER_CACHE[cache_key] = (monotonic(), results)

        # Remove expired entries and keep the cache bounded.
        now = monotonic()
        expired_keys = [
            key
            for key, (timestamp, _) in _DISCOVER_CACHE.items()
            if now - timestamp >= _DISCOVER_CACHE_TTL_SECONDS
        ]

        for key in expired_keys:
            _DISCOVER_CACHE.pop(key, None)

        while len(_DISCOVER_CACHE) > _DISCOVER_CACHE_MAX_ENTRIES:
            oldest_key = min(
                _DISCOVER_CACHE,
                key=lambda key: _DISCOVER_CACHE[key][0],
            )
            _DISCOVER_CACHE.pop(oldest_key, None)

    return [dict(item) for item in results]



def score_lead(company_name: str | None, description: str | None,
               emails: list[str], phones: list[str], socials: dict[str, list[str]],
               name_source: str | None = None) -> tuple[int, list[str]]:
    score = 10
    reasons = ["Public website found (+10)"]
    company_name_score = score_company_name(company_name, name_source)
    if company_name_score:
        score += company_name_score
        reasons.append(f"Company name identified (+{company_name_score})")
    if description:
        score += 15; reasons.append("Company description found (+15)")
    if emails:
        score += 25; reasons.append("Email contact signal found (+25)")
    if phones:
        score += 15; reasons.append("Phone contact signal found (+15)")
    if socials:
        score += 10; reasons.append("Social profile links found (+10)")
    return min(score, 100), reasons


def validate_email_syntax(value: str) -> dict[str, Any]:
    email = (value or "").strip()
    if not email:
        return {
            "email": "",
            "status": "not_checked",
            "reason": "No email address was supplied.",
            "domain": None,
            "normalized_email": None,
        }

    try:
        parsed = validate_email(email, check_deliverability=False)
        normalized_email = parsed.normalized
        domain = normalized_email.rsplit("@", 1)[1].lower()
        return {
            "email": email,
            "status": "not_checked",
            "reason": "Email syntax is valid. MX lookup pending.",
            "domain": domain,
            "normalized_email": normalized_email,
        }
    except EmailNotValidError as exc:
        return {
            "email": email,
            "status": "not_checked",
            "reason": f"Email syntax is invalid: {exc}",
            "domain": None,
            "normalized_email": None,
        }


def validate_email_domain(domain: str) -> dict[str, Any]:
    clean_domain = (domain or "").strip().lower().rstrip(".")
    if not clean_domain:
        return {"status": "not_checked", "reason": "No domain was available for DNS validation.", "records": []}

    try:
        records = dns.resolver.resolve(clean_domain, "MX", lifetime=2.0)
        mx_records = sorted((r.preference, str(r.exchange).rstrip(".")) for r in records)
        if not mx_records:
            return {"status": "no_mx_record", "reason": "No MX record was found for this domain.", "records": []}
        return {"status": "found", "reason": "MX records were found for this domain.", "records": mx_records}
    except dns.resolver.NXDOMAIN:
        return {"status": "domain_not_found", "reason": "The domain does not exist in DNS.", "records": []}
    except dns.exception.DNSException:
        return {"status": "lookup_error", "reason": "DNS lookup failed or timed out.", "records": []}


def normalize_email_address(value: str | None) -> str | None:
    email = (value or "").strip().lower()
    if not email:
        return None
    try:
        parsed = validate_email(email, check_deliverability=False)
    except EmailNotValidError:
        return None
    return parsed.normalized.lower()


def get_hunter_payload_status(raw_status: str | None) -> str:
    status = (raw_status or "").strip().lower()
    status = re.sub(r"[^a-z0-9]+", "_", status).strip("_")
    if status in {"accepted", "valid", "accept_all", "catch_all", "disposable", "temporary", "do_not_send", "do_not_send_anymore"}:
        return "accept_all"
    if status in {"pending", "processing", "in_progress"}:
        return "pending"
    if status in {"unknown", "unverified", "invalid", "error", "failed", "unrecognized", "undetermined", "not_available"}:
        return "unknown"
    mapping = {
        "not_checked": "not_checked",
        "not_checked_limit": "not_checked_limit",
        "rate_limited": "rate_limited",
        "rate_limit_exceeded": "rate_limited",
        "quota_exhausted": "quota_exhausted",
        "verification_unavailable": "verification_unavailable",
        "not_configured": "not_configured",
        "credentials_invalid": "credentials_invalid",
        "quota_unknown": "quota_unknown",
    }
    if status in mapping:
        return mapping[status]
    return "unknown"


async def read_hunter_cache(db: Client, email: str) -> dict[str, Any] | None:
    try:
        result = db.table("email_verification_cache").select("*", count="exact").eq("email", email).limit(1).execute()
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
        ts = datetime.fromisoformat(str(checked_at).replace("Z", "+00:00"))
    except ValueError:
        return row
    ttl = max(1, int(settings.hunter_cache_ttl_hours)) * 3600
    if (datetime.now(timezone.utc) - ts).total_seconds() > ttl:
        return None
    return row


async def persist_hunter_cache(db: Client, email: str, status: str, score: int | None, details: dict[str, Any], checked_at: datetime | None = None) -> None:
    payload = {
        "email": email,
        "status": status,
        "score": score,
        "details": details or {},
        "checked_at": (checked_at or datetime.now(timezone.utc)).isoformat(),
    }
    try:
        db.table("email_verification_cache").upsert(payload, on_conflict="email").execute()
    except Exception:
        pass


async def verify_email_with_hunter(db: Client, email: str) -> dict[str, Any]:
    normalized = normalize_email_address(email)
    if not normalized:
        return {
            "email": email,
            "status": "not_checked",
            "reason": "Email syntax is invalid.",
            "domain": None,
            "normalized_email": None,
        }

    cached = await read_hunter_cache(db, normalized)
    if cached:
        cached_status = str(cached.get("status") or "unknown")
        return {
            "email": normalized,
            "status": cached_status,
            "reason": f"Cached Hunter verification result used for {normalized}.",
            "domain": normalized.rsplit("@", 1)[1].lower() if "@" in normalized else None,
            "normalized_email": normalized,
            "score": cached.get("score"),
            "details": cached.get("details") or {},
        }

    if not settings.hunter_api_key:
        return {
            "email": normalized,
            "status": "not_configured",
            "reason": "Hunter verification is not configured.",
            "domain": normalized.rsplit("@", 1)[1].lower() if "@" in normalized else None,
            "normalized_email": normalized,
        }

    url = f"{settings.hunter_api_base_url.rstrip('/')}/email-verifier?email={quote(normalized)}&api_key={quote(settings.hunter_api_key)}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(settings.hunter_timeout_seconds)) as client:
            response = await client.get(url)
            status_code = response.status_code
            payload = response.json() if getattr(response, "content", b"") else {}
    except (httpx.HTTPError, ValueError, TimeoutError, OSError):
        return {
            "email": normalized,
            "status": "verification_unavailable",
            "reason": "Hunter verification is unavailable right now.",
            "domain": normalized.rsplit("@", 1)[1].lower() if "@" in normalized else None,
            "normalized_email": normalized,
        }

    if status_code == 401:
        return {
            "email": normalized,
            "status": "credentials_invalid",
            "reason": "Hunter API key is invalid or unauthorized.",
            "domain": normalized.rsplit("@", 1)[1].lower() if "@" in normalized else None,
            "normalized_email": normalized,
        }

    if status_code == 429:
        return {
            "email": normalized,
            "status": "rate_limited",
            "reason": "Hunter rate limit was reached; remaining verification checks are not guaranteed.",
            "domain": normalized.rsplit("@", 1)[1].lower() if "@" in normalized else None,
            "normalized_email": normalized,
        }

    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    result_status = get_hunter_payload_status((data.get("status") or data.get("result") or "unknown"))
    score = data.get("score")
    details = {
        "raw": payload,
        "status": data.get("status"),
        "result": data.get("result"),
        "score": score,
        "source": "hunter",
    }

    if result_status == "unknown":
        reason = "Hunter returned an unknown or pending status; this is not proof of deliverability."
    elif result_status == "accept_all":
        reason = "Hunter classified this address as accept-all; this does not confirm deliverability."
    else:
        reason = "Hunter verification result was recorded for review."

    await persist_hunter_cache(db, normalized, result_status, score, details)
    return {
        "email": normalized,
        "status": result_status,
        "reason": reason,
        "domain": normalized.rsplit("@", 1)[1].lower() if "@" in normalized else None,
        "normalized_email": normalized,
        "score": score,
        "details": details,
    }


def validate_lead_emails(emails: list[str]) -> list[dict[str, Any]]:
    seen_domains: set[str] = set()
    results: list[dict[str, Any]] = []
    for email in emails[:10]:
        parsed = validate_email_syntax(email)
        normalized = parsed.get("normalized_email")
        if not normalized:
            results.append({
                "email": email,
                "status": "not_checked",
                "reason": parsed.get("reason", "Email syntax is invalid."),
                "domain": parsed.get("domain"),
                "normalized_email": None,
            })
            continue

        domain = (normalized.rsplit("@", 1)[1] or "").lower().rstrip(".")
        if domain in seen_domains:
            results.append({
                "email": normalized,
                "status": "not_checked",
                "reason": "Duplicate domain lookup skipped in this validation pass.",
                "domain": domain,
                "normalized_email": normalized,
            })
            continue
        seen_domains.add(domain)

        lookup = validate_email_domain(domain)
        results.append({
            "email": normalized,
            "status": lookup["status"],
            "reason": lookup["reason"],
            "domain": domain,
            "normalized_email": normalized,
            "records": lookup.get("records", []),
        })
    return results


async def validate_website_reachability(url: str) -> dict[str, Any]:
    safe_url = normalized_url(url)
    timeout = httpx.Timeout(5.0, connect=4.0, read=5.0)
    headers = {"User-Agent": "LeadForgeValidationBot/1.0 (+public website validation)"}

    try:
        async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=False) as client:
            response = await client.get(safe_url)
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    return {"status": "blocked_or_invalid", "reason": "Redirect response was missing a target URL.", "final_url": safe_url}
                redirect_target = urljoin(safe_url, location)
                try:
                    normalized_url(redirect_target)
                except HTTPException:
                    return {"status": "blocked_or_invalid", "reason": "Redirect target is not a safe public URL.", "final_url": safe_url}
                response = await client.get(redirect_target)
                final_url = redirect_target
            else:
                final_url = safe_url

            if response.status_code >= 400:
                return {"status": "http_error", "reason": f"Website responded with HTTP {response.status_code}.", "final_url": final_url}
            content_type = (response.headers.get("content-type", "") or "").lower()
            if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                return {"status": "blocked_or_invalid", "reason": "The website does not expose HTML content for validation.", "final_url": final_url}
            if len(response.content) > 250_000:
                return {"status": "blocked_or_invalid", "reason": "The website response was larger than the validation size limit.", "final_url": final_url}
            return {"status": "reachable", "reason": "The website is reachable and returned an accessible HTML response.", "final_url": final_url}
    except httpx.HTTPError:
        return {"status": "unreachable", "reason": "The website did not respond within the short validation timeout.", "final_url": safe_url}
    except HTTPException:
        return {"status": "blocked_or_invalid", "reason": "The website URL is not allowed or is not a public URL.", "final_url": safe_url}


def compute_data_quality_score(
    email_checks: list[dict[str, Any]],
    website_check: dict[str, Any],
    hunter_checks: list[dict[str, Any]] | None = None,
) -> tuple[int, list[str], int]:
    score = 50
    reasons: list[str] = ["Website and email validation not yet proven (+50)"]

    website_status = website_check.get("status")
    if website_status == "reachable":
        score += 25
        reasons.append("Website reachable (+25)")
    elif website_status in {"http_error", "blocked_or_invalid"}:
        score = max(0, score - 20)
        reasons.append("Website validation raised a quality warning (-20)")
    elif website_status == "unreachable":
        score = max(0, score - 10)
        reasons.append("Website was not reachable during validation (-10)")

    if email_checks:
        valid_mx = sum(1 for item in email_checks if item.get("status") == "found")
        if valid_mx:
            score += 20
            reasons.append(f"MX-validated email domains found (+20; {valid_mx})")
        missing_mx = sum(1 for item in email_checks if item.get("status") == "no_mx_record")
        if missing_mx:
            score = max(0, score - 10)
            reasons.append(f"Email domains without MX records flagged (-10; {missing_mx})")
        invalid = sum(1 for item in email_checks if item.get("status") == "not_checked" and "syntax is invalid" in (item.get("reason") or ""))
        if invalid:
            score = max(0, score - 15)
            reasons.append(f"Invalid email syntax detected (-15; {invalid})")

    hunter_scores = [
        item.get("score")
        for item in hunter_checks or []
        if isinstance(item.get("score"), (int, float))
    ]
    if hunter_scores:
        best_hunter_score = max(hunter_scores)
        if best_hunter_score >= 70:
            score += 10
            reasons.append(
                f"High Hunter email-verification confidence (+10; {best_hunter_score}/100)"
            )
        elif best_hunter_score < 40:
            score = max(0, score - 10)
            reasons.append(
                f"Low Hunter email-verification confidence (-10; {best_hunter_score}/100)"
            )

    raw_score = score
    return max(0, min(raw_score, 100)), reasons, raw_score


async def build_lead_validation(
    website_url: str,
    emails: list[str],
    hunter_checks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    website_check = await validate_website_reachability(website_url) if website_url else {
        "status": "blocked_or_invalid",
        "reason": "No website URL was available for validation.",
        "final_url": "",
    }
    email_checks = validate_lead_emails(emails[:10])
    data_quality_score, quality_reasons, raw_score = compute_data_quality_score(
        email_checks,
        website_check,
        hunter_checks,
    )

    if data_quality_score >= 80 and website_check.get("status") == "reachable":
        validation_status = "passed"
    elif data_quality_score >= 40:
        validation_status = "warning"
    else:
        validation_status = "failed"

    if quality_reasons:
        capped_note = (
            f" (raw total {raw_score}/100, capped at 100)"
            if raw_score > 100
            else ""
        )
        summary = (
            f"Score {data_quality_score}/100{capped_note}: "
            f"{'; '.join(quality_reasons)}"
        )
    else:
        summary = "Validation not run."

    return {
        "data_quality_score": data_quality_score,
        "validation_summary": summary,
        "validation_status": validation_status,
        "validation_details": {
            "website": website_check,
            "emails": email_checks,
            "hunter": hunter_checks or [],
            "score_reasons": quality_reasons,
            "raw_score": raw_score,
            "final_score": data_quality_score,
        },
        "validation_last_run_at": datetime.now(timezone.utc).isoformat(),
    }


TRUSTED_COMPANY_NAME_SOURCES = {
    "page_title",
    "og_site_name",
    "organization_schema",
}

GENERIC_COMPANY_NAMES = {
    "404",
    "attention required cloudflare",
    "checking your browser",
    "home",
    "home page",
    "homepage",
    "index",
    "just a moment",
    "not found",
    "untitled",
    "verify you are human",
    "welcome",
    "welcome page",
    "one moment please",
}


def score_company_name(company_name: str | None, name_source: str | None) -> int:
    if not company_name or not company_name.strip():
        return 0
    if name_source not in TRUSTED_COMPANY_NAME_SOURCES:
        return 0
    return 20


def clean_company_name(value: str | None, from_title: bool = False) -> str | None:
    if not value:
        return None

    normalized = re.sub(r"\s+", " ", value).strip()
    candidates = re.split(r"\s*(?:\||—|–)\s*|\s+-\s+", normalized) if from_title else [normalized]
    for candidate in candidates:
        candidate = re.sub(r"(?i)^welcome\s+to\s+", "", candidate).strip()
        company_name_key = re.sub(r"[^a-z0-9]+", " ", candidate.casefold()).strip()
        if candidate and company_name_key not in GENERIC_COMPANY_NAMES:
            return candidate[:300]
    return None


def is_access_challenge_page(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    title_key = re.sub(r"[^a-z0-9]+", " ", title.casefold()).strip()
    if title_key in {
        "one moment please",
        "just a moment",
        "checking your browser",
        "verify you are human",
        "attention required cloudflare",
    }:
        return True

    visible_text = soup.get_text(" ", strip=True).casefold()
    challenge_phrases = (
        "enable javascript and cookies to continue",
        "checking if the site connection is secure",
        "performing security verification",
        "verify you are human",
        "cf-browser-verification",
    )
    if any(phrase in visible_text for phrase in challenge_phrases):
        return True

    html_lower = html.casefold()
    return "cf-chl-" in html_lower or "challenge-platform" in html_lower


def iter_jsonld_objects(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_jsonld_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_jsonld_objects(child)


def organization_schema_name(soup: BeautifulSoup) -> str | None:
    for script in soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.I)}):
        try:
            structured_data = json.loads(script.string or script.get_text())
        except json.JSONDecodeError:
            continue

        for node in iter_jsonld_objects(structured_data):
            node_types = node.get("@type", [])
            if isinstance(node_types, str):
                node_types = [node_types]
            if not isinstance(node_types, list):
                continue

            is_organization = any(
                isinstance(node_type, str)
                and (
                    node_type.rsplit("/", 1)[-1].rsplit("#", 1)[-1].casefold().endswith("organization")
                    or node_type.rsplit("/", 1)[-1].rsplit("#", 1)[-1].casefold()
                    in {"corporation", "localbusiness"}
                )
                for node_type in node_types
            )
            if is_organization:
                name = clean_company_name(node.get("name"))
                if name:
                    return name
    return None


def extract_page(url: str, html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else None
    organization_name = organization_schema_name(soup)
    og_site_name = soup.find("meta", attrs={"property": "og:site_name"})
    company_name = organization_name
    name_source = "organization_schema" if organization_name else None
    if not company_name and og_site_name and og_site_name.get("content"):
        company_name = clean_company_name(og_site_name["content"])
        if company_name:
            name_source = "og_site_name"
    if not company_name:
        company_name = clean_company_name(title, from_title=True)
        if company_name:
            name_source = "page_title"

    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    description = None
    meta = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    if meta and meta.get("content"):
        description = meta["content"].strip()[:1500]
    if not description:
        og = soup.find("meta", attrs={"property": "og:description"})
        if og and og.get("content"):
            description = og["content"].strip()[:1500]

    text = soup.get_text(" ", strip=True)
    emails = set(re.findall(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", html + " " + text))
    emails = {e.lower().strip(".,;:") for e in emails if not e.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".svg"))}
    phones = set()
    for candidate in re.findall(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)", text):
        digits = re.sub(r"\D", "", candidate)
        if 8 <= len(digits) <= 15:
            phones.add(re.sub(r"\s+", " ", candidate).strip()[:40])

    socials: dict[str, set[str]] = defaultdict(set)
    links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(url, a["href"].strip())
        parsed = urlparse(href)
        if parsed.scheme not in ("http", "https"):
            continue
        host = (parsed.hostname or "").lower()
        if any(s in host for s in ("linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com", "youtube.com")):
            platform = next((s for s in ("linkedin", "facebook", "instagram", "youtube", "twitter", "x") if s in host), "other")
            socials[platform].add(href.split("?")[0])
        elif domain_of(href) == domain_of(url):
            path = parsed.path.lower()
            if any(k in path for k in ("about", "contact", "team", "company")):
                links.append(href.split("#")[0])
    canonical = soup.find("link", rel="canonical")
    canonical_url = urljoin(url, canonical.get("href")) if canonical and canonical.get("href") else url
    return {
        "title": title[:300] if title else None,
        "company_name": company_name,
        "company_name_source": name_source,
        "description": description,
        "emails": sorted(emails),
        "phones": sorted(phones),
        "social_links": {k: sorted(v)[:10] for k, v in socials.items()},
        "candidate_links": list(dict.fromkeys(links))[:20],
        "canonical_url": canonical_url,
    }


async def fetch_html(client: httpx.AsyncClient, url: str) -> tuple[str, str] | None:
    # Revalidate each URL before requesting to reduce SSRF risk.
    safe_url = normalized_url(url)
    try:
        response = await client.get(safe_url, follow_redirects=False)
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location")
            if not location:
                return None
            redirect_url = urljoin(safe_url, location)
            normalized_url(redirect_url)
            response = await client.get(redirect_url, follow_redirects=False)
            safe_url = redirect_url
        if response.status_code in (403, 503) and is_access_challenge_page(response.text):
            return safe_url, response.text
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
            return None
        if len(response.content) > 2_000_000:
            return None
        return safe_url, response.text
    except (httpx.HTTPError, HTTPException, UnicodeDecodeError):
        return None


@app.get("/health")
def health():
    return {"status": "ok", "service": "leadforge-api"}

@app.post(
    "/api/discover",
    dependencies=[
        Depends(user_rate_limit("discover", 10, 60))
    ],
)
async def discover_companies(request: DiscoverRequest, _user: AuthenticatedUser = Depends(require_user)):
    query_parts = [
        request.industry.strip(),
        "companies official website",
    ]

    if request.location.strip():
        query_parts.append(request.location.strip())

    if request.keywords.strip():
        query_parts.append(request.keywords.strip())

    query = " ".join(query_parts)

    # Run the synchronous search without blocking the async event loop.
    try:
        results = await run_in_threadpool(
            search_web,
            query,
            request.limit,
            request.country,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Discover endpoint failed during web search")

        raise HTTPException(
            status_code=503,
            detail=(
                "The free web-search provider is temporarily "
                "unavailable or rate-limited. Please wait at least "
                "30 seconds before trying again."
            ),
            headers={"Retry-After": "30"},
        ) from exc

    excluded_domains = {
        "linkedin.com",
        "facebook.com",
        "instagram.com",
        "youtube.com",
        "twitter.com",
        "x.com",
        "indeed.com",
        "glassdoor.com",
        "wikipedia.org",
    }

    candidates = []
    seen_domains = set()

    for item in results:
        raw_url = item.get("href", "")
        parsed = urlparse(raw_url)

        if parsed.scheme not in {"http", "https"}:
            continue

        if not parsed.hostname:
            continue

        host = parsed.hostname.lower().rstrip(".")
        domain = (
            host[4:] if host.startswith("www.") else host
        )

        # Skip common social networks and directories.
        if any(
            domain == blocked
            or domain.endswith("." + blocked)
            for blocked in excluded_domains
        ):
            continue

        # Keep one result per domain.
        if domain in seen_domains:
            continue

        seen_domains.add(domain)

        # Remove query parameters and fragments from the candidate URL.
        clean_url = urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path or "/",
            "",
            "",
            "",
        ))

        candidates.append({
            "title": (item.get("title") or domain)[:250],
            "url": clean_url,
            "domain": domain,
            "snippet": (item.get("body") or "")[:500],
        })

        if len(candidates) >= request.limit:
            break

    return {
        "query": query,
        "count": len(candidates),
        "candidates": candidates,
        "message": (
            "Review candidate websites before enriching them."
        ),
    }


@app.post(
    "/api/scan",
    dependencies=[
        Depends(user_rate_limit("scan", 5, 60))
    ],
)
async def scan(request: ScanRequest, user: AuthenticatedUser = Depends(require_user)):
    start_url = normalized_url(str(request.url))
    start_domain = domain_of(start_url)
    max_pages = max(1, min(int(settings.max_pages_per_scan), 5))
    pages_to_visit = [start_url]
    visited: set[str] = set()
    aggregate: dict[str, Any] = {"company_name": None, "company_name_source": None,
                                 "description": None, "emails": set(), "phones": set(),
                                 "social_links": defaultdict(set)}
    timeout = httpx.Timeout(settings.request_timeout_seconds)
    headers = {"User-Agent": "LeadForgeResearchBot/1.0 (+respectful public business research)"}
    readable_pages = 0
    challenge_pages = 0

    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        while pages_to_visit and len(visited) < max_pages:
            url = pages_to_visit.pop(0)
            if url in visited:
                continue
            visited.add(url)
            fetched = await fetch_html(client, url)
            if not fetched:
                continue
            final_url, html = fetched
            if domain_of(final_url) != start_domain:
                continue
            if is_access_challenge_page(html):
                challenge_pages += 1
                continue
            readable_pages += 1
            data = extract_page(final_url, html)
            if data["company_name"]:
                source_priority = {
                    "organization_schema": 0,
                    "og_site_name": 1,
                    "page_title": 2,
                }
                current_source = aggregate["company_name_source"]
                candidate_source = data["company_name_source"]
                if (
                    not aggregate["company_name"]
                    or source_priority.get(candidate_source, 3)
                    < source_priority.get(current_source, 3)
                ):
                    aggregate["company_name"] = data["company_name"]
                    aggregate["company_name_source"] = candidate_source
            if not aggregate["description"] and data["description"]:
                aggregate["description"] = data["description"]
            aggregate["emails"].update(data["emails"])
            aggregate["phones"].update(data["phones"])
            for platform, urls in data["social_links"].items():
                aggregate["social_links"][platform].update(urls)
            for link in data["candidate_links"]:
                if domain_of(link) == start_domain and link not in visited and link not in pages_to_visit:
                    pages_to_visit.append(link)

    if not readable_pages:
        if challenge_pages:
            raise HTTPException(
                status_code=422,
                detail=(
                    "This website is showing an anti-bot or security challenge, so LeadForge "
                    "cannot read or score its company content. Try a public page that does "
                    "not require JavaScript verification or a CAPTCHA."
                ),
            )
        raise HTTPException(
            status_code=422,
            detail="No accessible HTML pages found. Check the URL and try a public website.",
        )
    emails = sorted(aggregate["emails"])[:30]
    phones = sorted(aggregate["phones"])[:20]
    socials = {k: sorted(v)[:10] for k, v in aggregate["social_links"].items()}
    company_name = aggregate["company_name"]
    name_source = aggregate["company_name_source"]
    description = aggregate["description"]
    score, reasons = score_lead(
        company_name,
        description,
        emails,
        phones,
        socials,
        name_source=name_source,
    )

    hunter_results: list[dict[str, Any]] = []
    db = get_user_db(user)
    cache_db = get_db()
    unique_emails = []
    seen_emails: set[str] = set()
    for email in emails:
        normalized = normalize_email_address(email)
        if not normalized or normalized in seen_emails:
            continue
        seen_emails.add(normalized)
        unique_emails.append(normalized)

    hunter_limit = int(settings.hunter_verify_limit_per_scan or settings.hunter_max_emails_per_scan or 5)
    if hunter_limit <= 0:
        hunter_limit = 1

    for email in unique_emails[: max(1, hunter_limit)]:
        hunter_result = await verify_email_with_hunter(cache_db, email)
        hunter_results.append(hunter_result)

    validation_data = await build_lead_validation(
        start_url,
        emails,
        hunter_results,
    )

    existing = db.table("leads").select("id,status").eq("domain", start_domain).limit(1).execute()
    existing_rows = existing.data or []
    existing_status = existing_rows[0].get("status") if existing_rows else None

    lead = {
        "user_id": user.id,
        "domain": start_domain,
        "website_url": start_url,
        "company_name": company_name,
        "description": description,
        "emails": emails,
        "phones": phones,
        "social_links": socials,
        "source_url": start_url,
        "lead_score": score,
        "score_reasons": reasons,
        "status": existing_status or "new",
        "hunter_verification_results": hunter_results,
        "hunter_verification_checked_at": datetime.now(timezone.utc).isoformat(),
        **validation_data,
    }
    result = db.table("leads").upsert(lead, on_conflict="user_id,domain").execute()
    saved = result.data[0] if result.data else lead
    return {"lead": saved, "pages_checked": len(visited), "hunter_verification": hunter_results, "message": "Lead saved or updated successfully."}


@app.get(
    "/api/leads",
    dependencies=[
        Depends(user_rate_limit("leads", 60, 60))
    ],
)
def list_leads(
    search: str = Query(default="", max_length=150),
    status: str = Query(default="all", pattern="^(all|new|reviewed|qualified|disqualified)$"),
    min_score: int = Query(default=0, ge=0, le=100),
    limit: int = Query(default=100, ge=1, le=500),
    user: AuthenticatedUser = Depends(require_user),
):
    db = get_user_db(user)
    query = db.table("leads").select("*").gte("lead_score", min_score).order("created_at", desc=True).limit(limit)
    if status != "all":
        query = query.eq("status", status)
    if search.strip():
        term = search.strip().replace("%", "").replace(",", " ")
        query = query.or_(f"company_name.ilike.%{term}%,domain.ilike.%{term}%,description.ilike.%{term}%")
    result = query.execute()
    return {"leads": result.data or []}


@app.patch(
    "/api/leads/{lead_id}/status",
    dependencies=[
        Depends(user_rate_limit("status_update", 30, 60))
    ],
)
def update_status(lead_id: str, request: StatusRequest, user: AuthenticatedUser = Depends(require_user)):
    require_owned_lead(lead_id, user)
    db = get_user_db(user)
    result = db.table("leads").update({"status": request.status}).eq("id", lead_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Lead not found.")
    return {"lead": result.data[0]}


@app.post(
    "/api/leads/{lead_id}/validate",
    dependencies=[
        Depends(user_rate_limit("validate", 10, 60))
    ],
)
async def validate_lead(lead_id: str, user: AuthenticatedUser = Depends(require_user)):
    require_owned_lead(lead_id, user)
    db = get_user_db(user)
    lead_result = db.table("leads").select("*").eq("id", lead_id).limit(1).execute()
    if not lead_result.data:
        raise HTTPException(status_code=404, detail="Lead not found.")

    lead = lead_result.data[0]
    emails = list(lead.get("emails") or [])
    website_url = lead.get("website_url") or lead.get("source_url") or ""
    validation_data = await build_lead_validation(
        website_url,
        emails,
        list(lead.get("hunter_verification_results") or []),
    )
    updated = db.table("leads").update(validation_data).eq("id", lead_id).select("*").execute()
    if not updated.data:
        raise HTTPException(status_code=500, detail="Lead validation could not be persisted.")
    return {
        "lead": updated.data[0],
        "validation": {
            "status": validation_data["validation_status"],
            "score": validation_data["data_quality_score"],
            "summary": validation_data["validation_summary"],
            "website": validation_data["validation_details"]["website"],
            "emails": validation_data["validation_details"]["emails"],
        },
    }


@app.get(
    "/api/stats",
    dependencies=[
        Depends(user_rate_limit("stats", 60, 60))
    ],
)
def stats(user: AuthenticatedUser = Depends(require_user)):
    db = get_user_db(user)
    result = db.table("leads").select("id,lead_score,status,emails").limit(5000).execute()
    rows = result.data or []
    return {
        "total": len(rows),
        "qualified": sum(1 for r in rows if r.get("status") == "qualified"),
        "with_email": sum(1 for r in rows if r.get("emails")),
        "high_score": sum(1 for r in rows if (r.get("lead_score") or 0) >= 70),
    }


@app.get(
    "/api/exports/leads",
    dependencies=[
        Depends(user_rate_limit("export", 3, 60))
    ],
)
def export_leads_to_s3(
    user: AuthenticatedUser = Depends(require_user),
):
    bucket = os.getenv("AWS_S3_BUCKET")
    region = os.getenv("AWS_REGION", "ap-south-1")

    if not bucket:
        raise HTTPException(
            status_code=503,
            detail="AWS S3 export is not configured.",
        )

    # This client uses credentials from Render's environment.
    s3 = boto3.client("s3", region_name=region)

    # Use the authenticated user's Supabase client so RLS applies.
    db = get_user_db(user)
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

    fields = [
        "company_name", "domain", "website_url",
        "emails", "phones", "lead_score",
        "status", "created_at",
    ]

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()

    for lead in result.data or []:
        row = dict(lead)
        for field in ("emails", "phones"):
            value = row.get(field) or []
            row[field] = "; ".join(map(str, value))
        writer.writerow(row)

    csv_bytes = output.getvalue().encode("utf-8-sig")
    object_key = f"exports/{user.id}/{uuid.uuid4()}.csv"

    try:
        s3.put_object(
            Bucket=bucket,
            Key=object_key,
            Body=csv_bytes,
            ContentType="text/csv; charset=utf-8",
            ServerSideEncryption="AES256",
        )

        # The link works temporarily; it does not make
        # the S3 bucket public.
        download_url = s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": bucket,
                "Key": object_key,
            },
            ExpiresIn=300,
        )
    except Exception as exc:
        # Log the underlying exception in server logs
        # during debugging; don't expose credentials.
        raise HTTPException(
            status_code=502,
            detail="Could not create the S3 export.",
        ) from exc

    return {
        "download_url": download_url,
        "expires_in_seconds": 300,
        "rows_exported": len(result.data or []),
    }

