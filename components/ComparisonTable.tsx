import type { Model } from '@/lib/types'
import { PAIRS, VERDICT_TEXT, compare, orderedVariants, variantLabel, type Verdict } from '@/lib/verdict'
import { domainName } from '@/lib/labels'

const VERDICT_CLASS: Record<Verdict, string> = {
  clear: 'text-good',
  hint: 'text-warn',
  none: 'text-muted',
}
const VERDICT_MARK: Record<Verdict, string> = { clear: '✓', hint: '~', none: '–' }

const fmt = (m: any, digits: number) => {
  if (!m) return '—'
  const val = Array.isArray(m) ? m[0] : (m.kld ? m.kld[0] : 0)
  const err = Array.isArray(m) ? m[1] : (m.kld ? m.kld[1] : 0)
  return `${Number(val).toFixed(digits)} ± ${Number(err).toFixed(digits)}`
}

export default function ComparisonTable({ models }: { models: Model[] | null }) {
  const measured = (models ?? []).filter(
    (m) => m.results && Object.values(m.results).some((d) => Object.keys(d).length > 1)
  )

  if (measured.length === 0) {
    return (
      <div className="rounded-md border border-line bg-surface p-6">
        <p className="font-medium">Abhi koi measured result nahi hai</p>
      </div>
    )
  }

  return (
    <div className="space-y-16">
      {measured.map((m) => {
        const results = m.results!
        const domains = Object.keys(results)
        const pairs = PAIRS.filter(([a, b]) => domains.some((d) => results[d][a] && results[d][b]))
        
        // SAFE FALLBACK: Cast as any to bypass static TS2339 structure limits cleanly
        const displayModelName = (m as any).hf_model_identifier || (m as any).model_name || "Unknown Model";

        return (
          <article key={m.id} aria-labelledby={`b-${m.id}`}>
            <h2 id={`b-${m.id}`} className="font-display text-2xl font-semibold mb-6">
              {displayModelName}
            </h2>

            {pairs.length > 0 && (
              <section className="mb-10">
                <h3 className="font-display font-semibold text-lg mb-1">Kaun kisse behtar</h3>
                <div className="overflow-x-auto rounded-lg border border-line bg-surface">
                  <table className="w-full text-sm">
                    <thead className="text-left text-muted border-b border-line">
                      <tr>
                        <th scope="col" className="px-3 sm:px-4 py-3 font-medium">Muqabla</th>
                        {domains.map((d) => (
                          <th key={d} scope="col" className="px-3 sm:px-4 py-3 font-medium">{domainName(d)}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                      {pairs.map(([a, b]) => (
                        <tr key={`${a}-${b}`}>
                          <th scope="row" className="px-3 sm:px-4 py-3 text-left font-medium">
                            {variantLabel(a)} <span className="text-muted font-normal">vs</span> {variantLabel(b)}
                          </th>
                          {domains.map((d) => {
                            const ra = results[d][a]
                            const rb = results[d][b]
                            if (!ra || !rb) return <td key={d} className="px-3 sm:px-4 py-3 text-muted">—</td>
                            const c = compare(ra, rb, 'kld')
                            return (
                              <td key={d} className={`px-3 sm:px-4 py-3 ${VERDICT_CLASS[c.verdict]}`}>
                                <span aria-hidden>{VERDICT_MARK[c.verdict]} </span>
                                {VERDICT_TEXT[c.verdict]}
                                <span className="block text-xs text-muted">
                                  {c.gain >= 0 ? '−' : '+'}
                                  {Math.abs(c.gain).toFixed(4)} KLD
                                </span>
                              </td>
                            )
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}

            <section>
              <h3 className="font-display font-semibold text-lg mb-3">Poore numbers</h3>
              <div className="overflow-x-auto rounded-lg border border-line bg-surface">
                <table className="w-full text-sm">
                  <thead className="text-left text-muted border-b border-line">
                    <tr>
                      <th scope="col" className="px-3 sm:px-4 py-3 font-medium">Test set</th>
                      <th scope="col" className="px-3 sm:px-4 py-3 font-medium">Variant</th>
                      <th scope="col" className="px-3 sm:px-4 py-3 font-medium whitespace-nowrap">KLD (kam = behtar)</th>
                      <th scope="col" className="px-3 sm:px-4 py-3 font-medium whitespace-nowrap">Same top-p % (zyada = behtar)</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {domains.flatMap((d) =>
                      orderedVariants(results[d]).map((v) => (
                        <tr key={`${d}-${v}`}>
                          <td className="px-3 sm:px-4 py-2.5 text-muted">{domainName(d)}</td>
                          <td className="px-3 sm:px-4 py-2.5 font-medium">{variantLabel(v)}</td>
                          <td className="px-3 sm:px-4 py-2.5 whitespace-nowrap">{fmt(results[d][v].kld, 4)}</td>
                          <td className="px-3 sm:px-4 py-2.5 whitespace-nowrap">{fmt(results[d][v].top, 2)}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </section>
          </article>
        )
      })}
    </div>
  )
}
