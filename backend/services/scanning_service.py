"""Website scanning, HTML extraction, and company-name selection."""

import json
import re
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import HTTPException
from supabase import Client

from core.config import settings
from repositories.lead_repository import existing_lead_status, upsert_lead
from services.email_verification import (
    normalize_email_address,
    verify_email_with_hunter,
)
from services.lead_scoring import score_lead
from services.lead_validation import build_lead_validation
from utils.url_utils import domain_of, normalized_url


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


def clean_company_name(value: str | None, from_title: bool = False) -> str | None:
    """Clean and validate a candidate company name."""
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
    """Detect anti-bot/security challenge pages (Cloudflare, etc.)."""
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
    """Recursively yield all dict objects from JSON-LD structured data."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_jsonld_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_jsonld_objects(child)


def organization_schema_name(soup: BeautifulSoup) -> str | None:
    """Extract an organization name from JSON-LD structured data."""
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
    """Extract structured company information from an HTML page."""
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

    # Exclude common year ranges and dates before phone extraction.
    date_or_year_range_pattern = re.compile(
        r"\b(?:19|20)\d{2}\s*[-–—]\s*(?:19|20)\d{2}\b|"
        r"\b(?:19|20)\d{2}\s*[./-]\s*(?:0?[1-9]|1[0-2])"
        r"\s*[./-]\s*(?:0?[1-9]|[12]\d|3[01])\b|"
        r"\b(?:0?[1-9]|[12]\d|3[01])\s*[./-]\s*"
        r"(?:0?[1-9]|1[0-2])\s*[./-]\s*(?:19|20)?\d{2}\b"
    )

    # Remove common date formats and year ranges from searchable text.
    phone_source_text = date_or_year_range_pattern.sub(" ", text)

    # Extract likely phone numbers from the remaining text.
    phone_candidate_pattern = re.compile(
        r"(?<!\w)\+?\d[\d\s()./–—-]{7,}\d(?!\w)"
    )

    for match in phone_candidate_pattern.finditer(phone_source_text):
        candidate = match.group().strip()
        digits = re.sub(r"\D", "", candidate)

        # Require 10+ digits unless a shorter number has nearby
        # phone-related context, such as "Phone" or "Contact".
        context = phone_source_text[
            max(0, match.start() - 40):
            min(len(phone_source_text), match.end() + 40)
        ]

        has_phone_label = bool(
            re.search(
                r"\b(?:phone|telephone|tel|mobile|call|contact|hotline|fax)\b",
                context,
                re.IGNORECASE,
            )
        )

        if 10 <= len(digits) <= 15 or (
            8 <= len(digits) <= 9 and has_phone_label
        ):
            phones.add(
                re.sub(r"\s+", " ", candidate).strip()[:40]
            )

    # Prefer explicitly marked telephone links when available.
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()

        if not href.lower().startswith("tel:"):
            continue

        candidate = href[4:].split("?")[0].strip()

        if date_or_year_range_pattern.fullmatch(candidate):
            continue

        digits = re.sub(r"\D", "", candidate)

        if 8 <= len(digits) <= 15:
            phones.add(
                re.sub(r"\s+", " ", candidate).strip()[:40]
            )


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
    """Fetch an HTML page, handling redirects and SSRF checks."""
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


async def scan_and_save_lead(
    request_url: str,
    user_id: str,
    user_db_factory: Callable[[], Client],
    cache_db_factory: Callable[[], Client],
) -> dict[str, Any]:
    """Scan a bounded public site, validate its signals, and upsert its lead."""
    start_url = normalized_url(request_url)
    start_domain = domain_of(start_url)
    max_pages = max(1, min(int(settings.max_pages_per_scan), 5))
    pages_to_visit = [start_url]
    visited: set[str] = set()
    aggregate: dict[str, Any] = {
        "company_name": None,
        "company_name_source": None,
        "description": None,
        "emails": set(),
        "phones": set(),
        "social_links": defaultdict(set),
    }
    timeout = httpx.Timeout(settings.request_timeout_seconds)
    headers = {
        "User-Agent": (
            "LeadForgeResearchBot/1.0 (+respectful public business research)"
        )
    }
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
                if (
                    domain_of(link) == start_domain
                    and link not in visited
                    and link not in pages_to_visit
                ):
                    pages_to_visit.append(link)

    if not readable_pages:
        if challenge_pages:
            raise HTTPException(
                status_code=422,
                detail=(
                    "This website is showing an anti-bot or security challenge, so "
                    "LeadForge cannot read or score its company content. Try a public "
                    "page that does not require JavaScript verification or a CAPTCHA."
                ),
            )
        raise HTTPException(
            status_code=422,
            detail=(
                "No accessible HTML pages found. Check the URL and try a public "
                "website."
            ),
        )

    emails = sorted(aggregate["emails"])[:30]
    phones = sorted(aggregate["phones"])[:20]
    socials = {
        platform: sorted(urls)[:10]
        for platform, urls in aggregate["social_links"].items()
    }
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

    # Preserve the prior ordering: no database client is created for an unreadable
    # or anti-bot page.
    db = user_db_factory()
    cache_db = cache_db_factory()
    unique_emails: list[str] = []
    seen_emails: set[str] = set()
    for email in emails:
        normalized = normalize_email_address(email)
        if not normalized or normalized in seen_emails:
            continue
        seen_emails.add(normalized)
        unique_emails.append(normalized)

    hunter_limit = int(
        settings.hunter_verify_limit_per_scan
        or settings.hunter_max_emails_per_scan
        or 5
    )
    if hunter_limit <= 0:
        hunter_limit = 1
    hunter_results = [
        await verify_email_with_hunter(cache_db, email)
        for email in unique_emails[:max(1, hunter_limit)]
    ]
    validation_data = await build_lead_validation(
        start_url,
        emails,
        hunter_results,
    )
    existing_status = existing_lead_status(db, start_domain)
    lead = {
        "user_id": user_id,
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
    saved = upsert_lead(db, lead)
    return {
        "lead": saved,
        "pages_checked": len(visited),
        "hunter_verification": hunter_results,
        "message": "Lead saved or updated successfully.",
    }

