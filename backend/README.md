# LeadForge backend

The FastAPI backend verifies Supabase access tokens, searches and scans public websites, saves private leads, and calculates data-quality signals.

## Modules

- `main.py`: settings, authentication, URL safety, discovery, scraping, scoring, validation, and routes.
- `api/index.py`: Vercel adapter entrypoint.
- `tests/`: isolated `unittest` suite with external services mocked.
- `vercel.json`: routes all Vercel traffic to `api/index.py`.

## Setup

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn main:app --reload --port 8000
```

For coverage, install `pip install -r requirements-dev.txt`. OpenAPI docs are at `http://127.0.0.1:8000/docs`.

## Environment

| Variable | Required | Purpose |
| --- | --- | --- |
| `SUPABASE_URL` | Yes | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | Backend-only token verification, ownership lookup, Hunter cache |
| `SUPABASE_PUBLISHABLE_KEY` | Yes | Request-scoped RLS lead client |
| `FRONTEND_ORIGIN` | Deployed | Comma-separated CORS allowlist |
| `MAX_PAGES_PER_SCAN`, `REQUEST_TIMEOUT_SECONDS` | No | Scanner settings (implementation caps pages at 5) |
| `HUNTER_API_KEY`, `HUNTER_API_BASE_URL` | No | Optional Hunter integration |
| `HUNTER_VERIFY_LIMIT_PER_SCAN`, `HUNTER_MAX_EMAILS_PER_SCAN`, `HUNTER_CACHE_TTL_HOURS`, `HUNTER_TIMEOUT_SECONDS` | No | Hunter limits/cache/timeouts |

See [`.env.example`](.env.example) for defaults. Do not commit secrets.

## Authentication and API

Every `/api/*` route requires `Authorization: Bearer <Supabase access token>`; `GET /health` is public. The backend validates tokens with Supabase, then uses the caller's RLS-scoped client. Status and validation operations also confirm ownership and return `403` for another user's lead.

| Method and path | Request | Response |
| --- | --- | --- |
| `GET /health` | none | `{"status":"ok","service":"leadforge-api"}` |
| `POST /api/discover` | `industry` (2-100), optional `location`/`keywords`, uppercase 2-char `country`, `limit` 1-20 | query, count, deduplicated candidates |
| `POST /api/scan` | `{"url":"https://example.com"}` | saved lead, pages checked, Hunter results |
| `GET /api/leads` | `search`, `status`, `min_score`, `limit` | `{"leads":[...]}` |
| `PATCH /api/leads/{lead_id}/status` | `{"status":"new|reviewed|qualified|disqualified"}` | updated lead |
| `POST /api/leads/{lead_id}/validate` | none | updated lead plus validation summary |
| `GET /api/stats` | none | total, qualified, with_email, high_score |

Discovery excludes several social/directory domains and returns one result per normalized domain. Scanning accepts public HTTP(S) targets, follows selected same-domain pages, prefers Organization JSON-LD names over Open Graph and title values, and rejects anti-bot pages rather than saving them.

## Storage and scoring

`public.leads` is unique by `(user_id, domain)`; rescans preserve that user's pipeline status. It holds extracted signals, scores, validation data, and Hunter output. `public.email_verification_cache` stores backend-only Hunter cache records. Details are in [the database README](../database/README.md).

Lead scoring starts at 10 for a public site, adds trusted name/description/email/phone/social signals, and caps at 100. Data quality starts at 50 and adjusts for reachability, MX records, invalid syntax, and numerical Hunter confidence. Both are advisory heuristics.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m coverage run -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m coverage report -m
```

Tests cover auth/ownership, URL safety, extraction priority, anti-bot handling, search filtering, scoring, validation branches, Hunter cache/errors, and scan upsert behavior. No test calls Supabase, Hunter, DNS, DDGS, or public websites.

## Limitations

DDGS and public sites can be rate-limited or unavailable. URL checks reduce SSRF risk but production should additionally control network egress and DNS rebinding. There are no background jobs, queues, or retries. See the [root README](../README.md) for deployment.
