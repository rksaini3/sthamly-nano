import { createClient, type SupabaseClient } from '@supabase/supabase-js'
import type { Model } from '@/lib/types'

// Public client: uses the anon key, so it is safe in the browser. 
// It can only SELECT from `quantization_jobs` and manage basic client handshakes.
// The service-role key is never used here and must never be put in a NEXT_PUBLIC_ variable.
let client: SupabaseClient | null = null

export const NOT_CONFIGURED =
  'Supabase configure nahi hai (NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY).'

export function getClient(): SupabaseClient | null {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY
  if (!url || !key) return null
  client ??= createClient(url, key)
  return client
}

/** Never throws: pages show `error` instead of crashing (or silently showing "no models"). */
export async function getModels(): Promise<{ models: Model[]; error: string | null }> {
  const sb = getClient()
  if (!sb) return { models: [], error: NOT_CONFIGURED }
  
  // --- FIXED TABLE NAME AND SCHEMATIC CONNECTION MATRIX ---
  // Maps the historical telemetry data points right off our active quantization_jobs registry
  const { data, error } = await sb
    .from('quantization_jobs') // Corrected matching the DB infrastructure specs
    .select('*')
    .order('created_at', { ascending: false })
    .returns<Model[]>()
    
  if (error) {
    return { models: [], error: `Supabase se data nahi aaya: ${error.message}` }
  }
  return { models: data ?? [], error: null }
}
