import { NextRequest, NextResponse } from 'next/server'
import { PDFDocument, rgb, StandardFonts } from 'pdf-lib'
import { createClient } from '@supabase/supabase-js'

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || ''
const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY || ''

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url)
  const modelName = (searchParams.get('model') || '').trim()
  if (!modelName) return NextResponse.json({ error: 'model_required' }, { status: 400 })
  if (!supabaseUrl || !serviceKey) return NextResponse.json({ error: 'not_configured' }, { status: 503 })

  // No fallback numbers. If there is no completed audit, the PDF is not made.
  const sb = createClient(supabaseUrl, serviceKey, { auth: { persistSession: false, autoRefreshToken: false } })
  const { data, error } = await sb
    .from('jobs')
    .select('result, finished_at')
    .eq('hf_model', modelName)
    .eq('status', 'done')
    .eq('kind', 'calibrate')
    .order('finished_at', { ascending: false })
    .limit(1)
    .maybeSingle()

  if (error) return NextResponse.json({ error: 'lookup_failed' }, { status: 500 })
  const kld = data?.result?.results?.hindi?.hindi60?.kld
  if (!Array.isArray(kld)) return NextResponse.json({ error: 'no_completed_audit' }, { status: 404 })

  const kldScore = Number(kld[0]).toFixed(4)
  const errorBar = Number(kld[1]).toFixed(4)

  const pdfDoc = await PDFDocument.create()
  // ... existing drawing code continues unchanged, using modelName, kldScore and errorBar ...