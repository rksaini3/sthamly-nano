import ComparisonTable from '@/components/ComparisonTable'
import { getModels } from '@/lib/supabase'

export const revalidate = 60

export default async function BenchmarkPage() {
  const { models, error } = await getModels()

  return (
    <main className="min-h-screen p-8 max-w-5xl mx-auto">
      <h1 className="text-3xl font-bold mb-2">Hindi Benchmark</h1>
      <p className="text-gray-400 mb-8">
        Plain Q4 vs Hindi / English / Mixed imatrix Q4, FP16 ke muqable (KLD), alag-alag Hindi test sets par.
        Sirf measured results yahan dikhte hain. Fark tabhi &quot;saaf&quot; kehte hain jab wo error se 2x bada ho.
      </p>
      {error && (
        <div className="mb-8 border border-red-900 rounded-lg p-4 text-sm text-red-300">{error}</div>
      )}
      <ComparisonTable models={models} />
    </main>
  )
}