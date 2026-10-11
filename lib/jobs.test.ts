import { test } from 'node:test'
import assert from 'node:assert/strict'
import fixtures from '../tests/fixtures/model_ids.json'
import { createErrorMessage, isTerminal, isUuid, jobErrorMessage, normalizeModelId, outOf100, safeLink } from './jobs'

test('normalizeModelId matches the shared fixtures (the same file the SQL and Python tests use)', () => {
  assert.ok(fixtures.length > 25)
  for (const { input, expected } of fixtures as Array<{ input: string; expected: string | null }>) {
    assert.equal(normalizeModelId(input), expected, JSON.stringify(input))
  }
})

test('isUuid accepts uuids only', () => {
  assert.ok(isUuid('0b9d3a4e-6f1c-4c0e-9d57-2d6f3f6f9a11'))
  assert.ok(!isUuid('not-a-uuid'))
  assert.ok(!isUuid("0b9d3a4e-6f1c-4c0e-9d57-2d6f3f6f9a11'; drop"))
})

test('known error codes map to Hindi messages, unknown ones to a generic line', () => {
  assert.match(createErrorMessage('rate_limited'), /3 free audit/)
  assert.match(createErrorMessage('queue_full'), /line/)
  assert.match(createErrorMessage('invalid_model'), /owner\/model-name/)
  assert.match(createErrorMessage('something_else'), /gadbad/)
})

test('a refusal shows the worker sentence; a failure never leaks technical detail', () => {
  assert.equal(jobErrorMessage('refused', 'Ye model gated hai.'), 'Ye model gated hai.')
  assert.match(jobErrorMessage('refused', null), /nahi liya/)
  const failed = jobErrorMessage('failed', 'Job fail hua: audit.py fail hua: /root/models/x OOM hf_secret')
  assert.ok(!failed.includes('audit.py') && !failed.includes('/root') && !failed.includes('hf_'))
  assert.match(failed, /dobara try/)
})

test('terminal states and the same-top-p count', () => {
  assert.ok(isTerminal('done') && isTerminal('failed') && isTerminal('refused'))
  assert.ok(!isTerminal('queued') && !isTerminal('running'))
  assert.equal(outOf100(87.48), 87)
  assert.equal(outOf100(85.5), 86)
})

test('safeLink only lets http(s) and mailto links through', () => {
  assert.equal(safeLink('https://wa.me/911234567890'), 'https://wa.me/911234567890')
  assert.equal(safeLink(' mailto:hello@example.com '), 'mailto:hello@example.com')
  assert.equal(safeLink('javascript:alert(1)'), undefined)
  assert.equal(safeLink(''), undefined)
  assert.equal(safeLink(undefined), undefined)
})
