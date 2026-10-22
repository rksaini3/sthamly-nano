import { NextRequest, NextResponse } from 'next/server'
import { PDFDocument, rgb, StandardFonts, type PDFFont, type PDFPage } from 'pdf-lib'
import { createClient } from '@supabase/supabase-js'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

// SERVER ONLY. Service-role key. Never import this file from a client component.
const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || ''
const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY || ''
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

type Pair = [number, number]
type Row = { domain: string; variant: string; kld: Pair; rms: Pair | null; top: Pair | null }

const PAGE_W = 595.28 // A4
const PAGE_H = 841.89
const MARGIN = 50
const BOTTOM = 60
const INK = rgb(0.1, 0.1, 0.15)
const COLS = [MARGIN, 165, 255, 365, 455]

function json(status: number, body: Record<string, string>) {
  return NextResponse.json(body, { status })
}

function isNum(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v)
}

function pair(v: unknown): Pair | null {
  if (!Array.isArray(v) || v.length !== 2 || !isNum(v[0]) || !isNum(v[1])) return null
  return [v[0], v[1]]
}

function fmt(p: Pair, digits = 4): string {
  return `${p[0].toFixed(digits)} +/- ${p[1].toFixed(digits)}`
}

/** WinAnsi font cannot draw Hindi or most Unicode. Replace those characters with '?'. */
function ascii(s: string): string {
  return s.replace(/[^\x20-\x7E]/g, '?')
}

function wrap(text: string, maxChars: number): string[] {
  const out: string[] = []
  let current = ''
  for (const word of ascii(text).split(/\s+/).filter(Boolean)) {
    if ((current + ' ' + word).trim().length > maxChars) {
      out.push(current)
      current = word
    } else {
      current = (current + ' ' + word).trim()
    }
  }
  if (current) out.push(current)
  return out
}

/** Only rows with a real KLD number. rms and top are shown as '-' when missing, never invented. */
function rowsOf(results: unknown): Row[] {
  const out: Row[] = []
  if (!results || typeof results !== 'object') return out
  for (const [domain, variants] of Object.entries(results as Record<string, unknown>)) {
    if (!variants || typeof variants !== 'object') continue
    for (const [variant, m] of Object.entries(variants as Record<string, any>)) {
      const kld = pair(m?.kld)
      if (!kld) continue
      out.push({ domain, variant, kld, rms: pair(m?.rms), top: pair(m?.top) })
    }
  }
  return out
}

class Writer {
  private doc: PDFDocument
  private regular: PDFFont
  private bold: PDFFont
  private page: PDFPage
  private y: number

  constructor(doc: PDFDocument, regular: PDFFont, bold: PDFFont) {
    this.doc = doc
    this.regular = regular
    this.bold = bold
    this.page = doc.addPage([PAGE_W, PAGE_H])
    this.y = PAGE_H - 60
  }

  private ensure(space: number): void {
    if (this.y - space < BOTTOM) {
      this.page = this.doc.addPage([PAGE_W, PAGE_H])
      this.y = PAGE_H - 60
    }
  }

  private draw(text: string, x: number, size: number, font: PDFFont): void {
    this.page.drawText(ascii(text), { x, y: this.y, size, font, color: INK })
  }

  title(text: string): void {
    this.ensure(40)
    this.draw(text, MARGIN, 18, this.bold)
    this.y -= 30
  }

  heading(text: string): void {
    this.y -= 6
    this.ensure(24)
    this.draw(text, MARGIN, 12, this.bold)
    this.y -= 18
  }

  line(text: string, size = 10, bold = false): void {
    this.ensure(size * 1.6 + 2)
    this.draw(text, MARGIN, size, bold ? this.bold : this.regular)
    this.y -= size * 1.6
  }

  paragraph(text: string): void {
    for (const part of wrap(text, 95)) this.line(part)
  }

  row(cells: string[], bold = false): void {
    this.ensure(16)
    cells.forEach((cell, i) => {
      this.page.drawText(ascii(cell), {
        x: COLS[i], y: this.y, size: 9.5,
        font: bold ? this.bold : this.regular, color: INK,
      })
    })
    this.y -= 16
  }
}

