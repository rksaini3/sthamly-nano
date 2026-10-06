import type { Model } from '@/lib/types'
import { PAIRS, VARIANT_LABELS, VARIANT_ORDER, VERDICT_TEXT, compare, type Verdict } from '@/lib/verdict'

const VERDICT_CLASS: Record<Verdict, string> = {
  clear: 'text-green-400',
  hint: 'text-amber-400',
  none: 'text-gray-500',
}

const fmt = (m: [number, number], digits: number) => `${m[0].toFixed(digits)} ± ${m[1].toFixed(digits)}`

export default function ComparisonTable({ models }: { models: Model[] | null }) {
  const measured = (models ?? []).filter(
    (m) => m.results && Object.values(m.results).some((d) => d.standard && Object.keys(d).length > 1)
  )

  if (measured.length === 0) {
    return (
      <div className="border border-gray-800 rounded-lg p-6 text-gray-500">
        Abhi koi measured result nahi hai. Number tabhi dikhte hain jab scripts/trl4_bench.py ka asli
        output publish ho. Koi number pehle se nahi dikhaya jaata.
      </div>
    )
  }

  return (
    <div className="space-y-10">
      {measured.map((m) => {
        const results = m.results!
        const domains = Object.keys(results)
        return (
          <div key={m.id}>
            <h3 className="text-lg font-semibold text-white mb-3">{m.model_name}</h3>

            <div className="overflow-x-auto border border-gray-800 rounded-lg">
              <table className="min-w-full divide-y divide-gray-800 text-sm">
                <thead className="bg-gray-900 text-gray-300">
                  <tr>
                    <th className="px-4 py-3 text-left">Test set</th>
                    <th className="px-4 py-3 text-left">Variant</th>
                    <th className="px-4 py-3 text-left">KLD vs FP16 (kam = behtar)</th>
                    <th className="px-4 py-3 text-left">Same top-p % (zyada = behtar)</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-800 text-gray-400">
                  {domains.flatMap((d) =>
                    VARIANT_ORDER.filter((v) => results[d][v]).map((v) => (
                      <tr key={`${d}-${v}`}>
                        <td className="px-4 py-2">{d}</td>
                        <td className="px-4 py-2 text-white">{VARIANT_LABELS[v]}</td>
                        <td className="px-4 py-2">{fmt(results[d][v].kld, 4)}</td>
                        <td className="px-4 py-2">{fmt(results[d][v].top, 2)}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

            <h4 className="text-sm font-semibold text-gray-300 mt-5 mb-2">
              Kaun kisse behtar (KLD, "saaf" = fark &gt; 2x error)
            </h4>
            <div className="overflow-x-auto border border-gray-800 rounded-lg">
              <table className="min-w-full divide-y divide-gray-800 text-sm">
                <thead className="bg-gray-900 text-gray-300">
                  <tr>
                    <th className="px-4 py-3 text-left">Muqabla</th>
                    {domains.map((d) => (
                      <th key={d} className="px-4 py-3 text-left">{d}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-800 text-gray-400">
                  {PAIRS.map(([a, b]) => (
                    <tr key={`${a}-${b}`}>
                      <td className="px-4 py-2 text-white">
                        {VARIANT_LABELS[a]} vs {VARIANT_LABELS[b]}
                      </td>
                      {domains.map((d) => {
                        const ra = results[d][a]
                        const rb = results[d][b]
                        if (!ra || !rb) return <td key={d} className="px-4 py-2 text-gray-600">—</td>
                        const c = compare(ra, rb, 'kld')
                        return (
                          <td key={d} className={`px-4 py-2 ${VERDICT_CLASS[c.verdict]}`}>
                            {c.gain >= 0 ? '−' : '+'}
                            {Math.abs(c.gain).toFixed(4)} ({VERDICT_TEXT[c.verdict]})
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )
      })}
      <p className="text-xs text-gray-500">
        Ye proxy naap hai (FP16 se distribution ka fark), asli Hindi task (sawal-jawab, summary) nahi.
        Hari = saaf fark, peela = sirf ishara, dhundla = fark nahi.
      </p>
    </div>
  )
}