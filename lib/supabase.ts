import { createClient, type SupabaseClient } from '@supabase/supabase-js'
import type { Model } from '@/lib/types'

// Public client: uses the anon key, so it is safe in the browser. The anon role can only
// SELECT from `models` and call the three public RPC functions (db/schema.sql, db/schema_v3.sql).
// The service-role key is never used here and must never be put in a NEXT_PUBLIC_ variable.
let client: SupabaseClient | null = null

export function getClient(): SupabaseClient | null {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY
  if (!url || !key) return null
  client ??= createClient(url, key)
  return client
}

export const NOT_CONFIGURED =
  'Supabase configure nahi hai (NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY).'

/** Never throws: pages show `error` instead of crashing (or silently showing "no models"). */
export async function getModels(): Promise<{ models: Model[]; error: string | null }> {
  const sb = getClient()
  if (!sb) return { models: [], error: NOT_CONFIGURED }
  const { data, error } = await sb
    .from('models')
    .select('*')
    .order('created_at', { ascending: false })
    .returns<Model[]>()
  if (error) return { models: [], error: `Supabase se data nahi aaya: ${error.message}` }
  return { models: data ?? [], error: null }
}
