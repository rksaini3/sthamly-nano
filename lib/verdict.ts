import type { Metric, Model, Results, VariantResult } from '@/lib/types'

// Same rule as scripts/trl4_bench.py: a difference only counts as "clear" when it is
// more than 2x its combined error. Anything smaller is a hint at best.
export type Verdict = 'clear' | 'hint' | 'none'
type Key = 'kld' | 'top'

export const VARIANT_ORDER = ['standard', 'hindi60', 'english60', 'mixed60'] as const

export const VARIANT_LABELS: Record<string, string> = {
  standard: 'Plain Q4',
  hindi60: 'Hindi imatrix',
  english60: 'English imatrix',
  mixed60: 'Mixed imatrix',
  plain: 'Plain Q4', // label used by scripts/audit.py runs (scripts/worker.py)
}

export const VERDICT_TEXT: Record<Verdict, string> = {
  clear: 'saaf fark',
  hint: 'ishara, saaf nahi',
  none: 'fark nahi',
}

/** Human label for a variant key; audit.py labels (e.g. "bartowski-Q4_K_M") are shown as they are. */
export function variantLabel(key: string): string {
  return VARIANT_LABELS[key] ?? key
}

/** Known variants first (in a fixed order), then any custom ones in their stored order. */
export function orderedVariants(byVariant: Record<string, unknown>): string[] {
  const keys = Object.keys(byVariant)
  const known = VARIANT_ORDER.filter((v) => keys.includes(v)) as string[]
  const other = keys.filter((k) => !(VARIANT_ORDER as readonly string[]).includes(k))
  return [...known, ...other]
}

/** How much better `a` is than `b` (positive = a better), its error, and the verdict. */
export function compare(a: VariantResult, b: VariantResult, key: Key = 'kld') {
  const lowIsBetter = key === 'kld'
  const gain = lowIsBetter ? b[key][0] - a[key][0] : a[key][0] - b[key][0]
  const err = Math.sqrt(a[key][1] ** 2 + b[key][1] ** 2)
  const verdict: Verdict = gain > 2 * err ? 'clear' : gain > 0 ? 'hint' : 'none'
  return { gain, err, verdict }
}

/** The comparisons shown on the benchmark page: [a, b] means "a vs b". */
export const PAIRS: ReadonlyArray<readonly [string, string]> = [
  ['hindi60', 'standard'],
  ['english60', 'standard'],
  ['hindi60', 'english60'],
  ['mixed60', 'english60'],
]

/** Relative KLD reduction of `variant` vs plain Q4, per domain, in percent (omitted if not measured). */
export function kldReductionPct(results: Results | null, variant = 'hindi60'): Record<string, number> {
  const out: Record<string, number> = {}
  for (const [domain, byVariant] of Object.entries(results ?? {})) {
    const v = byVariant[variant]
    const s = byVariant.standard
    if (v && s && s.kld[0] > 0) out[domain] = 100 * (1 - v.kld[0] / s.kld[0])
  }
  return out
}

export interface LeaderboardRow {
  variant: string
  label: string
  isBest: boolean
  kld: number // mean over test sets (lower = better)
  kldErr: number
  top: number // mean same-top-p % over test sets (higher = better)
  perDomain: Record<string, Metric>
  sizeGb: number | null
  /** number of test sets where the best row is CLEARLY better than this row */
  behindBest: number
  domains: number
}

/**
 * One table per model, ranked by mean KLD over its test sets. Only variants measured on
 * every test set are ranked. Ranks are only meaningful within one model (the same FP16
 * reference), so this never mixes different base models.
 */
export function leaderboardRows(model: Pick<Model, 'results' | 'variants_meta'>): LeaderboardRow[] {
  const results = model.results
  if (!results) return []
  const domains = Object.keys(results)
  if (domains.length === 0) return []

  const variants = orderedVariants(results[domains[0]] ?? {}).filter((v) => domains.every((d) => results[d]?.[v]))
  const n = domains.length
  const rows: LeaderboardRow[] = variants.map((v) => ({
    variant: v,
    label: variantLabel(v),
    isBest: false,
    kld: domains.reduce((s, d) => s + results[d][v].kld[0], 0) / n,
    kldErr: Math.sqrt(domains.reduce((s, d) => s + results[d][v].kld[1] ** 2, 0)) / n,
    top: domains.reduce((s, d) => s + results[d][v].top[0], 0) / n,
    perDomain: Object.fromEntries(domains.map((d) => [d, results[d][v].kld])),
    sizeGb: model.variants_meta?.[v]?.size_gb ?? null,
    behindBest: 0,
    domains: n,
  }))
  rows.sort((a, b) => a.kld - b.kld)
  if (rows.length === 0) return rows

  const best = rows[0]
  best.isBest = true
  for (const r of rows.slice(1)) {
    r.behindBest = domains.filter((d) => compare(results[d][best.variant], results[d][r.variant], 'kld').verdict === 'clear').length
  }
  return rows
}
