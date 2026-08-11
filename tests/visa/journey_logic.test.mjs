// Hermetic unit tests for the applicant-journey pure logic (no backend / DOM):
// guidance continuation, passport-profile display + prefill, derived age, and
// the route-checklist helpers. Mirrors of backend rules are display-only — the
// backend stays authoritative — but the mapping must agree.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

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

// The 13 H1B document types the backend added to intake_flow.MANUAL_DOC_TYPES
// must be mirrored in the renderer registries (finding #13). Without them a
// photographed 学位证 that OCRs poorly and classifies as 'document' can never be
// picked as its real type (the manual picker maps MANUAL_DOC_TYPES), and every
// mismatch advisory for a detected H1B type degrades to "Ellis detected this as
// document" because docTypeLabelKey falls back to 'doctype.document'.
const H1B_DOC_TYPES = [
  'degree_certificate', 'graduation_certificate', 'transcript', 'resume_cv',
  'prior_i797', 'i94_record', 'credential_evaluation', 'employer_support_letter',
  'job_description', 'fein_evidence', 'employer_financials',
  'corporate_relationship_evidence', 'certified_lca'
]

test('renderer doc-type registries mirror the backend H1B types, localized in every locale (finding #13)', () => {
  for (const dt of H1B_DOC_TYPES) {
    // KNOWN_DOC_TYPES (tested through docTypeLabelKey) recognizes the type: a
    // known type resolves to its own label, never the generic fallback.
    assert.equal(docTypeLabelKey(dt), `doctype.${dt}`, `known: ${dt}`)
    // The applicant may manually pick it for an ambiguous upload.
    assert.ok(MANUAL_DOC_TYPES.includes(dt), `manual: ${dt}`)
    // Every locale carries a non-empty applicant-facing label.
    for (const lang of SUPPORTED) {
      const label = STRINGS[lang][`doctype.${dt}`]
      assert.ok(typeof label === 'string' && label.trim().length > 0, `${lang} ${dt}`)
    }
  }
  // passport is still never manually pickable (identity comes only from the MRZ).
  assert.ok(!MANUAL_DOC_TYPES.includes('passport'))
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

// --- the dead-control regression ------------------------------------------
// SignatureModal is the only thing that grants the standing authorization,
// the Authorize card is the only thing that opens it, and the readiness gate
// refuses any live run without it. So a continuation kind excluded from that
// card cannot start: entry_preparation recorded a stage and did nothing
// (2026-08-03), and passport_renewal enqueued a run that failed on a missing
// representative_submission_permitted. Both were excluded by a `kind !== ...`
// guard on the card, which is exactly how this bug is written.

test('the Authorize card excludes no continuation kind', async () => {
  const src = await readFile(
    new URL('../../src/renderer/src/components/visa/CaseFlow.jsx', import.meta.url), 'utf8')
  const card = src.slice(src.indexOf("data-testid=\"authorize-and-start\"") - 2500,
                         src.indexOf("data-testid=\"authorize-and-start\""))
  const guard = card.match(/\{!started && !docsPending([^&]*&&[^(]*)?\(/)
  assert.ok(guard, 'could not find the Authorize card render guard')
  assert.ok(!/kind\s*!==/.test(guard[0]),
    `the Authorize card excludes a kind, which makes its button dead: ${guard[0].trim()}`)
})

// The inverse of the dead-control bug (finding #18): the H1B parent case is a
// petition CONTAINER whose tourist "Authorize & start" /start can never do the
// right thing (no live adapter, CHN→USA tourist wording; the real filings live
// in the H1B workspace). Leaving the h1b kinds IN that card is the same bug
// written the other way, so the parent must be guarded OUT of the tourist card
// via a NAMED boolean (never a `kind !== ...` exclusion, which the test above
// forbids) and shown an honest placeholder instead.
test('the H1B parent is guarded out of the tourist Authorize card and shown a placeholder', async () => {
  const src = await readFile(
    new URL('../../src/renderer/src/components/visa/CaseFlow.jsx', import.meta.url), 'utf8')
  // isH1bParent is derived from exactly the two H1B continuation kinds.
  assert.match(src,
    /isH1bParent\s*=\s*kind === 'h1b_petition' \|\| kind === 'h1b_filing'/)
  // The tourist Authorize card render guard excludes the H1B parent.
  const card = src.slice(src.indexOf('data-testid="authorize-and-start"') - 2500,
                         src.indexOf('data-testid="authorize-and-start"'))
  const guard = card.match(/\{!started && !docsPending([^(]*)\(/)
  assert.ok(guard, 'could not find the Authorize card render guard')
  assert.ok(/!isH1bParent/.test(guard[0]),
    `the Authorize card does not exclude the H1B parent: ${guard[0].trim()}`)
  // An honest H1B placeholder renders in its place (never the tourist card).
  assert.match(src, /data-testid="h1b-placeholder"/)
})

test('ContinuePanel never carries the burden of starting a run', () => {
  // It renders only while documents are missing; it explains what remains and
  // records the stage. If it ever has to start a run again, some kind has
  // been excluded from the Authorize card — fix that instead.
  const meta = continueButtonMeta({ continuation_kind: 'entry_preparation',
    checklist_counts: { required_missing: 0 } })
  assert.equal(meta.startsRun, undefined)
})

// ---------------------------------------------------------------------------
// H1B edition (docs/H1B_ARCHITECTURE.md P3): typed client contract, persona
// detection, per-party walkthrough rendering, and Ask Ellis action honesty.
import { createVisaClient } from '../../src/renderer/src/lib/visaBackend.js'
import {
  detectPersona, partyForPersona, newEmployerSession, newSession,
  h1bWhoActs, h1bStepMeta, assistantActionMeta,
  setActiveH1bCase, getActiveH1bCase, subscribeActiveH1bCase
} from '../../src/renderer/src/lib/visaSession.js'

// The H1B client methods must hit exactly the pinned endpoint paths with the
// pinned body shapes — the backend routers (h1b/api.py + the forms/assistant/
// counsel routers) implement these same paths.
test('h1b client methods hit the pinned endpoint paths with the pinned bodies', async () => {
  const calls = []
  const realFetch = globalThis.fetch
  globalThis.fetch = async (url, opts = {}) => {
    calls.push({ url: String(url), method: opts.method || 'GET', body: opts.body })
    return { ok: true, status: 200, text: async () => '{}' }
  }
  try {
    const c = createVisaClient(newEmployerSession())
    await c.h1bCreateCase({ case_kind: 'extension' })
    await c.h1bEmployerProfiles()
    await c.h1bCreateEmployerProfile({ legal_name: 'Trip.com US' })
    await c.h1bPipeline('c1')
    await c.h1bWalkthrough('c1')
    await c.h1bAssistant('c1', { message: 'hi', locale: 'zh-CN', history: [] })
    await c.h1bReleaseStep('c1', 'lca')
    await c.h1bVerifyStep('c1', 'lca', { receipts: { lca_number: 'I-200-1' } })
    await c.h1bPrepareForm('c1', 'eta-9035', 'zh-CN')
    await c.h1bPaperPacket('c1')
    await c.h1bRfeRisks('c1')
    await c.h1bNarrative('c1', 'support_letter')
    await c.h1bEvidenceIndex('c1')
    await c.h1bPartyAnswers('c1', 'petitioner', { job_title: 'SWE' })
  } finally {
    globalThis.fetch = realFetch
  }
  const seen = calls.map((x) => `${x.method} ${new URL(x.url).pathname}`)
  assert.deepEqual(seen, [
    'POST /h1b/cases',
    'GET /h1b/employer-profiles',
    'POST /h1b/employer-profiles',
    'GET /h1b/cases/c1/pipeline',
    'GET /h1b/cases/c1/walkthrough',
    'POST /h1b/cases/c1/assistant',
    'POST /h1b/cases/c1/steps/lca/release',
    'POST /h1b/cases/c1/steps/lca/verify',
    'POST /h1b/cases/c1/forms/eta-9035/prepare',
    'POST /h1b/cases/c1/paper-packet',
    'GET /h1b/cases/c1/counsel/rfe-risks',
    'POST /h1b/cases/c1/counsel/narrative',
    'GET /h1b/cases/c1/counsel/evidence-index',
    'POST /h1b/cases/c1/party/petitioner/answers'
  ])
  const byPath = Object.fromEntries(calls.map((x) => [new URL(x.url).pathname, x]))
  // The assistant body carries message + locale + history (the locale rides so
  // replies speak the UI language); party answers are wrapped in {answers};
  // the narrative kind rides in the body (counsel_api.NarrativeBody).
  assert.deepEqual(JSON.parse(byPath['/h1b/cases/c1/assistant'].body),
    { message: 'hi', locale: 'zh-CN', history: [] })
  assert.deepEqual(JSON.parse(byPath['/h1b/cases/c1/party/petitioner/answers'].body),
    { answers: { job_title: 'SWE' } })
  assert.deepEqual(JSON.parse(byPath['/h1b/cases/c1/steps/lca/verify'].body),
    { receipts: { lca_number: 'I-200-1' } })
  assert.deepEqual(JSON.parse(byPath['/h1b/cases/c1/counsel/narrative'].body),
    { kind: 'support_letter' })
  // Locale rides as a query parameter on the localized endpoints.
  assert.equal(new URL(byPath['/h1b/cases/c1/forms/eta-9035/prepare'].url)
    .searchParams.get('locale'), 'zh-CN')
})

// Persona detection: three-way, hash-driven, persisted, with the legacy
// ellis_admin flag still honored (back-compat) and failure-safe to applicant.
function memStorage(init = {}) {
  const m = new Map(Object.entries(init))
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k)
  }
}

test('persona detection is three-way with ellis_admin back-compat', () => {
  assert.equal(detectPersona({ hash: '#admin', storage: memStorage() }), 'admin')
  assert.equal(detectPersona({ hash: '#ops', storage: memStorage() }), 'admin')
  assert.equal(detectPersona({ hash: '#employer', storage: memStorage() }), 'employer')
  assert.equal(detectPersona({ hash: '', storage: memStorage() }), 'applicant')
  // The choice persists across loads without the hash.
  const s = memStorage()
  detectPersona({ hash: '#employer', storage: s })
  assert.equal(detectPersona({ hash: '', storage: s }), 'employer')
  // #applicant clears BOTH the persona and the legacy admin flag.
  detectPersona({ hash: '#applicant', storage: s })
  assert.equal(detectPersona({ hash: '', storage: s }), 'applicant')
  assert.equal(s.getItem('ellis_admin'), null)
  // Back-compat: a legacy ellis_admin flag alone still grants admin.
  assert.equal(detectPersona({ hash: '', storage: memStorage({ ellis_admin: '1' }) }), 'admin')
  // #admin still WRITES the legacy flag so older builds keep working.
  const s2 = memStorage()
  detectPersona({ hash: '#admin', storage: s2 })
  assert.equal(s2.getItem('ellis_admin'), '1')
  // #employer never leaves a stale admin flag behind.
  const s3 = memStorage({ ellis_admin: '1' })
  detectPersona({ hash: '#employer', storage: s3 })
  assert.equal(s3.getItem('ellis_admin'), null)
  // Failure-safe: no storage, broken storage → applicant, never a throw.
  assert.equal(detectPersona({ hash: '', storage: null }), 'applicant')
  assert.equal(detectPersona({ hash: '', storage: { getItem() { throw new Error('denied') } } }), 'applicant')
})

test('employer session shares the org but never the applicant identity', () => {
  const emp = newEmployerSession()
  const app = newSession()
  assert.equal(emp.orgId, app.orgId)          // org tenancy shares the case
  assert.notEqual(emp.userId, app.userId)     // per-party authz can tell them apart
  assert.equal(emp.userId, 'petitioner-1')
  assert.equal(partyForPersona('applicant'), 'beneficiary')
  assert.equal(partyForPersona('employer'), 'petitioner')
  assert.equal(partyForPersona('admin'), 'admin')
  // Unknown persona fails safe to the beneficiary view (least surface).
  assert.equal(partyForPersona('whatever'), 'beneficiary')
})

// Walkthrough who-acts: the other party's step reads "waiting on the
// employer/worker", never as an action the viewer could take.
test('walkthrough maps the other party to waiting-on, the own party to acting', () => {
  const lca = { step_key: 'lca', acting_party: 'petitioner', status: 'ready' }
  const consular = { step_key: 'ds160_consular', acting_party: 'beneficiary', status: 'ready' }
  // Beneficiary viewer: the LCA waits on the employer.
  const w = h1bWhoActs(lca, 'beneficiary')
  assert.equal(w.mine, false)
  assert.equal(w.waiting, true)
  assert.equal(w.i18nKey, 'h1b.waitingOn.petitioner')
  // Petitioner viewer: the consular leg waits on the worker.
  const w2 = h1bWhoActs(consular, 'petitioner')
  assert.equal(w2.mine, false)
  assert.equal(w2.waiting, true)
  assert.equal(w2.i18nKey, 'h1b.waitingOn.beneficiary')
  // The acting party sees its own step as its own.
  assert.equal(h1bWhoActs(lca, 'petitioner').mine, true)
  assert.equal(h1bWhoActs(lca, 'petitioner').i18nKey, 'h1b.whoActs.you')
  assert.equal(h1bWhoActs(consular, 'beneficiary').mine, true)
  // Admin sees the acting party named, with no waiting framing.
  const wa = h1bWhoActs(lca, 'admin')
  assert.equal(wa.waiting, false)
  assert.equal(wa.i18nKey, 'h1b.actingParty.petitioner')
  // The English copy names the right human.
  assert.match(STRINGS.en['h1b.waitingOn.petitioner'], /employer/i)
  assert.match(STRINGS.en['h1b.waitingOn.beneficiary'], /worker/i)
  // Every who-acts key exists in every locale.
  for (const lang of SUPPORTED) {
    for (const k of ['h1b.whoActs.you', 'h1b.waitingOn.petitioner',
                     'h1b.waitingOn.beneficiary', 'h1b.actingParty.petitioner',
                     'h1b.actingParty.beneficiary']) {
      assert.ok(STRINGS[lang][k], `${lang} ${k}`)
    }
  }
})

test('h1b step statuses are localized and unknown fails safe', () => {
  for (const s of ['blocked', 'ready', 'in_progress', 'awaiting_government',
                   'verified', 'failed']) {
    const meta = h1bStepMeta(s)
    for (const lang of SUPPORTED) {
      assert.ok(STRINGS[lang][meta.i18nKey], `${lang} ${s}`)
    }
  }
  // Only a genuinely verified step gets the ok tone; unknown is muted, never ok.
  assert.equal(h1bStepMeta('verified').tone, 'ok')
  assert.equal(h1bStepMeta('surprise_status').i18nKey, 'h1b.status.unknown')
  assert.notEqual(h1bStepMeta('surprise_status').tone, 'ok')
})

// Ask Ellis action honesty: a denied action renders AS denied, and an
// unconfirmed action can never display as performed.
test('AskEllis renders denied actions as denied and never claims unconfirmed ones', () => {
  const denied = assistantActionMeta({
    action: 'release_step', status: 'denied',
    reason: "this action is the petitioner party's" })
  assert.equal(denied.denied, true)
  assert.equal(denied.done, false)
  assert.equal(denied.i18nKey, 'askellis.actionDenied')
  assert.match(denied.detail, /petitioner/)
  // A confirmed action is done.
  assert.equal(assistantActionMeta({ action: 'release_step', status: 'done' }).done, true)
  assert.equal(assistantActionMeta({ action: 'release_step', status: 'done' }).i18nKey,
    'askellis.actionDone')
  // Fail-safe honesty: unknown/absent status is NEITHER done NOR denied.
  const unknown = assistantActionMeta({ action: 'release_step', status: 'maybe?' })
  assert.equal(unknown.done, false)
  assert.equal(unknown.denied, false)
  assert.equal(unknown.i18nKey, 'askellis.actionNotDone')
  assert.equal(assistantActionMeta(null).done, false)
  assert.equal(assistantActionMeta({}).done, false)
  // The real backend shape ({tool, summary, ok} from h1b/assistant.py
  // execute_tool): ok:true is done; ok:false can NEVER render as done, and
  // the honest localized summary is the chip label.
  const backendDenied = assistantActionMeta({
    tool: 'release_step', ok: false,
    summary: 'Ellis was not allowed to do this: release_step (403).' })
  assert.equal(backendDenied.done, false)
  assert.match(backendDenied.label, /not allowed/)
  const backendDone = assistantActionMeta({
    tool: 'release_step', ok: true, summary: 'Done: release_step.' })
  assert.equal(backendDone.done, true)
  assert.equal(backendDone.label, 'Done: release_step.')
  for (const lang of SUPPORTED) {
    for (const k of ['askellis.actionDenied', 'askellis.actionDone',
                     'askellis.actionNotDone', 'askellis.actionsTaken']) {
      assert.ok(STRINGS[lang][k], `${lang} ${k}`)
    }
  }
})

// The AskEllis surface pins the attorney disclaimer at the panel top and
// renders actions only through the honest meta helper.
test('AskEllis pins the disclaimer and routes actions through assistantActionMeta', async () => {
  const src = await readFile(
    new URL('../../src/renderer/src/components/visa/AskEllis.jsx', import.meta.url), 'utf8')
  assert.ok(src.includes("t('h1b.disclaimer')"), 'the attorney disclaimer must be in the panel')
  assert.ok(src.includes('assistantActionMeta'), 'actions must render through the honest meta')
  assert.ok(src.includes('askellis-action-denied'), 'denied actions must be distinguishable')
})

// The active-case registry that mounts Ask Ellis in App.jsx.
test('the active H1B case registry notifies subscribers and clears honestly', () => {
  const seen = []
  const unsub = subscribeActiveH1bCase((id) => seen.push(id))
  setActiveH1bCase('case-9')
  assert.equal(getActiveH1bCase(), 'case-9')
  setActiveH1bCase('')
  assert.equal(getActiveH1bCase(), '')
  unsub()
  setActiveH1bCase('case-10')
  assert.deepEqual(seen, ['case-9', ''])      // unsubscribed: no further pushes
  setActiveH1bCase('')                        // leave global state clean
})

// The H1B parent petition renders the full walkthrough; child filing cases
// keep the standard flow (and the honest placeholder instead of the tourist
// Authorize card).
test('CaseFlow renders H1bPipeline for the parent petition kind only', async () => {
  const src = await readFile(
    new URL('../../src/renderer/src/components/visa/CaseFlow.jsx', import.meta.url), 'utf8')
  assert.match(src, /kind === 'h1b_petition' && \(\s*<H1bPipeline/,
    'the parent petition must render the pipeline walkthrough')
  assert.match(src, /kind === 'h1b_filing' && \(/,
    'child filings must keep their own branch')
  assert.match(src, /data-testid="h1b-placeholder"/)
})

// ---------------------------------------------------------------------------
// H1B wage-level + SOC/NAICS suggestion surface (Agent 4). Ellis computes the
// prevailing-wage LEVEL and suggests SOC/NAICS codes from OFFICIAL free
// government data. The two typed client methods hit the pinned wage_api.py
// paths; the display normalizers keep every wage / level / code an HONEST,
// confirm-required SUGGESTION (never a filed value); and the employer console
// renders the DOL caveats and the "you must confirm this" note.
import {
  wageLevelView, socSuggestionsView
} from '../../src/renderer/src/lib/visaBackend.js'

test('h1b wage + occupation client methods hit the pinned paths with pinned bodies', async () => {
  const calls = []
  const realFetch = globalThis.fetch
  globalThis.fetch = async (url, opts = {}) => {
    calls.push({ url: String(url), method: opts.method || 'GET', body: opts.body })
    return { ok: true, status: 200, text: async () => '{}' }
  }
  try {
    const c = createVisaClient(newEmployerSession())
    await c.h1bWageAnalysis('c1')
    await c.h1bClassifyOccupation('c1',
      { industry_text: 'online travel', occupation_text: 'Software Engineer' })
    // A missing argument defaults to empty strings — never undefined fields.
    await c.h1bClassifyOccupation('c1')
  } finally {
    globalThis.fetch = realFetch
  }
  const seen = calls.map((x) => `${x.method} ${new URL(x.url).pathname}`)
  assert.deepEqual(seen, [
    'GET /h1b/cases/c1/wage-analysis',
    'POST /h1b/cases/c1/classify-occupation',
    'POST /h1b/cases/c1/classify-occupation'
  ])
  // The classifier body carries industry + occupation text (NIOCCS inputs).
  assert.deepEqual(JSON.parse(calls[1].body),
    { industry_text: 'online travel', occupation_text: 'Software Engineer' })
  assert.deepEqual(JSON.parse(calls[2].body), { industry_text: '', occupation_text: '' })
})

test('wageLevelView normalizes the four levels and surfaces DOL caveats honestly', () => {
  const view = wageLevelView({
    available: true,
    source: 'U.S. DOL OFLC OEWS Wage Data', as_of: '2026-07-01',
    soc_code: '15-1252', soc_title: 'Software Developers',
    area_name: 'Statewide, CA', geo_level: 3,
    level_wages: { 1: 120000, 2: 145000, 3: 170000, 4: 195000 },
    offered_wage: 150000, offered_unit: 'year',
    computed_level: 'III', meets_prevailing: true, label: 'High Wage'
  })
  assert.equal(view.available, true)
  assert.deepEqual(view.levels.map((l) => l.roman), ['I', 'II', 'III', 'IV'])
  assert.equal(view.levels[2].wage, 170000)
  assert.equal(view.computedLevel, 'III')
  assert.equal(view.meetsPrevailing, true)
  assert.equal(view.source, 'U.S. DOL OFLC OEWS Wage Data')
  assert.equal(view.asOf, '2026-07-01')
  // A statewide fallback (GeoLvl 3) AND a High Wage label are BOTH surfaced.
  const codes = view.caveats.map((c) => c.code)
  assert.ok(codes.includes('geo_statewide'), 'statewide caveat surfaced')
  assert.ok(codes.includes('label_high'), 'High Wage caveat surfaced')
  // Every structured caveat code localizes in every locale.
  for (const code of ['geo_broadened', 'geo_statewide', 'geo_national',
                      'label_high', 'label_annual']) {
    for (const lang of SUPPORTED) {
      assert.ok(STRINGS[lang][`h1b.wage.caveat.${code}`], `${lang} ${code}`)
    }
  }
  // The wage panel's own strings resolve everywhere too.
  for (const lang of SUPPORTED) {
    for (const k of ['h1b.wage.title', 'h1b.wage.check', 'h1b.wage.caveatsTitle',
                     'h1b.wage.confirmNote', 'h1b.wage.meets', 'h1b.wage.belowPrevailing',
                     'h1b.wage.sourceLine', 'h1b.wage.unavailable']) {
      assert.ok(STRINGS[lang][k], `${lang} ${k}`)
    }
  }
})

test('wageLevelView honest-degrades and never invents a wage', () => {
  // Explicitly unavailable, or simply missing the level wages, is unavailable.
  assert.equal(wageLevelView({ available: false, reason: 'no soc' }).available, false)
  assert.equal(wageLevelView({}).available, false)
  assert.equal(wageLevelView(null).available, false)
  // A partial payload (only two levels) never fabricates the missing two.
  assert.equal(wageLevelView({ level_wages: { 1: 100000, 2: 120000 } }).levels.length, 2)
  // No geo fallback (GeoLvl 1 = the actual worksite MSA) and no label -> no
  // caveats invented.
  assert.deepEqual(wageLevelView({ geo_level: 1, level_wages: [1, 2, 3, 4] }).caveats, [])
  // GeoLvl 2 (broadened) and GeoLvl 4 (national) each surface their own caveat.
  assert.ok(wageLevelView({ geo_level: 2, level_wages: [1, 2, 3, 4] }).caveats
    .some((c) => c.code === 'geo_broadened'))
  assert.ok(wageLevelView({ geo_level: 4, level_wages: [1, 2, 3, 4] }).caveats
    .some((c) => c.code === 'geo_national'))
  // The Annual-wage label invalidates the 2080-hour conversion -> surfaced.
  assert.ok(wageLevelView({ level_wages: [1, 2, 3, 4], label: 'Annual Wage' }).caveats
    .some((c) => c.code === 'label_annual'))
  // meets_prevailing stays a genuine tri-state: absent is null, not false.
  assert.equal(wageLevelView({ level_wages: [1, 2, 3, 4] }).meetsPrevailing, null)
  assert.equal(wageLevelView({ level_wages: [1, 2, 3, 4], meets_prevailing: false })
    .meetsPrevailing, false)
})

test('socSuggestionsView ranks suggestions and always requires confirmation', () => {
  const view = socSuggestionsView({
    source: 'CDC NIOCCS', as_of: '2026-08-01',
    occupation: [
      { Code: '15-1252', Title: 'Software Developers', Probability: 0.97 },
      { Code: '15-1211', Title: 'Computer Systems Analysts', Probability: 0.42 }
    ],
    industry: [{ Code: '518210', Title: 'Data Processing', Probability: 0.88 }]
  })
  assert.equal(view.available, true)
  assert.equal(view.occupation.length, 2)
  assert.equal(view.occupation[0].code, '15-1252')
  assert.equal(view.occupation[0].confidence, 'high')   // 0.97 -> high
  assert.equal(view.occupation[1].confidence, 'low')    // 0.42 -> low
  assert.equal(view.industry[0].code, '518210')
  assert.equal(view.industry[0].confidence, 'high')     // 0.88 -> high
  // Confirmation is required no matter how confident the top match is.
  assert.equal(view.confirmRequired, true)
  assert.equal(view.lowConfidence, false)               // top match is high
})

test('a low-confidence top SOC match sets lowConfidence but never drops confirmRequired', () => {
  const view = socSuggestionsView({
    occupation: [{ code: '13-1111', title: 'Management Analysts', probability: 0.31 }]
  })
  assert.equal(view.lowConfidence, true)
  assert.equal(view.confirmRequired, true)              // still a legal representation
  // The confirm-required note + low-confidence note + confidence tiers localize
  // everywhere.
  for (const lang of SUPPORTED) {
    for (const k of ['h1b.soc.confirmNote', 'h1b.soc.lowConfidenceNote',
                     'h1b.soc.title', 'h1b.soc.suggest', 'h1b.soc.occupationTitle',
                     'h1b.soc.industryTitle', 'h1b.soc.sourceCaveat',
                     'h1b.soc.unavailable', 'h1b.soc.needJobTitle', 'h1b.soc.probability']) {
      assert.ok(STRINGS[lang][k], `${lang} ${k}`)
    }
    for (const tier of ['high', 'medium', 'low', 'unknown']) {
      assert.ok(STRINGS[lang][`h1b.soc.confidence.${tier}`], `${lang} ${tier}`)
    }
  }
  // The confirm note names the code as a legal representation the user confirms.
  assert.match(STRINGS.en['h1b.soc.confirmNote'], /confirm/i)
  assert.match(STRINGS.en['h1b.soc.confirmNote'], /legal representation/i)
})

test('socSuggestionsView honest-degrades when the classifier is unavailable', () => {
  assert.equal(socSuggestionsView({ available: false, reason: 'unconfigured' }).available, false)
  assert.equal(socSuggestionsView({}).available, false)
  assert.equal(socSuggestionsView(null).available, false)
  // Unavailable still reports confirmRequired true (defensive default).
  assert.equal(socSuggestionsView(null).confirmRequired, true)
})

test('the employer console renders the wage caveats and the SOC confirm-required note', async () => {
  const src = await readFile(
    new URL('../../src/renderer/src/screens/EmployerConsole.jsx', import.meta.url), 'utf8')
  // The two official-data actions are wired to the pinned client methods and
  // render through the honest view helpers.
  assert.ok(src.includes('h1bWageAnalysis'), 'wage check must call h1bWageAnalysis')
  assert.ok(src.includes('h1bClassifyOccupation'), 'SOC suggest must call h1bClassifyOccupation')
  assert.ok(src.includes('wageLevelView') && src.includes('socSuggestionsView'),
    'the console must render through the honest view helpers')
  // The caveats block renders, keyed to the localized caveat strings.
  assert.ok(src.includes('h1b-wage-caveats'), 'the caveats block must be distinguishable')
  assert.ok(/h1b\.wage\.caveat\./.test(src), 'caveats must localize through h1b.wage.caveat.*')
  // The confirm-required notes render (both the SOC code note and the wage note).
  assert.ok(src.includes('h1b-soc-confirm-note'), 'the SOC confirm note must be distinguishable')
  assert.ok(src.includes("t('h1b.soc.confirmNote')"), 'the SOC confirm note must render its string')
  assert.ok(src.includes("t('h1b.wage.confirmNote')"), 'the wage panel must render its confirm note')
  // Nothing auto-fills a filed answer: neither suggestion panel ever calls the
  // party-answers writer — the petitioner types the confirmed value in.
  const panels = src.slice(src.indexOf('function WageLevelCheck'),
                           src.indexOf('function JobAnswersForm'))
  assert.ok(panels.length > 0, 'the suggestion panels must exist above JobAnswersForm')
  assert.ok(!/h1bPartyAnswers/.test(panels),
    'a suggestion panel must never write a filed answer')
})
