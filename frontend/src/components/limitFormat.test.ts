// Run with `npm test` (Node's built-in test runner, TypeScript types stripped natively --
// no test framework dependency). Excluded from tsconfig.app.json, which has no Node types.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { formatLimit, formatUsd, limitLabel } from './limitFormat.ts'

test('OpenAI spend renders as dollars to two decimal places', () => {
  assert.equal(formatLimit(0.42, 1, 'usd'), '$0.42 / $1.00 (42%)')
  assert.equal(formatLimit(0, 1, 'usd'), '$0.00 / $1.00 (0%)')
  assert.equal(formatLimit(0.005, 1, 'usd'), '$0.01 / $1.00 (1%)')
  assert.equal(formatUsd(12.3), '$12.30')
})

test('spend over budget still reports the real percentage', () => {
  assert.equal(formatLimit(1.25, 1, 'usd'), '$1.25 / $1.00 (125%)')
})

test('Gemini requests render as a plain count', () => {
  assert.equal(formatLimit(8, 80, 'requests'), '8 / 80 (10%)')
})

test('Ollama is unmetered and disabled providers say so', () => {
  assert.equal(formatLimit(3, null, null), 'unmetered (local)')
  assert.equal(formatLimit(0, 80, 'requests', false), 'disabled')
  assert.equal(formatLimit(0, null, null, false), 'disabled')
})

test('a zero limit never divides by zero', () => {
  assert.match(formatLimit(0, 0, 'usd'), /^\$0\.00 \/ \$0\.00/)
})

test('bar labels follow the unit', () => {
  assert.equal(limitLabel('usd'), 'Daily spend')
  assert.equal(limitLabel('requests'), 'Daily requests')
  assert.equal(limitLabel(null), 'Usage')
})
