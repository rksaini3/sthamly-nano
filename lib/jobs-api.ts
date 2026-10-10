import { getClient, NOT_CONFIGURED } from '@/lib/supabase'
import { isUuid, normalizeModelId, type JobData } from '@/lib/jobs'

export type CreateResult = { ok: true; id: string; deduped: boolean } | { ok: false; code: string }
export type FetchResult = { ok: true; job: JobData | null } | { ok: false; message: string }

/** Starts a free audit (RPC create_audit_job). `code` is invalid_model, rate_limited or queue_full. */
export async function createAuditJob(input: string): Promise<CreateResult> {
  const model = normalizeModelId(input)
  if (!model) return { ok: false, code: 'invalid_model' }
  const sb = getClient()
  if (!sb) return { ok: false, code: 'not_configured' }
  const { data, error } = await sb.rpc('create_audit_job', { p_model: model })
  if (error) return { ok: false, code: error.message }
  const row = data as { id?: string; deduped?: boolean } | null
  if (!row?.id) return { ok: false, code: 'bad_response' }
  return { ok: true, id: row.id, deduped: Boolean(row.deduped) }
}

/** Reads one job (RPC get_job). `job` is null if it does not exist or has expired. */
export async function fetchJob(id: string): Promise<FetchResult> {
  if (!isUuid(id)) return { ok: true, job: null }
  const sb = getClient()
  if (!sb) return { ok: false, message: NOT_CONFIGURED }
  const { data, error } = await sb.rpc('get_job', { p_id: id })
  if (error) return { ok: false, message: error.message }
  return { ok: true, job: (data as JobData | null) ?? null }
}
