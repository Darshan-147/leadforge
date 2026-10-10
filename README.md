# LeadForge — Phase 1

A lead discovery MVP built with Next.js, Tailwind CSS, FastAPI, and Supabase PostgreSQL.

## Phase 1 features
- Submit a public business website URL for bounded scraping.
- Extract verified company names from organization schema, `og:site_name`, or a meaningful page title, plus descriptions, emails, phone-like strings, and social links.
- Store results in Supabase with normalized domain deduplication.
- Search/filter saved leads and export CSV.
- Explainable heuristic lead score.

## Architecture
- `frontend/`: Next.js dashboard, deployed as one Vercel project.
- `backend/`: FastAPI API, deployed as a separate Vercel project.
- Supabase: PostgreSQL database.

Two Vercel projects are intentional: they let the frontend and Python API use their native runtimes without relying on mixed-runtime routing assumptions.

## Prerequisites
Install:
1. VS Code
2. Git
3. Node.js LTS (includes npm)
4. Python 3.11 or 3.12
5. A Supabase account
6. A GitHub account and Vercel account for deployment

## 1. Create the Supabase database
1. Create a Supabase project.
2. Open **SQL Editor** → **New query**.
3. For a new project, paste and run `database/schema.sql`. For an existing project, run the unapplied numbered migration files in order, ending with `005_add_lead_ownership.sql`.
   - For an existing project, do not rerun the base schema. Run only migrations that are not already applied.
4. In **Project Settings → API**, copy the Project URL and `service_role` key.
   - Keep the `service_role` key private. Never put it in the frontend or commit it to Git.
   - Use the `service_role` key only in the backend environment variables.

## 2. Run the backend locally
Open a terminal in VS Code and navigate to `backend`.

Windows PowerShell:
```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```
If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that terminal and activate again.

Edit `backend/.env` and set:
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_PUBLISHABLE_KEY`
- `FRONTEND_ORIGIN=http://localhost:3000`
- `HUNTER_API_KEY` (optional; leave blank to disable verification)
- `HUNTER_API_BASE_URL=https://api.hunter.io/v2`
- `HUNTER_VERIFY_LIMIT_PER_SCAN=5`
- `HUNTER_CACHE_TTL_HOURS=24`
- `HUNTER_TIMEOUT_SECONDS=6`

Start the API:
```powershell
uvicorn main:app --reload --port 8000
```
Check `http://127.0.0.1:8000/health` and API docs at `http://127.0.0.1:8000/docs`.

### Hunter email verification (optional backend-only feature)
Hunter verification is intentionally server-side only. The API key stays in the Python backend environment and is never exposed to the Next.js frontend.

Behavior:
- Each scanned lead deduplicates emails before calling Hunter.
- Verification is capped per scan using `HUNTER_VERIFY_LIMIT_PER_SCAN`.
- Results are cached in `public.email_verification_cache` and reused until TTL expiry.
- Missing API keys, invalid credentials, rate limits, timeouts, and API errors degrade gracefully without failing lead scanning.
- Hunter statuses are stored in `public.leads.hunter_verification_results` and kept separate from the generic `validation_*` fields.
- `accept_all` and `unknown` are warnings only; they are not treated as proof of email deliverability.

## 3. Run the frontend locally
Open a second terminal at the project root:
```powershell
cd frontend
npm install
Copy-Item .env.example .env
```
Edit `frontend/.env`:
```env
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
NEXT_PUBLIC_SUPABASE_URL=https://YOUR_PROJECT.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=YOUR_SUPABASE_PUBLISHABLE_KEY
```
Start Next.js:
```powershell
npm run dev
```
Open `http://localhost:3000`.

## 4. How to use it
1. Sign in, then paste a public company website URL, e.g. `https://example.com`.
2. Click **Discover leads**.
3. Review the extracted company profile and contact signals.
4. Use search and the score filter to find relevant leads.
5. Export the visible records as CSV.

The scraper checks only the submitted page and a small set of same-domain links (about, contact, team, company). It does not bypass logins, CAPTCHAs, paywalls, or access controls. If a site returns an anti-bot/security challenge, LeadForge reports that it could not read the site and does not save a lead or generate a score from the challenge page. Try another publicly readable page. Respect each site's terms, robots policy, rate limits, and applicable privacy laws. Use only data you have a lawful basis to process.

## 5. Deploy backend to Vercel
1. Push the repository to GitHub.
2. In Vercel, **Add New → Project**, import the repository.
3. Set **Root Directory** to `backend`.
4. Add environment variables:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE_KEY`
   - `SUPABASE_PUBLISHABLE_KEY`
   - `FRONTEND_ORIGIN` (set to the deployed frontend origin after frontend deployment)
   - `MAX_PAGES_PER_SCAN` = `5`
   - `REQUEST_TIMEOUT_SECONDS` = `8`
5. Deploy and copy the backend URL.

Note: Vercel functions have execution limits; this MVP intentionally limits the number of pages and request duration. If your Vercel account/runtime rejects the FastAPI adapter, deploy this same backend to Render or another Python ASGI host and keep the frontend on Vercel.

## 6. Deploy frontend to Vercel
1. Create another Vercel project from the same GitHub repository.
2. Set **Root Directory** to `frontend`.
3. Add `NEXT_PUBLIC_API_BASE_URL` = your deployed backend URL.
4. Deploy.
5. Update the backend's `FRONTEND_ORIGIN` to the deployed frontend URL and redeploy the backend.

## Authentication and ownership setup

For an existing project, migration 005 preserves every lead but requires you to explicitly assign current rows to an initial owner:

1. Create an account in Supabase **Authentication > Users** (or use `/signup` after frontend setup).
2. In **SQL Editor**, run `select id, email from auth.users order by created_at;` and copy your account UUID.
3. Open `database/migrations/005_add_lead_ownership.sql`. Replace the sample UUID in its documented `update public.leads` statement, then run it in SQL Editor.
4. Run the migration's final count query. It must return `0`; only then run its `validate constraint` command.

This migration changes deduplication to `(user_id, domain)`, blocks future ownerless writes, and never silently assigns legacy leads.

Enable Supabase Email authentication under **Authentication > Providers > Email**. Under **Authentication > URL Configuration**, set the Site URL to `http://localhost:3000` locally and add the deployed frontend URL as a Redirect URL. Leave confirmation enabled if users must verify their address before signing in.

## Security / production notes
- Every lead endpoint requires a Supabase access token. The backend verifies it with Supabase; it never trusts merely decoded JWT claims.
- `public.leads` uses select, insert, update, and delete RLS policies. Lead requests use a database client scoped to the caller's token, so users see only their own rows.
- The service-role key is used only for the shared Hunter cache and an explicit ownership lookup that returns `403` for cross-user access. Never expose it in frontend settings.
- Hunter cache records are shared verification data only; company lead records remain private under RLS.
- Run checks locally with `cd backend; python -m unittest discover -s tests -v`, then `cd frontend; npm run lint; npm run build`.
- A public URL scraper can be abused for SSRF. This implementation blocks non-public IP destinations and common local/private hostnames, but production should also enforce outbound network controls and DNS-rebinding protections.
- Never commit `.env`, credentials, or service-role keys.
- The lead score is an explainable heuristic and should be tuned against real outcomes.
