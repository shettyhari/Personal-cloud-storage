-- Move the policy helper out of the API-exposed public schema (Supabase security advisor 0029).
create schema if not exists private;
revoke all on schema private from public, anon;
grant usage on schema private to authenticated;

create or replace function private.can_use_server(p_server text) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (
    select 1 from public.servers s
    where s.id::text = p_server
      and ((s.owner_id = (select auth.uid()) and (select public.is_aal2()))
           or s.agent_user_id = (select auth.uid())))
$$;
revoke execute on function private.can_use_server(text) from public, anon;
grant execute on function private.can_use_server(text) to authenticated;

drop policy server_channel_read on realtime.messages;
drop policy server_channel_write on realtime.messages;
create policy server_channel_read on realtime.messages for select to authenticated
  using (realtime.messages.extension in ('broadcast', 'presence')
         and realtime.topic() like 'server:%'
         and (select private.can_use_server(substr(realtime.topic(), 8))));
create policy server_channel_write on realtime.messages for insert to authenticated
  with check (realtime.messages.extension in ('broadcast', 'presence')
              and realtime.topic() like 'server:%'
              and (select private.can_use_server(substr(realtime.topic(), 8))));

drop policy relay_rw on storage.objects;
create policy relay_rw on storage.objects for all to authenticated
  using (bucket_id = 'relay' and (select private.can_use_server((storage.foldername(name))[1])))
  with check (bucket_id = 'relay' and (select private.can_use_server((storage.foldername(name))[1])));

drop function public.can_use_server(text);
