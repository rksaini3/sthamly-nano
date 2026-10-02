import type { Model } from '@/lib/types'

type Scored = Model & {
  hindi_score_fp16: number
  hindi_score_standard_q4: number
  hindi_score_hindi_q4: number
}

// Perplexity: kam = behtar. Green sirf us variant ko jiska score sach me kam hai.
// Barabar ho to koi rang nahi (neutral).
function colorFor(value: number, other: number): string {
  if (value < other) return 'text-green-400'
  if (value > other) return 'text-red-400'
  return 'text-gray-400'
}

function verdict(m: Scored): { text: string; cls: string } {
  const diff = m.hindi_score_standard_q4 - m.hindi_score_hindi_q4
  if (diff > 0) return { text: `Hindi-calibrated jeeta (${diff.toFixed(2)} behtar)`, cls: 'text-green-400' }
  if (diff < 0) return { text: `Standard jeeta (${Math.abs(diff).toFixed(2)} behtar)`, cls: 'text-red-400' }
  return { text: 'Barabar', cls: 'text-gray-400' }
}

export default function ComparisonTable({ models }: { models: Model[] | null }) {
  const withScores = (models || []).filter(
    (m): m is Scored =>
      m.hindi_score_fp16 != null &&
      m.hindi_score_standard_q4 != null &&
      m.hindi_score_hindi_q4 != null
  )

  if (withScores.length === 0) {
    return (
      <div className="border border-gray-800 rounded-lg p-6 text-gray-500">
        Benchmark abhi pending hai. Jab scripts/run_benchmark.py se real score log honge,
        tabhi yahan dikhenge. Koi number pehle se nahi dikhaya jaata.
      </div>
    )
  }

  return (
    <div>
      <div className="overflow-x-auto border border-gray-800 rounded-lg">
        <table className="min-w-full divide-y divide-gray-800 text-sm">
          <thead className="bg-gray-900 text-gray-300">
            <tr>
              <th className="px-4 py-3 text-left">Model</th>
              <th className="px-4 py-3 text-left">FP16 (original)</th>
              <th className="px-4 py-3 text-left">Standard Q4</th>
              <th className="px-4 py-3 text-left">Hindi-calibrated Q4</th>
              <th className="px-4 py-3 text-left">Result</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800 text-gray-400">
            {withScores.map((m) => {
              const v = verdict(m)
              return (
                <tr key={m.id}>
                  <td className="px-4 py-3 font-medium text-white">{m.model_name}</td>
                  <td className="px-4 py-3">{m.hindi_score_fp16}</td>
                  <td className={`px-4 py-3 ${colorFor(m.hindi_score_standard_q4, m.hindi_score_hindi_q4)}`}>
                    {m.hindi_score_standard_q4}
                  </td>
                  <td className={`px-4 py-3 ${colorFor(m.hindi_score_hindi_q4, m.hindi_score_standard_q4)}`}>
                    {m.hindi_score_hindi_q4}
                  </td>
                  <td className={`px-4 py-3 ${v.cls}`}>{v.text}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-gray-500">
        Perplexity score: kam = behtar. Hari value wahi hai jo sach me kam aayi.
      </p>
    </div>
  )
}
