-- Personal Cloud OS - hosted control plane.
-- Owners sign in with Supabase Auth (+ TOTP 2FA). Each linked server signs in as its own "agent" auth user.
-- Every table uses RLS; anything that controls a server additionally requires the owner's session to be aal2 (2FA).

create extension if not exists pgcrypto with schema extensions;

-- ---------------------------------------------------------------- helpers
create or replace function public.is_aal2() returns boolean
language sql stable set search_path = '' as $$
  select coalesce((select auth.jwt() ->> 'aal'), '') = 'aal2'
$$;

-- ---------------------------------------------------------------- servers
create table public.servers (
  id            uuid primary key default gen_random_uuid(),
  owner_id      uuid not null references auth.users(id) on delete cascade,
  agent_user_id uuid unique references auth.users(id) on delete set null,
  name          text not null check (char_length(name) between 1 and 60),
  kind          text not null default 'windows_pc' check (kind in ('windows_pc', 'linux_vps')),
  hostname      text,
  os            text,
  version       text,
  status        text not null default 'offline' check (status in ('online', 'offline')),
  last_seen     timestamptz,
  metrics       jsonb not null default '{}'::jsonb,
  roots         jsonb not null default '[]'::jsonb,
  created_at    timestamptz not null default now()
);
create index servers_owner_idx on public.servers(owner_id);
alter table public.servers enable row level security;

create policy servers_select on public.servers for select to authenticated
  using (owner_id = (select auth.uid()) or agent_user_id = (select auth.uid()));
create policy servers_update_owner on public.servers for update to authenticated
  using (owner_id = (select auth.uid()) and (select public.is_aal2()))
  with check (owner_id = (select auth.uid()));
create policy servers_delete_owner on public.servers for delete to authenticated
  using (owner_id = (select auth.uid()) and (select public.is_aal2()));
-- Owners may only rename; everything else is written by agent_heartbeat() or the enroll function.
revoke insert, update on public.servers from anon, authenticated;
grant update (name) on public.servers to authenticated;

-- Server-side helper used by storage and realtime policies.
create or replace function public.can_use_server(p_server text) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (
    select 1 from public.servers s
    where s.id::text = p_server
      and ((s.owner_id = (select auth.uid()) and (select public.is_aal2()))
           or s.agent_user_id = (select auth.uid())))
$$;
revoke execute on function public.can_use_server(text) from public, anon;
grant execute on function public.can_use_server(text) to authenticated;

-- Heartbeat from an agent: only touches its own row.
create or replace function public.agent_heartbeat(p_status text, p_metrics jsonb, p_roots jsonb, p_version text)
returns void language plpgsql security definer set search_path = '' as $$
begin
  update public.servers
     set status = case when p_status = 'online' then 'online' else 'offline' end,
         last_seen = now(),
         metrics = coalesce(p_metrics, '{}'::jsonb),
         roots = coalesce(p_roots, '[]'::jsonb),
         version = left(p_version, 32),
         hostname = coalesce(left(p_metrics ->> 'hostname', 120), hostname),
         os = coalesce(left(p_metrics ->> 'os', 120), os)
   where agent_user_id = (select auth.uid());
  if not found then
    raise exception 'not a registered server agent';
  end if;
end $$;
revoke execute on function public.agent_heartbeat(text, jsonb, jsonb, text) from public, anon;
grant execute on function public.agent_heartbeat(text, jsonb, jsonb, text) to authenticated;

-- ------------------------------------------------------------ enrollments
create table public.enrollments (
  id         uuid primary key default gen_random_uuid(),
  owner_id   uuid not null references auth.users(id) on delete cascade,
  code_hash  text not null unique,
  name       text,
  expires_at timestamptz not null default now() + interval '30 minutes',
  used_at    timestamptz,
  created_at timestamptz not null default now()
);
create index enrollments_owner_idx on public.enrollments(owner_id);
alter table public.enrollments enable row level security;
create policy enrollments_select on public.enrollments for select to authenticated
  using (owner_id = (select auth.uid()));
revoke insert, update, delete on public.enrollments from anon, authenticated;

-- Returns a one-time code (shown once); only its hash is stored. Requires 2FA.
create or replace function public.create_enrollment(p_name text default null) returns text
language plpgsql security definer set search_path = '' as $$
declare v_code text;
begin
  if (select auth.uid()) is null then raise exception 'sign in required'; end if;
  if not (select public.is_aal2()) then raise exception 'two-factor authentication required'; end if;
  if (select count(*) from public.enrollments
       where owner_id = (select auth.uid()) and created_at > now() - interval '1 hour') >= 20 then
    raise exception 'too many link codes, try again later';
  end if;
  v_code := encode(extensions.gen_random_bytes(24), 'hex');
  insert into public.enrollments(owner_id, code_hash, name)
    values ((select auth.uid()), encode(extensions.digest(v_code, 'sha256'), 'hex'), left(p_name, 60));
  return v_code;
end $$;
revoke execute on function public.create_enrollment(text) from public, anon;
grant execute on function public.create_enrollment(text) to authenticated;

-- --------------------------------------------------------- realtime (RPC)
-- Private channel "server:<id>": only the owner (with 2FA) and that server's agent may read or send.
create policy server_channel_read on realtime.messages for select to authenticated
  using (realtime.messages.extension in ('broadcast', 'presence')
         and realtime.topic() like 'server:%'
         and (select public.can_use_server(substr(realtime.topic(), 8))));
create policy server_channel_write on realtime.messages for insert to authenticated
  with check (realtime.messages.extension in ('broadcast', 'presence')
              and realtime.topic() like 'server:%'
              and (select public.can_use_server(substr(realtime.topic(), 8))));

-- ----------------------------------------------------------------- storage
insert into storage.buckets (id, name, public, file_size_limit)
values ('relay', 'relay', false, 52428800), ('cloud-files', 'cloud-files', false, 52428800)
on conflict (id) do nothing;

-- relay/<server id>/<transfer id>/part-NNNNN : temporary transfer staging (agent purges after use / 1 h)
create policy relay_rw on storage.objects for all to authenticated
  using (bucket_id = 'relay' and (select public.can_use_server((storage.foldername(name))[1])))
  with check (bucket_id = 'relay' and (select public.can_use_server((storage.foldername(name))[1])));

-- cloud-files/<owner id>/... : the owner's cloud storage location (2FA session required)
create policy cloud_files_rw on storage.objects for all to authenticated
  using (bucket_id = 'cloud-files' and (storage.foldername(name))[1] = (select auth.uid())::text
         and (select public.is_aal2()))
  with check (bucket_id = 'cloud-files' and (storage.foldername(name))[1] = (select auth.uid())::text
              and (select public.is_aal2()));
