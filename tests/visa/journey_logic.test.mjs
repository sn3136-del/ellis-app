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
// Checklist helpers. Only a SUBMITTED (or waived) item counts as complete —
// uploads alone never do.
test('checklist status meta + counts', () => {
  assert.equal(checklistStatusMeta('submitted').tone, 'ok')
  assert.equal(checklistStatusMeta('pending').tone, 'pending')
  assert.equal(checklistStatusMeta('mismatch').tone, 'blocked')
  assert.equal(checklistStatusMeta('whatever').i18nKey, 'checklist.pending') // fail-safe
  const items = [
    { id: 'passport', kind: 'document', required: true, status: 'submitted' },
    { id: 'flight_itinerary', kind: 'document', required: true, status: 'ready_to_submit' },
    { id: 'photo', kind: 'document', required: false, status: 'pending' },
    { id: 'passport_validity', kind: 'check', required: true, status: 'auto' }
  ]
  const c = checklistCounts(items)
  assert.equal(c.required, 2)
  assert.equal(c.missing, 1)      // uploaded-but-not-submitted is still missing
  assert.equal(c.complete, false)
  assert.equal(checklistCounts([]).complete, false)   // empty is never "complete"
  // Submitting the remaining item completes the required set.
  items[1].status = 'submitted'
  assert.equal(checklistCounts(items).complete, true)
  // A waived item never blocks completion.
  items[1].status = 'waived'
  assert.equal(checklistCounts(items).complete, true)
  for (const lang of SUPPORTED) {
    for (const s of ['pending', 'processing', 'needs_review', 'mismatch', 'unreadable',
                     'ready_to_submit', 'submitted', 'waived', 'auto', 'prepared_later']) {
      assert.ok(STRINGS[lang][checklistStatusMeta(s).i18nKey], `${lang} ${s}`)
    }
  }
})

// ---------------------------------------------------------------------------
// Continue button (document intake → next stage). Disabled with an exact
// remaining count while any mandatory item is unresolved; label follows the
// route's continuation kind; backend re-validates server-side regardless.
import { continueButtonMeta, docTypeLabelKey, MANUAL_DOC_TYPES } from '../../src/renderer/src/lib/intake.js'

test('continue button stays disabled until every mandatory item is fulfilled', () => {
  const blocked = continueButtonMeta({ continuation_kind: 'visa_application',
    checklist_counts: { required_missing: 2 }, intake_stage: { completed: false } })
  assert.equal(blocked.visible, true)
  assert.equal(blocked.enabled, false)
  assert.equal(blocked.remaining, 2)
  const ready = continueButtonMeta({ continuation_kind: 'visa_application',
    checklist_counts: { required_missing: 0 }, intake_stage: { completed: false } })
  assert.equal(ready.enabled, true)
  assert.equal(ready.labelKey, 'checklist.continue.visa')
})

test('continue button label follows the route kind; unknown journey hides it', () => {
  assert.equal(continueButtonMeta({ continuation_kind: 'entry_preparation',
    checklist_counts: { required_missing: 0 } }).labelKey, 'checklist.continue.exempt')
  assert.equal(continueButtonMeta({ continuation_kind: 'authorization_application',
    checklist_counts: { required_missing: 0 } }).labelKey, 'checklist.continue.eta')
  assert.equal(continueButtonMeta({ continuation_kind: 'passport_renewal',
    checklist_counts: { required_missing: 0 } }).labelKey, 'checklist.continue.renewal')
  assert.equal(continueButtonMeta(null).visible, false)
  assert.equal(continueButtonMeta({}).visible, false)
  // Missing counts fail safe to disabled — never an enabled button on unknown state.
  assert.equal(continueButtonMeta({ continuation_kind: 'visa_application' }).enabled, false)
  // Every continue label exists in every locale.
  for (const lang of SUPPORTED) {
    for (const k of ['visa', 'eta', 'exempt', 'renewal']) {
      assert.ok(STRINGS[lang][`checklist.continue.${k}`], `${lang} ${k}`)
    }
  }
})

// ---------------------------------------------------------------------------
// Backend error surfacing: structured FastAPI details ({reason, message, …})
// must reach the applicant as their honest explanation — never a bare
// "HTTP 409" when the backend said exactly why it refused.
import { errorMessageFrom } from '../../src/renderer/src/lib/visaBackend.js'

