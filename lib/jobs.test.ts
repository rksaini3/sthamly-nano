import { test } from 'node:test'
import assert from 'node:assert/strict'
import fixtures from '../tests/fixtures/model_ids.json'
import { createErrorMessage, isTerminal, isUuid, jobErrorMessage, normalizeModelId, outOf100 } from './jobs'

test('normalizeModelId matches the shared fixtures (same ones the SQL and Python tests use)', () => {
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
  assert.match(createErrorMessage('something_else'), /gadbad/)
})

test('refused and failed jobs get plain messages', () => {
  assert.match(jobErrorMessage('refused', 'gated_or_private'), /gated ya private/)
  assert.match(jobErrorMessage('refused', 'weird'), /nahi liya/)
  assert.match(jobErrorMessage('failed', 'worker_timeout'), /ruk gaya/)
  assert.match(jobErrorMessage('failed', 'convert fail'), /convert fail/)
})

test('terminal states and the same-top-p count', () => {
  assert.ok(isTerminal('done') && isTerminal('failed') && isTerminal('refused'))
  assert.ok(!isTerminal('queued') && !isTerminal('running'))
  assert.equal(outOf100(87.48), 87)
  assert.equal(outOf100(85.5), 86)
})
