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
import { safeLink } from '@/lib/jobs'
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
  const contact = safeLink(process.env.NEXT_PUBLIC_CONTACT_URL)
  
  // वेबसाइट को अनब्लर और एक्टिव करने के लिए इसे हमेशा 'true' रखेंगे
  const auditOn = true

  // वह पहला मॉडल ढूंढें जिसका चार्ट डेटा उपलब्ध है
  const hero = models?.find((m) => chartData(m) !== null)

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
              note={undefined}
            />
            <div className="mt-5 space-y-1.5 border-t border-line pt-4 font-mono text-xs">
              <p className="text-muted">// live progress aise dikhega</p>
              <p className="text-good">[✓] Model Hugging Face par check</p>
              <p className="text-ink">
                [⚙] Hindi test sets par FP16 se naap ... <span className="text-accent">100%</span>
              </p>
              <p className="whitespace-pre text-good">[✓] Result processed</p>
            </div>
          </Terminal>
        </div>
      </section>

      {/* ------------------------------------------------------------ middle: diagnostic */}
      {showDiagnostic && (
        <section className="mt-20 border-t border-line pt-14" aria-labelledby="diagnostic-title">
          <h2 id="diagnostic-title" className="font-display text-2xl font-bold tracking-tight sm:text-3xl">
            Diagnostic
          </h2>
          {error ? (
            <ErrorNote message="Data load karne me dikkat aayi. Kripya baad me check karein." />
          ) : hero ? (
            <div className="mt-8 grid gap-8 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
              <div>
                <KldChart model={hero} />
              </div>
              <div className="space-y-6">
                {/* FIX: कंपोनेंट की टाइपिस्क्रिप्ट एरर को दूर करने के लिए गैर-ज़रूरी और अतिरिक्त Props (gains, findings) हटा दिए गए हैं */}
                <ModelCard model={hero} />
              </div>
            </div>
          ) : (
            <div className="mt-6 rounded-xl border border-line bg-surface p-6 text-center text-muted">
              <p className="font-semibold text-ink text-base">Abhi koi result nahi hai</p>
              <p className="mt-2 text-sm">Pehla model naapte hi yahan FP16 se doori ka chart dikhega.</p>
            </div>
          )}
        </section>
      )}

      {/* ------------------------------------------------------------ bottom: manual */}
      <section className="mt-20 border-t border-line pt-14" aria-labelledby="manual-title">
        <h2 id="manual-title" className="font-display text-2xl font-bold tracking-tight sm:text-3xl">
          Chart kaise padhein
        </h2>
        <div className="mt-8 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {HOW_TO_READ.map((item) => (
            <div key={item.term} className="rounded-xl border border-line bg-surface p-5">
              <h3 className="font-bold text-ink">{item.term}</h3>
              <p className="mt-2 text-sm text-muted">{item.text}</p>
            </div>
          ))}
        </div>
      </section>
    </Main>
  )
}
