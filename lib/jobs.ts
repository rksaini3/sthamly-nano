import type { Results, VariantsMeta } from '@/lib/types'

// ---------------------------------------------------------------- model id
// Must behave exactly like public.normalize_hf_model() in db/schema_v3.sql and like
// normalize_model_id() in scripts/sthamly_common.py. All three are checked against
// tests/fixtures/model_ids.json. If you change one, change all three.
const MODEL_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,95}\/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$/
const URL_PREFIX_RE = /^(https?:\/\/)?(www\.)?(huggingface\.co|hf\.co)\//i
const RESERVED_OWNERS = new Set([
  'datasets', 'spaces', 'papers', 'collections', 'docs', 'blog', 'api', 'organizations', 'models',
])

/** "owner/name" from an id or a Hugging Face URL, or null if it is not a valid model id. */
export function normalizeModelId(input: string): string | null {
  let v = input.replace(/^[ \t\r\n]+|[ \t\r\n]+$/g, '')
  v = v.replace(/[?#][\s\S]*$/, '')
  let hadUrl = false
  if (URL_PREFIX_RE.test(v)) {
    v = v.replace(URL_PREFIX_RE, '')
    hadUrl = true
  }
  v = v.replace(/\/+$/, '')
  if (hadUrl) v = /^([^/]+\/[^/]+)/.exec(v)?.[1] ?? ''
  if (!MODEL_ID_RE.test(v)) return null
  if (RESERVED_OWNERS.has(v.split('/')[0].toLowerCase())) return null
  return v
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
export const isUuid = (s: string): boolean => UUID_RE.test(s)

// ---------------------------------------------------------------- job data (shape of get_job())
export type JobStatus = 'queued' | 'running' | 'done' | 'failed' | 'refused'
export type StepState = 'pending' | 'running' | 'done' | 'failed'

export interface JobStep {
  id: string
  label: string
  state: StepState
}

export interface AuditResult {
  kind: 'audit'
  model: string
  chunks: number
  /** test set -> number of lines used */
  lines: Record<string, number>
  fp16_gb: number | null
  results: Results
  variants_meta: VariantsMeta
}

export interface CalibrateResult {
  kind: 'calibrate'
  model: string
  outcome: 'improved' | 'no_clear_gain'
  /** best imatrix variant by mean KLD (null if none could be compared) */
  best: string | null
  chunks: number
  lines: Record<string, number>
  results: Results
  variants_meta: VariantsMeta
}

export type JobResult = AuditResult | CalibrateResult

export interface JobData {
  id: string
  kind: 'audit' | 'calibrate'
  hf_model: string
  status: JobStatus
  step: string | null
  progress: number
  steps: JobStep[]
  result: JobResult | null
  error: string | null
  queue_position: number | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export const isTerminal = (s: JobStatus): boolean => s === 'done' || s === 'failed' || s === 'refused'

// ---------------------------------------------------------------- messages (Hindi, plain)
const CREATE_ERRORS: Record<string, string> = {
  invalid_model: 'Model ID sahi nahi hai. Aise likho: owner/model-name (jaise Qwen/Qwen2.5-1.5B-Instruct).',
  rate_limited: 'Aaj ke 3 free audit ho chuke. Kal dobara try karo.',
  queue_full: 'Abhi line bahut lambi hai. Thodi der baad dobara try karo.',
}

/** Message for an error code returned by create_audit_job (anything unknown gets a generic line). */
export function createErrorMessage(code: string): string {
  return CREATE_ERRORS[code] ?? 'Kuch gadbad hui. Thodi der baad dobara try karo.'
}

const REFUSALS: Record<string, string> = {
  model_not_found: 'Ye model Hugging Face par nahi mila. ID check karo.',
  gated_or_private: 'Ye model gated ya private hai. Abhi sirf public models ka audit hota hai.',
  no_safetensors: 'Is repo me safetensors weights nahi hain. Abhi sirf safetensors models chalte hain.',
  size_unknown: 'Model ka size pata nahi chala, isliye ise nahi liya.',
  too_big: 'Ye model bahut bada hai (limit lagbhag 4 GB weights). Chhota model try karo.',
  license_not_allowed: 'Is model ka license calibration ke liye permissive nahi hai.',
  needs_custom_code: 'Is model ko chalane ke liye custom code chahiye. Suraksha ke liye hum aisa code nahi chalate.',
}

/** Message for a refused or failed job (`error` is a refusal code, or a short worker message). */
export function jobErrorMessage(status: JobStatus, error: string | null): string {
  if (status === 'refused') return (error && REFUSALS[error]) || 'Ye model abhi nahi liya ja sakta.'
  if (error === 'worker_timeout') return 'Kaam bahut der chala aur ruk gaya. Thodi der baad dobara try karo.'
  return error ? `Kaam poora nahi hua: ${error}` : 'Kaam poora nahi hua.'
}

/** "FP16 jaisa agla token ~87/100 baar": same-top-p percent rounded to a count out of 100. */
export const outOf100 = (topPercent: number): number => Math.round(topPercent)
