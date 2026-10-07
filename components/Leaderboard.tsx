import type { Model } from '@/lib/types'
import { leaderboardRows } from '@/lib/verdict'
import KldChart from '@/components/KldChart'

/** How a row stands against the best row of the same model (text + color, never color alone). */
function standing(isBest: boolean, behind: number, total: number): { text: string; cls: string } {
  if (isBest) return { text: 'Best', cls: 'text-good' }
  if (behind === total) return { text: `Saaf peeche (${behind}/${total} test sets)`, cls: 'text-bad' }
  if (behind > 0) return { text: `Kuch me peeche (${behind}/${total} test sets)`, cls: 'text-warn' }
  return { text: 'Best se alag nahi', cls: 'text-muted' }
}

export default function Leaderboard({ models }: { models: Model[] | null }) {
  const tables = (models ?? [])
    .map((m) => ({ model: m, rows: leaderboardRows(m) }))
    .filter((t) => t.rows.length > 0)

  if (tables.length === 0) {
    return (
      <div className="rounded-md border border-line bg-surface p-6">
        <p className="font-medium">Abhi koi result nahi hai</p>
        <p className="text-sm text-muted mt-1">
          Pehla result aate hi yahan chart aur ranking dikhegi. Result jodne ke liye apne computer par
          <code className="mx-1 rounded bg-bg px-1.5 py-0.5 text-ink">python scripts/audit.py --upload --hf-repo owner/name</code>
          ya
          <code className="mx-1 rounded bg-bg px-1.5 py-0.5 text-ink">python scripts/publish_mvp.py --go</code>
          chalao. Koi number pehle se nahi dikhaya jaata.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-16">
      {tables.map(({ model, rows }) => (
        <article key={model.id} aria-labelledby={`m-${model.id}`}>
          <h2 id={`m-${model.id}`} className="font-display text-2xl font-semibold leading-snug">
            {model.model_name}
          </h2>
          <p className="text-sm text-muted mb-6">
            {model.base_model ? `Base: ${model.base_model} · ` : ''}
            {model.fp16_size_gb ? `FP16 reference ${model.fp16_size_gb} GB` : 'FP16 reference'} · {rows.length} quants
          </p>

          <div className="rounded-lg border border-line bg-surface p-4 sm:p-6">
            <KldChart model={model} />
          </div>

          <h3 className="font-display font-semibold text-lg mt-8 mb-3">Ranking</h3>
          <div className="overflow-x-auto rounded-lg border border-line bg-surface">
            <table className="w-full text-sm">
              <caption className="sr-only">{model.model_name}: quants ko mean KLD se rank kiya gaya (kam = behtar)</caption>
              <thead className="text-left text-muted border-b border-line">
                <tr>
                  <th scope="col" className="px-3 sm:px-4 py-3 font-medium w-8">#</th>
                  <th scope="col" className="px-3 sm:px-4 py-3 font-medium">Quant</th>
                  <th scope="col" className="px-3 sm:px-4 py-3 font-medium hidden sm:table-cell">Size</th>
                  <th scope="col" className="px-3 sm:px-4 py-3 font-medium whitespace-nowrap">Mean KLD</th>
                  <th scope="col" className="px-3 sm:px-4 py-3 font-medium hidden sm:table-cell">Best ke muqable</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((r, i) => {
                  const s = standing(r.isBest, r.behindBest, r.domains)
                  return (
                    <tr key={r.variant}>
                      <td className="px-3 sm:px-4 py-3 text-muted">{i + 1}</td>
                      <td className="px-3 sm:px-4 py-3">
                        <span className="font-medium">{r.label}</span>
                        <span className={`block sm:hidden text-xs ${s.cls}`}>{s.text}</span>
                      </td>
                      <td className="px-3 sm:px-4 py-3 hidden sm:table-cell text-muted">
                        {r.sizeGb ? `${r.sizeGb} GB` : '—'}
                      </td>
                      <td className="px-3 sm:px-4 py-3 whitespace-nowrap">
                        {r.kld.toFixed(4)} <span className="text-muted">± {r.kldErr.toFixed(4)}</span>
                      </td>
                      <td className={`px-3 sm:px-4 py-3 hidden sm:table-cell ${s.cls}`}>{s.text}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-muted mt-3 max-w-prose">
            Mean KLD = test sets ka average. Rank sirf isi model ke andar hai (wahi FP16 reference): alag models ke
            KLD aapas me mat milao.
          </p>
        </article>
      ))}
    </div>
  )
}