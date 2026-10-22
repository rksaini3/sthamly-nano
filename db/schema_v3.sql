-- ============================================================================
-- Sthamly NanoBrain: database schema, v3.1 (consolidated)
--
-- Canonical tables: jobs, licenses, license_devices, models, license_profiles
-- This file = schema_v3.sql + db/migrations/0002_align_jobs_v3.sql + fixes.
--
-- Fresh project:            run this whole file once in the Supabase SQL Editor.
-- Already on schema_v3:     run db/migrations/0002_align_jobs_v3.sql instead.
--
-- Run on a development/test project first. Do not run on production blindly.
-- Never expose the service-role key to browser code or NEXT_PUBLIC_* variables.
-- ============================================================================

begin;

-- ============================================================================
-- 1. MODEL ID NORMALIZATION
-- Must match lib/jobs.ts -> normalizeModelId() and scripts/sthamly_common.py -> normalize_model_id().
-- Checked against tests/fixtures/model_ids.json.
-- ============================================================================

create or replace function public.normalize_hf_model(input_text text)
returns text
language plpgsql
immutable
parallel safe
as $$
declare
    v text;
    had_url boolean := false;
    parts text[];
begin
    if input_text is null then
        return null;
    end if;

    -- Trim only outer whitespace (space, tab, CR, LF), like the TypeScript and Python versions.
    v := btrim(input_text, E' \\t\\r\\n');

    if v = '' then
        return null;
    end if;

    -- Remove query string or fragment. NOTE: '.' also matches newlines in PostgreSQL regex.
    v := regexp_replace(v, '[?#].*$', '');

    -- Accept huggingface.co and hf.co links, with or without scheme and www.
    if v ~* '^(https?://)?(www\.)?(huggingface\.co|hf\.co)/' then
        v := regexp_replace(
            v,
            '^(https?://)?(www\.)?(huggingface\.co|hf\.co)/',
            '',
            'i'
        );
        had_url := true;
    end if;

    -- Remove trailing slashes.
    v := regexp_replace(v, '/+$', '');

    -- For a link, keep only owner/model (drops /tree/main, /resolve/main, ...).
    if had_url then
        parts := regexp_match(v, '^([^/]+/[^/]+)');
        if parts is null then
            return null;
        end if;
        v := parts[1];
    end if;

    -- Exactly two components. Each starts with a letter or digit, then letters,
    -- digits, '.', '_' or '-'. At most 96 characters per component.
    if v !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$' then
        return null;
    end if;

    -- Reject Hugging Face non-model namespaces.
    if lower(split_part(v, '/', 1)) = any (
        array[
            'datasets', 'spaces', 'papers', 'collections',
            'docs', 'blog', 'api', 'organizations', 'models'
        ]
    ) then
        return null;
    end if;

    return v;
end;
$$;

-- ============================================================================
-- 2. LICENSE KEY NORMALIZATION, SHAPE AND HASHING
-- Raw keys are never stored. Only the SHA-256 of the normalized key is kept.
-- Key format: STH-XXXX-XXXX-XXXX-XXXX, X from A-Z and 2-7.
-- Dashes and spaces are optional when a key is typed in.
-- Must match scripts/sthamly_common.py (normalize_key, valid_key_shape, hash_key).
-- ============================================================================

create or replace function public.normalize_license_key(input_key text)
returns text
language sql
immutable
parallel safe
as $$
    select regexp_replace(upper(coalesce(input_key, '')), '[^A-Z0-9]', '', 'g');
$$;

create or replace function public.valid_license_key_shape(input_key text)
returns boolean
language sql
immutable
parallel safe
as $$
    select public.normalize_license_key(input_key) ~ '^STH[A-Z2-7]{16}$';
$$;

-- Built-in sha256() (PostgreSQL 11+). No pgcrypto needed.
create or replace function public.hash_license_key(input_key text)
returns text
language sql
immutable
parallel safe
set search_path = ''
as $$
    select encode(
        sha256(convert_to(public.normalize_license_key(input_key), 'UTF8')),
        'hex'
    );
