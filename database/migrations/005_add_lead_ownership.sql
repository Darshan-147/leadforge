-- Private per-user lead lists for existing LeadForge projects.
-- This is non-destructive: existing rows remain intact but must be assigned below.
-- Run this file after migrations 002 and 003 in the Supabase SQL Editor.

alter table public.leads
  add column if not exists user_id uuid references auth.users(id) on delete cascade;

-- Remove only the actual one-column UNIQUE constraint on leads.domain. The name
-- is discovered from PostgreSQL rather than assumed to be leads_domain_key.
do $$
declare
  domain_constraint text;
begin
  select c.conname into domain_constraint
  from pg_constraint c
  join pg_class t on t.oid = c.conrelid
  join pg_namespace n on n.oid = t.relnamespace
  where n.nspname = 'public'
    and t.relname = 'leads'
    and c.contype = 'u'
    and array_length(c.conkey, 1) = 1
    and exists (
      select 1 from unnest(c.conkey) as key(attnum)
      join pg_attribute a on a.attrelid = t.oid and a.attnum = key.attnum
      where a.attname = 'domain'
    );

  if domain_constraint is not null then
    execute format('alter table public.leads drop constraint %I', domain_constraint);
  end if;
end
$$;

create unique index if not exists leads_user_id_domain_key
  on public.leads (user_id, domain);

create index if not exists leads_user_id_created_at_idx
  on public.leads (user_id, created_at desc);

-- NOT VALID preserves legacy records while enforcing an owner for every future
-- insert/update. After assigning legacy records, run the validation SQL below.
alter table public.leads
  add constraint leads_user_id_required check (user_id is not null) not valid;

alter table public.leads enable row level security;

drop policy if exists "Users can select their own leads" on public.leads;
drop policy if exists "Users can insert their own leads" on public.leads;
drop policy if exists "Users can update their own leads" on public.leads;
drop policy if exists "Users can delete their own leads" on public.leads;

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

-- REQUIRED MANUAL STEP (replace the UUID after creating/signing in as owner):
-- update public.leads
-- set user_id = '00000000-0000-0000-0000-000000000000'
-- where user_id is null;
--
-- Confirm no legacy records are unassigned, then finalize the data check:
-- select count(*) as unassigned_leads from public.leads where user_id is null;
-- alter table public.leads validate constraint leads_user_id_required;
