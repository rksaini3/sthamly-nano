import { supabase } from '@/lib/supabase'
import ComparisonTable from '@/components/ComparisonTable'
import type { Model } from '@/lib/types'

export const revalidate = 60

export default async function BenchmarkPage() {
  const { data: models } = await supabase
    .from('models')
    .select('*')
    .order('created_at', { ascending: false })
    .returns<Model[]>()

  return (
    <main className="min-h-screen p-8 max-w-5xl mx-auto">
      <h1 className="text-3xl font-bold mb-2">Hindi Benchmark</h1>
      <p className="text-gray-400 mb-8">
        FP16 vs standard Q4 vs Hindi-calibrated Q4, hamare apne Hindi/Hinglish test set par.
        Sirf measured results yahan dikhte hain — koi number pehle se nahi dikhaya jaata.
      </p>
      <ComparisonTable models={models} />
    </main>
  )
}