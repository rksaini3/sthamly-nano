-- ============================================================================
-- 0002: align the database with the worker, admin and web code (Option A: jobs).
-- Run after db/schema_v3.sql. Safe to re-run.
-- ============================================================================

begin;

-- 0. Key shape is checked on the NORMALIZED form (dashes and spaces optional).
--    Python valid_key_shape() in scripts/sthamly_common.py must use the same rule.
create or replace function public.valid_license_key_shape(input_key text)
returns boolean
language sql
immutable
parallel safe
as $$
    select public.normalize_license_key(input_key) ~ '^STH[A-Z2-7]{16}$';
$$;

-- 1. One active job per model at a time (race-safe duplicate guard).
--    If this fails, first remove duplicate active rows for the same model.
create unique index if not exists jobs_one_active_model
    on public.jobs (lower(hf_model))
    where status in ('queued', 'running');

-- 2. Devices that activated a license (desk app, device limit).
create table if not exists public.license_devices (
    id uuid primary key default gen_random_uuid(),
    license_id uuid not null references public.licenses(id) on delete cascade,
    hardware_fingerprint text not null,
    client_os text,
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),

    constraint license_devices_fingerprint_valid
        check (length(hardware_fingerprint) between 8 and 128),
    constraint license_devices_unique unique (license_id, hardware_fingerprint)
);

alter table public.license_devices enable row level security;
-- No policies: only SECURITY DEFINER functions below touch this table.

-- 3. Activation RPC called by desk/desk_license.py (anon key).
--    Messages are matched by desk_license.py: "deactivated" = revoked, "limit" = device limit.
create or replace function public.verify_client_activation(
    p_license_key text,
    p_hardware_fingerprint text,
    p_client_os text default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    lic public.licenses%rowtype;
    used_count integer;
    fingerprint text := btrim(coalesce(p_hardware_fingerprint, ''));
begin
    if not public.valid_license_key_shape(p_license_key) then
        return jsonb_build_object('success', false, 'message', 'invalid_key');
    end if;

    if length(fingerprint) < 8 or length(fingerprint) > 128 then
        return jsonb_build_object('success', false, 'message', 'invalid_device');
    end if;

    select * into lic
    from public.licenses
    where key_hash = public.hash_license_key(p_license_key)
    for update;

    if not found then
        return jsonb_build_object('success', false, 'message', 'invalid_key');
    end if;

    if lic.revoked then
        return jsonb_build_object('success', false, 'message', 'key deactivated');
    end if;

    if exists (
        select 1 from public.license_devices
        where license_id = lic.id and hardware_fingerprint = fingerprint
    ) then
        update public.license_devices
        set last_seen_at = now(),
            client_os = coalesce(p_client_os, client_os)
        where license_id = lic.id and hardware_fingerprint = fingerprint;
    else
        select count(*)::integer into used_count
        from public.license_devices
        where license_id = lic.id;

        if used_count >= lic.max_devices then
            return jsonb_build_object('success', false, 'message', 'device limit reached');
        end if;

        insert into public.license_devices (license_id, hardware_fingerprint, client_os)
        values (lic.id, fingerprint, p_client_os);
    end if;

    return jsonb_build_object(
        'success', true,
        'message', 'ok',
        'profile', lic.profile,
        'label', 'Sthamly ' || initcap(lic.profile) || ' Desk',
        'actions', '[]'::jsonb,
        'disclaimer', '100% Private Offline Workspace footprint.'
    );
end;
$$;

-- 4. Function privileges. Worker-only functions are service_role only.
revoke all on function public.claim_next_job() from public, anon, authenticated;
grant execute on function public.claim_next_job() to service_role;

revoke all on function public.fail_stale_jobs(integer) from public, anon, authenticated;
grant execute on function public.fail_stale_jobs(integer) to service_role;

revoke all on function public.purge_old_jobs(integer) from public, anon, authenticated;
grant execute on function public.purge_old_jobs(integer) to service_role;

-- Public-safe: get_job() omits requester_hash. Web pages call it with the anon key.
revoke all on function public.get_job(uuid) from public;
grant execute on function public.get_job(uuid) to anon, authenticated, service_role;

-- Desk activation must work with the anon key.
revoke all on function public.verify_client_activation(text, text, text) from public;
grant execute on function public.verify_client_activation(text, text, text)
    to anon, authenticated, service_role;

commit;