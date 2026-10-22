import { createClient } from '@supabase/supabase-js'
import type { Model, Results, VariantsMeta } from '@/lib/types'

// SERVER ONLY. Uses the service-role key. Import only from server components and route handlers.
export const NOT_CONFIGURED =
  'Supabase configure nahi hai (NEXT_PUBLIC_SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY).'

/** Completed calibrate jobs, shaped as the Model type that the leaderboard pages expect. */
export async function getModels(): Promise<{ models: Model[]; error: string | null }> {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY
  if (!url || !key) return { models: [], error: NOT_CONFIGURED }

  const sb = createClient(url, key, { auth: { persistSession: false, autoRefreshToken: false } })
  const { data, error } = await sb
    .from('jobs')
    .select('id, hf_model, kind, result, finished_at, created_at')
    .eq('status', 'done')
    .eq('kind', 'calibrate')
    .order('finished_at', { ascending: false })
    .limit(200)

  if (error) return { models: [], error: `Supabase se data nahi aaya: ${error.message}` }
  return { models: (data ?? []).map(toModel), error: null }
}

function toModel(row: any): Model {
  const r = row.result ?? {}
  const sizes = r.sizes_gb ?? {}
  const variantsMeta: VariantsMeta = {
    standard: { size_gb: sizes.quantized_standard ?? null, source: 'worker' },
    hindi60: { size_gb: sizes.quantized_hindi ?? null, source: 'worker' },
  }
  return {
    id: row.id,
    model_name: row.hf_model,
    hf_repo: row.hf_model,
    base_model: row.hf_model,
    quant_type: 'Q4_K_M',
    size_gb: sizes.quantized_hindi ?? null,
    fp16_size_gb: sizes.fp16 ?? null,
    download_url: null,
    results: (r.results ?? null) as Results | null,
    variants_meta: variantsMeta,
    notes: r.note ?? null,
    created_at: row.finished_at ?? row.created_at,
  }
}