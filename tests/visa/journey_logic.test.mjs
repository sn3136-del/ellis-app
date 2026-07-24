// Hermetic unit tests for the applicant-journey pure logic (no backend / DOM):
// guidance continuation, passport-profile display + prefill, derived age, and
// the route-checklist helpers. Mirrors of backend rules are display-only — the
// backend stays authoritative — but the mapping must agree.
import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  continuationMeta, CONTINUATION_KIND, deriveAge, profileRows,
  prefillWithEdits, checklistStatusMeta, checklistCounts
} from '../../src/renderer/src/lib/intake.js'
import { STRINGS, SUPPORTED } from '../../src/renderer/src/lib/i18n.js'

// ---------------------------------------------------------------------------
// continuationMeta — the primary CTA after Kimi guidance. No normal route may
// dead-end at the guidance page.
test('visa-required guidance continues into the visa application', () => {
  const m = continuationMeta({ status: 'KIMI_PRIMARY', ai_generated: true,
    guidance: { disposition: 'VISA_REQUIRED' } })
  assert.equal(m.blocked, false)
  assert.equal(m.kind, 'visa_application')
  assert.equal(m.ctaKey, 'guidance.continue.visa')
})

test('visa-exempt guidance continues into entry preparation, never consular', () => {
  const m = continuationMeta({ status: 'KIMI_PRIMARY', ai_generated: true,
    guidance: { disposition: 'VISA_EXEMPT' } })
  assert.equal(m.blocked, false)
  assert.equal(m.kind, 'entry_preparation')
  assert.equal(m.ctaKey, 'guidance.continue.exempt')
  // The CTA copy is entry preparation — not a consular visa application.
  for (const lang of SUPPORTED) {
    const label = STRINGS[lang]['guidance.continue.exempt']
    assert.ok(label && !/consular|領事|领事/i.test(label))
  }
})

test('electronic-authorization guidance continues correctly', () => {
  const m = continuationMeta({ status: 'KIMI_PRIMARY', ai_generated: true,
    guidance: { disposition: 'ELECTRONIC_AUTHORIZATION_REQUIRED' } })
  assert.equal(m.kind, 'authorization_application')
  assert.equal(m.ctaKey, 'guidance.continue.eta')
})

test('conditional guidance continues with available guidance (partial)', () => {
  const m = continuationMeta({ status: 'KIMI_PRIMARY', ai_generated: true,
    guidance: { disposition: 'CONDITIONAL' } })
  assert.equal(m.blocked, false)
  assert.equal(m.partial, true)
  assert.equal(m.ctaKey, 'guidance.continue.partial')
})

test('uncertain guidance with a known disposition continues with gaps listed', () => {
  const m = continuationMeta({ status: 'KIMI_UNCERTAIN',
    guidance: { disposition: 'VISA_EXEMPT' }, missing_fields: ['processing_time'] })
  assert.equal(m.blocked, false)
  assert.equal(m.kind, 'entry_preparation')
  assert.equal(m.partial, true)
  assert.deepEqual(m.blockers, ['processing_time'])
})

test('guidance without a disposition is blocked with the precise blocker', () => {
  const m = continuationMeta({ status: 'KIMI_UNCERTAIN', guidance: {},
    missing_fields: ['disposition'] })
  assert.equal(m.blocked, true)
  assert.ok(m.blockers.includes('disposition'))
  // Unknown/garbage shapes fail safe to blocked, never to a CTA.
  assert.equal(continuationMeta(null).blocked, true)
  assert.equal(continuationMeta({}).blocked, true)
  assert.equal(continuationMeta({ status: 'KIMI_PRIMARY',
    guidance: { disposition: 'SOMETHING_NEW' } }).blocked, true)
})

test('every continuation CTA key resolves in every locale', () => {
  const keys = [...new Set(Object.values(CONTINUATION_KIND).map((e) => e.ctaKey))]
  keys.push('guidance.continue.partial', 'guidance.continue.blockedTitle', 'guidance.continuing')
  for (const lang of SUPPORTED) {
    for (const k of keys) {
      assert.ok(STRINGS[lang][k], `${lang} missing ${k}`)
    }
  }
})

// ---------------------------------------------------------------------------
// deriveAge — age is ALWAYS calculated from the date of birth, never typed by
// OCR or a model.
test('age derives correctly from date of birth', () => {
  assert.equal(deriveAge('1990-01-15', '2026-07-24'), 36)
  assert.equal(deriveAge('1990-08-15', '2026-07-24'), 35)   // birthday not yet passed
  assert.equal(deriveAge('1990-07-24', '2026-07-24'), 36)   // birthday today
  assert.equal(deriveAge('2000-02-29', '2026-02-28'), 25)   // leap-day birth
  assert.equal(deriveAge('2000-02-29', '2026-03-01'), 26)
  assert.equal(deriveAge('garbage', '2026-01-01'), null)
  assert.equal(deriveAge('1990-01-15', ''), null)
  assert.equal(deriveAge('2050-01-01', '2026-01-01'), null) // future DOB -> honest null
})

