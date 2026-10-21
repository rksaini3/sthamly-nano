-- ============================================================
-- STHAMLY NANOBRAIN
-- Database Schema v3
-- ============================================================
--
-- Main components:
--   1. quantization_jobs
--   2. models
--   3. activation_profiles
--   4. activation_keys
--   5. activation_devices
--   6. verify_client_activation()
--   7. normalize_hf_model()
--
-- IMPORTANT:
--   Run using the Supabase SQL Editor.
--   Back up an existing database before applying changes.
-- ============================================================


BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;


-- ============================================================
-- 1. MODEL ID NORMALIZATION
-- ============================================================

CREATE OR REPLACE FUNCTION public.normalize_hf_model(input_text text)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
PARALLEL SAFE
AS $$
DECLARE
    v text;
    had_url boolean := false;
    owner_name text;
    model_name text;
BEGIN
    IF input_text IS NULL THEN
        RETURN NULL;
    END IF;

    v := btrim(input_text);

    IF v = '' THEN
        RETURN NULL;
    END IF;

    -- Remove query string or fragment.
    v := regexp_replace(v, '[?#].*$', '');

    -- Recognize Hugging Face URLs.
    IF v ~* '^(https?://)?(www\.)?(huggingface\.co|hf\.co)/' THEN
        v := regexp_replace(
            v,
            '^(https?://)?(www\.)?(huggingface\.co|hf\.co)/',
            '',
            'i'
        );

        had_url := true;
    END IF;

    -- Remove trailing slash.
    v := regexp_replace(v, '/+$', '');

    -- A Hugging Face URL can include /tree/main, /blob/main, etc.
    IF had_url THEN
        v := substring(v FROM '^([^/]+/[^/]+)');
    END IF;

    IF v IS NULL OR v = '' THEN
        RETURN NULL;
    END IF;

    IF v !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$' THEN
        RETURN NULL;
    END IF;

    owner_name := split_part(v, '/', 1);
    model_name := split_part(v, '/', 2);

    IF lower(owner_name) IN (
        'datasets',
        'spaces',
        'papers',
        'collections',
        'docs',
        'blog',
        'api',
        'organizations',
        'models'
    ) THEN
        RETURN NULL;
    END IF;

    RETURN owner_name || '/' || model_name;
END;
$$;


-- ============================================================
-- 2. QUANTIZATION JOBS
-- ============================================================

CREATE TABLE IF NOT EXISTS public.quantization_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    hf_model_identifier text NOT NULL,

    status text NOT NULL DEFAULT 'pending'
        CHECK (
            status IN (
                'pending',
                'extracting',
                'benchmarking',
                'quantizing',
                'completed',
                'failed',
                'refused'
            )
        ),

    progress integer NOT NULL DEFAULT 0
        CHECK (progress >= 0 AND progress <= 100),

    step text,

    steps jsonb NOT NULL DEFAULT '[]'::jsonb,

    result jsonb,

    error_note text,

    heartbeat_at timestamptz,

    created_at timestamptz NOT NULL DEFAULT now(),

    started_at timestamptz,

    finished_at timestamptz,

    CONSTRAINT quantization_jobs_model_valid
        CHECK (
            public.normalize_hf_model(hf_model_identifier) IS NOT NULL
        )
);


CREATE INDEX IF NOT EXISTS idx_quantization_jobs_status_created
ON public.quantization_jobs (status, created_at);


CREATE INDEX IF NOT EXISTS idx_quantization_jobs_model_created
ON public.quantization_jobs (hf_model_identifier, created_at DESC);


CREATE INDEX IF NOT EXISTS idx_quantization_jobs_heartbeat
ON public.quantization_jobs (heartbeat_at);


COMMENT ON TABLE public.quantization_jobs IS
'NanoBrain worker queue and processing history.';


COMMENT ON COLUMN public.quantization_jobs.result IS
'JSON result produced by actual model processing and audit.';


COMMENT ON COLUMN public.quantization_jobs.error_note IS
'Sanitized error message. Never store credentials or private tokens here.';


-- ============================================================
-- 3. MODEL REGISTRY
-- ============================================================

CREATE TABLE IF NOT EXISTS public.models (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    model_name text NOT NULL,

    hf_repo text NOT NULL,

    base_model text,

    quant_type text NOT NULL,

    size_gb numeric(12, 4),

    fp16_size_gb numeric(12, 4),

    download_url text,

    results jsonb,

    variants_meta jsonb,

    notes text,

    created_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT models_hf_repo_valid
        CHECK (
            public.normalize_hf_model(hf_repo) IS NOT NULL
        ),

    CONSTRAINT models_size_nonnegative
        CHECK (
            (size_gb IS NULL OR size_gb >= 0)
            AND
            (fp16_size_gb IS NULL OR fp16_size_gb >= 0)
        )
);


