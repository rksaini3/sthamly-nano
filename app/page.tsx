import Link from 'next/link'
import AuditForm from '@/components/AuditForm'
import DeskSection from '@/components/DeskSection'
import ErrorNote from '@/components/ErrorNote'
import KldChart from '@/components/KldChart'
import Main from '@/components/Main'
import ModelCard from '@/components/ModelCard'
import Terminal from '@/components/Terminal'
import { chartData } from '@/lib/chart'
import { findings } from '@/lib/findings'
import { domainName } from '@/lib/labels'
import { LADDER, NOT_CLAIMED } from '@/lib/ladder'
import { getModels } from '@/lib/supabase'
import { compare, kldReductionPct, type Verdict } from '@/lib/verdict'

export const revalidate = 60

const TONE_BAR: Record<Verdict, string> = { clear: 'bg-good', hint: 'bg-warn', none: 'bg-flat' }
const TONE_TEXT: Record<Verdict, string> = { clear: 'text-good', hint: 'text-warn', none: 'text-muted' }

const PROMISES = ['FP16 se seedha naap', 'Error bars ke saath', 'Jo saabit nahi, wo claim nahi']

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
  // The audit form works only when the Phase 1 backend (db/schema_v3.sql + worker) is switched on.
  const auditOn = process.env.NEXT_PUBLIC_AUDIT_ENABLED === '1'

  // The chart shows the first model that has at least two quants measured.
  const hero = models.find((m) => chartData(m) !== null)
  const finds = hero ? findings(hero.results) : []
  // Only gains that are CLEAR (more than 2x the error) are quoted: no hardcoded percentages.
  const results = hero?.results ?? null
  const clearGains = results
    ? Object.entries(kldReductionPct(results)).filter(
        ([d, g]) => g > 0 && compare(results[d].hindi60, results[d].standard).verdict === 'clear'
      )
    : []

  // With a load error and no data there is nothing to chart: the ErrorNote already says so.
  const showDiagnostic = Boolean(hero) || !error

  return (
    <Main>
      {/* ------------------------------------------------------------ top: the engine */}
      <section className="grid gap-10 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)] lg:items-center" aria-labelledby="hero-title">
        <div>
          <p className="eyebrow">Hindi quantization audit</p>
          <h1 id="hero-title" className="mt-3 font-display text-4xl font-bold leading-[1.08] tracking-tight sm:text-5xl">
            Quantized model Hindi par <span className="text-brand">FP16</span> se kitna door jaata hai?
          </h1>
          <p className="mt-5 max-w-xl text-lg text-muted">
            Hum naapte hain, error bars ke saath. Jo saabit nahi hota, wo hum likhte bhi nahi.
          </p>
          <ul className="mt-6 flex flex-wrap gap-2">
            {PROMISES.map((t) => (
              <li key={t} className="rounded-full border border-line bg-surface px-3 py-1 text-sm text-muted">
                <span className="text-good" aria-hidden>✓ </span>
                {t}
              </li>
            ))}
          </ul>
          <p className="mt-7">
            <Link href="/leaderboard" className="btn btn-ghost">
              Leaderboard dekho
            </Link>
          </p>
        </div>

        <div id="audit">
          <Terminal title="sthamly · free hindi audit">
            <AuditForm
              disabled={!auditOn}
              note={
                auditOn ? undefined : (
                  <>
                    Free audit abhi chalu nahi hua.{' '}
                    {contact ? (
                      <a className="text-accent underline underline-offset-2" href={contact}>
                        Pehle audit ke liye contact karo
                      </a>
                    ) : null}
                  </>
                )
              }
            />
            <div className="mt-5 space-y-1.5 border-t border-line pt-4 font-mono text-xs">
              <p className="text-muted">// live progress aise dikhega (udaharan)</p>
              <p className="text-good">[✓] Model Hugging Face par check</p>
              <p className="text-ink">
                [⚙] Hindi test sets par FP16 se naap ... <span className="text-accent">42%</span>
              </p>
              <p className="whitespace-pre text-muted">[ ] Result likhna</p>
            </div>
          </Terminal>
        </div>
      </section>

      {error && (
        <div className="mt-10">
          <ErrorNote message={error} />
        </div>
      )}

      {/* ------------------------------------------------------------ diagnostic + conversion */}
      {showDiagnostic && (
        <section className="mt-20" aria-labelledby="chart-title">
          <p className="eyebrow">Diagnostic</p>
          {hero ? (
            <>
              <h2 id="chart-title" className="mt-2 font-display text-2xl font-bold tracking-tight sm:text-3xl">
                {hero.model_name}: FP16 se doori
              </h2>
              <p className="mt-2 text-sm text-muted">Do Hindi test sets par naapa gaya. Kam = behtar.</p>

              <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_21rem]">
                <div className="panel p-4 sm:p-6">
                  <KldChart model={hero} />
                </div>

                <aside className="panel flex flex-col p-5" aria-labelledby="callout-title">
                  <h3 id="callout-title" className="font-display text-lg font-semibold">
                    Imatrix calibration se kya mila
                  </h3>
                  {clearGains.length > 0 ? (
                    <>
                      <ul className="mt-4 grid grid-cols-2 gap-3">
                        {clearGains.map(([d, g]) => (
                          <li key={d} className="rounded-lg border border-line bg-raised p-3">
                            <p className="font-display text-3xl font-bold text-good">−{Math.round(g)}%</p>
                            <p className="text-xs text-muted">KLD, {domainName(d)}</p>
                          </li>
                        ))}
                      </ul>
                      <p className="mt-4 text-sm text-muted">
                        Hindi imatrix se plain Q4 ke muqable, {hero.model_name} par. Aapke model par alag ho sakta hai:
                        pehle free audit, phir calibration.
                      </p>
                    </>
                  ) : (
                    <p className="mt-3 text-sm text-muted">
                      Calibration ka fayda har model par alag hota hai, isliye hum koi percentage pehle se nahi likhte.
                      Pehle naapo.
                    </p>
                  )}

                  <div className="mt-auto space-y-3 pt-6">
                    {contact ? (
                      <a href={contact} className="btn btn-primary w-full">
                        ⚡ Calibrate & Quantize: Rs 399
                      </a>
                    ) : (
                      <button type="button" className="btn btn-primary w-full" disabled>
                        ⚡ Calibrate & Quantize: Rs 399
                      </button>
                    )}
                    <p className="text-xs text-muted">Abhi manual UPI se. Self-serve payment baad me aayega.</p>
                    <a href="#audit" className="block text-sm text-accent underline underline-offset-2">
                      Pehle free audit chalao
                    </a>
                  </div>
                </aside>
              </div>
            </>
          ) : (
            !error && (
              <div className="panel mt-4 p-6">
                <h2 id="chart-title" className="font-display text-xl font-semibold">Abhi koi result nahi hai</h2>
                <p className="mt-1 max-w-prose text-sm text-muted">
                  Pehla model naapte hi yahan FP16 se doori ka chart dikhega. Koi number pehle se nahi dikhaya jaata.
                </p>
              </div>
            )
          )}
        </section>
      )}

      {finds.length > 0 && (
        <section className="mt-16" aria-labelledby="found-title">
          <h2 id="found-title" className="mb-5 font-display text-2xl font-bold tracking-tight">Isse kya nikalta hai</h2>
          <ul className="max-w-3xl space-y-4">
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
          <p className="mt-5 max-w-3xl text-sm text-muted">
            Ye sirf Qwen2.5-1.5B par, chhote test sets (150 lines) par naapa hai. Task accuracy (sawal-jawab, summary)
            abhi nahi naapi gayi.
          </p>
        </section>
      )}

      <section className="mt-16" aria-labelledby="read-title">
        <h2 id="read-title" className="mb-5 font-display text-2xl font-bold tracking-tight">Chart kaise padhein</h2>
        <dl className="grid gap-4 sm:grid-cols-3">
          {HOW_TO_READ.map((h) => (
            <div key={h.term} className="panel p-4">
              <dt className="font-semibold text-accent">{h.term}</dt>
              <dd className="mt-1 text-sm text-muted">{h.text}</dd>
            </div>
          ))}
        </dl>
      </section>

      {models.length > 0 && (
        <section className="mt-16" aria-labelledby="models-title">
          <h2 id="models-title" className="mb-3 font-display text-2xl font-bold tracking-tight">Models</h2>
          <ul className="divide-y divide-line border-y border-line">
            {models.map((m) => (
              <ModelCard key={m.id} model={m} />
            ))}
          </ul>
        </section>
      )}

      <section className="mt-16" aria-labelledby="offer-title">
        <h2 id="offer-title" className="font-display text-2xl font-bold tracking-tight">Aap kya le sakte ho</h2>
        <p className="mb-6 mt-2 max-w-2xl text-muted">Shuru me aapko kuch badalna nahi. Pehle sirf ek report.</p>
        <ol className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          {LADDER.map((s, i) => (
            <li key={s.name} className="panel flex flex-col p-5">
              <span className="font-mono text-xs text-accent">0{i + 1}</span>
              <h3 className="mt-1 font-semibold">{s.name}</h3>
              <p className="mt-2 text-sm text-muted">{s.what}</p>
              {s.note && <p className="mt-2 text-xs text-muted">{s.note}</p>}
              <p className="mt-auto pt-5 font-display text-xl font-bold text-brand">{s.price}</p>
            </li>
          ))}
        </ol>
        <p className="mt-3 text-xs text-muted">Prices shuruaati hain; asli customers se baat karke tay hote hain.</p>
      </section>

      {/* ------------------------------------------------------------ bottom: the desk hub */}
      <DeskSection />

      <section className="mt-20 max-w-3xl" aria-labelledby="no-claim-title">
        <h2 id="no-claim-title" className="mb-3 font-display text-xl font-semibold">Hum ye claim nahi karte</h2>
        <ul className="list-disc space-y-1.5 pl-5 text-sm text-muted">
          {NOT_CLAIMED.map((t) => (
            <li key={t}>{t}</li>
          ))}
        </ul>
      </section>
    </Main>
  )
}
