ALTER TABLE public.leads
  ADD COLUMN IF NOT EXISTS data_quality_score integer CHECK (data_quality_score BETWEEN 0 AND 100),
  ADD COLUMN IF NOT EXISTS validation_status text CHECK (validation_status IN ('passed', 'warning', 'failed')),
  ADD COLUMN IF NOT EXISTS validation_summary text,
  ADD COLUMN IF NOT EXISTS validation_details jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS validation_last_run_at timestamptz;

CREATE INDEX IF NOT EXISTS leads_validation_status_idx
  ON public.leads (validation_status);

CREATE INDEX IF NOT EXISTS leads_data_quality_score_idx
  ON public.leads (data_quality_score DESC);
