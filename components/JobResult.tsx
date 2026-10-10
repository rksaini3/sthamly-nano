import Link from 'next/link'
import KldChart from '@/components/KldChart'
import { chartData } from '@/lib/chart'
import { findings, type Finding } from '@/lib/findings'
import { outOf100, type AuditResult, type CalibrateResult } from '@/lib/jobs'
import { domainName } from '@/lib/labels'
import { kldReductionPct, orderedVariants, variantLabel, type Verdict } from '@/lib/verdict'

const TONE_BAR: Record<Verdict, string> = { clear: 'bg-good', hint: 'bg-warn', none: 'bg-flat' }
const TONE_TEXT: Record<Verdict, string> = { clear: 'text-good', hint: 'text-warn', none: 'text-muted' }

const fmt = (m: [number, number], digits: number) => `${m[0].toFixed(digits)} ± ${m[1].toFixed(digits)}`

function Findings({ items }: { items: Finding[] }) {
  if (items.length === 0) return null
  return (
    <ul className="mt-4 space-y-3">
      {items.map((f) => (
        <li key={f.text} className="flex gap-3">
          <span className={`mt-1 w-1 shrink-0 rounded-full ${TONE_BAR[f.tone]}`} aria-hidden />
          <p className="text-sm">
            <span className={`font-semibold ${TONE_TEXT[f.tone]}`}>{f.lead}. </span>
            {f.text}
          </p>
        </li>
      ))}
    </ul>
  )
}

