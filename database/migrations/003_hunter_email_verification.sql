-- Hunter-specific results, separate from general email validation.
ALTER TABLE public.leads
ADD COLUMN IF NOT EXISTS hunter_verification_results
JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE public.leads
ADD COLUMN IF NOT EXISTS hunter_verification_checked_at
TIMESTAMPTZ;

-- Cache Hunter results to avoid spending credits repeatedly.
CREATE TABLE IF NOT EXISTS public.email_verification_cache (
    email TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    score INTEGER,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.email_verification_cache
ENABLE ROW LEVEL SECURITY;

-- This migration is idempotent and non-destructive: it only adds columns and a cache table
-- used by the backend service-role account. The generic validation fields remain distinct.
