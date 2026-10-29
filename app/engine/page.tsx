import EngineRunner from '@/components/EngineRunner'
import Main from '@/components/Main'

export const metadata = {
  title: 'Sthamly Engine',
  description: 'Polymorphic AI Accelerator Explorer, graphene-ready',
}

export default function EnginePage() {
  return (
    <Main>
      <section className="max-w-3xl" aria-labelledby="engine-title">
        <p className="eyebrow">Sthamly Engine</p>
        <h1
          id="engine-title"
          className="mt-3 font-display text-4xl font-bold leading-[1.08] tracking-tight sm:text-5xl"
        >
          AI model se <span className="text-brand">chip design</span> tak
        </h1>
        <p className="mt-5 max-w-xl text-lg text-muted">
          Model quantize hota hai, 2D grid par map hota hai, aur Verilog banta hai. Accuracy naapi hui hai. Power aur
          speed simulated estimate hain.
        </p>
      </section>

      <section className="mt-12" aria-label="Engine">
        <EngineRunner />
      </section>
    </Main>
  )
}