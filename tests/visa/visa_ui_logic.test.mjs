// Hermetic unit tests for the Visa Console's pure logic (no backend / DOM).
import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  confidenceLevel, fieldRows, documentReady, defaultPreferences, formatFee,
  handoffCopy, formatSlot, isTerminal, dateToMs, msToDate, newSession, resultDisposition
} from '../../src/renderer/src/lib/visaSession.js'
import { HANDOFF_UI, HANDOFF_SIGNAL, HANDOFF_COPY } from '../../src/renderer/src/lib/visaBackend.js'

test('resultDisposition never presents a MOCK completed case as real', () => {
  const d = resultDisposition({
    state: 'COMPLETED', execution_class: 'MOCK',
    disposition: { is_real_government_result: false, execution_class: 'MOCK',
      display_status: 'COMPLETED (MOCK — automated-test portal)', disclaimer: 'not real' },
    confirmation: { reference_no: 'REF-123' }
  })
  assert.equal(d.isReal, false)
  assert.equal(d.canClaimReal, false)
  assert.match(d.displayStatus, /MOCK/)
  assert.ok(d.disclaimer.length > 0)
})

test('resultDisposition treats a missing disposition as NOT real (fail safe)', () => {
  const d = resultDisposition({ state: 'COMPLETED', confirmation: { reference_no: 'X' } })
  assert.equal(d.isReal, false)
  assert.equal(d.executionClass, 'MOCK')
  assert.ok(d.disclaimer.length > 0)
})

test('resultDisposition presents a verified LIVE_PRODUCTION result as real', () => {
  const d = resultDisposition({
    state: 'COMPLETED', execution_class: 'LIVE_PRODUCTION',
    disposition: { is_real_government_result: true, execution_class: 'LIVE_PRODUCTION',
      display_status: 'COMPLETED', disclaimer: '' }
  })
  assert.equal(d.isReal, true)
  assert.equal(d.canClaimReal, true)
  assert.equal(d.displayStatus, 'COMPLETED')
  assert.equal(d.disclaimer, '')
})

test('confidenceLevel buckets by threshold', () => {
  assert.equal(confidenceLevel(0.95), 'ok')
  assert.equal(confidenceLevel(0.7), 'mid')
  assert.equal(confidenceLevel(0.4), 'bad')
  assert.equal(confidenceLevel(null), 'mid')
})

test('fieldRows sorts, buckets, flags conflicts + missing values', () => {
  const rows = fieldRows(
    { surname: { value: 'DOE', confidence: 0.99 },
      dob: { value: '', confidence: 0.5 },
      passport_no: { value: 'X123', confidence: 0.4 } },
    [{ keys: ['dob'] }])
  assert.deepEqual(rows.map((r) => r.key), ['dob', 'passport_no', 'surname'])
  const dob = rows.find((r) => r.key === 'dob')
  assert.equal(dob.conflict, true)
  assert.equal(dob.needsAttention, true)         // empty value
  const pp = rows.find((r) => r.key === 'passport_no')
  assert.equal(pp.level, 'bad')
  assert.equal(pp.needsAttention, true)           // low confidence
  const sn = rows.find((r) => r.key === 'surname')
  assert.equal(sn.needsAttention, false)
})

test('documentReady requires every field to have a value', () => {
  assert.equal(documentReady(fieldRows({ a: { value: '1' }, b: { value: '2' } })), true)
  assert.equal(documentReady(fieldRows({ a: { value: '' }, b: { value: '2' } })), false)
  assert.equal(documentReady([]), false)
})

test('defaultPreferences matches backend keys', () => {
  const p = defaultPreferences(1000)
  for (const k of ['preferredLocation', 'alternativeLocations', 'earliestUtc', 'latestUtc',
    'preferredWeekdays', 'minAdvanceMs', 'allowAutoBook', 'allowAutoReschedule',
    'askBeforeReschedule', 'applicantTimeZone', 'maxAutoReschedules', 'maxRescheduleFeeCents']) {
    assert.ok(k in p, `missing pref key ${k}`)
  }
  assert.equal(p.earliestUtc, 1000)
  assert.equal(p.allowAutoBook, false)          // conservative default
  assert.equal(p.askBeforeReschedule, true)
})

test('date <-> ms round trips', () => {
  const ms = dateToMs('2026-08-01')
  assert.equal(msToDate(ms), '2026-08-01')
  assert.equal(dateToMs(''), null)
  assert.equal(msToDate(null), '')
})

test('formatFee prefers display, falls back to cents', () => {
  assert.equal(formatFee({ display: 'US$80.00' }), 'US$80.00')
  assert.equal(formatFee({ amount: 8000, currency: 'USD' }), 'USD 80.00')
  assert.equal(formatFee(null), '')
})

test('handoffCopy returns label + guidance with safe fallback', () => {
  const c = handoffCopy(HANDOFF_COPY, 'payment')
  assert.ok(/pay/i.test(c.title))
  const fb = handoffCopy(HANDOFF_COPY, 'unknown_handoff')
  assert.ok(fb.title && fb.sub)
})

test('every handoff with a signal has UI + copy mappings', () => {
  for (const handoff of Object.keys(HANDOFF_SIGNAL)) {
    assert.ok(HANDOFF_UI[handoff], `handoff ${handoff} has no UI surface`)
    assert.ok(HANDOFF_COPY[handoff], `handoff ${handoff} has no copy`)
  }
  // The two personal steps Ellis must never automate map to explicit surfaces.
  assert.equal(HANDOFF_UI.personal_declaration, 'DeclarationModal')
  assert.equal(HANDOFF_UI.captcha, 'LiveViewModal')
})

test('isTerminal recognizes COMPLETED', () => {
  assert.equal(isTerminal('COMPLETED'), true)
  assert.equal(isTerminal('PAYMENT_ACTION_REQUIRED'), false)
})

test('formatSlot renders a date without throwing', () => {
  const s = formatSlot(Date.UTC(2026, 7, 1, 9, 0), 'UTC')
  assert.ok(typeof s === 'string' && s.length > 0)
})

test('newSession carries dev token + org/user', () => {
  const s = newSession({ orgId: 'acme', userId: 'u1' })
  assert.equal(s.token, 'dev-token')
  assert.equal(s.orgId, 'acme')
  assert.equal(s.userId, 'u1')
})
