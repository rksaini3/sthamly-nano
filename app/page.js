import { supabase } from '@/lib/supabase'
import ModelCard from '@/components/ModelCard'
import ComparisonTable from '@/components/ComparisonTable'

export const revalidate = 60

export default async function Home() {
  const { data: models } = await supabase
    .from('models')
    .select('*')
    .order('created_at', { ascending: false })

  return (
    <main className="min-h-screen p-8 max-w-5xl mx-auto">
      <header className="mb-12 text-center">
        <h1 className="text-4xl font-bold mb-2">Sthamly NanoBrain</h1>
        <p className="text-gray-400">AI models that fit in your pocket and still speak proper Hindi.</p>
      </header>

      <section className="mb-12">
        <h2 className="text-2xl font-semibold mb-4">Hindi-Calibrated Models</h2>
        {models && models.length > 0 ? (
          <div className="grid gap-4 sm:grid-cols-2">
            {models.map((m) => <ModelCard key={m.id} model={m} />)}
          </div>
        ) : (
          <p className="text-gray-500">
            Abhi koi model publish nahi hua. scripts/compress_and_log.py chalao pehla model add karne ke liye.
          </p>
        )}
      </section>

      <section>
        <h2 className="text-2xl font-semibold mb-4">Hindi Benchmark</h2>
        <ComparisonTable models={models} />
      </section>
    </main>
  )
}