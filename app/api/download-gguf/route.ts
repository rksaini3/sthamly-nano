import { NextRequest, NextResponse } from 'next/server'
import { createClient } from '@supabase/supabase-js'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

// SERVER ONLY. Service-role key. The GGUF file never passes through this route:
// we check the job, then hand out a short-lived signed link to the private storage object.
const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || ''
const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY || ''
const LINK_SECONDS = 300
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function json(status: number, body: Record<string, string>) {
  return NextResponse.json(body, { status })
}

export async function GET(request: NextRequest) {
  const jobId = (new URL(request.url).searchParams.get('job') || '').trim()
  if (!UUID_RE.test(jobId)) return json(400, { error: 'job_required' })
  if (!supabaseUrl || !serviceKey) return json(503, { error: 'not_configured' })

  const sb = createClient(supabaseUrl, serviceKey, { auth: { persistSession: false, autoRefreshToken: false } })
  const { data, error } = await sb
    .from('jobs')
    .select('hf_model, status, result')
    .eq('id', jobId)
    .maybeSingle()

  if (error) return json(500, { error: 'lookup_failed' })
  if (!data) return json(404, { error: 'not_found' })
  if (data.status !== 'done') return json(409, { error: 'not_ready' })

  // Paths are written by scripts/worker.py into result.artifacts.
  const artifacts = data.result?.artifacts ?? {}
  const bucket = typeof artifacts.bucket === 'string' ? artifacts.bucket : ''
  const path = typeof artifacts.quantized_standard === 'string' ? artifacts.quantized_standard : ''
  if (!bucket || !path) return json(404, { error: 'file_missing' })

  const filename = `${String(data.hf_model).replace(/[^A-Za-z0-9._-]+/g, '_')}_Q4_K_M.gguf`
  const { data: signed, error: signError } = await sb.storage
    .from(bucket)
    .createSignedUrl(path, LINK_SECONDS, { download: filename })
  if (signError || !signed?.signedUrl) return json(502, { error: 'link_failed' })

  const res = NextResponse.redirect(signed.signedUrl, { status: 302 })
  res.headers.set('Cache-Control', 'no-store')
  return res
}