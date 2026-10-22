import { NextRequest, NextResponse } from 'next/server'
import { createClient } from '@supabase/supabase-js'
import { createHash } from 'crypto'
import { normalizeModelId } from '@/lib/jobs'

export const runtime = 'nodejs'

// Keep in sync with AUDIT_STEPS in scripts/sthamly_common.py (STEP_LABELS['audit']).
const AUDIT_STEPS = [
  'Preflight checks',
  'Download and policy check',
  'Convert to FP16 GGUF',
  'Quantize plain Q4_K_M',
  'Run audit',
  'Save results',
]

const MAX_ACTIVE_JOBS = 20          // whole queue
const WINDOW_MS = 10 * 60 * 1000    // per requester
const MAX_PER_WINDOW = 3

// In-memory limiter. Per server instance only. For production use a shared store (e.g. Upstash).
const hits = new Map<string, number[]>()

const url = process.env.NEXT_PUBLIC_SUPABASE_URL
const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY
const salt = process.env.REQUESTER_SALT || ''

const admin =
  url && serviceKey
    ? createClient(url, serviceKey, { auth: { persistSession: false, autoRefreshToken: false } })
    : null

function clientIp(req: NextRequest): string {
  const forwarded = (req.headers.get('x-forwarded-for') || '').split(',')[0].trim()
  return forwarded || req.headers.get('x-real-ip') || 'unknown'
}

function rateLimited(key: string): boolean {
  const now = Date.now()
  const recent = (hits.get(key) || []).filter((t) => now - t < WINDOW_MS)
  if (recent.length >= MAX_PER_WINDOW) {
    hits.set(key, recent)
    return true
  }
  recent.push(now)
  hits.set(key, recent)
  return false
}

export async function POST(req: NextRequest) {
  if (!admin) {
    return NextResponse.json({ error: 'server_not_configured' }, { status: 503 })
  }

  let body: any
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'invalid_json' }, { status: 400 })
  }

  const model = normalizeModelId(String(body?.hf_model_identifier ?? body?.modelId ?? ''))
  if (!model) {
    return NextResponse.json({ error: 'invalid_model' }, { status: 400 })
  }

  // Pseudonymous requester id. The raw IP is never stored.
  const requester = createHash('sha256').update(`${salt}|${clientIp(req)}`).digest('hex')
  if (rateLimited(requester)) {
    return NextResponse.json({ error: 'rate_limited' }, { status: 429 })
  }

  // Same model already queued or running: return that job instead of a new one.
  const { data: active, error: activeError } = await admin
    .from('jobs')
    .select('id, status')
    .eq('hf_model', model)
    .in('status', ['queued', 'running'])
    .limit(1)
  if (activeError) {
    return NextResponse.json({ error: 'lookup_failed' }, { status: 500 })
  }
  if (active && active.length > 0) {
    return NextResponse.json({ id: active[0].id, status: active[0].status, deduped: true }, { status: 200 })
  }

  const { count, error: countError } = await admin
    .from('jobs')
    .select('id', { count: 'exact', head: true })
    .in('status', ['queued', 'running'])
  if (countError) {
    return NextResponse.json({ error: 'lookup_failed' }, { status: 500 })
  }
  if ((count ?? 0) >= MAX_ACTIVE_JOBS) {
    return NextResponse.json({ error: 'queue_full' }, { status: 503 })
  }

  const { data, error } = await admin
    .from('jobs')
    .insert({
      kind: 'audit',
      hf_model: model,
      requester_hash: requester,
      status: 'queued',
      step: 'Queued',
      progress: 0,
      steps: AUDIT_STEPS.map((label) => ({ label, state: 'pending' })),
    })
    .select('id, status')
    .single()

  if (error || !data) {
    return NextResponse.json({ error: 'db_insert_failed' }, { status: 500 })
  }
  return NextResponse.json({ id: data.id, status: data.status, deduped: false }, { status: 201 })
}