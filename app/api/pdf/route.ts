import { NextRequest, NextResponse } from 'next/server'
import { PDFDocument, rgb, StandardFonts, type PDFFont, type PDFPage } from 'pdf-lib'
import { createClient } from '@supabase/supabase-js'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

// SERVER ONLY. Service-role key. Never import this file from a client component.
const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || ''
const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY || ''

type Pair = [number, number]
type Variant = { kld: Pair; rms: Pair; top: Pair }

const PAGE_W = 595.28 // A4
const PAGE_H = 841.89
const MARGIN = 50
const BOTTOM = 60
const INK = rgb(0.1, 0.1, 0.15)
const COLS = [MARGIN, 230, 330, 430]

function json(status: number, body: Record<string, string>) {
  return NextResponse.json(body, { status })
}

/** LIKE pattern ke special characters ko escape karo, taaki model name ka '_' wildcard na bane. */
function escapeLike(value: string): string {
  return value.replace(/[\\%_]/g, (c) => `\\${c}`)
}

function isNum(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v)
}

function pair(v: unknown): Pair | null {
  if (!Array.isArray(v) || v.length !== 2 || !isNum(v[0]) || !isNum(v[1])) return null
  return [v[0], v[1]]
}

/** Sirf poora, valid variant. Adhoora data par report nahi banti. */
function variant(v: any): Variant | null {
  const kld = pair(v?.kld)
  const rms = pair(v?.rms)
  const top = pair(v?.top)
  return kld && rms && top ? { kld, rms, top } : null
}

function fmt(p: Pair, digits = 4): string {
  return `${p[0].toFixed(digits)} +/- ${p[1].toFixed(digits)}`
}

/** WinAnsi font Hindi ya bahut se Unicode characters nahi dikha sakta. Unhe '?' bana do. */
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
    this.gap(6)
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
        x: COLS[i], y: this.y, size: 10,
        font: bold ? this.bold : this.regular, color: INK,
      })
    })
    this.y -= 16
  }

  gap(px = 8): void {
    this.y -= px
  }
}

async function buildReport(
  row: { hf_model: string; finished_at: string | null; result: any },
  std: Variant,
  h60: Variant,
): Promise<Uint8Array> {
  const doc = await PDFDocument.create()
  const regular = await doc.embedFont(StandardFonts.Helvetica)
  const bold = await doc.embedFont(StandardFonts.HelveticaBold)
  const w = new Writer(doc, regular, bold)
  const r = row.result ?? {}

  w.title('Sthamly NanoBrain - Quantization Quality Report')
  w.line(`Model: ${r.model ?? row.hf_model}`, 12, true)
  w.line(`License: ${r.license ?? 'unknown'}    Commit: ${String(r.commit_sha ?? '-').slice(0, 12)}`)
  w.line(`Calibration chunks: ${r.chunks ?? '-'}    Hindi test lines: ${r.domains?.hindi ?? '-'}`)
  w.line(`Generated: ${r.generated_at ?? row.finished_at ?? '-'}`)

  w.heading('Measured on held-out Hindi text (KLD vs FP16, lower is better)')
  w.row(['Variant', 'KLD (mean +/- err)', 'RMS %', 'Same top-token %'], true)
  w.row(['Plain Q4_K_M', fmt(std.kld), fmt(std.rms, 2), fmt(std.top, 2)])
  w.row(['Hindi-calibrated Q4_K_M', fmt(h60.kld), fmt(h60.rms, 2), fmt(h60.top, 2)])

  w.heading('Verdict')
  if (r.decision === 'improved') {
    w.line('Hindi-calibrated quant plain Q4 se behtar nikla (sabhi test sets par).')
  } else {
    w.line('Koi clear sudhar nahi mila. Plain Q4 hi rakhna sahi hai.')
  }
  w.line(`Best variant: ${r.best ?? '-'}`)
  const reductions = Object.entries(r.reduction_pct ?? {}) as Array<[string, number]>
  for (const [domain, pct] of reductions) {
    w.line(`KLD reduction vs plain Q4 (${domain}): ${Number(pct).toFixed(2)}%`)
  }
  const clear: string[] = Array.isArray(r.clear_domains) ? r.clear_domains : []
  w.line(`Clear win on: ${clear.length ? clear.join(', ') : 'none'}`)

  w.heading('What this means')
  w.paragraph(r.note ?? 'KLD vs FP16 on held-out text. A proxy, not task accuracy.')
  w.paragraph('Ye score task accuracy nahi hai. Is report ko kisi legal ya finance jawab ki sahi-galat ki guarantee na samjhein.')

  const warnings: string[] = Array.isArray(r.audit_warnings) ? r.audit_warnings : []
  if (warnings.length) {
    w.heading('Audit warnings')
    for (const warning of warnings) w.paragraph(`- ${warning}`)
  }

  return doc.save()
}

export async function GET(request: NextRequest) {
  const modelName = (new URL(request.url).searchParams.get('model') || '').trim()
  if (!modelName || modelName.length > 200) return json(400, { error: 'model_required' })
  if (!supabaseUrl || !serviceKey) return json(503, { error: 'not_configured' })

  const sb = createClient(supabaseUrl, serviceKey, { auth: { persistSession: false, autoRefreshToken: false } })
  const { data, error } = await sb
    .from('jobs')
    .select('hf_model, finished_at, result')
    .ilike('hf_model', escapeLike(modelName))
    .eq('status', 'done')
    .eq('kind', 'calibrate')
    .order('finished_at', { ascending: false })
    .limit(1)
    .maybeSingle()

  if (error) return json(500, { error: 'lookup_failed' })

  // No fallback numbers. If there is no complete calibrate audit, no PDF.
  const hindi = data?.result?.results?.hindi
  const std = variant(hindi?.standard)
  const h60 = variant(hindi?.hindi60)
  if (!data || !std || !h60) return json(404, { error: 'no_completed_audit' })

  const bytes = await buildReport(data, std, h60)
  const safeName = data.hf_model.replace(/[^A-Za-z0-9._-]+/g, '_')

  return new NextResponse(Buffer.from(bytes), {
    status: 200,
    headers: {
      'Content-Type': 'application/pdf',
      'Content-Disposition': `inline; filename="sthamly-${safeName}.pdf"`,
      'Cache-Control': 'no-store',
    },
  })
}