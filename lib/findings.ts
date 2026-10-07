import type { Results } from '@/lib/types'
import { compare, variantLabel, type Verdict } from '@/lib/verdict'

/** One plain-language sentence about what the numbers do (and do not) show. */
export interface Finding {
  tone: Verdict // clear = proven, hint = partly, none = not shown
  lead: string
  text: string
}

const LEAD: Record<Verdict, string> = {
  clear: 'Saaf behtar',
  hint: 'Aadhe me saaf',
  none: 'Saaf fark nahi',
}

/**
 * "A vs B" as one sentence, using the same rule as everywhere else: a difference counts only
 * when it is more than 2x its combined error. Returns null if no test set has both quants.
 */
export function pairFinding(results: Results, a: string, b: string, noteIfNone = ''): Finding | null {
  const rows = Object.entries(results)
    .filter(([, byV]) => byV[a] && byV[b])
    .map(([domain, byV]) => ({
      domain,
      verdict: compare(byV[a], byV[b], 'kld').verdict,
      reduction: 100 * (1 - byV[a].kld[0] / byV[b].kld[0]),
    }))
  if (rows.length === 0) return null

  const A = variantLabel(a)
  const B = variantLabel(b)
  const clear = rows.filter((r) => r.verdict === 'clear')
  if (clear.length === rows.length) {
    const detail = rows.map((r) => `${r.domain} ${r.reduction.toFixed(0)}% kam`).join(', ')
    return { tone: 'clear', lead: LEAD.clear, text: `${A}, ${B} se behtar: KLD ${detail}.` }
  }
  if (clear.length > 0) {
    const yes = clear.map((r) => r.domain).join(', ')
    const no = rows.filter((r) => r.verdict !== 'clear').map((r) => r.domain).join(', ')
    return { tone: 'hint', lead: LEAD.hint, text: `${A}, ${B} se ${yes} par saaf behtar hai, ${no} par saaf nahi.` }
  }
  const tail = noteIfNone ? ` ${noteIfNone}` : ''
  return {
    tone: 'none',
    lead: LEAD.none,
    text: `${A} aur ${B} ke beech kisi test set me fark error se bada nahi.${tail}`,
  }
}

/** The headline findings for a model that has the imatrix experiment variants. */
export function findings(results: Results | null): Finding[] {
  if (!results) return []
  const out = [
    pairFinding(results, 'hindi60', 'standard'),
    pairFinding(results, 'english60', 'standard'),
    pairFinding(
      results,
      'hindi60',
      'english60',
      'Isliye hum "Hindi text se calibrate karna alag se behtar hai" nahi kehte.'
    ),
  ]
  return out.filter((f): f is Finding => f !== null)
}