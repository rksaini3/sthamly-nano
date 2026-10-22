import { isUuid, normalizeModelId, type JobData } from '@/lib/jobs'

export type CreateResult = { ok: true; id: string; deduped: boolean } | { ok: false; code: string }
export type FetchResult = { ok: true; job: JobData | null } | { ok: false; message: string }

/** Free audit: POST /api/models. The server validates the model and rate-limits. */
export async function createAuditJob(input: string): Promise<CreateResult> {
  const model = normalizeModelId(input)
  if (!model) return { ok: false, code: 'invalid_model' }

  try {
    const response = await fetch('/api/models', {


      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ hf_model_identifier: model }),
    })
    // A proxy or crash can return HTML instead of JSON. That must not look like a network error.
    const data = await response.json().catch(() => null)
    if (!response.ok) {
      return { ok: false, code: typeof data?.error === 'string' ? data.error : 'failed' }
    }
    if (typeof data?.id !== 'string') return { ok: false, code: 'bad_response' }
    return { ok: true, id: data.id, deduped: Boolean(data.deduped) }
  } catch {
    return { ok: false, code: 'network_error' }
  }
}

/** Live job state: GET /api/jobs/[id] (get_job RPC, anon key, no requester hash). */
export async function fetchJob(id: string): Promise<FetchResult> {
  if (!isUuid(id)) return { ok: false, message: 'Job ID galat hai.' }
  try {
    const response = await fetch(`/api/jobs/${id}`, { cache: 'no-store' })
    if (response.status === 404) return { ok: true, job: null }
    if (!response.ok) return { ok: false, message: 'Data fetch karne mein dikkat hai.' }
    const job = (await response.json()) as JobData
    if (!job || typeof job.status !== 'string') return { ok: false, message: 'Server ka jawab samajh nahi aaya.' }
    return { ok: true, job }
  } catch {
    return { ok: false, message: 'Network connection bahut hua hai.' }
  }
}