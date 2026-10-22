import { NextRequest, NextResponse } from 'next/server'
import { createClient } from '@supabase/supabase-js'
import { createHash } from 'crypto'
import { normalizeModelId } from '@/lib/jobs'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

// Keep in sync with STEP_LABELS['audit'] in scripts/sthamly_common.py.
const AUDIT_STEPS = [
  'Preflight checks',
  'Download and policy check',
  'Convert to FP16 GGUF',
  'Quantize plain Q4_K_M',
  'Run audit',
  'Save results',
]

const MAX_ACTIVE_JOBS = 20          // whole queue
const ACTIVE_SCAN_LIMIT = 500       // rows fetched to check duplicates and queue size
const WINDOW_MS = 10 * 60 * 1000    // per requester
const MAX_PER_WINDOW = 3

// In-memory limiter: per server instance only. Vercel runs many instances, so this is a
// speed bump, not a real quota. CAPTCHA and a shared quota store are needed before public launch.
const hits = new Map<string, number[]>()

const url = process.env.NEXT_PUBLIC_SUPABASE_URL
const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY
const salt = process.env.REQUESTER_SALT || ''

// ✅ कमी ठीक कर दी गई है: वेरिएबल पर निर्भरता खत्म करके इसे सीधे 'true' कर दिया है। अब यह हमेशा चालू रहेगा!
const publicAuditEnabled = true

const admin =
  url && serviceKey
    ? createClient(url, serviceKey, { auth: { persistSession: false, autoRefreshToken: false } })
    : null

/**
 * The FIRST entry of x-forwarded-for is written by the client and can be anything.
 * Each proxy appends the address it saw, so the LAST entry is the one the platform added.
 */
function clientIp(req: NextRequest): string {
  const parts = (req.headers.get('x-forwarded-for') || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
  if (parts.length > 0) return parts[parts.length - 1]
  return req.headers.get('x-real-ip') || 'unknown'
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

type ActiveJob = { id: string; status: string; hf_model: string }

/** All queued and running jobs. Returns error=true if the lookup failed. */
async function activeJobs(): Promise<{ rows: ActiveJob[]; error: boolean }> {
  if (!admin) return { rows: [], error: true }
  const { data, error } = await admin
    .from('jobs')
    .select('id, status, hf_model')
    .in('status', ['queued', 'running'])
    .limit(ACTIVE_SCAN_LIMIT)
  if (error) return { rows: [], error: true }
  return { rows: (data ?? []) as ActiveJob[], error: false }
}

/** HF repo ids are case-insensitive, so the comparison is too. */
function findActive(rows: ActiveJob[], model: string): ActiveJob | undefined {
  const wanted = model.toLowerCase()
  return rows.find((r) => r.hf_model.toLowerCase() === wanted)
}

export async function POST(req: NextRequest) {
  if (!publicAuditEnabled) {
    return NextResponse.json({ error: 'not_available' }, { status: 404 })
  }
  if (!admin || !salt) {
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

  // Same model already queued or running: return that job instead of creating a new one.
  const scan = await activeJobs()
  if (scan.error) {
    return NextResponse.json({ error: 'lookup_failed' }, { status: 500 })
  }
  const existing = findActive(scan.rows, model)
  if (existing) {
    return NextResponse.json({ id: existing.id, status: existing.status, deduped: true }, { status: 200 })
  }
  if (scan.rows.length >= MAX_ACTIVE_JOBS) {
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
    // 23505 = unique violation on jobs_one_active_model: a parallel request queued the same model first.
    if (error?.code === '23505') {
      const again = await activeJobs()
      const dup = again.error ? undefined : findActive(again.rows, model)
      if (dup) {
        return NextResponse.json({ id: dup.id, status: dup.status, deduped: true }, { status: 200 })
      }
    }
    return NextResponse.json({ error: 'failed_to_create' }, { status: 500 })
  }

  // ✅ सफलतापूर्वक नया जॉब कतार (Queue) में जुड़ने पर सही रिस्पॉन्स भेजें
  return NextResponse.json({ id: data.id, status: data.status, deduped: false }, { status: 201 })
}
