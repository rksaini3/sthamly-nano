import { createClient, type SupabaseClient } from '@supabase/supabase-js'
import type { Model } from '@/lib/types'

// Public, read-only client: uses the anon key, so it is safe in the browser.
// Writes are blocked by RLS (see db/schema.sql). The service-role key is never used here.
let client: SupabaseClient | null = null

function getClient(): SupabaseClient | null {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY
  if (!url || !key) return null
  client ??= createClient(url, key)
  return client
}

/** Never throws: pages show `error` instead of crashing (or silently showing "no models"). */
export async function getModels(): Promise<{ models: Model[]; error: string | null }> {
  const sb = getClient()
  if (!sb) {
    return {
      models: [],
      error: 'Supabase configure nahi hai (NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY).',
    }
  }
  const { data, error } = await sb
    .from('models')
    .select('*')
    .order('created_at', { ascending: false })
    .returns<Model[]>()
  if (error) return { models: [], error: `Supabase se data nahi aaya: ${error.message}` }
  return { models: data ?? [], error: null }
}