import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  validateAdapter, withDefaults, registerAdapter, selectAdapter, _clearRegistryForTests, CONSERVATIVE_DEFAULTS
} from '../../src/main/portal/adapterContract.mjs'
import { CaseMachine, canTransition, isHandoff, STATES } from '../../src/main/portal/stateMachine.mjs'
import { SecretsVault, generatePassword } from '../../src/main/portal/vault.mjs'
import { AuditLog } from '../../src/main/portal/audit.mjs'
import {
  defaultPreferences, validatePreferences, qualifyingSlots, earliestQualifying, isImprovement
} from '../../src/main/portal/appointments.mjs'
import { capabilityReport, StripeIssuing, BrowserbaseLiveView } from '../../src/main/portal/providers.mjs'

// ---- Adapter contract ------------------------------------------------------
test('adapter validation rejects missing required fields', () => {
  const { ok, errors } = validateAdapter({ adapterId: 'x' })
  assert.equal(ok, false)
  assert.ok(errors.length > 0)
})

test('conservative defaults force applicant control + no auto submit', () => {
  const a = withDefaults({ adapterId: 'y' })
  assert.equal(a.paymentPolicy, 'applicant')
  assert.equal(a.representativeSubmission, 'applicant')
  assert.equal(a.personalDeclarationRequired, true)
  assert.equal(a.productionEnabled, false)
  assert.ok(CONSERVATIVE_DEFAULTS.prohibitedActions.includes('solve_captcha'))
})

test('adapter cannot be production-enabled without approval', () => {
  const base = minimalAdapter()
  const { ok, errors } = validateAdapter({ ...base, productionEnabled: true, productionApprovalStatus: 'tested' })
  assert.equal(ok, false)
  assert.ok(errors.some((e) => /production_approved/.test(e)))
})

test('adapter must allowlist its own URL hosts', () => {
  const base = minimalAdapter()
  const { ok, errors } = validateAdapter({ ...base, approvedDomains: [] })
  assert.equal(ok, false)
  assert.ok(errors.some((e) => /approvedDomains/.test(e)))
})

test('registry selects highest version for a route', () => {
  _clearRegistryForTests()
  registerAdapter({ ...minimalAdapter(), adapterId: 'm-v1', adapterVersion: 1 })
  registerAdapter({ ...minimalAdapter(), adapterId: 'm-v2', adapterVersion: 2 })
  const sel = selectAdapter('Mockland', 'tourist')
  assert.equal(sel.adapterVersion, 2)
})

function minimalAdapter() {
  return {
    adapterId: 'min', destinationCountry: 'Mockland', visaType: 'tourist', portalOperator: 'Op',
    approvedDomains: ['portal.mockland.gov.example'],
    registrationUrl: 'https://portal.mockland.gov.example/register',
    loginUrl: 'https://portal.mockland.gov.example/login',
    applicationUrl: 'https://portal.mockland.gov.example/apply',
    appointmentUrl: 'https://portal.mockland.gov.example/appointments',
    cancellationRisks: 'n/a', paymentSuccessSignal: 'a', appointmentSuccessSignal: 'b',
    submissionSuccessSignal: 'c', confirmationExtraction: 'd', receiptExtraction: 'e',
    resumeBehavior: 'f', portalPolicyReviewDate: '2026-07-21',
    driver: {}
  }
}

// ---- State machine ---------------------------------------------------------
test('state machine allows only declared transitions', () => {
  const m = new CaseMachine('DRAFT')
  m.transition('READY_FOR_REVIEW')
  assert.throws(() => m.transition('SUBMITTED'), /illegal transition/)
})

test('every non-terminal state can escape to failure/cancel', () => {
  for (const s of STATES) {
    if (['COMPLETED', 'CANCELLED', 'MANUAL_REVIEW_REQUIRED'].includes(s)) continue
    assert.ok(canTransition(s, 'CANCELLED'), `${s} -> CANCELLED`)
  }
})

test('handoff states are recognized', () => {
  assert.ok(isHandoff('CAPTCHA_ACTION_REQUIRED'))
  assert.ok(isHandoff('PAYMENT_ACTION_REQUIRED'))
  assert.ok(isHandoff('PERSONAL_DECLARATION_REQUIRED'))
  assert.equal(isHandoff('SUBMITTING'), false)
})

// ---- Vault / secrets -------------------------------------------------------
test('vault never persists plaintext; store returns only a ref', () => {
  const table = new Map()
  const v = new SecretsVault({ backend: 'node-aes-gcm', table })
  const secret = 'S3cret-Passw0rd!xyz'
  const { ref } = v.store(secret, { kind: 'portal_password' })
  assert.ok(ref.startsWith('vault://'))
  // The persisted table must not contain the plaintext anywhere.
  assert.equal(JSON.stringify([...table.entries()]).includes(secret), false)
  assert.equal(SecretsVault.persistsPlaintext(), false)
  // But an authorized reveal round-trips.
  assert.equal(v.reveal(ref), secret)
})

