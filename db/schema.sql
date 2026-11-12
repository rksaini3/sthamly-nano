-- ============================================================================
-- Sthamly Legal Desk: license database (Supabase)
--
-- Sirf license ke tables. Ye file ek baar Supabase SQL Editor me chalao.
-- Dev project par pehle chalao, production par nahi.
-- Service-role key kabhi desk software ya website (NEXT_PUBLIC_*) me nahi jaati.
-- Server par kisi document ya chat ka data nahi hota.
-- ============================================================================

begin;

-- 1. Key normalize, shape aur hash. Asli key kabhi store nahi hoti, sirf SHA-256.
--    Key format: STH-XXXX-XXXX-XXXX-XXXX (A-Z aur 2-7). Dash ya space optional.
--    Must match desk/desk_license.py (normalize_key) aur scripts/licenses.py.

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

create or replace function public.hash_license_key(input_key text)
returns text
language sql
immutable
parallel safe
set search_path = ''
as $$
    select encode(sha256(convert_to(public.normalize_license_key(input_key), 'UTF8')), 'hex');
$$;

-- 2. Updated-at trigger
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

-- 3. Licenses: sirf hash, profile, device limit, revoke
create table if not exists public.licenses (
    id uuid primary key default gen_random_uuid(),
    key_hash text not null unique,
    profile text not null default 'lawyer',
    max_devices integer not null default 2,
    note text,
    revoked boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),

    constraint licenses_key_hash_valid check (key_hash ~ '^[0-9a-f]{64}$'),
    constraint licenses_profile_valid check (profile in ('lawyer')),
    constraint licenses_max_devices_valid check (max_devices between 1 and 10)
);

drop trigger if exists licenses_set_updated_at on public.licenses;
create trigger licenses_set_updated_at
before update on public.licenses
for each row
execute function public.set_updated_at();

-- 4. Har device ka ek row (hash ki pehchaan, naam ya MAC nahi). max_devices isi se lagta hai.
create table if not exists public.license_devices (
    id uuid primary key default gen_random_uuid(),
    license_id uuid not null references public.licenses(id) on delete cascade,
    hardware_fingerprint text not null,
    client_os text,
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),

    constraint license_devices_fingerprint_valid check (length(hardware_fingerprint) between 8 and 128),
    constraint license_devices_unique unique (license_id, hardware_fingerprint)
);

-- 5. Verify: desk app anon key se yahi call karta hai
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

    -- Row lock: do activation ek saath device limit ko paar na kar saken.
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
        'label', 'Sthamly Legal Desk',
        'actions', '[]'::jsonb,
        'disclaimer', 'Ye drafting aur analysis sahayak hai, kanooni salah nahi. Istemaal se pehle kisi qualified vakeel se verify karwayein.'
    );
end;
$$;

-- 6. Row Level Security: on, aur koi public policy nahi. Anon key sirf verify function chala sakti hai.
alter table public.licenses enable row level security;
alter table public.license_devices enable row level security;

revoke all on function public.verify_client_activation(text, text, text) from public;
grant execute on function public.verify_client_activation(text, text, text)
    to anon, authenticated, service_role;

commit;

-- Sanity check (ye value match honi chahiye):
--   select public.hash_license_key('STH-ABCD-EFGH-IJKL-MNOP');
--     -> tests/fixtures/license_key.json ka "hash"
