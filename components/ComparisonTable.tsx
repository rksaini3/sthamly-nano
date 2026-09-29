import type { Model } from '@/lib/types'

export default function ComparisonTable({ models }: { models: Model[] | null }) {
  const withScores = (models || []).filter(
    (m): m is Model & { hindi_score_fp16: number; hindi_score_standard_q4: number; hindi_score_hindi_q4: number } =>
      m.hindi_score_fp16 != null &&
      m.hindi_score_standard_q4 != null &&
      m.hindi_score_hindi_q4 != null
  )

  if (withScores.length === 0) {
    return (
      <div className="border border-gray-800 rounded-lg p-6 text-gray-500">
        Benchmark abhi pending hai. Jab scripts/run_benchmark.py se real score log honge,
        tabhi yahan dikhenge — koi number pehle se nahi dikhaya jaata.
      </div>
    )
  }

  return (
    <div className="overflow-x-auto border border-gray-800 rounded-lg">
      <table className="min-w-full divide-y divide-gray-800 text-sm">
        <thead className="bg-gray-900 text-gray-300">
          <tr>
            <th className="px-4 py-3 text-left">Model</th>
            <th className="px-4 py-3 text-left">FP16 score</th>
            <th className="px-4 py-3 text-left">Standard Q4</th>
            <th className="px-4 py-3 text-left">Hindi-calibrated Q4</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-800 text-gray-400">
          {withScores.map((m) => (
            <tr key={m.id}>
              <td className="px-4 py-3 font-medium text-white">{m.model_name}</td>
              <td className="px-4 py-3">{m.hindi_score_fp16}</td>
              <td className="px-4 py-3 text-red-400">{m.hindi_score_standard_q4}</td>
              <td className="px-4 py-3 text-green-400">{m.hindi_score_hindi_q4}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}