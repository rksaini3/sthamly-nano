import type { Results } from '@/lib/types'

// ---------------------------------------------------------------- model id
// Must behave exactly like public.normalize_hf_model() in db/schema_v3.sql and normalize_model_id() in
// scripts/sthamly_common.py. All three are checked against tests/fixtures/model_ids.json.
// If you change one, change all three.
const MODEL_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,95}\/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$/
const URL_PREFIX_RE = /^(https?:\/\/)?(www\.)?(huggingface\.co|hf\.co)\//i
const RESERVED_OWNERS = new Set(['datasets', 'spaces', 'papers', 'collections', 'docs', 'blog', 'api', 'organizations', 'models'])

/** "owner/name" from an id or a Hugging Face link, or null if it is not a valid model id. */
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

// ---------------------------------------------------------------- job data (shape of get_job() in db/schema_v3.sql)
export type JobStatus = 'queued' | 'running' | 'done' | 'failed' | 'refused'
export type StepState = 'pending' | 'running' | 'done' | 'failed'

export interface JobStep {
  label: string
  state: StepState
}

/** Free audit: plain Q4 against FP16. Written by scripts/worker.py. */
export interface AuditResult {
  kind: 'audit'
  model: string
  license: string | null
  weights_gb: number
  chunks: number
  /** test set -> number of lines used */
  domains: Record<string, number>
  fp16_gb: number | null
  quant_gb: number | null
  results: Results
  note: string
}

/** Paid calibration: Hindi / English / Mixed imatrix against plain Q4. */
export interface CalibrateResult {
  kind: 'calibrate'
  model: string
  license: string | null
  weights_gb: number
  results: Results
  decision: 'improved' | 'no_clear_gain'
  /** best variant by mean KLD (null when nothing could be compared) */
  best: string | null
  /** test sets on which the best variant is clearly better than plain Q4 */
  clear_domains: string[]
  /** KLD reduction versus plain Q4, per test set, in percent */
  reduction_pct: Record<string, number>
  output_gb?: number
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
  finished_at: string | null
}

export const isTerminal = (s: JobStatus): boolean => s === 'done' || s === 'failed' || s === 'refused'

// ---------------------------------------------------------------- messages (plain Hindi)
const CREATE_ERRORS: Record<string, string> = {
  invalid_model: 'Model ID sahi nahi hai. Aise likho: owner/model-name (jaise Qwen/Qwen2.5-1.5B-Instruct).',
  rate_limited: 'Aaj ke 3 free audit ho chuke. Kal dobara try karo.',
  queue_full: 'Abhi line bahut lambi hai. Thodi der baad dobara try karo.',
  not_available: 'Free public audit abhi band hai. Apna model Sthamly Desk par check karein.',
  server_not_configured: 'Server ki setup adhoori hai. Thodi der baad try karo ya admin ko batao.',
  lookup_failed: 'Queue abhi check nahi ho paayi. Thodi der baad dobara try karo.',
  failed_to_create: 'Job queue nahi ho payi. Thodi der baad dobara try karo.',
}

/** Message for an error code returned by create_audit_job (anything unknown gets a generic line). */
export function createErrorMessage(code: string): string {
  return CREATE_ERRORS[code] ?? 'Kuch gadbad hui. Thodi der baad dobara try karo.'
}

/**
 * Message for a job that did not finish. A refusal is a short, curated sentence from the worker, so it is shown.
 * A failure carries technical detail that only the operator needs, so visitors get a plain line instead.
 */
export function jobErrorMessage(status: JobStatus, error: string | null): string {
  if (status === 'refused') return error || 'Ye model abhi nahi liya ja sakta.'
  return 'Kaam beech me ruk gaya. Ho sakta hai ye model abhi support na ho, ya hamari taraf koi dikkat ho. Thodi der baad dobara try karo.'
}

/** "FP16 jaisa agla token ~87/100 baar": same-top-p percent rounded to a count out of 100. */
export const outOf100 = (topPercent: number): number => Math.round(topPercent)

/** Is the contact link a real http(s) or mailto link? (an env var could hold anything) */
export function safeLink(v: string | undefined): string | undefined {
  const s = (v ?? '').trim()
  return /^(https?:\/\/|mailto:)/i.test(s) ? s : undefined
}