CREATE INDEX IF NOT EXISTS idx_models_created
ON public.models (created_at DESC);


CREATE INDEX IF NOT EXISTS idx_models_hf_repo
ON public.models (hf_repo);


COMMENT ON TABLE public.models IS
'Public catalogue of verified, published model artifacts.';


-- ============================================================
-- 4. ACTIVATION PROFILES
-- ============================================================

CREATE TABLE IF NOT EXISTS public.activation_profiles (
    profile text PRIMARY KEY,

    label text NOT NULL,

    actions jsonb NOT NULL DEFAULT '[]'::jsonb,

    system_prompt text NOT NULL DEFAULT '',

    disclaimer text NOT NULL DEFAULT
        'AI-generated output must be reviewed by a qualified professional.',

    is_active boolean NOT NULL DEFAULT true,

    created_at timestamptz NOT NULL DEFAULT now()
);


INSERT INTO public.activation_profiles
    (profile, label, actions, system_prompt, disclaimer)
VALUES
(
    'general',
    'Sthamly General Desk',
    '[]'::jsonb,
    'You are a local AI assistant. Be clear about uncertainty. Never invent facts, sources, or citations.',
    'AI-generated output can contain errors. Verify important information independently.'
),
(
    'lawyer',
    'Sthamly Legal Desk',
    '["summarize_document","draft_document","translate_document","extract_dates","extract_parties"]'::jsonb,
    'You assist legal professionals with document review and drafting. Never invent legal provisions, case names, citations, quotations, or court holdings. Distinguish source text from inference. Flag outdated or uncertain law. The lawyer must independently verify every legal proposition and citation.',
    'This software provides assistance, not legal advice or a guarantee of legal accuracy. Verify statutes, amendments, judgments, citations, and all generated documents before use.'
),
(
    'finance',
    'Sthamly Finance Desk',
    '[]'::jsonb,
    'You are a financial research assistant. Separate facts, assumptions, and projections. Do not invent figures or sources.',
    'Financial outputs may be incomplete or incorrect. Verify data independently.'
),
(
    'student',
    'Sthamly Student Desk',
    '[]'::jsonb,
    'You are a learning assistant. Explain concepts clearly and encourage independent understanding.',
    'AI-generated educational content may contain mistakes. Verify important facts.'
)
ON CONFLICT (profile) DO NOTHING;


-- ============================================================
-- 5. ACTIVATION KEYS
-- ============================================================

CREATE TABLE IF NOT EXISTS public.activation_keys (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    license_key_hash text NOT NULL UNIQUE
        CHECK (license_key_hash ~ '^[0-9a-f]{64}$'),

    assigned_profile text NOT NULL
        REFERENCES public.activation_profiles(profile),

    associated_job_id uuid
        REFERENCES public.quantization_jobs(id)
        ON DELETE SET NULL,

    max_activations integer NOT NULL DEFAULT 2
        CHECK (max_activations BETWEEN 1 AND 10),

    is_active boolean NOT NULL DEFAULT true,

    note text,

    created_at timestamptz NOT NULL DEFAULT now(),

    revoked_at timestamptz
);


CREATE INDEX IF NOT EXISTS idx_activation_keys_profile
ON public.activation_keys (assigned_profile);


CREATE INDEX IF NOT EXISTS idx_activation_keys_active
ON public.activation_keys (is_active);


COMMENT ON TABLE public.activation_keys IS
'Stores only SHA-256 hashes of activation keys, never plaintext keys.';


-- ============================================================
-- 6. ACTIVATION DEVICES
-- ============================================================

CREATE TABLE IF NOT EXISTS public.activation_devices (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    activation_key_id uuid NOT NULL
        REFERENCES public.activation_keys(id)
        ON DELETE CASCADE,

    hardware_fingerprint text NOT NULL
        CHECK (hardware_fingerprint ~ '^[0-9a-f]{32}$'),

    client_os text NOT NULL
        CHECK (client_os IN ('windows', 'macos', 'linux')),

    first_seen_at timestamptz NOT NULL DEFAULT now(),

    last_seen_at timestamptz NOT NULL DEFAULT now(),

    UNIQUE (activation_key_id, hardware_fingerprint)
);


CREATE INDEX IF NOT EXISTS idx_activation_devices_key
ON public.activation_devices (activation_key_id);


-- ============================================================
-- 7. KEY HASH NORMALIZATION
-- ============================================================

CREATE OR REPLACE FUNCTION public.normalize_license_key(input_key text)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT upper(regexp_replace(coalesce(input_key, ''), '[^A-Za-z0-9]', '', 'g'));
$$;


CREATE OR REPLACE FUNCTION public.hash_license_key(input_key text)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT encode(
        digest(
            convert_to(public.normalize_license_key(input_key), 'UTF8'),
            'sha256'
        ),
        'hex'
    );
$$;


-- ============================================================
-- 8. LEGAL DESK ACTIVATION RPC
-- ============================================================

