# LeadForge frontend

This Next.js 15 application is the authenticated LeadForge workspace.

## Architecture

- `src/app/page.tsx` redirects signed-in users to `/dashboard` and others to `/login`.
- `src/app/login/page.tsx` and `src/app/signup/page.tsx` implement Supabase email/password flows.
- `src/app/dashboard/page.tsx` protects the dashboard during server rendering.
- `src/middleware.ts` refreshes cookie-backed Supabase sessions.
- `src/components/dashboard.tsx` loads leads/stats, discovers companies, scans candidates, changes status, reruns validation, exports CSV, and signs out.
- `src/lib/supabase/` creates browser and server Supabase clients.

Dashboard requests send the active Supabase access token as `Authorization: Bearer <token>`. The API URL defaults to `http://127.0.0.1:8000` only when `NEXT_PUBLIC_API_BASE_URL` is unset.

## Setup

```powershell
npm install
Copy-Item .env.example .env
npm run dev
```

Set `NEXT_PUBLIC_API_BASE_URL`, `NEXT_PUBLIC_SUPABASE_URL`, and `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` in `.env`. The backend must include this frontend origin in `FRONTEND_ORIGIN`.

## Commands

```powershell
npm run dev            # development server
npm run lint           # ESLint
npm run test           # Vitest + React Testing Library
npm run test:watch     # watch mode
npm run test:coverage  # text and HTML coverage
npm run build          # production build and type validation
npm run start          # serve production build
```

Tests are colocated with component behavior and mock Supabase plus HTTP responses, so they require no real account or external service.

## Current limitations

- The dashboard is a single client component with no pagination UI or server-side lead prefetching.
- The country picker exposes India, United States, United Kingdom, and Canada.
- Failures appear as toast messages; there is no dedicated offline/error page.

See the [root README](../README.md) for full setup and [the backend README](../backend/README.md) for API details.
