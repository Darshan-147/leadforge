"""Lead scoring heuristics."""

from typing import Any


TRUSTED_COMPANY_NAME_SOURCES = {
    "page_title",
    "og_site_name",
    "organization_schema",
}


def score_company_name(company_name: str | None, name_source: str | None) -> int:
    """Return bonus points for a company name from a trusted extraction source."""
    if not company_name or not company_name.strip():
        return 0
    if name_source not in TRUSTED_COMPANY_NAME_SOURCES:
        return 0
    return 20


def score_lead(company_name: str | None, description: str | None,
               emails: list[str], phones: list[str], socials: dict[str, list[str]],
               name_source: str | None = None) -> tuple[int, list[str]]:
    """Compute a heuristic lead score from available signals."""
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

