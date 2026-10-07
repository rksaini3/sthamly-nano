import Leaderboard from '@/components/Leaderboard'
import ErrorNote from '@/components/ErrorNote'
import Main from '@/components/Main'
import { getModels } from '@/lib/supabase'

export const revalidate = 60

export const metadata = { title: 'Hindi Quant Report: Sthamly NanoBrain' }

export default async function LeaderboardPage() {
  const { models, error } = await getModels()

  return (
    <Main>
      <header className="mb-10 max-w-2xl">
        <h1 className="font-display text-3xl sm:text-4xl font-bold tracking-tight">Hindi Quant Report</h1>
        <p className="text-muted mt-3">
          Alag-alag Q4 files Hindi text par FP16 se kitni door hain. Hum kisi ki file bechte nahi, sirf naapte hain.
          Do quants me fark tabhi &quot;saaf&quot; hai jab wo error se 2 guna bada ho.
        </p>
      </header>
      {error && <ErrorNote message={error} />}
      <Leaderboard models={models} />
    </Main>
  )
}