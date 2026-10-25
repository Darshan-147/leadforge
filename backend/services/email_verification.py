"""Email syntax, DNS MX validation, and Hunter.io email verification."""

import re
from typing import Any
from urllib.parse import quote

import dns.exception
import dns.resolver
import httpx
from email_validator import EmailNotValidError, validate_email
from supabase import Client

from core.config import settings
from repositories.email_cache_repository import (
    persist_hunter_cache,
    read_hunter_cache,
)


def validate_email_syntax(value: str) -> dict[str, Any]:
    """Check email syntax and return a structured result."""
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
    """Perform a DNS MX lookup for the given domain."""
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
    """Normalize an email address, returning None if invalid."""
    email = (value or "").strip().lower()
    if not email:
        return None
    try:
        parsed = validate_email(email, check_deliverability=False)
    except EmailNotValidError:
        return None
    return parsed.normalized.lower()


def get_hunter_payload_status(raw_status: str | None) -> str:
    """Normalize a Hunter.io verification status string."""
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


async def verify_email_with_hunter(db: Client, email: str) -> dict[str, Any]:
    """Verify an email address through the Hunter.io API, with caching."""
    normalized = normalize_email_address(email)
    if not normalized:
        return {
            "email": email,
            "status": "not_checked",
            "reason": "Email syntax is invalid.",
            "domain": None,
            "normalized_email": None,
        }

    cached = await read_hunter_cache(
        db,
        normalized,
        settings.hunter_cache_ttl_hours,
    )
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
    """Validate a batch of email addresses with syntax and MX checks."""
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

