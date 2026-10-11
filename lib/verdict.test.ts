import { test } from 'node:test'
import assert from 'node:assert/strict'
import { compare, kldReductionPct, leaderboardRows, orderedVariants, variantLabel } from './verdict'
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

test('Hindi imatrix clearly beats plain Q4 in both domains', () => {
  assert.equal(compare(r.wiki.hindi60, r.wiki.standard).verdict, 'clear')
  assert.equal(compare(r.sangraha.hindi60, r.sangraha.standard).verdict, 'clear')
})

test('English imatrix beats plain Q4 clearly on wiki but NOT on sangraha', () => {
  assert.equal(compare(r.wiki.english60, r.wiki.standard).verdict, 'clear')
  assert.equal(compare(r.sangraha.english60, r.sangraha.standard).verdict, 'hint')
})

test('Hindi vs English is never "clear" (the honest finding)', () => {
  for (const d of ['wiki', 'sangraha']) {
    assert.notEqual(compare(r[d].hindi60, r[d].english60).verdict, 'clear')
  }
})

test('a worse variant is "none"', () => {
  assert.equal(compare(r.wiki.standard, r.wiki.hindi60).verdict, 'none')
})

test('same-top-p is higher-is-better', () => {
  const c = compare(r.wiki.hindi60, r.wiki.standard, 'top')
  assert.ok(c.gain > 0)
  assert.equal(c.verdict, 'hint') // 1.47 gain vs 2*0.82 error: a hint, not clear
})

test('kldReductionPct matches the published model card (21% wiki, 10% sangraha)', () => {
  const g = kldReductionPct(r)
  assert.equal(Math.round(g.wiki), 21)
  assert.equal(Math.round(g.sangraha), 10)
})

test('kldReductionPct tolerates missing results', () => {
  assert.deepEqual(kldReductionPct(null), {})
})

test('variantLabel falls back to the raw key for custom (audit) labels', () => {
  assert.equal(variantLabel('hindi60'), 'Hindi imatrix')
  assert.equal(variantLabel('bartowski-Q4_K_M'), 'bartowski-Q4_K_M')
})

test('orderedVariants puts known variants first, custom ones after', () => {
  assert.deepEqual(orderedVariants({ zzz: 1, mixed60: 1, standard: 1, aaa: 1 }), ['standard', 'mixed60', 'zzz', 'aaa'])
})

test('leaderboard ranks by mean KLD and counts clear separation per test set', () => {
  const rows = leaderboardRows({ results: r, variants_meta: { standard: { size_gb: 1.12 } } })
  assert.deepEqual(rows.map((x) => x.variant), ['hindi60', 'mixed60', 'english60', 'standard'])
  const by = Object.fromEntries(rows.map((x) => [x.variant, x]))
  assert.equal(by.hindi60.isBest, true)
  assert.equal(by.standard.behindBest, 2) // clearly behind the best on both test sets
  assert.equal(by.english60.behindBest, 0) // not clearly behind: the honest "Hindi vs English" result
  assert.equal(by.mixed60.behindBest, 0)
  assert.equal(by.standard.sizeGb, 1.12)
  assert.equal(by.hindi60.sizeGb, null)
  assert.ok(Math.abs(by.hindi60.kld - 0.0541) < 1e-9)
})

test('leaderboard ignores variants that are missing on some test set, and empty results', () => {
  const partial: Results = { wiki: { ...r.wiki }, sangraha: { standard: r.sangraha.standard } }
  assert.deepEqual(leaderboardRows({ results: partial, variants_meta: null }).map((x) => x.variant), ['standard'])
  assert.deepEqual(leaderboardRows({ results: null, variants_meta: null }), [])
  assert.deepEqual(leaderboardRows({ results: {}, variants_meta: null }), [])
})