// ---------------------------------------------------------------------------
// profileRows — extracted-passport preview rows with provenance.
const PROFILE = {
  mrz_valid: true,
  fields: {
    surname: { value: 'DOE', confidence: 0.99, source: 'mrz', needs_confirmation: false },
    given_names: { value: 'JOHN', confidence: 0.99, source: 'mrz', needs_confirmation: true,
      note: 'printed zone disagrees with the machine-readable zone' },
    passport_number: { value: 'X1234567', confidence: 0.98, source: 'mrz', needs_confirmation: false },
    birth_date: { value: '1990-01-15', confidence: 0.98, source: 'mrz', needs_confirmation: false },
    _age: { value: '36', confidence: 1, source: 'derived', needs_confirmation: false },
    issue_date: { value: '2023-01-01', confidence: 0.6, source: 'ocr_text', needs_confirmation: true }
  },
  prefill: {
    passport_nationality: 'USA', passport_issuing_country: 'USA',
    travel_document_type: 'ordinary_passport', surname: 'DOE', given_names: 'JOHN',
    full_name: 'JOHN DOE', passport_number: 'X1234567', birth_date: '1990-01-15',
    passport_expiry_date: '2033-01-01', age: 36
  }
}

test('profileRows carries provenance, confidence and confirmation flags', () => {
  const rows = profileRows(PROFILE)
  const byKey = Object.fromEntries(rows.map((r) => [r.key, r]))
  assert.equal(byKey.surname.source, 'mrz')
  assert.equal(byKey.surname.level, 'ok')
  assert.equal(byKey.given_names.needsConfirm, true)
  assert.equal(byKey.given_names.level, 'bad')       // conflicting field is highlighted
  assert.equal(byKey._age.source, 'derived')
  assert.equal(byKey.issue_date.source, 'ocr')
  // Missing fields simply do not appear — never invented.
  assert.ok(!byKey.place_of_birth)
  // Every row label resolves in every locale.
  for (const lang of SUPPORTED) {
    for (const r of rows) assert.ok(STRINGS[lang][r.labelKey], `${lang} ${r.labelKey}`)
  }
  // Garbage-safe.
  assert.deepEqual(profileRows(null), [])
  assert.deepEqual(profileRows({}), [])
})

test('prefillWithEdits applies applicant corrections and re-derives age', () => {
  const out = prefillWithEdits(PROFILE, { given_names: 'JOHNNY', birth_date: '1991-01-15' })
  assert.equal(out.given_names, 'JOHNNY')
  assert.equal(out.birth_date, '1991-01-15')
  const expected = deriveAge('1991-01-15', new Date().toISOString().slice(0, 10))
  assert.equal(out.age, expected)                     // age follows the edited DOB
  assert.equal(out.passport_number, 'X1234567')       // untouched values survive
  // Empty edits fall back to extracted values; unknown keys are ignored.
  const out2 = prefillWithEdits(PROFILE, { given_names: '  ', hacker_field: 'x' })
  assert.equal(out2.given_names, 'JOHN')
  assert.ok(!('hacker_field' in out2))
})

// ---------------------------------------------------------------------------
// Checklist helpers.
test('checklist status meta + counts', () => {
  assert.equal(checklistStatusMeta('provided').tone, 'ok')
  assert.equal(checklistStatusMeta('pending').tone, 'pending')
  assert.equal(checklistStatusMeta('whatever').i18nKey, 'checklist.pending') // fail-safe
  const items = [
    { id: 'passport', kind: 'document', required: true, status: 'provided' },
    { id: 'flight_itinerary', kind: 'document', required: true, status: 'pending' },
    { id: 'photo', kind: 'document', required: false, status: 'pending' },
    { id: 'passport_validity', kind: 'check', required: true, status: 'auto' }
  ]
  const c = checklistCounts(items)
  assert.equal(c.required, 2)
  assert.equal(c.missing, 1)
  assert.equal(c.complete, false)
  assert.equal(checklistCounts([]).complete, false)   // empty is never "complete"
  for (const lang of SUPPORTED) {
    for (const s of ['provided', 'pending', 'auto', 'prepared_later']) {
      assert.ok(STRINGS[lang][checklistStatusMeta(s).i18nKey], `${lang} ${s}`)
    }
  }
})