test('structured error details surface their honest message, never a bare status', () => {
  // real_only_stop shape (fail-closed portal gate).
  assert.equal(errorMessageFrom({
    reason: 'real_only_stop', status: 'PORTAL_UNAVAILABLE',
    detail: 'runtime mode requires an approved live adapter'
  }, 409), 'runtime mode requires an approved live adapter')
  // documents_incomplete shape (server-side checklist gate).
  assert.equal(errorMessageFrom({
    reason: 'documents_incomplete',
    message: 'Submit 2 remaining required documents before starting.'
  }, 409), 'Submit 2 remaining required documents before starting.')
  assert.equal(errorMessageFrom('plain string detail', 409), 'plain string detail')
  assert.equal(errorMessageFrom(null, 409), 'HTTP 409')
  assert.equal(errorMessageFrom({}, 503), 'HTTP 503')
  // The applicant-facing portal-unavailable copy exists in every locale and
  // never claims a submission happened or was simulated.
  for (const lang of SUPPORTED) {
    assert.ok(STRINGS[lang]['case.portalUnavailable'], lang)
  }
  assert.ok(/never simulates/i.test(STRINGS.en['case.portalUnavailable']))
})

// ---------------------------------------------------------------------------
// Preview rotation geometry: rotate around the center inside a wrapper sized
// to the ROTATED bounding box — width/height swap at 90°/270°, the container
// resizes, and the full document always stays inside the layout at any zoom.
import { rotatedFrame } from '../../src/renderer/src/lib/intake.js'

test('rotation swaps the bounding box at 90/270 and preserves it at 0/180', () => {
  const portrait = { w: 600, h: 800 }
  for (const [rot, expW, expH] of [[0, 600, 800], [90, 800, 600],
                                   [180, 600, 800], [270, 800, 600]]) {
    const f = rotatedFrame({ ...portrait, rotation: rot, zoom: 1 })
    assert.equal(f.boxW, expW, `rot ${rot} boxW`)
    assert.equal(f.boxH, expH, `rot ${rot} boxH`)
    // The element keeps its own aspect — only the wrapper swaps.
    assert.equal(f.imgW, 600)
    assert.equal(f.imgH, 800)
  }
  assert.equal(rotatedFrame({ ...portrait, rotation: 360 }).quarter, 0)
  assert.equal(rotatedFrame({ ...portrait, rotation: -90 }).quarter, 270)
})

test('rotated documents fit the container width at every orientation and zoom', () => {
  // Extremely tall document rotated sideways must fit a 700px-wide area.
  const tall = { w: 500, h: 3000, maxW: 700 }
  for (const rot of [0, 90, 180, 270]) {
    for (const zoom of [0.5, 1]) {
      const f = rotatedFrame({ ...tall, rotation: rot, zoom })
      assert.ok(f.boxW <= 700 * zoom + 1e-9, `rot ${rot} zoom ${zoom} fits width`)
      // The wrapper is EXACTLY the rotated bounding box — nothing can clip.
      const swap = rot === 90 || rot === 270
      assert.ok(Math.abs(f.boxW - (swap ? f.imgH : f.imgW)) < 1e-9)
      assert.ok(Math.abs(f.boxH - (swap ? f.imgW : f.imgH)) < 1e-9)
    }
  }
  // Landscape at 90° becomes portrait and still fits.
  const land = rotatedFrame({ w: 2000, h: 900, rotation: 90, zoom: 1, maxW: 700 })
  assert.ok(land.boxW <= 700 && land.boxH > land.boxW)
  // Zoom scales linearly after rotation.
  const z2 = rotatedFrame({ w: 600, h: 800, rotation: 90, zoom: 2, maxW: 700 })
  const z1 = rotatedFrame({ w: 600, h: 800, rotation: 90, zoom: 1, maxW: 700 })
  assert.ok(Math.abs(z2.boxW - z1.boxW * 2) < 1e-9)
  // Degenerate input never crashes.
  assert.equal(rotatedFrame({ w: 0, h: 0, rotation: 90 }).boxW, 0)
})

// ---------------------------------------------------------------------------
// Structured home address (mandatory at intake; country-aware — no state or
// postal code required anywhere).
import { missingAddress, formatAddress, ADDRESS_REQUIRED_KEYS } from '../../src/renderer/src/lib/intake.js'