async function buildReport(job: { kind: string; hf_model: string; finished_at: string | null; result: any }): Promise<Uint8Array> {
  const doc = await PDFDocument.create()
  const regular = await doc.embedFont(StandardFonts.Helvetica)
  const bold = await doc.embedFont(StandardFonts.HelveticaBold)
  const w = new Writer(doc, regular, bold)
  const r = job.result ?? {}
  const calibrate = job.kind === 'calibrate'

  w.title(calibrate ? 'Sthamly NanoBrain - Hindi Calibration Report' : 'Sthamly NanoBrain - Quantization Quality Report')
  w.line(`Model: ${r.model ?? job.hf_model}`, 12, true)
  w.line(`License: ${r.license ?? 'unknown'}    Commit: ${String(r.commit_sha ?? '-').slice(0, 12)}`)
  w.line(`Calibration chunks: ${r.chunks ?? '-'}    Generated: ${r.generated_at ?? job.finished_at ?? '-'}`)

  w.heading('Measured against FP16 (KLD: lower is better; top-token: higher is better)')
  const rows = rowsOf(r.results)
  if (rows.length === 0) {
    w.line('Koi number nahi mila. Ye report adhoori hai.')
  } else {
    w.row(['Test set', 'Variant', 'KLD (mean +/- err)', 'RMS %', 'Top-token %'], true)
    for (const row of rows) {
      w.row([
        row.domain,
        row.variant,
        fmt(row.kld, 4),
        row.rms ? fmt(row.rms, 2) : '-',
        row.top ? fmt(row.top, 2) : '-',
      ])
    }
  }

  if (calibrate) {
    w.heading('Verdict')
    w.line(r.decision === 'improved'
      ? 'Hindi-calibrated quant plain Q4 se behtar nikla (kuch test sets par).'
      : 'Koi clear sudhar nahi mila. Plain Q4 hi rakhna sahi hai.')
    w.line(`Best variant: ${r.best ?? '-'}`)
    const reductions = Object.entries(r.reduction_pct ?? {}) as Array<[string, number]>
    for (const [domain, pct] of reductions) {
      w.line(`KLD reduction vs plain Q4 (${domain}): ${Number(pct).toFixed(2)}%`)
    }
    const clear: string[] = Array.isArray(r.clear_domains) ? r.clear_domains : []
    w.line(`Clear win on: ${clear.length ? clear.join(', ') : 'none'}`)
  }

  w.heading('What this means')
  w.paragraph(r.note || 'KLD vs FP16 on held-out text. A proxy, not task accuracy.')
  w.paragraph('Ye score task accuracy nahi hai. Is report ko kisi legal ya finance jawab ki sahi-galat ki guarantee na samjhein.')

  const warnings: string[] = Array.isArray(r.audit_warnings) ? r.audit_warnings : []
  if (warnings.length) {
    w.heading('Audit warnings')
    for (const warning of warnings) w.paragraph(`- ${warning}`)
  }

  return doc.save()
}

export async function GET(request: NextRequest) {
  const jobId = (new URL(request.url).searchParams.get('job') || '').trim()
  if (!UUID_RE.test(jobId)) return json(400, { error: 'job_required' })
  if (!supabaseUrl || !serviceKey) return json(503, { error: 'not_configured' })

  const sb = createClient(supabaseUrl, serviceKey, { auth: { persistSession: false, autoRefreshToken: false } })
  const { data, error } = await sb
    .from('jobs')
    .select('id, kind, hf_model, status, finished_at, result')
    .eq('id', jobId)
    .maybeSingle()

  if (error) return json(500, { error: 'lookup_failed' })
  if (!data) return json(404, { error: 'not_found' })
  if (data.status !== 'done') return json(409, { error: 'not_ready' })
  if (rowsOf(data.result?.results).length === 0) return json(404, { error: 'no_completed_audit' })

  const bytes = await buildReport(data)
  const safeName = String(data.hf_model).replace(/[^A-Za-z0-9._-]+/g, '_')

  return new NextResponse(Buffer.from(bytes), {
    status: 200,
    headers: {
      'Content-Type': 'application/pdf',
      'Content-Disposition': `inline; filename="sthamly-${safeName}.pdf"`,
      'Cache-Control': 'no-store',
    },
  })
}