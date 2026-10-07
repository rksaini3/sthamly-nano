import ComparisonTable from '@/components/ComparisonTable'
import ErrorNote from '@/components/ErrorNote'
import Main from '@/components/Main'
import { getModels } from '@/lib/supabase'

export const revalidate = 60

export const metadata = { title: 'Hindi Benchmark: Sthamly NanoBrain' }

export default async function BenchmarkPage() {
  const { models, error } = await getModels()

  return (
    <Main>
      <header className="mb-10 max-w-2xl">
        <h1 className="font-display text-3xl sm:text-4xl font-bold tracking-tight">Hindi Benchmark</h1>
        <p className="text-muted mt-3">
          Plain Q4 ke muqable imatrix variants, FP16 ke saath naapa gaya, alag-alag Hindi test sets par. Pehle seedha
          jawab (kaun kisse behtar), uske neeche poore numbers.
        </p>
      </header>
      {error && <ErrorNote message={error} />}
      <ComparisonTable models={models} />
    </Main>
  )
}