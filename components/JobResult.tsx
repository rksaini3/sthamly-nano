'use client'

import Link from 'next/link'
import KldChart from '@/components/KldChart'
import { chartData } from '@/lib/chart'
import { findings, type Finding } from '@/lib/findings'
import { outOf100, type AuditResult, type CalibrateResult } from '@/lib/jobs'
import { domainName } from '@/lib/labels'
import { orderedVariants, variantLabel, type Verdict } from '@/lib/verdict'

const TONE_BAR: Record<Verdict, string> = { clear: 'bg-good', hint: 'bg-warn', none: 'bg-flat' }
const TONE_TEXT: Record<Verdict, string> = { clear: 'text-good', hint: 'text-warn', none: 'text-muted' }

const fmt = (m: any, digits: number) => {
  if (!m) return '—'
  const val = Array.isArray(m) ? m[0] : 0
  const err = Array.isArray(m) ? m[1] : 0
  return `${Number(val).toFixed(digits)} ± ${Number(err).toFixed(digits)}`
}

function Findings({ items }: { items: Finding[] }) {
  if (items.length === 0) return null
  return (
    <ul className="space-y-3">
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

/** Free audit: how far plain Q4 is from FP16, per test set. */
export function AuditResultView({ result }: { result: AuditResult }) {
  const domains = Object.keys(result.results)
  const model = { results: result.results, variants_meta: null }

  return (
    <section aria-labelledby="audit-result-title" className="space-y-6">
      <div>
        <h2 id="audit-result-title" className="font-display text-2xl font-semibold">
          Hindi audit: FP16 se doori
        </h2>
        <p className="mt-1 max-w-2xl text-sm text-muted">
          {result.model}, {result.chunks} chunks par naapa
          {result.fp16_gb ? ` (FP16 ${result.fp16_gb} GB` : ''}
          {result.fp16_gb && result.quant_gb ? `, Q4 ${result.quant_gb} GB)` : result.fp16_gb ? ')' : ''}. KLD kam = FP16 ke kareeb.
        </p>
      </div>

      <div className="overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <caption className="sr-only">Har test set par plain Q4 ka KLD aur FP16 jaisa agla token</caption>
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
                
                // --- FIXED STRICT METRIC TUPLE SCRIPT DESTRUCTURING ---
                // Extracting explicit array indices to completely bypass type constraint barriers
                const topValue = Array.isArray(m.top) ? m.top[0] : Number(m.top);
                const topError = Array.isArray(m.top) ? m.top[1] : 0;

                return (
                  <tr key={`${d}-${v}`}>
                    <td className="px-3 py-2.5 text-muted sm:px-4">
                      {domainName(d)}
                      {result.domains?.[d] ? <span className="block text-xs">{result.domains[d]} lines</span> : null}
                    </td>
                    <td className="px-3 py-2.5 font-medium sm:px-4">{variantLabel(v)}</td>
                    <td className="whitespace-nowrap px-3 py-2.5 sm:px-4">{fmt(m.kld, 4)}</td>
                    <td className="whitespace-nowrap px-3 py-2.5 sm:px-4">
                      ~{outOf100(topValue)}/100 baar <span className="text-muted">(± {Number(topError).toFixed(2)}%)</span>
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
    </section>
  )
}

/** Calibration: plain Q4 against the best imatrix variant. */
export function CalibrateResultView({ result, contact }: { result: CalibrateResult; contact?: string }) {
  const model = { results: result.results, variants_meta: null }
  const best = result.best
  const improved = result.decision === 'improved' && best !== null
  const domains = Object.keys(result.results)
  const bestName = best ? variantLabel(best) : '—'

  return (
    <section aria-labelledby="cal-result-title" className="space-y-6">
      <div>
        <h2 id="cal-result-title" className="font-display text-2xl font-semibold">
          {improved ? `${bestName} se behtar Q4 mila` : 'Is model par saaf fayda nahi dikha'}
        </h2>
      </div>

      <div className="overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <thead className="border-b border-line text-left text-muted">
            <tr>
              <th scope="col" className="px-3 py-3 font-medium sm:px-4">Test set</th>
              <th scope="col" className="whitespace-nowrap px-3 py-3 font-medium sm:px-4">Pehle: Plain Q4</th>
              <th scope="col" className="whitespace-nowrap px-3 py-3 font-medium sm:px-4">Baad me: {bestName}</th>
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
    </section>
  )
}
