create extension if not exists pgcrypto;

create table if not exists public.leads (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  domain text not null,
  website_url text not null,
  company_name text,
  description text,
  emails text[] not null default '{}',
  phones text[] not null default '{}',
  social_links jsonb not null default '{}'::jsonb,
  source_url text,
  lead_score integer not null default 0 check (lead_score between 0 and 100),
  score_reasons text[] not null default '{}',
  status text not null default 'new' check (status in ('new','reviewed','qualified','disqualified')),
  data_quality_score integer check (data_quality_score between 0 and 100),
  validation_status text check (validation_status in ('passed','warning','failed')),
  validation_summary text,
  validation_details jsonb not null default '{}'::jsonb,
  validation_last_run_at timestamptz,
  hunter_verification_results jsonb not null default '[]'::jsonb,
  hunter_verification_checked_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists leads_user_id_domain_key on public.leads (user_id, domain);

create index if not exists leads_created_at_idx on public.leads (created_at desc);
create index if not exists leads_user_id_created_at_idx on public.leads (user_id, created_at desc);
create index if not exists leads_score_idx on public.leads (lead_score desc);
create index if not exists leads_company_name_idx on public.leads using gin (to_tsvector('simple', coalesce(company_name,'') || ' ' || coalesce(description,'')));

create table if not exists public.email_verification_cache (
  email text primary key,
  status text not null,
  score integer,
  details jsonb not null default '{}'::jsonb,
  checked_at timestamptz not null default now()
);

alter table public.email_verification_cache enable row level security;

alter table public.leads enable row level security;

create policy "Users can select their own leads"
on public.leads for select to authenticated
using ((select auth.uid()) = user_id);

create policy "Users can insert their own leads"
on public.leads for insert to authenticated
with check ((select auth.uid()) = user_id);

create policy "Users can update their own leads"
on public.leads for update to authenticated
using ((select auth.uid()) = user_id)
with check ((select auth.uid()) = user_id);

create policy "Users can delete their own leads"
on public.leads for delete to authenticated
using ((select auth.uid()) = user_id);

-- Ensure the app can cache Hunter verification without exposing the API key in browser code.
-- The backend uses service_role access for these server-side reads and writes.

create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists leads_updated_at_trigger on public.leads;
create trigger leads_updated_at_trigger
before update on public.leads
for each row execute function public.set_updated_at();

-- The backend uses the service-role key only for its private Hunter cache and
-- explicit ownership checks. Lead queries use a request-scoped user token so RLS applies.