$$;

-- ============================================================================
-- 3. SHARED UPDATED-AT TRIGGER
-- ============================================================================

create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    new.updated_at := now();
    return new;
end;
$$;

-- ============================================================================
-- 4. MODELS
-- Model metadata only. Never store weights here.
-- ============================================================================

create table if not exists public.models (
    id uuid primary key default gen_random_uuid(),

    hf_model text not null unique,
    display_name text,
    publisher text,
    description text,
    metadata jsonb not null default '{}'::jsonb,
    enabled boolean not null default true,

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),

    constraint models_hf_model_valid
        check (public.normalize_hf_model(hf_model) = hf_model)
);

create index if not exists models_enabled_created_idx
    on public.models (enabled, created_at desc);

drop trigger if exists models_set_updated_at on public.models;
create trigger models_set_updated_at
before update on public.models
for each row
execute function public.set_updated_at();

-- ============================================================================
-- 5. JOBS
-- Statuses: queued, running, done, failed, refused
--   refused = policy rejection (bad model, custom code, license, size)
--   failed  = runtime error (tool crash, timeout, upload failure)
-- Kinds:    audit (free plain-Q4 audit), calibrate (full Hindi build)
-- ============================================================================

create table if not exists public.jobs (
    id uuid primary key default gen_random_uuid(),

    kind text not null,
    hf_model text not null,

    -- Pseudonymous requester id (server-side sha256 of salt|ip). Never a raw IP or email.
    requester_hash text not null,

    status text not null default 'queued',

    step text,
    progress integer not null default 0,
    steps jsonb not null default '[]'::jsonb,

    result jsonb,
    error text,

    queue_position integer,

    heartbeat_at timestamptz,

    created_at timestamptz not null default now(),
    started_at timestamptz,
    finished_at timestamptz,
    updated_at timestamptz not null default now(),

    constraint jobs_kind_valid
        check (kind in ('audit', 'calibrate')),

    constraint jobs_status_valid
        check (status in ('queued', 'running', 'done', 'failed', 'refused')),

    constraint jobs_progress_valid
        check (progress between 0 and 100),

    constraint jobs_hf_model_valid
        check (public.normalize_hf_model(hf_model) = hf_model),

    constraint jobs_requester_hash_nonempty
        check (length(btrim(requester_hash)) between 1 and 200),

    constraint jobs_steps_array
        check (jsonb_typeof(steps) = 'array'),

    constraint jobs_result_object
        check (result is null or jsonb_typeof(result) = 'object'),

    constraint jobs_error_length
        check (error is null or length(error) <= 2000)
);

-- At most one active (queued or running) job per model. Race-safe duplicate guard.
-- If this fails on an existing database, remove the duplicate active rows first.
create unique index if not exists jobs_one_active_model
    on public.jobs (lower(hf_model))
    where status in ('queued', 'running');

create index if not exists jobs_created_at_idx
    on public.jobs (created_at desc);

create index if not exists jobs_status_created_idx
    on public.jobs (status, created_at asc);

create index if not exists jobs_requester_created_idx
    on public.jobs (requester_hash, created_at desc);

create index if not exists jobs_finished_idx
    on public.jobs (status, finished_at desc);

drop trigger if exists jobs_set_updated_at on public.jobs;
create trigger jobs_set_updated_at
before update on public.jobs
for each row
execute function public.set_updated_at();

-- ============================================================================
-- 6. LICENSES
-- Only key_hash is stored. The plaintext key is shown once by admin.py.
-- ============================================================================

create table if not exists public.licenses (
    id uuid primary key default gen_random_uuid(),

    key_hash text not null unique,
    profile text not null,
    max_devices integer not null default 2,
    note text,
    revoked boolean not null default false,

    -- Optional link to the job this license was sold for. Survives job purge.
    job_id uuid references public.jobs(id) on delete set null,

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    revoked_at timestamptz,

    constraint licenses_key_hash_valid
        check (key_hash ~ '^[0-9a-f]{64}$'),

    constraint licenses_profile_valid
        check (profile in ('general', 'lawyer', 'finance', 'student')),

    constraint licenses_max_devices_valid
        check (max_devices between 1 and 10)
);