test('address requires only line1/city/country — never state or postal code', () => {
  assert.deepEqual(ADDRESS_REQUIRED_KEYS,
    ['address_line1', 'address_city', 'address_country'])
  assert.deepEqual(missingAddress({}),
    ['address_line1', 'address_city', 'address_country'])
  // A valid address with NO region and NO postal code (many countries).
  const intl = { address_line1: 'Plot 5, Airport Road', address_city: 'Kigali',
                 address_country: 'RWA' }
  assert.deepEqual(missingAddress(intl), [])
  assert.equal(formatAddress(intl), 'Plot 5, Airport Road, Kigali, RWA')
  assert.deepEqual(missingAddress({ address_line1: '  ', address_city: 'X',
                                    address_country: 'USA' }), ['address_line1'])
  // Address i18n exists in every locale.
  for (const lang of SUPPORTED) {
    for (const k of ['address.title', 'field.address_line1', 'field.address_city',
                     'field.address_country', 'field.mailing_address_same']) {
      assert.ok(STRINGS[lang][k], `${lang} ${k}`)
    }
  }
})

// Advisory-trust + translation strings exist everywhere; the advisory wording
// names both sides and never claims verification.
test('advisory and translation strings are honest and fully localized', () => {
  for (const lang of SUPPORTED) {
    for (const k of ['checklist.advisoryNote', 'checklist.submitAnyway',
                     'checklist.detectedLanguage', 'checklist.translateTo',
                     'checklist.translateConsent', 'checklist.machineTranslation',
                     'checklist.certifiedNote', 'checklist.applicantConfirmed']) {
      assert.ok(STRINGS[lang][k], `${lang} ${k}`)
    }
  }
  assert.ok(STRINGS.en['checklist.advisoryNote'].includes('{detected}'))
  assert.ok(STRINGS.en['checklist.advisoryNote'].includes('{selected}'))
  assert.ok(!/verified|guarantee/i.test(STRINGS.en['checklist.machineTranslation']))
  assert.ok(/certified human translation/i.test(STRINGS.en['checklist.certifiedNote']))
})

test('doc-type labels are applicant-friendly and localized; whitelist excludes passport', () => {
  assert.equal(docTypeLabelKey('flight_itinerary'), 'doctype.flight_itinerary')
  assert.equal(docTypeLabelKey('weird_internal_thing'), 'doctype.document') // never internal ids
  assert.ok(!MANUAL_DOC_TYPES.includes('passport'))
  for (const lang of SUPPORTED) {
    for (const dt of MANUAL_DOC_TYPES.concat(['passport', 'document'])) {
      assert.ok(STRINGS[lang][docTypeLabelKey(dt)], `${lang} ${dt}`)
    }
  }
})

// ---------------------------------------------------------------------------
// Route-specific journey rendering: only applicable stages, appointment-gated
// Preferences tab, calculated validity display, and the two-pass verification
// chip.
import {
  applicableStages, preferencesTabVisible,
  validityMeta, verificationMeta
} from '../../src/renderer/src/lib/intake.js'

test('visa-exempt entry preparation has NO submission stages at all', () => {
  assert.deepEqual(applicableStages('entry_preparation', [
    { step: 'collect_documents' }, { step: 'arrival_card_preparation' }
  ]), [])
})

test('stages come from the route plan — never the whole state machine', () => {
  const plan = [
    { step: 'collect_documents' }, { step: 'prepare_forms' },
    { step: 'account_registration' }, { step: 'payment' }, { step: 'submission' }
  ]
  const stages = applicableStages('visa_application', plan)
  assert.ok(stages.includes('PORTAL_ACCOUNT_CREATING'))
  assert.ok(stages.includes('PAYMENT_APPROVAL_REQUIRED'))
  assert.ok(stages.includes('SUBMITTING'))
  assert.ok(!stages.includes('APPOINTMENT_BOOKING'))     // no appointment step
  // Without an account/payment step those stages disappear too.
  const lean = applicableStages('visa_application', [{ step: 'submission' }])
  assert.ok(!lean.includes('PORTAL_ACCOUNT_CREATING'))
  assert.ok(!lean.includes('PAYMENT_APPROVAL_REQUIRED'))
})

test('legacy cases without a journey keep their previous display (null)', () => {
  assert.equal(applicableStages(null, []), null)
  assert.equal(applicableStages(undefined, undefined), null)
})

test('Preferences tab appears only when an appointment is actually required', () => {
  const exempt = { continuation_kind: 'entry_preparation',
    guidance: { guidance: { appointment_required: false } } }
  assert.equal(preferencesTabVisible(exempt), false)
  const embassy = { continuation_kind: 'visa_application',
    guidance: { guidance: { appointment_required: true } } }
  assert.equal(preferencesTabVisible(embassy), true)
  const evisa = { continuation_kind: 'visa_application',
    guidance: { guidance: { appointment_required: false } } }
  assert.equal(preferencesTabVisible(evisa), false)
})

