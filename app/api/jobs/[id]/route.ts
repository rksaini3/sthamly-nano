import { NextRequest, NextResponse } from 'next/server'
import { createClient } from '@supabase/supabase-js'
import { isUuid } from '@/lib/jobs'

export const dynamic = 'force-dynamic'

// Uses the ANON key on purpose: get_job() is public-safe (it omits requester_hash).
export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  if (!isUuid(id)) {
    return NextResponse.json({ error: 'bad_id' }, { status: 400 })
  }

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL
  const anon = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY
  if (!url || !anon) {
    return NextResponse.json({ error: 'not_configured' }, { status: 503 })
  }

  const sb = createClient(url, anon, { auth: { persistSession: false, autoRefreshToken: false } })
  const { data, error } = await sb.rpc('get_job', { job_id: id })
  if (error) {
    return NextResponse.json({ error: 'lookup_failed' }, { status: 500 })
  }
  if (!data) {
    return NextResponse.json({ error: 'not_found' }, { status: 404 })
  }
  return NextResponse.json(data, { status: 200 })
}