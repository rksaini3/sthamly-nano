import type { Model } from '@/lib/types'
import { chartData, type Status } from '@/lib/chart'
import { domainName } from '@/lib/labels'

// Static class names on purpose (Tailwind only generates classes it can see in the source).
const DOT: Record<Status, string> = { best: 'bg-good', worse: 'bg-bad', same: 'bg-flat' }
const LINE: Record<Status, string> = { best: 'bg-good', worse: 'bg-bad', same: 'bg-flat' }
const TAG: Record<Status, { text: string; cls: string } | null> = {
  best: { text: 'best', cls: 'text-good' },
  worse: { text: 'saaf peeche', cls: 'text-bad' },
  same: null,
}

function Legend() {
  const items: Array<[string, string]> = [
    ['bg-good', 'Sabse FP16 ke kareeb'],
    ['bg-flat', 'Best se alag nahi (bars overlap)'],
    ['bg-bad', 'Best se saaf peeche'],
  ]
  return (
    <ul className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted">
      {items.map(([cls, text]) => (
        <li key={text} className="flex items-center gap-1.5">
          <span className={`inline-block h-2.5 w-2.5 rounded-full ${cls}`} aria-hidden />
          {text}
        </li>
      ))}
    </ul>
  )
}

/**
 * "Distance from FP16" for one model. Left edge = identical to FP16 (KLD 0), so further left is
 * better. Each dot is a measurement, its line is the +/- error. A faint band repeats the best
 * quant's error range on every row: if a row's line touches the band, the difference is not proven.
 */
export default function KldChart({ model }: { model: Pick<Model, 'results' | 'variants_meta'> }) {
  const data = chartData(model)
  if (!data) return null
  const { axis, panels } = data

  return (
    <div className="space-y-8">
      {panels.map((panel) => (
        <section key={panel.domain} aria-label={`Test set: ${domainName(panel.domain)}`}>
          <h4 className="font-display font-semibold text-base mb-3">{domainName(panel.domain)}</h4>

          <ul className="space-y-3">
            {panel.rows.map((r) => {
              const tag = TAG[r.status]
              return (
                <li key={r.variant} className="grid gap-x-4 gap-y-1 sm:grid-cols-[11rem_minmax(0,1fr)] sm:items-center">
                  <div className="flex items-baseline justify-between gap-3 sm:block">
                    <p className="text-sm font-medium leading-tight">
                      {r.label}
                      {tag && <span className={`ml-2 text-xs font-normal ${tag.cls}`}>{tag.text}</span>}
                    </p>
                    <p className="text-xs text-muted">
                      {r.mean.toFixed(4)} ± {r.err.toFixed(4)}
                    </p>
                  </div>

                  <div className="relative h-7" aria-hidden>
                    {axis.ticks.map((t) => (
                      <span
                        key={t}
                        className="absolute inset-y-0 w-px bg-line"
                        style={{ left: `${(t / axis.top) * 100}%` }}
                      />
                    ))}
                    {r.status !== 'best' && (
                      <span
                        className="absolute inset-y-1 rounded-sm bg-good/15"
                        style={{ left: `${r.bandLeftPct}%`, width: `${Math.max(r.bandWidthPct, 0.6)}%` }}
                      />
                    )}
                    <span
                      className={`absolute top-1/2 h-0.5 -translate-y-1/2 ${LINE[r.status]}`}
                      style={{ left: `${r.leftPct}%`, width: `${Math.max(r.widthPct, 0.4)}%` }}
                    />
                    <span
                      className={`absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-surface ${DOT[r.status]}`}
                      style={{ left: `${r.dotPct}%` }}
                    />
                  </div>
                </li>
              )
            })}
          </ul>

          <div className="mt-2 grid gap-x-4 sm:grid-cols-[11rem_minmax(0,1fr)]">
            <span className="hidden sm:block" />
            <div className="relative h-5 text-xs text-muted" aria-hidden>
              {axis.ticks.map((t, i) => (
                <span
                  key={t}
                  className={`absolute ${i === 0 ? '' : i === axis.ticks.length - 1 ? '-translate-x-full' : '-translate-x-1/2'}`}
                  style={{ left: `${(t / axis.top) * 100}%` }}
                >
                  {t === 0 ? '0 = FP16' : t}
                </span>
              ))}
            </div>
          </div>
        </section>
      ))}

      <div className="space-y-1">
        <p className="text-xs text-muted">
          Jitna baayein, utna FP16 ke kareeb (KLD kam = behtar). Dot = naap, line = ± error.
        </p>
        <Legend />
      </div>
    </div>
  )
}