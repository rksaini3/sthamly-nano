import type { Model, Results, VariantResult, VariantsMeta } from '@/lib/types'

// Same rule as scripts/trl4_bench.py and scripts/worker.py: a difference only counts as "clear"
// when it is more than 2x its combined error. Anything smaller is a hint at best.
export type Verdict = 'clear' | 'hint' | 'none'
type Key = 'kld' | 'top'

export const VARIANT_ORDER = ['standard', 'hindi60', 'english60', 'mixed60'] as const

export const VARIANT_LABELS: Record<string, string> = {
  standard: 'Plain Q4',
  hindi60: 'Hindi imatrix',
  english60: 'English imatrix',
  mixed60: 'Mixed imatrix',
  plain: 'Plain Q4',
}

export const VERDICT_TEXT: Record<Verdict, string> = {
  clear: 'saaf fark',
  hint: 'ishara, saaf nahi',
  none: 'fark nahi',
}

/** Display name of a variant: custom label (audit uploads) > known label > the raw key. */
export function variantLabel(variant: string, meta?: VariantsMeta | null): string {
  return meta?.[variant]?.label ?? VARIANT_LABELS[variant] ?? variant
}

/** Variant keys of one test set: the known ones in their fixed order, then any others A-Z. */
export function orderedVariants(byVariant: Record<string, VariantResult>): string[] {
  const known = (VARIANT_ORDER as readonly string[]).filter((v) => byVariant[v])
  const rest = Object.keys(byVariant)
    .filter((v) => !(VARIANT_ORDER as readonly string[]).includes(v))
    .sort()
  return [...known, ...rest]
}

/** How much better `a` is than `b` (positive = a better), its error, and the verdict. */
export function compare(a: VariantResult, b: VariantResult, key: Key = 'kld') {
  const lowIsBetter = key === 'kld'
  const gain = lowIsBetter ? b[key][0] - a[key][0] : a[key][0] - b[key][0]
  const err = Math.sqrt(a[key][1] ** 2 + b[key][1] ** 2)
  const verdict: Verdict = gain > 2 * err ? 'clear' : gain > 0 ? 'hint' : 'none'
  return { gain, err, verdict }
}

/** The comparisons shown on the site: [a, b] means "a vs b". */
export const PAIRS: ReadonlyArray<readonly [string, string]> = [
  ['hindi60', 'standard'],
  ['english60', 'standard'],
  ['hindi60', 'english60'],
  ['mixed60', 'english60'],
]

/** Relative KLD reduction of `variant` vs plain Q4, per domain, in percent (null if not measured). */
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
  sizeGb: number | null
  /** mean KLD over the test sets */
  kld: number
  /** error of that mean (combined error of the per-set errors, divided by the number of sets) */
  kldErr: number
  isBest: boolean
  /** number of test sets on which the best row is clearly better than this row */
  behindBest: number
  /** number of test sets the row was measured on */
  domains: number
}

/**
 * One row per quant that was measured on EVERY test set, best (lowest mean KLD) first.
 * Rank is only meaningful inside one model: every row shares that model's FP16 reference.
 */
export function leaderboardRows(model: Pick<Model, 'results' | 'variants_meta'>): LeaderboardRow[] {
  const results = model.results
  if (!results) return []
  const domains = Object.keys(results)
  if (domains.length === 0) return []
  const common = Object.keys(results[domains[0]]).filter((v) => domains.every((d) => results[d][v]))

  const rows: LeaderboardRow[] = common.map((variant) => {
    const k = domains.map((d) => results[d][variant].kld)
    return {
      variant,
      label: variantLabel(variant, model.variants_meta),
      sizeGb: model.variants_meta?.[variant]?.size_gb ?? null,
      kld: k.reduce((s, x) => s + x[0], 0) / k.length,
      kldErr: Math.sqrt(k.reduce((s, x) => s + x[1] ** 2, 0)) / k.length,
      isBest: false,
      behindBest: 0,
      domains: domains.length,
    }
  })
  rows.sort((a, b) => a.kld - b.kld || a.variant.localeCompare(b.variant))

  const best = rows[0]
  if (best) {
    for (const r of rows) {
      r.isBest = r === best
      r.behindBest =
        r === best
          ? 0
          : domains.filter((d) => compare(results[d][best.variant], results[d][r.variant]).verdict === 'clear').length
    }
  }
  return rows
}