test('generated passwords meet policy and are unique', () => {
  const p1 = generatePassword({ minLength: 16 })
  const p2 = generatePassword({ minLength: 16 })
  assert.ok(p1.length >= 16)
  assert.notEqual(p1, p2)
  assert.match(p1, /[A-Z]/); assert.match(p1, /[a-z]/); assert.match(p1, /[0-9]/)
})

// ---- Audit -----------------------------------------------------------------
test('audit log redacts sensitive keys and stays append-only', () => {
  const log = new AuditLog()
  log.record('case1', 'test', { password: 'hunter2', ref: 'vault://x', nested: { token: 'abc' } })
  const entries = log.forCase('case1')
  assert.equal(entries.length, 1)
  assert.equal(log.containsPlaintext('hunter2'), false)
  assert.equal(log.containsPlaintext('abc'), false)
  assert.ok(log.containsPlaintext('vault://x')) // refs are safe to keep
  assert.throws(() => { entries[0].action = 'mutated' }, TypeError) // frozen
})

// ---- Appointment preferences + ranking ------------------------------------
const DAY = 86400000
function slot(id, loc, iso) { return { slotId: id, locationId: loc, startUtc: Date.parse(iso) } }

test('preference validation catches contradictions', () => {
  const p = defaultPreferences({ preferredWeekdays: [1], excludedWeekdays: [1], earliestUtc: 2, latestUtc: 1 })
  const problems = validatePreferences(p)
  assert.ok(problems.some((x) => /preferred and excluded/.test(x)))
  assert.ok(problems.some((x) => /earliest date/.test(x)))
})

test('earliest-qualifying honors blackout, weekday, advance notice, window', () => {
  const now = Date.parse('2026-10-01T00:00:00Z')
  const slots = [
    slot('a', 'L1', '2026-10-02T09:00:00Z'), // too soon (advance 2d)
    slot('b', 'L1', '2026-10-05T09:00:00Z'), // Monday, ok
    slot('c', 'L1', '2026-10-06T09:00:00Z'), // Tuesday
    slot('d', 'L2', '2026-10-07T09:00:00Z')  // wrong location
  ]
  const prefs = defaultPreferences({
    preferredLocation: 'L1', earliestUtc: now, latestUtc: now + 30 * DAY,
    minAdvanceMs: 2 * DAY, applicantTimeZone: 'UTC', blackoutDates: ['2026-10-06']
  })
  const e = earliestQualifying(slots, prefs, now)
  assert.equal(e.slotId, 'b') // a too soon, c blacked out, d wrong loc
})

test('timezone affects weekday/time interpretation', () => {
  const now = Date.parse('2026-10-01T00:00:00Z')
  // 2026-10-05T01:00Z is still Sunday evening in New York (UTC-4).
  const slots = [slot('x', 'L1', '2026-10-05T01:00:00Z')]
  const prefs = defaultPreferences({
    preferredLocation: 'L1', earliestUtc: now, latestUtc: now + 30 * DAY, minAdvanceMs: 0,
    preferredWeekdays: [1], applicantTimeZone: 'America/New_York'
  })
  // In NY this instant is Sunday (weekday 0), so it must NOT qualify for Monday-only.
  assert.equal(earliestQualifying(slots, prefs, now), null)
  // In UTC it is Monday, so it qualifies.
  const utcPrefs = { ...prefs, applicantTimeZone: 'UTC' }
  assert.equal(earliestQualifying(slots, utcPrefs, now)?.slotId, 'x')
})

test('reschedule improvement threshold', () => {
  const prefs = defaultPreferences({ minRescheduleImprovementMs: 3 * DAY })
  const cur = Date.parse('2026-10-20T09:00:00Z')
  assert.equal(isImprovement(cur, Date.parse('2026-10-18T09:00:00Z'), prefs), false) // 2 days < 3
  assert.equal(isImprovement(cur, Date.parse('2026-10-16T09:00:00Z'), prefs), true)  // 4 days >= 3
})

// ---- Providers / capability degradation ------------------------------------
test('missing credentials disable only the affected provider (safe fallbacks)', () => {
  const rep = capabilityReport()
  assert.equal(StripeIssuing.isConfigured(), false)
  assert.equal(BrowserbaseLiveView.isConfigured(), false)
  assert.equal(rep.fallbacks.payment, 'applicant_payment_window')
  assert.equal(rep.fallbacks.handoff, 'local_handoff')
  assert.equal(rep.fallbacks.authorization, 'in_app_authorization')
})
