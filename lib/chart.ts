import type { Model } from '@/lib/types'
import { compare, leaderboardRows } from '@/lib/verdict'

/** Where one quant sits on the shared KLD axis of one test set. All positions are percentages of the axis. */
export type Status = 'best' | 'worse' | 'same'

export interface ChartRow {
  variant: string
  label: string
  mean: number
  err: number
  dotPct: number
  leftPct: number // start of the +/- error line
  widthPct: number
  /** best (in this test set) / clearly worse than the best / not separable from the best */
  status: Status
  /** error range of the best quant, drawn as a faint band so overlap with it is visible */
  bandLeftPct: number
  bandWidthPct: number
}

export interface ChartPanel {
  domain: string
  rows: ChartRow[]
}

export interface ChartData {
  axis: { top: number; ticks: number[] }
  panels: ChartPanel[]
}

/** A round axis maximum and tick list (at most about 5 intervals) that covers `max`. */
export function niceAxis(max: number): { top: number; ticks: number[] } {
  const steps = [0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10]
  const safe = max > 0 ? max : 0.01
  const step = steps.find((s) => safe / s <= 5) ?? 10
  const top = Math.round(Math.ceil(safe / step - 1e-9) * step * 1e6) / 1e6
  const ticks: number[] = []
  for (let i = 0; i * step <= top + 1e-9; i++) ticks.push(Math.round(i * step * 1e6) / 1e6)
  return { top, ticks }
}

/**
 * Data for the "distance from FP16" chart of one model. Rows follow the leaderboard rank
 * (mean KLD) so the order is the same in every test set. Returns null if fewer than two
 * quants were measured on every test set (a one-row chart says nothing).
 */
export function chartData(model: Pick<Model, 'results' | 'variants_meta'>): ChartData | null {
  const lb = leaderboardRows(model)
  if (lb.length < 2 || !model.results) return null
  const results = model.results
  const domains = Object.keys(results)

  let max = 0
  for (const d of domains) for (const r of lb) max = Math.max(max, results[d][r.variant].kld[0] + results[d][r.variant].kld[1])
  const axis = niceAxis(max * 1.04)
  const pct = (x: number) => Math.min(100, Math.max(0, (x / axis.top) * 100))

  const panels = domains.map((d) => {
    const best = lb.reduce((a, r) => (results[d][r.variant].kld[0] < results[d][a.variant].kld[0] ? r : a))
    const bk = results[d][best.variant]
    const bandLeft = pct(bk.kld[0] - bk.kld[1])
    const bandRight = pct(bk.kld[0] + bk.kld[1])
    const rows: ChartRow[] = lb.map((r) => {
      const v = results[d][r.variant]
      const [mean, err] = v.kld
      const left = pct(mean - err)
      const right = pct(mean + err)
      const status: Status =
        r.variant === best.variant ? 'best' : compare(bk, v, 'kld').verdict === 'clear' ? 'worse' : 'same'
      return {
        variant: r.variant,
        label: r.label,
        mean,
        err,
        dotPct: pct(mean),
        leftPct: left,
        widthPct: right - left,
        status,
        bandLeftPct: bandLeft,
        bandWidthPct: bandRight - bandLeft,
      }
    })
    return { domain: d, rows }
  })
  return { axis, panels }
}