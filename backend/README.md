# LeadForge Backend

It is a FastAPI application that powers B2B lead discovery, website scanning, lead management, data validation, and CSV exports. It integrates Supabase for authentication and PostgreSQL storage, DDGS for company discovery, optional Hunter API verification, and Amazon S3 for private CSV exports.

## Architecture and Responsibilities

The backend follows a modular architecture that separates HTTP request handling, business logic, data access, and shared application infrastructure.

### API Layer

The API layer exposes REST endpoints for the application's features, including company discovery, website scanning, lead retrieval, status updates, validation, statistics, health checks, and CSV exports.

The application uses FastAPI routers to keep endpoints organized by functionality. The main application entry point initializes the FastAPI application, configures CORS middleware, and registers these routers.

### Core Infrastructure

The core layer manages shared application functionality:

- **Configuration:** Loads environment variables and application settings.
- **Authentication and security:** Verifies Supabase access tokens and enforces authorization.
- **Rate limiting:** Applies in-memory per-user request limits to reduce repeated API requests.

### Business Services

The service layer contains the main application workflows:

- **Company discovery:** Searches for candidate company websites using DDGS.
- **Website scanning:** Fetches accessible public web pages and extracts company information and contact signals.
- **Lead scoring:** Calculates heuristic scores using available company and contact information.
- **Lead validation:** Evaluates data quality and contact signals.
- **Email verification:** Checks email syntax, DNS MX records, and optional Hunter API verification results.
- **CSV export:** Generates CSV files, uploads them to a private Amazon S3 bucket, and provides temporary download URLs.

### Data Access

The repository layer isolates database operations for lead records and cached email-verification results. This keeps persistence logic separate from business workflows and makes the application easier to maintain and test.

### Utilities and Testing

Shared utility functions handle URL parsing, normalization, and safety checks.

The automated test suite verifies application structure and core behaviors, including company-name extraction, URL safety, search filtering, scoring, validation, email-verification caching, and scan upsert behavior. External services are mocked so the tests do not require live calls to Supabase, Hunter, DNS, DDGS, or public websites.

## Setup

### Prerequisites

- Python 3.12
- pip
- A Supabase project
- AWS S3 credentials or an appropriate AWS IAM role for CSV exports
- A Hunter API key if optional Hunter verification is enabled

### Install Dependencies

From the `backend` directory, create and activate a virtual environment:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install the project dependencies:

```powershell
pip install -r requirements.txt
```

Copy the example environment file:

```powershell
Copy-Item .env.example .env
```

Configure the required environment variables before starting the application.

### Run the Development Server

```powershell
uvicorn main:app --reload --port 8000
```

The API will be available at:

- **Base URL:** `http://127.0.0.1:8000`
- **Swagger UI:** `http://127.0.0.1:8000/docs`
- **ReDoc:** `http://127.0.0.1:8000/redoc`
- **Health check:** `http://127.0.0.1:8000/health`

## Environment Variables

Configure the following variables in the backend `.env` file or in the deployment environment.

| Variable | Required | Purpose |
|---|---|---|
| `SUPABASE_URL` | Yes | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | Backend-only token verification, ownership checks, and Hunter cache access |
| `SUPABASE_PUBLISHABLE_KEY` | Yes | Used for request-scoped, Row Level Security-protected database access |
| `FRONTEND_ORIGIN` | For deployment | Comma-separated list of permitted frontend origins for CORS |
| `MAX_PAGES_PER_SCAN` | No | Configures the scanner's page limit; the implementation caps scans at five pages |
| `REQUEST_TIMEOUT_SECONDS` | No | HTTP request timeout for website scanning |
| `HUNTER_API_KEY` | No | Enables optional Hunter email verification |
| `HUNTER_API_BASE_URL` | No | Hunter API base URL |
| `HUNTER_VERIFY_LIMIT_PER_SCAN` | No | Limits Hunter verification requests per scan |
| `HUNTER_MAX_EMAILS_PER_SCAN` | No | Optional limit on the number of email addresses processed per scan |
| `HUNTER_CACHE_TTL_HOURS` | No | Cache lifetime for Hunter verification results |
| `HUNTER_TIMEOUT_SECONDS` | No | Timeout for Hunter API requests |
| `AWS_S3_BUCKET` | For S3 exports | Name of the private S3 bucket used for CSV exports |
| `AWS_REGION` | For S3 exports | AWS region containing the S3 bucket |
| `AWS_ACCESS_KEY_ID` | If using static AWS credentials | AWS access key for backend access to S3 |
| `AWS_SECRET_ACCESS_KEY` | If using static AWS credentials | AWS secret access key for backend access to S3 |

See [`.env.example`](.env.example) for the available defaults.

**Security considerations:**

- Never commit the `.env` file or real credentials to version control.
- Keep the Supabase service-role key and AWS secret access key exclusively on the backend.
- Do not expose privileged credentials through frontend environment variables.
- The AWS region is configuration, not a secret.
- When running in an AWS environment with an appropriate IAM role, use role-based credentials instead of storing long-lived access keys.

## Authentication and Authorization

LeadForge uses Supabase Auth to authenticate users.

Protected API requests must include the Supabase access token in the `Authorization` header:

```http
Authorization: Bearer <SUPABASE_ACCESS_TOKEN>
```

The backend verifies the token before processing protected operations. Database operations use the authenticated user's context and Row Level Security (RLS) to isolate user-owned records.

Lead status updates and validation operations also check record ownership. Attempts to modify another user's lead are rejected with an HTTP `403 Forbidden` response.