CREATE OR REPLACE FUNCTION public.verify_client_activation(
    p_license_key text,
    p_hardware_fingerprint text,
    p_client_os text
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_key public.activation_keys%ROWTYPE;
    v_profile public.activation_profiles%ROWTYPE;
    v_device public.activation_devices%ROWTYPE;
    v_key_hash text;
    v_device_count integer;
BEGIN
    -- Basic input validation.
    IF p_license_key IS NULL
       OR length(public.normalize_license_key(p_license_key)) <> 19
       OR left(public.normalize_license_key(p_license_key), 3) <> 'STH'
    THEN
        RETURN jsonb_build_object(
            'success', false,
            'message', 'invalid_key'
        );
    END IF;

    IF p_hardware_fingerprint IS NULL
       OR p_hardware_fingerprint !~ '^[0-9a-f]{32}$'
    THEN
        RETURN jsonb_build_object(
            'success', false,
            'message', 'invalid_device'
        );
    END IF;

    IF p_client_os IS NULL
       OR p_client_os NOT IN ('windows', 'macos', 'linux')
    THEN
        RETURN jsonb_build_object(
            'success', false,
            'message', 'invalid_device'
        );
    END IF;

    v_key_hash := public.hash_license_key(p_license_key);

    -- Lock the license row while checking/updating device activation.
    SELECT *
    INTO v_key
    FROM public.activation_keys
    WHERE license_key_hash = v_key_hash
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object(
            'success', false,
            'message', 'invalid_key'
        );
    END IF;

    IF NOT v_key.is_active THEN
        RETURN jsonb_build_object(
            'success', false,
            'message', 'revoked'
        );
    END IF;

    SELECT *
    INTO v_profile
    FROM public.activation_profiles
    WHERE profile = v_key.assigned_profile
      AND is_active = true;

    IF NOT FOUND THEN
        RETURN jsonb_build_object(
            'success', false,
            'message', 'profile_unavailable'
        );
    END IF;

    -- Existing device: update its last-seen timestamp.
    SELECT *
    INTO v_device
    FROM public.activation_devices
    WHERE activation_key_id = v_key.id
      AND hardware_fingerprint = p_hardware_fingerprint
    FOR UPDATE;

    IF FOUND THEN
        UPDATE public.activation_devices
        SET
            last_seen_at = now(),
            client_os = p_client_os
        WHERE id = v_device.id;
    ELSE
        SELECT count(*)
        INTO v_device_count
        FROM public.activation_devices
        WHERE activation_key_id = v_key.id;

        IF v_device_count >= v_key.max_activations THEN
            RETURN jsonb_build_object(
                'success', false,
                'message', 'device_limit'
            );
        END IF;

        INSERT INTO public.activation_devices (
            activation_key_id,
            hardware_fingerprint,
            client_os
        )
        VALUES (
            v_key.id,
            p_hardware_fingerprint,
            p_client_os
        );
    END IF;

    RETURN jsonb_build_object(
        'success', true,
        'profile', v_profile.profile,
        'label', v_profile.label,
        'actions', v_profile.actions,
        'disclaimer', v_profile.disclaimer,
        'payload', jsonb_build_object(
            'system_prompt', v_profile.system_prompt
        )
    );
END;
$$;


-- ============================================================
-- 9. PERMISSIONS
-- ============================================================

-- Remove public access to sensitive tables.
ALTER TABLE public.activation_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.activation_devices ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.activation_profiles ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.quantization_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.models ENABLE ROW LEVEL SECURITY;


-- Public/anonymous users must not directly read or modify license records.
REVOKE ALL ON public.activation_keys FROM PUBLIC, anon, authenticated;
REVOKE ALL ON public.activation_devices FROM PUBLIC, anon, authenticated;


-- Profile information is returned only through the controlled RPC.
REVOKE ALL ON public.activation_profiles FROM PUBLIC, anon, authenticated;


-- Do not allow browser clients to write queue jobs directly.
REVOKE ALL ON public.quantization_jobs FROM PUBLIC, anon, authenticated;


-- Public catalogue reads may be enabled later using a carefully scoped
-- SELECT policy. Do not expose the raw worker queue as a public catalogue.
REVOKE ALL ON public.models FROM PUBLIC, anon, authenticated;


-- The client must be able to invoke the activation function, but cannot
-- directly access the tables above.
REVOKE ALL ON FUNCTION public.verify_client_activation(text, text, text)
FROM PUBLIC;

GRANT EXECUTE ON FUNCTION public.verify_client_activation(text, text, text)
TO anon, authenticated, service_role;


-- Model normalization is safe to expose as a pure validation function.
GRANT EXECUTE ON FUNCTION public.normalize_hf_model(text)
TO anon, authenticated, service_role;


COMMIT;


-- ============================================================
-- END OF SCHEMA V3
-- ============================================================