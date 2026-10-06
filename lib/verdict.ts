import type { Results, VariantResult } from '@/lib/types'

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
}

export const VERDICT_TEXT: Record<Verdict, string> = {
  clear: 'saaf fark',
  hint: 'ishara, saaf nahi',
  none: 'fark nahi',
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