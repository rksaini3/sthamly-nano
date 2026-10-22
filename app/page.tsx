import Link from 'next/link'
import AuditForm from '@/components/AuditForm'
import ErrorNote from '@/components/ErrorNote'
import Leaderboard from '@/components/Leaderboard'
import Main from '@/components/Main'
import Terminal from '@/components/Terminal'
import { getModels } from '@/lib/supabase'

export const revalidate = 60

const PROMISES = ['FP16 se seedha naap', 'Error bars ke saath', 'Jo saabit nahi, wo claim nahi']

export default async function Home() {
  const { models, error } = await getModels()

  // Public audit sirf tab on hai jab server par PUBLIC_AUDIT_ENABLED="true" ho. Default: band.
  const auditOn = process.env.PUBLIC_AUDIT_ENABLED === 'true'
  const preview = models?.slice(0, 3) ?? null

  return (
    <Main>
      {/* 1. Hero */}
      <section className="max-w-3xl" aria-labelledby="hero-title">
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
      </section>

      {/* 2. Free audit */}
      <section id="audit" className="mt-16 scroll-mt-24" aria-labelledby="audit-title">
        <h2 id="audit-title" className="sr-only">Free Hindi audit</h2>
        <Terminal title="sthamly · free hindi audit">
          <AuditForm
            disabled={!auditOn}
            note={auditOn ? undefined : 'Public audit abhi band hai. Apna model Sthamly Desk par check karein.'}
          />
        </Terminal>
      </section>

      {/* 3. Leaderboard preview */}
      <section id="leaderboard" className="mt-20 scroll-mt-24" aria-labelledby="leaderboard-title">
        <h2 id="leaderboard-title" className="font-display text-2xl font-bold tracking-tight sm:text-3xl">
          Hindi Quant Report
        </h2>
        <p className="mt-2 max-w-2xl text-muted">
          Alag-alag Q4 files Hindi text par FP16 se kitni door hain. Hum kisi ki file bechte nahi, sirf naapte hain.
        </p>
        {error && <ErrorNote message={error} />}
        <div className="mt-6">
          <Leaderboard models={preview} />
        </div>
        <p className="mt-6">
          <Link href="/leaderboard" className="btn btn-ghost">Poori leaderboard dekho</Link>
        </p>
      </section>

      {/* 4. Benchmark */}
      <section id="benchmark" className="mt-20 scroll-mt-24" aria-labelledby="benchmark-title">
        <h2 id="benchmark-title" className="font-display text-2xl font-bold tracking-tight sm:text-3xl">
          Benchmark
        </h2>
        <p className="mt-2 max-w-2xl text-muted">
          Plain Q4 ke muqable imatrix variants, FP16 ke saath naapa gaya, alag-alag Hindi test sets par.
        </p>
        <p className="mt-6">
          <Link href="/benchmark" className="btn btn-ghost">Benchmark dekho</Link>
        </p>
      </section>

      {/* 5. Desk */}
      <section id="desk" className="mt-20 scroll-mt-24 rounded-2xl border bg-surface/30 p-8 text-center" aria-labelledby="desk-title">
        <p className="font-mono text-sm uppercase tracking-wider text-brand">100% Private Offline AI Assistant</p>
        <h2 id="desk-title" className="mx-auto mt-3 max-w-3xl font-display text-3xl font-bold tracking-tight text-ink sm:text-4xl">
          Sthamly Desk: Vakeel, CA aur Students ke liye Offline Hindi Sahayak
        </h2>
        <p className="mx-auto mt-4 max-w-xl text-base text-muted">
          Aapke Case PDF, Financial Ledgers aur personal chat aapke computer par hi rehte hain. Zero Cloud, Zero Data Leaks.
        </p>
        <div className="mt-8 flex flex-col justify-center gap-4 sm:flex-row">
          <Link href="/download/windows" className="btn btn-primary flex items-center justify-center gap-2 rounded-xl px-8 py-3 text-base font-semibold shadow-md">
            <span>📥</span> Download for Windows (.exe)
          </Link>
          <Link href="/download/mac" className="btn btn-secondary flex items-center justify-center gap-2 rounded-xl px-8 py-3 text-base font-semibold shadow-md">
            <span>🍏</span> Download for Mac (.dmg)
          </Link>
        </div>
      </section>
    </Main>
  )
}