/** Free audit: how far each measured quant is from FP16, per test set. No High/Low verdicts on purpose. */
export function AuditResultView({ result }: { result: AuditResult }) {
  const domains = Object.keys(result.results)
  const model = { results: result.results, variants_meta: result.variants_meta }

  return (
    <section aria-labelledby="audit-result-title" className="space-y-6">
      <div>
        <h2 id="audit-result-title" className="font-display text-2xl font-semibold">
          Hindi audit: FP16 se doori
        </h2>
        <p className="mt-1 max-w-2xl text-sm text-muted">
          {result.model}, {result.chunks} chunks par naapa. KLD kam = FP16 ke kareeb. &quot;FP16 jaisa agla token&quot; =
          100 jagah me se kitni baar quantized model ne wahi sabse-sambhav token chuna jo FP16 ne chuna.
        </p>
      </div>

      <div className="overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <caption className="sr-only">Har test set par har quant ka KLD aur FP16 jaisa agla token</caption>
          <thead className="border-b border-line text-left text-muted">
            <tr>
              <th scope="col" className="px-3 py-3 font-medium sm:px-4">Test set</th>
              <th scope="col" className="px-3 py-3 font-medium sm:px-4">Quant</th>
              <th scope="col" className="whitespace-nowrap px-3 py-3 font-medium sm:px-4">KLD (kam = behtar)</th>
              <th scope="col" className="whitespace-nowrap px-3 py-3 font-medium sm:px-4">FP16 jaisa agla token</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {domains.flatMap((d) =>
              orderedVariants(result.results[d]).map((v) => {
                const m = result.results[d][v]
                return (
                  <tr key={`${d}-${v}`}>
                    <td className="px-3 py-2.5 text-muted sm:px-4">
                      {domainName(d)}
                      {result.lines[d] ? <span className="block text-xs">{result.lines[d]} lines</span> : null}
                    </td>
                    <td className="px-3 py-2.5 font-medium sm:px-4">{variantLabel(v, result.variants_meta)}</td>
                    <td className="whitespace-nowrap px-3 py-2.5 sm:px-4">{fmt(m.kld, 4)}</td>
                    <td className="whitespace-nowrap px-3 py-2.5 sm:px-4">
                      ~{outOf100(m.top[0])}/100 baar <span className="text-muted">(± {m.top[1].toFixed(2)}%)</span>
                    </td>
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>

      {chartData(model) && (
        <div className="rounded-lg border border-line bg-surface p-4 sm:p-6">
          <KldChart model={model} />
        </div>
      )}

      <p className="max-w-2xl text-xs text-muted">
        Ye proxy naap hai (FP16 se distribution ka fark), asli Hindi task nahi. KLD alag models me aapas me compare nahi
        hota, isliye hum isse &quot;achha&quot; ya &quot;kharab&quot; ka label nahi dete. Is model par plain Q4 ke saath imatrix
        calibration se fayda hoga ya nahi, ye alag naap se hi pata chalega.
      </p>
    </section>
  )
}

/** Calibration: plain Q4 vs the best imatrix variant, said only as far as the numbers go. */
export function CalibrateResultView({ result, contact }: { result: CalibrateResult; contact?: string }) {
  const model = { results: result.results, variants_meta: result.variants_meta }
  const improved = result.outcome === 'improved' && result.best !== null
  const best = result.best
  const gains = best ? Object.entries(kldReductionPct(result.results, best)) : []
  const domains = Object.keys(result.results)

  return (
    <section aria-labelledby="cal-result-title" className="space-y-6">
      <div>
        <h2 id="cal-result-title" className="font-display text-2xl font-semibold">
          {improved && best ? `${variantLabel(best, result.variants_meta)} se behtar Q4 mila` : 'Is model par saaf fayda nahi dikha'}
        </h2>
        <p className="mt-2 max-w-2xl text-sm">
          {improved ? (
            <>
              Plain Q4 ke muqable KLD kam hua: {gains.map(([d, g]) => `${domainName(d)} ${g.toFixed(0)}%`).join(', ')}.{' '}
              Ye {result.model} par, {result.chunks} chunks aur chhote test sets par naapa gaya hai; doosre models par
              alag ho sakta hai.
            </>
          ) : (
            <>Imatrix variants plain Q4 se saaf behtar nahi nikle (ya kisi test set me saaf kharab nikle). Plain Q4 hi rakho.</>
          )}
        </p>
      </div>

      <div className="overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <caption className="sr-only">Plain Q4 aur best imatrix variant ka KLD, har test set par</caption>
          <thead className="border-b border-line text-left text-muted">
            <tr>
              <th scope="col" className="px-3 py-3 font-medium sm:px-4">Test set</th>
              <th scope="col" className="whitespace-nowrap px-3 py-3 font-medium sm:px-4">Pehle: Plain Q4</th>
              <th scope="col" className="whitespace-nowrap px-3 py-3 font-medium sm:px-4">
                Baad me: {best ? variantLabel(best, result.variants_meta) : '—'}
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {domains.map((d) => (
              <tr key={d}>
                <td className="px-3 py-2.5 text-muted sm:px-4">{domainName(d)}</td>
                <td className="whitespace-nowrap px-3 py-2.5 sm:px-4">
                  {result.results[d].standard ? fmt(result.results[d].standard.kld, 4) : '—'}
                </td>
                <td className="whitespace-nowrap px-3 py-2.5 sm:px-4">
                  {best && result.results[d][best] ? fmt(result.results[d][best].kld, 4) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Findings items={findings(result.results)} />

      {chartData(model) && (
        <div className="rounded-lg border border-line bg-surface p-4 sm:p-6">
          <KldChart model={model} />
        </div>
      )}

      {improved && (
        <p className="text-sm">
          GGUF file abhi haath se bheji jaati hai.{' '}
          {contact ? (
            <a className="underline underline-offset-2" href={contact}>
              File maangne ke liye contact karo
            </a>
          ) : (
            'Contact link abhi set nahi hai.'
          )}
        </p>
      )}

      <p className="max-w-2xl text-xs text-muted">
        Ye proxy naap hai (FP16 se distribution ka fark), asli Hindi task (sawal-jawab, summary) nahi.{' '}
        <Link className="underline underline-offset-2" href="/">Home par wapas</Link>
      </p>
    </section>
  )
}
