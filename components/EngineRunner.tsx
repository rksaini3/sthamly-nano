'use client'

import { useState } from 'react'
import Terminal from '@/components/Terminal'

const API = process.env.NEXT_PUBLIC_ENGINE_API_URL ?? 'http://localhost:8000'

type Result = {
  precision: string
  precision_note?: string
  tech: string
  accuracy: number
  accuracy_se: number
  accuracy_verdict: string
  energy_uj: number
  time_us: number
  area_mm2: number
  weight_bytes: number
  energy_saving_pct?: number | null
  speedup_x?: number | null
  size_reduction_x?: number | null
}

type Sim = {
  cycles_per_image?: number | null
  clock_mhz_assumed?: number
  latency_us_at_assumed_clock?: number | null
  lanes?: number | null
  images_checked?: number | null
  logit_mismatches?: number | null
  pred_mismatches?: number | null
  hw_correct?: number | null
  hw_total?: number | null
  bit_exact: boolean
  label: string
}

type Frame = {
  layer_index: number
  layer_name: string
  kind: string
  active_pes: number
  passes: number
  cells: (number | null)[]
}

type Data = {
  report: {
    model: string
    total_macs: number
    total_params: number
    baseline: string
    results: Result[]
    hardware_simulation?: Sim | null
    not_claimed: string[]
  }
  placement: {
    grid: { rows: number; cols: number; size: number }
    avg_utilization: number
    frames: Frame[]
  }
  verilog: string
}

const num = (v: number | null | undefined, d = 2) => (v == null ? '-' : v.toFixed(d))

