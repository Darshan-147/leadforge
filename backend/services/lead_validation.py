"""Website reachability validation and data-quality scoring."""

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import httpx
from fastapi import HTTPException

from services.email_verification import validate_lead_emails
from utils.url_utils import normalized_url


async def validate_website_reachability(url: str) -> dict[str, Any]:
    """Check whether a website is reachable and returns accessible HTML."""
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
    """Compute a data-quality score from validation signals."""
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
    """Build a complete validation result for a lead."""
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

