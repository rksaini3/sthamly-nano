import { test } from 'node:test'
import assert from 'node:assert/strict'
import { chartData, niceAxis } from './chart'
import type { Results } from './types'

// Real numbers from results/results_trl4_Qwen__Qwen2.5-1.5B-Instruct.json
const r: Results = {
  wiki: {
    standard: { kld: [0.0642, 0.003], rms: [9.91, 0.39], top: [87.48, 0.6] },
    hindi60: { kld: [0.0509, 0.0018], rms: [8.41, 0.28], top: [88.95, 0.57] },
    english60: { kld: [0.0545, 0.0019], rms: [9.06, 0.31], top: [88.43, 0.58] },
    mixed60: { kld: [0.0498, 0.0018], rms: [8.31, 0.28], top: [88.79, 0.57] },
  },
  sangraha: {
    standard: { kld: [0.0635, 0.0019], rms: [9.27, 0.28], top: [85.39, 0.64] },
    hindi60: { kld: [0.0573, 0.0024], rms: [8.9, 0.33], top: [86.08, 0.63] },
    english60: { kld: [0.0627, 0.0023], rms: [9.39, 0.35], top: [85.75, 0.63] },
    mixed60: { kld: [0.0586, 0.0019], rms: [8.88, 0.3], top: [85.78, 0.63] },
  },
}

test('niceAxis gives round ticks that cover the max', () => {
  const a = niceAxis(0.0698)
  assert.equal(a.top, 0.08)
  assert.deepEqual(a.ticks, [0, 0.02, 0.04, 0.06, 0.08])
  assert.ok(niceAxis(0.0001).ticks.length >= 2)
  assert.ok(niceAxis(0).top > 0)
  assert.ok(niceAxis(3.3).top >= 3.3)
})

test('chart rows follow leaderboard rank and mark who is separable from the best', () => {
  const c = chartData({ results: r, variants_meta: null })!
  assert.deepEqual(c.panels.map((p) => p.domain), ['wiki', 'sangraha'])
  const wiki = c.panels[0].rows
  assert.deepEqual(wiki.map((x) => x.variant), ['hindi60', 'mixed60', 'english60', 'standard'])
  // wiki: mixed60 is the best on this test set; plain Q4 is clearly behind it
  const status = Object.fromEntries(wiki.map((x) => [x.variant, x.status]))
  assert.equal(status.mixed60, 'best')
  assert.equal(status.standard, 'worse')
  assert.equal(status.hindi60, 'same') // Hindi vs best: error bars overlap
  // sangraha: Hindi is the best, plain Q4 clearly behind, English not separable
  const sang = Object.fromEntries(c.panels[1].rows.map((x) => [x.variant, x.status]))
  assert.equal(sang.hindi60, 'best')
  assert.equal(sang.standard, 'worse')
  assert.equal(sang.english60, 'same')
})

test('chart positions stay inside the axis', () => {
  const c = chartData({ results: r, variants_meta: null })!
  for (const p of c.panels) {
    for (const row of p.rows) {
      assert.ok(row.dotPct >= 0 && row.dotPct <= 100)
      assert.ok(row.leftPct >= 0 && row.leftPct + row.widthPct <= 100 + 1e-9)
      assert.ok(row.leftPct <= row.dotPct && row.dotPct <= row.leftPct + row.widthPct)
    }
  }
})

test('chartData is null when there is nothing to compare', () => {
  assert.equal(chartData({ results: null, variants_meta: null }), null)
  assert.equal(chartData({ results: { wiki: { standard: r.wiki.standard } }, variants_meta: null }), null)
})