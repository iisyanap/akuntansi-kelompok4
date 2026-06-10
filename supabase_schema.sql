create table if not exists public.accsys_companies (
  id text primary key,
  owner_id text not null default 'default',
  data jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

create index if not exists accsys_companies_owner_updated_idx
  on public.accsys_companies (owner_id, updated_at desc);

alter table public.accsys_companies enable row level security;

drop policy if exists "Allow anon read accsys companies" on public.accsys_companies;
drop policy if exists "Allow anon insert accsys companies" on public.accsys_companies;
drop policy if exists "Allow anon update accsys companies" on public.accsys_companies;
drop policy if exists "Allow anon delete accsys companies" on public.accsys_companies;

create policy "Allow anon read accsys companies"
  on public.accsys_companies for select
  to anon
  using (true);

create policy "Allow anon insert accsys companies"
  on public.accsys_companies for insert
  to anon
  with check (true);

create policy "Allow anon update accsys companies"
  on public.accsys_companies for update
  to anon
  using (true)
  with check (true);

create policy "Allow anon delete accsys companies"
  on public.accsys_companies for delete
  to anon
  using (true);