test('validity is displayed as a calculated verdict, never raw unknown', () => {
  for (const lang of SUPPORTED) {
    for (const s of ['ok', 'ok_rule_unverified', 'ok_pending_travel_dates',
                     'ok_with_conditions', 'insufficient_validity', 'expired', 'unknown']) {
      assert.ok(STRINGS[lang][validityMeta(s).i18nKey], `${lang} ${s}`)
    }
  }
  assert.equal(validityMeta('insufficient_validity').offerRenewal, true)
  assert.equal(validityMeta('expired').offerRenewal, true)
  assert.equal(validityMeta('ok').offerRenewal, false)
  assert.equal(validityMeta('weird-new-status').i18nKey, 'validity.unknown')
})

test('two-pass verification chip only for genuinely verified results', () => {
  assert.equal(verificationMeta({ verdict: 'ACCEPT' }).verified, true)
  assert.equal(verificationMeta({ verdict: 'REVISE' }).verified, true)
  assert.equal(verificationMeta({}).verified, false)
  assert.equal(verificationMeta(null).verified, false)
  for (const lang of SUPPORTED) {
    assert.ok(STRINGS[lang]['guidance.verified'], lang)
    // The badge names the Kimi second pass, never official sources.
    assert.ok(!STRINGS.en['guidance.verified'].toLowerCase().includes('official source'))
  }
})

test('no applicant-facing string ever claims an official-source check', () => {
  for (const lang of SUPPORTED) {
    assert.equal(STRINGS[lang]['case.audit.done'], undefined)
    assert.equal(STRINGS[lang]['case.audit.running'], undefined)
  }
  assert.ok(!('Checked against official sources' in
    Object.fromEntries(Object.values(STRINGS.en).map((v) => [v, 1]))))
})

// ---------------------------------------------------------------------------
// No mock/fictional placeholder may exist anywhere in the applicant renderer.
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, out)
    else out.push(p)
  }
  return out
}

test('the applicant renderer contains no MOCKLAND or fictional center', () => {
  const root = new URL('../../src/renderer/src', import.meta.url).pathname
  for (const file of walk(root)) {
    if (!/\.(jsx?|css)$/.test(file)) continue
    const text = readFileSync(file, 'utf8')
    assert.ok(!text.includes('MOCKLAND'), `${file} contains MOCKLAND`)
  }
})

// ---------------------------------------------------------------------------
// Document preview security: bytes are fetched from the authenticated backend
// and rendered from local blob: URLs only — the preview never embeds a
// third-party page or a raw backend URL in an iframe/img.
test('document preview renders only local blob URLs, never a framed page', () => {
  const src = readFileSync(
    new URL('../../src/renderer/src/components/visa/DocPreview.jsx', import.meta.url), 'utf8')
  assert.ok(src.includes('URL.createObjectURL'), 'preview must render via blob URLs')
  assert.ok(src.includes('URL.revokeObjectURL'), 'blob URLs must be revoked')
  // Every iframe/img rendered by the preview uses the blob-backed state.url.
  for (const m of src.matchAll(/<(iframe|img)\b[^>]*src=\{([^}]+)\}/g)) {
    assert.equal(m[2].trim(), 'state.url', `unexpected ${m[1]} src: ${m[2]}`)
  }
  // No external origin appears anywhere in the preview component.
  assert.ok(!/https?:\/\//.test(src), 'preview must not reference external origins')
})

