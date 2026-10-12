# LeadForge database

LeadForge uses Supabase PostgreSQL and Supabase Auth. It stores private leads in `public.leads` and backend-only Hunter verification cache entries in `public.email_verification_cache`.

## New projects

Run [schema.sql](schema.sql) in the Supabase SQL Editor. It creates current tables, indexes, the `updated_at` trigger, and RLS policies. Leads are unique by `(user_id, domain)` and contain extracted signals, pipeline status, score reasons, validation output, Hunter results, and timestamps.

## Existing projects

Do not rerun the base schema. Run only unapplied migrations in numeric order:

1. `migrations/002_lead_validation.sql` adds validation fields and indexes.
2. `migrations/003_hunter_email_verification.sql` adds Hunter-result fields and cache storage.
3. `migrations/005_add_lead_ownership.sql` adds ownership, RLS policies, and per-user deduplication.

Migration 005 deliberately leaves old ownerless records unassigned. Create an Auth user, replace the sample UUID in its documented update statement, assign legacy rows, confirm the unassigned count is zero, then validate the `leads_user_id_required` constraint.

## Security

RLS lets authenticated users select, insert, update, and delete only leads where `auth.uid() = user_id`. Browser requests use the user's token. The backend service-role key is used only for server-side token verification, ownership checks, and Hunter cache access; never expose it to the browser or commit it.

See [the backend README](../backend/README.md) for API behavior and [the root README](../README.md) for setup.
