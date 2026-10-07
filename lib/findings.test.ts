import { test } from 'node:test'
import assert from 'node:assert/strict'
import { findings, pairFinding } from './findings'
import type { Results } from './types'

const r: Results = {
  wiki: {
    standard: { kld: [0.0642, 0.003], rms: [9.91, 0.39], top: [87.48, 0.6] },
    hindi60: { kld: [0.0509, 0.0018], rms: [8.41, 0.28], top: [88.95, 0.57] },
    english60: { kld: [0.0545, 0.0019], rms: [9.06, 0.31], top: [88.43, 0.58] },
  },
  sangraha: {
    standard: { kld: [0.0635, 0.0019], rms: [9.27, 0.28], top: [85.39, 0.64] },
    hindi60: { kld: [0.0573, 0.0024], rms: [8.9, 0.33], top: [86.08, 0.63] },
    english60: { kld: [0.0627, 0.0023], rms: [9.39, 0.35], top: [85.75, 0.63] },
  },
}

test('Hindi imatrix vs plain Q4 is a clear win on both sets, with the real percentages', () => {
  const f = pairFinding(r, 'hindi60', 'standard')!
  assert.equal(f.tone, 'clear')
  assert.match(f.text, /wiki 21% kam/)
  assert.match(f.text, /sangraha 10% kam/)
})

test('English imatrix vs plain Q4 is only clear on wiki: said honestly', () => {
  const f = pairFinding(r, 'english60', 'standard')!
  assert.equal(f.tone, 'hint')
  assert.match(f.text, /wiki par saaf behtar hai, sangraha par saaf nahi/)
})

test('Hindi vs English is "no clear difference" and the claim is refused', () => {
  const f = pairFinding(r, 'hindi60', 'english60', 'NOTE')!
  assert.equal(f.tone, 'none')
  assert.match(f.text, /NOTE$/)
})

test('findings() lists the three headline comparisons; nothing for empty or unrelated data', () => {
  assert.equal(findings(r).length, 3)
  assert.deepEqual(findings(null), [])
  assert.deepEqual(findings({ wiki: { foo: r.wiki.standard } }), [])
})