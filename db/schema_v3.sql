-- ============================================================================
-- Sthamly NanoBrain — Schema v3
-- Canonical tables: jobs, licenses, models
--
-- IMPORTANT:
-- 1. Run this in the Supabase SQL Editor on a development/test project first.
-- 2. Do not run blindly on a production database containing important data.
-- 3. This schema standardizes the database contract expected by the current
--    admin.py and repository tests. The existing worker.py and API routes still
--    contain legacy quantization_jobs references and must be migrated separately.
-- ============================================================================

begin;

create extension if not exists pgcrypto;

-- ============================================================================
-- 1. MODEL ID NORMALIZATION
-- Must match:
--   lib/jobs.ts -> normalizeModelId()
--   scripts/sthamly_common.py -> normalize_model_id()
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

    -- Trim only outer whitespace, matching the TypeScript/Python functions.
    v := btrim(input_text, E' \t\r\n');

    if v = '' then
        return null;
    end if;

    -- Remove query string or fragment.
    v := regexp_replace(v, '[?#][\s\S]*$', '');

    -- Accept Hugging Face model URLs and short hf.co URLs.
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

    -- For a Hugging Face URL, keep only owner/model.
    if had_url then
        parts := regexp_match(v, '^([^/]+/[^/]+)');
        if parts is null then
            return null;
        end if;
        v := parts[1];
    end if;

    -- Exactly two path components. Each component:
    -- starts with an alphanumeric character, then allows alphanumeric,
    -- period, underscore or hyphen, up to 96 characters total.
    if v !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$' then
        return null;
    end if;

    -- Reject Hugging Face non-model namespaces.
    if lower(split_part(v, '/', 1)) = any (
        array[
            'datasets',
            'spaces',
            'papers',
            'collections',
            'docs',
            'blog',
            'api',
            'organizations',
            'models'
        ]
    ) then
        return null;
    end if;

    return v;
end;
$$;

-- ============================================================================
-- 2. LICENSE KEY NORMALIZATION AND HASHING
-- Raw license keys must never be stored in the database.
-- Expected key format: STH-XXXX-XXXX-XXXX-XXXX
-- X uses the Base32 alphabet A-Z and 2-7.
-- ============================================================================

create or replace function public.normalize_license_key(input_key text)
returns text
language sql
immutable
parallel safe
as $$
    select regexp_replace(
        upper(coalesce(input_key, '')),
        '[^A-Z0-9]',
        '',
        'g'
    );
$$;

create or replace function public.valid_license_key_shape(input_key text)
returns boolean
language sql
immutable
parallel safe
as $$
    select upper(
        regexp_replace(
            coalesce(input_key, ''),
            '[[:space:]]',
            '',
            'g'
        )
    ) ~ '^STH(-[A-Z2-7]{4}){4}$';
$$;

create or replace function public.hash_license_key(input_key text)
returns text
language sql
immutable
parallel safe
as $$
    select encode(
        digest(
            convert_to(public.normalize_license_key(input_key), 'UTF8'),
            'sha256'
        ),
        'hex'
    );
$$;

-- ============================================================================
-- 3. SHARED UPDATED-AT TRIGGER
-- ============================================================================

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at := now();
    return new;
end;
$$;

-- ============================================================================
-- 4. MODELS
-- Stores model metadata. Do not store model weights in this table.
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
-- Canonical statuses: queued, running, done, failed, refused
-- Canonical kinds: audit, calibrate
-- ============================================================================

create table if not exists public.jobs (
    id uuid primary key default gen_random_uuid(),

    kind text not null,
    hf_model text not null,

    -- Do not put an email, phone number, IP address or other direct identity
    -- here. Use a server-generated pseudonymous value where appropriate.
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
        check (result is null or jsonb_typeof(result) = 'object')
);

create index if not exists jobs_created_at_idx
    on public.jobs (created_at desc);

create index if not exists jobs_status_created_idx
    on public.jobs (status, created_at asc);

create index if not exists jobs_hf_model_lower_idx
    on public.jobs (lower(hf_model));

create index if not exists jobs_requester_created_idx
    on public.jobs (requester_hash, created_at desc);

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

    -- Optional association with a calibration job.
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
-- 7. CLAIM NEXT JOB
-- Atomically claims the oldest queued job.
-- Worker should call this RPC instead of selecting and updating separately.
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
-- 8. FAIL STALE JOBS
-- Fails jobs that have stopped sending heartbeats.
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
-- 9. PURGE OLD JOBS
-- Removes only terminal jobs older than the requested retention period.
-- Any associated license survives because licenses.job_id uses ON DELETE SET NULL.
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
-- 10. GET JOB
-- Returns the public job shape used by the web application.
-- Internal requester hashes are intentionally excluded.
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
-- 11. ROW LEVEL SECURITY
-- Browser access should use carefully scoped API routes.
-- Service-role access is intended for trusted server/worker environments.
-- ============================================================================

alter table public.models enable row level security;
alter table public.jobs enable row level security;
alter table public.licenses enable row level security;

-- No public policies are created here.
-- Supabase service-role requests bypass RLS. Never expose the service-role key
-- in browser code or NEXT_PUBLIC_* environment variables.

commit;

-- ============================================================================
-- 12. PROFILE SEEDS
-- These exact rows are checked by tests/test_common.py.
-- The table stores the allowed profile names for future UI/configuration use.
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