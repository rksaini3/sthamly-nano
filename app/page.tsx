import Link from 'next/link'
import ModelCard from '@/components/ModelCard'
import KldChart from '@/components/KldChart'
import ErrorNote from '@/components/ErrorNote'
import Main from '@/components/Main'
import { getModels } from '@/lib/supabase'
import { chartData } from '@/lib/chart'
import { findings } from '@/lib/findings'
import { LADDER, NOT_CLAIMED } from '@/lib/ladder'
import type { Verdict } from '@/lib/verdict'

export const revalidate = 60

const TONE_BAR: Record<Verdict, string> = { clear: 'bg-good', hint: 'bg-warn', none: 'bg-flat' }
const TONE_TEXT: Record<Verdict, string> = { clear: 'text-good', hint: 'text-warn', none: 'text-muted' }

const HOW_TO_READ = [
  {
    term: 'KLD',
    text: 'Quantized model ke jawab FP16 se kitne alag hain. Kam matlab FP16 ke kareeb, yaani behtar.',
  },
  {
    term: 'Error bar (±)',
    text: 'Naap kitna pakka hai. Do quants ki lines overlap karein to dono me fark saabit nahi.',
  },
  {
    term: 'Saaf fark',
    text: 'Fark tabhi saaf hai jab wo error se 2 guna bada ho. Isse chhota fark sirf ishara hai.',
  },
]

export default async function Home() {
  const { models, error } = await getModels()
  const contact = process.env.NEXT_PUBLIC_CONTACT_URL

  // The chart on top shows the first model that has at least two quants measured.
  const hero = models.find((m) => chartData(m) !== null)
  const finds = hero ? findings(hero.results) : []

  return (
    <Main>
      <header className="max-w-3xl">
        <h1 className="font-display text-4xl sm:text-5xl font-bold tracking-tight leading-[1.1]">
          Quantized model Hindi par FP16 se kitna door jaata hai?
        </h1>
        <p className="text-lg text-muted mt-5 max-w-2xl">
          Hum naapte hain, error bars ke saath. Jo saabit nahi hota, wo hum likhte bhi nahi.
        </p>
        <div className="mt-7 flex flex-wrap gap-3">
          <Link
            href="/leaderboard"
            className="inline-flex items-center justify-center rounded-md bg-ink px-5 py-2.5 text-sm font-medium text-bg hover:opacity-90"
          >
            Leaderboard dekho
          </Link>
          {contact && (
            <a
              href={contact}
              className="inline-flex items-center justify-center rounded-md border border-line bg-surface px-5 py-2.5 text-sm font-medium hover:border-ink"
            >
              Free audit maango
            </a>
          )}
        </div>
      </header>

      {error && (
        <div className="mt-10">
          <ErrorNote message={error} />
        </div>
      )}

      <section className="mt-12" aria-labelledby="chart-title">
        {hero ? (
          <div className="rounded-lg border border-line bg-surface p-4 sm:p-6">
            <h2 id="chart-title" className="font-display text-xl font-semibold leading-snug">
              {hero.model_name}: FP16 se doori
            </h2>
            <p className="text-sm text-muted mt-1 mb-6">Do Hindi test sets par naapa gaya. Kam = behtar.</p>
            <KldChart model={hero} />
          </div>
        ) : (
          !error && (
            <div className="rounded-lg border border-line bg-surface p-6">
              <h2 id="chart-title" className="font-display text-xl font-semibold">Abhi koi result nahi hai</h2>
              <p className="text-sm text-muted mt-1 max-w-prose">
                Pehla model naapte hi yahan FP16 se doori ka chart dikhega. Koi number pehle se nahi dikhaya jaata.
              </p>
            </div>
          )
        )}
      </section>

      {finds.length > 0 && (
        <section className="mt-14" aria-labelledby="found-title">
          <h2 id="found-title" className="font-display text-2xl font-semibold mb-5">Isse kya nikalta hai</h2>
          <ul className="space-y-4 max-w-3xl">
            {finds.map((f) => (
              <li key={f.text} className="flex gap-4">
                <span className={`mt-1 w-1 shrink-0 rounded-full ${TONE_BAR[f.tone]}`} aria-hidden />
                <p>
                  <span className={`font-semibold ${TONE_TEXT[f.tone]}`}>{f.lead}. </span>
                  {f.text}
                </p>
              </li>
            ))}
          </ul>
          <p className="text-sm text-muted mt-5 max-w-3xl">
            Ye sirf Qwen2.5-1.5B par, chhote test sets (150 lines) par naapa hai. Task accuracy (sawal-jawab, summary)
            abhi nahi naapi gayi.
          </p>
        </section>
      )}

      <section className="mt-14" aria-labelledby="read-title">
        <h2 id="read-title" className="font-display text-2xl font-semibold mb-5">Chart kaise padhein</h2>
        <dl className="grid gap-6 sm:grid-cols-3">
          {HOW_TO_READ.map((h) => (
            <div key={h.term} className="border-t border-ink pt-3">
              <dt className="font-semibold">{h.term}</dt>
              <dd className="text-sm text-muted mt-1">{h.text}</dd>
            </div>
          ))}
        </dl>
      </section>

      {models.length > 0 && (
        <section className="mt-14" aria-labelledby="models-title">
          <h2 id="models-title" className="font-display text-2xl font-semibold mb-2">Models</h2>
          <ul className="divide-y divide-line border-y border-line">
            {models.map((m) => (
              <ModelCard key={m.id} model={m} />
            ))}
          </ul>
        </section>
      )}

      <section className="mt-14" aria-labelledby="offer-title">
        <h2 id="offer-title" className="font-display text-2xl font-semibold mb-2">Aap kya le sakte ho</h2>
        <p className="text-muted mb-5 max-w-2xl">Shuru me aapko kuch badalna nahi. Pehle sirf ek report.</p>
        <ul className="divide-y divide-line border-y border-line">
          {LADDER.map((s) => (
            <li key={s.name} className="py-4 grid gap-1 sm:grid-cols-[minmax(0,1fr)_auto] sm:gap-8">
              <div>
                <h3 className="font-semibold">{s.name}</h3>
                <p className="text-sm text-muted max-w-2xl">{s.what}</p>
              </div>
              <p className="font-medium sm:text-right whitespace-nowrap">{s.price}</p>
            </li>
          ))}
        </ul>
        <p className="text-xs text-muted mt-3">Prices shuruaati hain; asli customers se baat karke tay hote hain.</p>
      </section>

      <section className="mt-14 max-w-3xl" aria-labelledby="no-claim-title">
        <h2 id="no-claim-title" className="font-display text-xl font-semibold mb-3">Hum ye claim nahi karte</h2>
        <ul className="list-disc pl-5 space-y-1.5 text-sm text-muted">
          {NOT_CLAIMED.map((t) => (
            <li key={t}>{t}</li>
          ))}
        </ul>
      </section>

      {contact && (
        <section className="mt-14 rounded-lg bg-ink text-bg p-6 sm:p-8" aria-labelledby="audit-title">
          <h2 id="audit-title" className="font-display text-2xl font-semibold">Apne quantized model ka free Hindi audit</h2>
          <p className="mt-2 max-w-2xl opacity-80">
            Model bhejo. Hum use FP16 ke muqable Hindi par naapkar report dete hain. Aapke system me kuch badalna nahi.
          </p>
          <a
            href={contact}
            className="mt-5 inline-flex items-center justify-center rounded-md bg-bg px-5 py-2.5 text-sm font-medium text-ink hover:opacity-90 focus-visible:outline-bg"
          >
            Audit maango
          </a>
        </section>
      )}
    </Main>
  )
}