export default function EngineRunner() {
  const [rows, setRows] = useState(8)
  const [cols, setCols] = useState(8)
  const [bits, setBits] = useState(8)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [data, setData] = useState<Data | null>(null)
  const [frameIdx, setFrameIdx] = useState(0)

  async function run() {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${API}/run?rows=${rows}&cols=${cols}&bits=${bits}`)
      if (!res.ok) throw new Error(`Backend error ${res.status}`)
      setData((await res.json()) as Data)
      setFrameIdx(0)
    } catch (e) {
      setError(
        e instanceof Error
          ? `${e.message}. Kya backend chal raha hai? (uvicorn engine.api:app --port 8000)`
          : 'Unknown error',
      )
    } finally {
      setLoading(false)
    }
  }

  function downloadVerilog() {
    if (!data) return
    const url = URL.createObjectURL(new Blob([data.verilog], { type: 'text/plain' }))
    const a = document.createElement('a')
    a.href = url
    a.download = 'sthamly_top.v'
    a.click()
    URL.revokeObjectURL(url)
  }

  const frame = data?.placement.frames[frameIdx]
  const sim = data?.report.hardware_simulation ?? null
  const notes: { precision: string; note: string }[] = []
  if (data) {
    const seen = new Set<string>()
    for (const r of data.report.results) {
      if (r.precision_note && !seen.has(r.precision)) {
        seen.add(r.precision)
        notes.push({ precision: r.precision, note: r.precision_note })
      }
    }
  }
  const input = 'w-20 rounded border border-line bg-surface px-2 py-1 text-ink'

  return (
    <div className="space-y-8">
      <Terminal title="sthamly · engine">
        <div className="flex flex-wrap items-end gap-4 text-sm">
          <label className="flex flex-col gap-1 text-muted">
            Rows
            <input type="number" min={1} max={32} value={rows}
              onChange={(e) => setRows(Number(e.target.value) || 1)} className={input} />
          </label>
          <label className="flex flex-col gap-1 text-muted">
            Cols
            <input type="number" min={1} max={32} value={cols}
              onChange={(e) => setCols(Number(e.target.value) || 1)} className={input} />
          </label>
          <label className="flex flex-col gap-1 text-muted">
            Bits
            <select value={bits} onChange={(e) => setBits(Number(e.target.value))} className={input}>
              <option value={8}>INT8</option>
              <option value={4}>INT4</option>
            </select>
          </label>
          <button onClick={run} disabled={loading}
            className="rounded bg-brand px-4 py-2 font-semibold text-brand-ink disabled:opacity-50">
            {loading ? 'Chal raha hai...' : 'Compile & Simulate'}
          </button>
        </div>
        <p className="mt-3 text-xs text-muted">Model: sample MNIST MLP (784-128-10).</p>
        {error && <p className="mt-3 text-sm text-bad">{error}</p>}
      </Terminal>

      {data && (
        <>
          <section>
            <h2 className="font-display text-2xl font-bold tracking-tight">Results</h2>
            <p className="mt-1 text-sm text-muted">
              Accuracy: naapi hui. Energy, time, area: <b>simulated estimate</b>. Baseline: {data.report.baseline}.
              Graphene = hypothetical what-if.
            </p>
            <div className="mt-4 overflow-x-auto">
              <table className="w-full min-w-[640px] text-left text-sm">
                <thead className="text-muted">
                  <tr>
                    <th className="py-2 pr-3">Precision</th>
                    <th className="pr-3">Tech</th>
                    <th className="pr-3">Accuracy</th>
                    <th className="pr-3">Energy (uJ)</th>
                    <th className="pr-3">Time (us)</th>
                    <th className="pr-3">Energy save %</th>
                    <th className="pr-3">Speed x</th>
                    <th className="pr-3">Size x</th>
                  </tr>
                </thead>
                <tbody>
                  {data.report.results.map((r) => (
                    <tr key={`${r.precision}-${r.tech}`} className="border-t border-line">
                      <td className="py-2 pr-3 font-mono">{r.precision}</td>
                      <td className="pr-3">{r.tech === 'graphene_what_if' ? 'graphene (what-if)' : 'silicon FPGA'}</td>
                      <td className="pr-3">
                        {(r.accuracy * 100).toFixed(2)}% ± {(r.accuracy_se * 100).toFixed(2)}
                        <span className="ml-1 text-xs text-muted">({r.accuracy_verdict})</span>
                      </td>
                      <td className="pr-3">{num(r.energy_uj)}</td>
                      <td className="pr-3">{num(r.time_us, 1)}</td>
                      <td className="pr-3">{num(r.energy_saving_pct, 1)}</td>
                      <td className="pr-3">{num(r.speedup_x)}</td>
                      <td className="pr-3">{num(r.size_reduction_x, 1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {notes.length > 0 && (
              <ul className="mt-4 space-y-1 text-xs text-muted">
                {notes.map((n) => (
                  <li key={n.precision}>
                    <b className="font-mono text-ink">{n.precision}</b>: {n.note}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section>
            <h2 className="font-display text-2xl font-bold tracking-tight">Hardware simulation</h2>
            {sim ? (
              <div className="mt-3 space-y-1 text-sm">
                <p>
                  Bit-exact vs Python integer model:{' '}
                  <b className={sim.bit_exact ? 'text-good' : 'text-bad'}>{sim.bit_exact ? 'YES' : 'NO'}</b>
                </p>
                <p className="text-muted">
                  Images checked: {sim.images_checked ?? '-'} · logit mismatches: {sim.logit_mismatches ?? '-'} ·
                  prediction mismatches: {sim.pred_mismatches ?? '-'}
                </p>
                {sim.hw_correct != null && (
                  <p className="text-muted">
                    Correct on those images: {sim.hw_correct}/{sim.hw_total}
                  </p>
                )}
                {sim.cycles_per_image ? (
                  <p className="text-muted">
                    Cycles per image: {sim.cycles_per_image.toLocaleString()}
                    {sim.latency_us_at_assumed_clock != null && sim.clock_mhz_assumed
                      ? ` (about ${sim.latency_us_at_assumed_clock.toFixed(1)} us at an assumed ${sim.clock_mhz_assumed.toFixed(0)} MHz)`
                      : ''}
                  </p>
                ) : null}
                <p className="text-xs text-muted">{sim.label}</p>
              </div>
            ) : (
              <p className="mt-2 text-sm text-muted">
                Abhi koi simulation result nahi hai. Terminal me chalao: <code>python engine/run_infer.py</code> phir{' '}
                <code>bash hardware/testbench/run_infer.sh</code>, aur backend dobara start karo.
              </p>
            )}
          </section>

          <section>
            <h2 className="font-display text-2xl font-bold tracking-tight">Grid mapping</h2>
            <p className="mt-1 text-sm text-muted">
              Har layer ke liye grid ka alag frame. Avg utilization: {(data.placement.avg_utilization * 100).toFixed(0)}%.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              {data.placement.frames.map((f, i) => (
                <button key={f.layer_index} onClick={() => setFrameIdx(i)}
                  className={`rounded-full border px-3 py-1 text-sm ${
                    i === frameIdx ? 'border-brand text-brand' : 'border-line text-muted'
                  }`}>
                  {f.layer_name} ({f.kind})
                </button>
              ))}
            </div>
            {frame && (
              <div className="mt-4">
                <div className="grid max-w-sm gap-[2px]"
                  style={{ gridTemplateColumns: `repeat(${data.placement.grid.cols}, minmax(0, 1fr))` }}>
                  {frame.cells.map((c, i) => (
                    <div key={i} className={`aspect-square rounded-sm ${c === null ? 'bg-line' : 'bg-brand'}`} />
                  ))}
                </div>
                <p className="mt-2 text-sm text-muted">
                  {frame.active_pes} PEs active, frame {frame.passes} baar dohraya jata hai.
                </p>
              </div>
            )}
          </section>

          <section>
            <div className="flex flex-wrap gap-3">
              <button onClick={downloadVerilog}
                className="rounded border border-brand px-4 py-2 font-semibold text-brand">
                Download Verilog (.v)
              </button>
              <a
                href={`${API}/report.pdf?rows=${data.placement.grid.rows}&cols=${data.placement.grid.cols}`}
                className="rounded border border-brand px-4 py-2 font-semibold text-brand"
              >
                Download PDF report
              </a>
            </div>
            <ul className="mt-6 list-disc space-y-1 pl-5 text-sm text-muted">
              {data.report.not_claimed.map((t) => (
                <li key={t}>{t}</li>
              ))}
            </ul>
          </section>
        </>
      )}
    </div>
  )
}