create index if not exists licenses_profile_created_idx
    on public.licenses (profile, created_at desc);

create index if not exists licenses_active_idx
    on public.licenses (revoked, created_at desc);

create index if not exists licenses_job_id_idx
    on public.licenses (job_id);

drop trigger if exists licenses_set_updated_at on public.licenses;
create trigger licenses_set_updated_at
before update on public.licenses
for each row
execute function public.set_updated_at();

-- ============================================================================
-- 7. LICENSE DEVICES
-- One row per device that activated a license. Enforces max_devices.
-- ============================================================================

create table if not exists public.license_devices (
    id uuid primary key default gen_random_uuid(),

    license_id uuid not null references public.licenses(id) on delete cascade,
    hardware_fingerprint text not null,
    client_os text,

    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),

    constraint license_devices_fingerprint_valid
        check (length(hardware_fingerprint) between 8 and 128),

    constraint license_devices_unique
        unique (license_id, hardware_fingerprint)
);

-- ============================================================================
-- 8. LICENSE PROFILES (reference list)
-- ============================================================================

create table if not exists public.license_profiles (
    profile text primary key,
    description text not null,
    created_at timestamptz not null default now(),

    constraint license_profiles_name_valid
        check (profile in ('general', 'lawyer', 'finance', 'student'))
);

insert into public.license_profiles (profile, description)
values
    ('general', 'General-purpose profile'),
    ('lawyer', 'Legal-work profile'),
    ('finance', 'Finance-work profile'),
    ('student', 'Student profile')
on conflict (profile) do update
set description = excluded.description;

-- ============================================================================
-- 9. CLAIM NEXT JOB (worker only, service role)
-- Atomically claims the oldest queued job. FOR UPDATE SKIP LOCKED makes
-- concurrent workers safe.
-- ============================================================================

create or replace function public.claim_next_job()
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    claimed public.jobs%rowtype;
begin
    update public.jobs
    set
        status = 'running',
        started_at = coalesce(started_at, now()),
        heartbeat_at = now(),
        step = coalesce(step, 'Starting'),
        updated_at = now()
    where id = (
        select j.id
        from public.jobs as j
        where j.status = 'queued'
        order by j.created_at asc
        for update skip locked
        limit 1
    )
    returning * into claimed;

    if not found then
        return null;
    end if;

    return jsonb_build_object(
        'id', claimed.id,
        'kind', claimed.kind,
        'hf_model', claimed.hf_model,
        'status', claimed.status,
        'progress', claimed.progress,
        'steps', claimed.steps,
        'created_at', claimed.created_at
    );
end;
$$;

-- ============================================================================
-- 10. FAIL STALE JOBS (worker only, service role)
-- Fails running jobs whose heartbeat is older than the threshold.
-- Returns the number of jobs changed.
-- ============================================================================

create or replace function public.fail_stale_jobs(
    stale_after_seconds integer default 900
)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
    changed_count integer;
begin
    if stale_after_seconds < 60 then
        raise exception 'stale_after_seconds must be at least 60';
    end if;

    update public.jobs
    set
        status = 'failed',
        error = 'Worker heartbeat timed out.',
        finished_at = now(),
        updated_at = now()
    where status = 'running'
      and coalesce(heartbeat_at, started_at, created_at)
          < now() - make_interval(secs => stale_after_seconds);

    get diagnostics changed_count = row_count;
    return changed_count;
end;
$$;

-- ============================================================================
-- 11. PURGE OLD JOBS (worker or admin, service role)
-- Removes only finished jobs older than the retention period.
-- Licenses survive: licenses.job_id is ON DELETE SET NULL.
-- ============================================================================

create or replace function public.purge_old_jobs(
    retention_days integer default 90
)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
    deleted_count integer;
