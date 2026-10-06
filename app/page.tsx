import ModelCard from '@/components/ModelCard'
import ComparisonTable from '@/components/ComparisonTable'
import { getModels } from '@/lib/supabase'

export const revalidate = 60

export default async function Home() {
  const { models, error } = await getModels()
  const contact = process.env.NEXT_PUBLIC_CONTACT_URL

  return (
    <main className="min-h-screen p-8 max-w-5xl mx-auto">
      <header className="mb-12 text-center">
        <h1 className="text-4xl font-bold mb-2">Sthamly NanoBrain</h1>
        <p className="text-gray-400">
          Hindi ke liye GGUF quantization, FP16 se naapa hua, error bars ke saath.
        </p>
      </header>

      {error && (
        <div className="mb-8 border border-red-900 rounded-lg p-4 text-sm text-red-300">{error}</div>
      )}

      <section className="mb-12">
        <h2 className="text-2xl font-semibold mb-4">Models</h2>
        {models.length > 0 ? (
          <div className="grid gap-4 sm:grid-cols-2">
            {models.map((m) => <ModelCard key={m.id} model={m} />)}
          </div>
        ) : (
          !error && (
            <p className="text-gray-500">
              Abhi koi model publish nahi hua. scripts/publish_mvp.py --go se pehla model add hota hai.
            </p>
          )
        )}
      </section>

      <section className="mb-12">
        <h2 className="text-2xl font-semibold mb-4">Benchmark</h2>
        <ComparisonTable models={models} />
      </section>

      {contact && (
        <section className="border border-gray-800 rounded-lg p-6">
          <h2 className="text-xl font-semibold mb-2">Free Hindi Quality Audit</h2>
          <p className="text-gray-400 mb-4">
            Apna Qwen/Llama model bhejo. Hum use quantize karke FP16 se KLD naap dete hain
            (Hindi aur English imatrix, plain Q4 ke saath), model card ke saath.
          </p>
          <a
            href={contact}
            className="inline-block px-4 py-2 rounded-md bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-500"
          >
            Audit maango
          </a>
        </section>
      )}
    </main>
  )
}