// The page CSP allows blob: for the preview and never allowlists the raw
// backend origin for frames/images (fetch + blob is the only path).
test('web and electron CSPs permit blob preview, never backend-framed content', () => {
  const webCfg = readFileSync(new URL('../../vite.web.config.mjs', import.meta.url), 'utf8')
  const html = readFileSync(new URL('../../src/renderer/index.html', import.meta.url), 'utf8')
  for (const [name, text] of [['web', webCfg], ['electron', html]]) {
    const img = text.match(/img-src[^;"]*/)[0]
    assert.ok(img.includes('blob:'), `${name} img-src must include blob:`)
    assert.ok(!img.includes('http://'), `${name} img-src must not allowlist http origins`)
    const frame = text.match(/frame-src[^;"]*/)[0]
    assert.ok(frame.includes('blob:'), `${name} frame-src must include blob:`)
    assert.ok(!frame.includes('http://'), `${name} frame-src must not allowlist http origins`)
  }
})

// ---------------------------------------------------------------------------
// Calendar dates: canonical ISO underneath, U.S. MM/DD/YYYY for the applicant,
// pure string transforms (a date can never shift through a timezone).
import {
  formatDateUS, parseUSDate, localTodayIso, isDateKey
} from '../../src/renderer/src/lib/intake.js'
import { fieldRows } from '../../src/renderer/src/lib/visaSession.js'

test('applicant-facing dates format as MM/DD/YYYY from canonical ISO', () => {
  assert.equal(formatDateUS('1990-02-15'), '02/15/1990')
  assert.equal(formatDateUS('2031-05-04'), '05/04/2031')
  assert.equal(formatDateUS(''), '')
  assert.equal(formatDateUS('940812'), '940812')       // non-canonical untouched
  assert.equal(formatDateUS(undefined), '')
})

test('date display is a pure string transform — no timezone can shift it', () => {
  // Would be 05/03/2031 in any negative-UTC zone if new Date() were involved.
  assert.equal(formatDateUS('2031-05-04'), '05/04/2031')
  assert.equal(parseUSDate(formatDateUS('2031-05-04')), '2031-05-04')  // round trip
  for (const iso of ['2000-02-29', '1990-02-15', '2027-01-01', '1926-12-25']) {
    assert.equal(parseUSDate(formatDateUS(iso)), iso)
  }
})

test('applicant date entry parses US format back to canonical ISO', () => {
  assert.equal(parseUSDate('02/15/1990'), '1990-02-15')
  assert.equal(parseUSDate('6/3/1988'), '1988-06-03')
  assert.equal(parseUSDate('1990-02-15'), '1990-02-15')  // ISO passes through
  assert.equal(parseUSDate('16/03/1990'), '')            // not a US date
  assert.equal(parseUSDate('02/30/2020'), '')            // impossible
  assert.equal(parseUSDate('02/29/2020'), '2020-02-29')  // leap day
  assert.equal(parseUSDate('garbage'), '')
})

test('localTodayIso uses the LOCAL calendar day, never UTC', () => {
  // 2026-07-24 23:30 local: toISOString() would already say 07-25 east of UTC
  // (and 07-24T06:30Z would say 07-23 west) — local components never flip.
  const lateEvening = new Date(2026, 6, 24, 23, 30)
  assert.equal(localTodayIso(lateEvening), '2026-07-24')
  const earlyMorning = new Date(2026, 6, 24, 0, 10)
  assert.equal(localTodayIso(earlyMorning), '2026-07-24')
})

test('profileRows and fieldRows display dates as MM/DD/YYYY over ISO values', () => {
  const rows = profileRows({ fields: {
    birth_date: { value: '1990-02-15', confidence: 0.98, source: 'mrz' },
    expiry_date: { value: '2028-03-20', confidence: 0.98, source: 'mrz' },
    surname: { value: 'CAO', confidence: 0.99, source: 'mrz' }
  } })
  const byKey = Object.fromEntries(rows.map((r) => [r.key, r]))
  assert.equal(byKey.birth_date.value, '1990-02-15')     // canonical underneath
  assert.equal(byKey.birth_date.display, '02/15/1990')   // applicant-facing
  assert.equal(byKey.expiry_date.display, '03/20/2028')
  assert.equal(byKey.surname.display, 'CAO')             // non-dates untouched

  const fr = fieldRows({ birth_date: { value: '1990-02-15', confidence: 0.98 },
                         passport_number: { value: 'X1234567', confidence: 0.99 } })
  const fby = Object.fromEntries(fr.map((r) => [r.key, r]))
  assert.equal(fby.birth_date.display, '02/15/1990')
  assert.equal(fby.birth_date.value, '1990-02-15')
  assert.equal(fby.passport_number.display, 'X1234567')
})

test('a US-format date edit becomes canonical ISO in the prefill', () => {
  const profile = { prefill: { birth_date: '1990-02-15', passport_number: 'X1' } }
  const out = prefillWithEdits(profile, { birth_date: '03/16/1990' })
  assert.equal(out.birth_date, '1990-03-16')             // parsed, canonical
  // An unparseable date edit never overwrites the extracted value.
  const kept = prefillWithEdits(profile, { birth_date: 'not a date' })
  assert.equal(kept.birth_date, '1990-02-15')
  // ISO typed directly also accepted.
  assert.equal(prefillWithEdits(profile, { birth_date: '1990-03-17' }).birth_date,
    '1990-03-17')
})

test('isDateKey covers passport and trip date keys only', () => {
  for (const k of ['birth_date', 'expiry_date', 'issue_date', 'arrival_date',
                   'departure_date', 'passport_expiry_date', 'date_of_birth']) {
    assert.equal(isDateKey(k), true, k)
  }
  for (const k of ['passport_number', 'nationality', 'age', 'full_name']) {
    assert.equal(isDateKey(k), false, k)
  }
})
