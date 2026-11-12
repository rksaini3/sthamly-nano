import DeskSection from '@/components/DeskSection'
import Main from '@/components/Main'

export default function Home() {
  return (
    <Main>
      <section className="max-w-3xl" aria-labelledby="hero-title">
        <p className="eyebrow">Sthamly Legal Desk</p>
        <h1 id="hero-title" className="mt-3 font-display text-4xl font-bold leading-[1.08] tracking-tight sm:text-5xl">
          Case ki PDF, <span className="text-brand">aapke computer</span> par.
        </h1>
        <p className="mt-5 max-w-xl text-lg text-muted">
          Vakeel ke liye offline AI sahayak. Notice aur bail application ka draft, aur case ke ratios, sab aapki PDF se.
        </p>
      </section>
      <DeskSection />
    </Main>
  )
}