The `GET /health` endpoint is public and does not require authentication.

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Returns the backend health status |
| `POST` | `/api/discover` | Discovers candidate company websites based on industry, location, keywords, and country |
| `POST` | `/api/scan` | Scans a website, extracts company information, evaluates lead signals, and saves the result |
| `GET` | `/api/leads` | Retrieves and filters the authenticated user's leads |
| `PATCH` | `/api/leads/{lead_id}/status` | Updates a lead's pipeline status |
| `POST` | `/api/leads/{lead_id}/validate` | Reruns validation for a specific lead |
| `GET` | `/api/stats` | Returns lead statistics for the authenticated user |
| `GET` | `/api/exports/leads` | Generates a CSV export, uploads it to S3, and returns a temporary download URL |

### Company Discovery

The discovery endpoint accepts parameters such as industry, location, keywords, country, and desired result count.

It searches for candidate websites using DDGS and filters duplicate domains and common social-network or directory results.

### Website Scanning

The scanning endpoint accepts a public HTTP or HTTPS website URL.

The scanner fetches accessible pages and extracts available company names, descriptions, email addresses, phone-like strings, and social links. It prioritizes reliable company-name signals when available, including Organization JSON-LD metadata.

Scanning is deliberately bounded by page limits and request timeouts. It does not bypass CAPTCHAs, authentication, paywalls, or anti-bot controls. Websites may block automated requests or return incomplete information.

### Lead Management

The lead-management endpoints retrieve records, filter results, update statuses, and return dashboard statistics for the authenticated user.

Supported lead statuses are:

- `new`
- `reviewed`
- `qualified`
- `disqualified`

The backend enforces user-specific data access through authenticated database operations and RLS policies.

### Lead Validation

Lead validation evaluates available company and contact information, including website reachability, email syntax, DNS MX records, and optional Hunter verification signals.

Validation results are intended to help assess data quality. They do not guarantee that an email address is deliverable or that a lead is commercially qualified.

## Database and Storage

LeadForge uses Supabase PostgreSQL for persistent application data.

### Leads

The `public.leads` table stores user-owned lead records, including extracted company information, contact signals, pipeline status, scoring information, validation results, and timestamps.

Leads are unique by `(user_id, domain)`. Rescanning a domain updates the existing record for that user rather than creating another record, while preserving the user's pipeline status.

### Email Verification Cache

The `public.email_verification_cache` table stores cached Hunter verification results to reduce repeated external requests.

Cache access is restricted to backend operations.

For database initialization, migration instructions, and RLS policies, see the [database README](../database/README.md).

## Lead Scoring and Data Quality

LeadForge uses heuristic scoring to help users prioritize discovered companies.

The lead score starts with a baseline for a publicly accessible website and adjusts according to available signals, such as company name, description, email, phone, and social links. The score is capped at 100.

Data-quality scoring considers signals such as website reachability, email syntax, DNS MX records, and available Hunter confidence information.

These scores are advisory indicators, not guarantees of business relevance, contact accuracy, or email deliverability.

## CSV Export and Amazon S3

LeadForge exports authenticated users' leads to CSV and stores the generated files in a private Amazon S3 bucket.

The workflow is:

1. The frontend requests `GET /api/exports/leads` with the user's Supabase access token.
2. The backend authenticates the user and retrieves that user's leads.
3. The backend generates a CSV file.
4. The file is uploaded to the configured private S3 bucket under a user-specific prefix.
5. The backend returns a presigned download URL.
6. The browser downloads the CSV using that temporary URL.

Exports currently include up to 500 records. Presigned URLs expire after five minutes. The S3 bucket does not need to be public to support this workflow.

AWS access must be configured through backend environment variables or an appropriate runtime IAM role. IAM permissions should be restricted to the required S3 resources.

## Rate Limiting and Resource Controls

LeadForge applies in-memory per-user rate limiting to protected API operations to reduce repeated requests.

Website scanning is restricted by page count and request timeout. URL safety checks reject common private or non-public network destinations to reduce server-side request forgery (SSRF) risk.

These protections are not a substitute for complete production network controls. Production deployments should also consider network egress restrictions, DNS rebinding, external provider limits, and resource usage.

The current rate limiter is process-local. It is not a distributed rate-limiting solution, and its counters reset when the backend process restarts.

## Testing

Install development dependencies:

```powershell
pip install -r requirements-dev.txt
```

Run the automated test suite:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Run tests with coverage:

```powershell
.\.venv\Scripts\python.exe -m coverage run -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m coverage report -m
```

The tests cover application structure, core behaviors, company-name extraction, authentication and ownership checks, URL safety, search filtering, scoring, validation, Hunter caching and error handling, and scan upsert behavior.

External dependencies are mocked to prevent tests from relying on live services or public websites.

## Deployment

The backend is deployed separately from the frontend.

For the current deployment, Render runs the FastAPI application. Configure the backend environment variables in the Render service settings and ensure that `FRONTEND_ORIGIN` contains the deployed frontend origin.

The frontend communicates with the backend through its deployed HTTPS API URL. Supabase provides authentication and PostgreSQL storage, while Amazon S3 stores private CSV exports.

Refer to the [root README](../README.md) for the overall architecture, request flows, security measures, and deployment overview.

## Current Limitations

- Company discovery depends on DDGS and may be affected by rate limits, provider changes, or temporary availability issues.
- Website scanning depends on publicly accessible pages and can produce incomplete or inconsistent extraction results.
- Email verification depends on available DNS information and the optional external Hunter service.
- Scanning and lead processing are synchronous; background job queues and scheduled scans are not implemented.
- Automatic retries and distributed rate limiting are not implemented.
- URL validation reduces SSRF risk but does not replace production network egress restrictions and DNS-rebinding defenses.
- Lead scoring and data-quality scores are heuristic indicators, not guarantees of lead quality or email deliverability.