begin
    if retention_days < 1 then
        raise exception 'retention_days must be at least 1';
    end if;

    delete from public.jobs
    where status in ('done', 'failed', 'refused')
      and coalesce(finished_at, updated_at, created_at)
          < now() - make_interval(days => retention_days);

    get diagnostics deleted_count = row_count;
    return deleted_count;
end;
$$;

-- ============================================================================
-- 12. GET JOB (public, anon key allowed)
-- Returns the public job shape. requester_hash is intentionally excluded.
-- ============================================================================

create or replace function public.get_job(job_id uuid)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
    select jsonb_build_object(
        'id', j.id,
        'kind', j.kind,
        'hf_model', j.hf_model,
        'status', j.status,
        'step', j.step,
        'progress', j.progress,
        'steps', j.steps,
        'result', j.result,
        'error', j.error,
        'queue_position',
            case
                when j.status = 'queued' then (
                    select count(*)::integer
                    from public.jobs q
                    where q.status = 'queued'
                      and (q.created_at, q.id) < (j.created_at, j.id)
                ) + 1
                else null
            end,
        'created_at', j.created_at,
        'finished_at', j.finished_at
    )
    from public.jobs j
    where j.id = job_id;
$$;

-- ============================================================================
-- 13. VERIFY CLIENT ACTIVATION (desk app, anon key)
-- Called by desk/desk_license.py as rpc('verify_client_activation', {...}).
-- Messages are matched by desk_license.py:
--   "deactivated" -> revoked, "limit" -> device limit reached.
-- ============================================================================

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

    -- Lock the license row so two activations cannot both pass the device limit.
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

-- ============================================================================
-- 14. ROW LEVEL SECURITY
-- RLS is on everywhere and NO public policies exist. The anon and authenticated
-- roles can read nothing directly. Access happens through:
--   - service_role (worker, admin CLI, Next.js server routes): bypasses RLS
--   - get_job() and verify_client_activation(): SECURITY DEFINER, limited output
-- ============================================================================

alter table public.models enable row level security;
alter table public.jobs enable row level security;
alter table public.licenses enable row level security;
alter table public.license_devices enable row level security;
alter table public.license_profiles enable row level security;

-- ============================================================================
-- 15. FUNCTION PRIVILEGES
-- Functions are executable by PUBLIC by default in PostgreSQL. Lock them down.
-- ============================================================================

-- Worker and maintenance functions: service role only.
revoke all on function public.claim_next_job() from public, anon, authenticated;
grant execute on function public.claim_next_job() to service_role;

revoke all on function public.fail_stale_jobs(integer) from public, anon, authenticated;
grant execute on function public.fail_stale_jobs(integer) to service_role;

revoke all on function public.purge_old_jobs(integer) from public, anon, authenticated;
grant execute on function public.purge_old_jobs(integer) to service_role;

-- Public-safe read: web pages call get_job() with the anon key.
revoke all on function public.get_job(uuid) from public;
grant execute on function public.get_job(uuid) to anon, authenticated, service_role;

-- Desk activation: called with the anon key.
revoke all on function public.verify_client_activation(text, text, text) from public;
grant execute on function public.verify_client_activation(text, text, text)
    to anon, authenticated, service_role;

commit;

-- ============================================================================
-- SANITY CHECKS (run after applying; all should return the value shown)
--
--   select public.normalize_hf_model('https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct?x=1');
--     -> 'Qwen/Qwen2.5-1.5B-Instruct'
--   select public.normalize_hf_model('https://huggingface.co/datasets/a/b');
--     -> NULL
--   select public.hash_license_key('STH-ABCD-EFGH-IJKL-MNOP');
--     -> must equal the hash in tests/fixtures/license_key.json
--   select public.valid_license_key_shape('sth abcd-efgh ijkl mnop');
--     -> true
--   select public.verify_client_activation('STH-ABCD-EFGH-IJKL-MNOP', 'device-0001', 'test');
--     -> {"success": false, "message": "invalid_key"} (no such license yet)
-- ============================================================================