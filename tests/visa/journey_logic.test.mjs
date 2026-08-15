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
  // The employer persona is a DOOR, not a home: a bare URL always returns to
  // the applicant menu, and the stale 'employer' flag is cleared so it can
  // never hijack a later visit (owner decision 2026-08-13). Admin (an ops
  // tool) still persists — see below.
  const s = memStorage()
  detectPersona({ hash: '#employer', storage: s })
  assert.equal(detectPersona({ hash: '', storage: s }), 'applicant')
  assert.equal(s.getItem('ellis_persona'), null)
  // Admin DOES persist across a bare-URL load.
  const sa = memStorage()
  detectPersona({ hash: '#admin', storage: sa })
  assert.equal(detectPersona({ hash: '', storage: sa }), 'admin')
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

// ---------------------------------------------------------------------------
// The filing + appointment cockpits (Agent 7). docs/MAX_AUTOMATION_SPEC.md's
// "one screen per filing": everything prepared, everything missing, and ONE
// action that opens the applicant's own secure window and stops there. The
// appointment surface (docs/APPOINTMENTS_DESIGN.md) carries the same boundary:
// Ellis prepares to the edge of a person's act and never books a slot.
import {
  filingCockpitView, appointmentTriageView, appointmentPrestageView,
  groupRosterView, appointmentAvailabilityView, deepLinkView, tapsToDone,
  bookingView, GROUP_MIN_MEMBERS, HUMAN_ACT_KEYS
} from '../../src/renderer/src/lib/visaBackend.js'

test('bookingView: slots name their reader, booked exists only behind evidence', () => {
  // No request yet -> an honest nonexistence, never an invented state.
  assert.equal(bookingView({ exists: false }).exists, false)
  assert.equal(bookingView(null).exists, false)

  const offered = bookingView({
    exists: true, id: 'b1', case_id: 'c1', route: 'us_b1b2',
    status: 'slots_offered', posts: ['Beijing'],
    offered_slots: [{ post: 'Beijing', when: '2026-10-12T09:30',
                      recorded_by: 'operator-1', recorded_at: '2026-08-14T10:00:00Z' }],
    slots_notice: 'not live inventory',
    legal_basis: { basis: 'official FAQ', limit: 'agency, not automation' },
    human_acts: [{ act: 'Read the calendar', who: 'a Trip.com operator' }]
  })
  assert.equal(offered.active, true)
  assert.equal(offered.offeredSlots[0].recordedBy, 'operator-1')
  assert.ok(offered.slotsNotice)
  assert.equal(offered.isRealGovernmentResult, false)
  assert.equal(offered.neverBooks, true)

  // A hand-built payload claiming booked WITHOUT the evidence pair renders as
  // NOT a real government result — the client repeats the server's rule.
  const fake = bookingView({ exists: true, id: 'b2', status: 'booked',
                             is_real_government_result: true, confirmation: {} })
  assert.equal(fake.isRealGovernmentResult, false)
  assert.equal(fake.confirmation, null)

  // The real thing: number + evidence document.
  const booked = bookingView({ exists: true, id: 'b3', status: 'booked',
    is_real_government_result: true,
    picked_slot: { post: 'Beijing', when: '2026-10-12T09:30' },
    confirmation: { number: 'USV-42', evidence_document_id: 'doc9',
                    recorded_by: 'operator-1' } })
  assert.equal(booked.isRealGovernmentResult, true)
  assert.equal(booked.confirmation.number, 'USV-42')
  assert.equal(booked.active, false)
})

test('this wave’s client methods hit the pinned endpoint paths with the pinned bodies', async () => {
  const calls = []
  const realFetch = globalThis.fetch
  globalThis.fetch = async (url, opts = {}) => {
    calls.push({ url: String(url), method: opts.method || 'GET', body: opts.body })
    return { ok: true, status: 200, text: async () => '{}', blob: async () => ({}) }
  }
  try {
    const c = createVisaClient(newEmployerSession())
    // Appointment cockpit (app/appt_api.py).
    await c.appointmentTriage('c1', 'zh-CN')
    await c.appointmentPrestage('c1')
    await c.appointmentGroupRoster({ case_ids: ['c1', 'c2'], group_kind: 'tour_group' })
    await c.downloadGroupRoster({ caseIds: ['c1', 'c2'], format: 'csv', post: 'Beijing' })
    await c.appointmentAvailability({ post: 'Beijing', country: 'CHN' })
    // Travel authorizations + Schengen stay (app/travel_api.py).
    await c.travelAuthorizations({ nationality: 'CHN', destination: 'USA' })
    await c.travelAuthorization('esta')
    await c.schengenStay({ stays: [{ entry: '2026-01-02', exit: '2026-01-20' }],
                           asOf: '2026-09-01' })
    // H1B ops (app/h1b/ops_api.py).
    await c.h1bBulkRegistration('org-1', { case_ids: ['c1'] })
    await c.h1bRfeAssemble('c1', { issues: ['specialty_occupation'] })
    await c.h1bRfeIssues()
    await c.h1bCapExemption('c1')
    await c.h1bCapExemptionQuestions()
    // ETA-9141 (both editions) + the public access file.
    await c.prepareEta9141('c1', 'zh-Hant')
    await c.h1bPafManifest('c1')
    await c.h1bPafNotice('c1')
    await c.h1bPafPosting('c1', { method: 'hard_copy', locations: ['lobby', 'break room'] })
    await c.h1bPafPackage('c1')
  } finally {
    globalThis.fetch = realFetch
  }
  const seen = calls.map((x) => `${x.method} ${new URL(x.url).pathname}`)
  assert.deepEqual(seen, [
    'GET /appointments/triage/c1',
    'GET /appointments/prestage/c1',
    'POST /appointments/group-roster',
    'GET /appointments/group-roster/export',
    'GET /appointments/availability',
    'GET /travel/authorizations',
    'GET /travel/authorizations/esta',
    'GET /travel/schengen-stay',
    'POST /h1b/orgs/org-1/bulk-registration',
    'POST /h1b/cases/c1/rfe/assemble',
    'GET /h1b/rfe/issues',
    'GET /h1b/cases/c1/cap-exemption',
    'GET /h1b/cap-exemption/questions',
    'POST /h1b/cases/c1/forms/eta-9141/prepare',
    'GET /h1b/cases/c1/paf/manifest',
    'POST /h1b/cases/c1/paf/notice',
    'POST /h1b/cases/c1/paf/posting',
    'GET /h1b/cases/c1/paf/package'
  ])
  const byPath = {}
  for (const x of calls) byPath[new URL(x.url).pathname] = x
  // The locale rides as a query parameter on every localized endpoint.
  assert.equal(new URL(byPath['/appointments/triage/c1'].url).searchParams.get('locale'), 'zh-CN')
  assert.equal(new URL(byPath['/h1b/cases/c1/forms/eta-9141/prepare'].url)
    .searchParams.get('locale'), 'zh-Hant')
  // The roster body carries the full GroupRosterRequest shape, defaults filled
  // in — never an undefined field the server would have to guess at.
  const roster = JSON.parse(byPath['/appointments/group-roster'].body)
  assert.deepEqual(roster.case_ids, ['c1', 'c2'])
  assert.equal(roster.group_kind, 'tour_group')
  assert.equal(roster.include_passport_numbers, false)   // identifiers are opt-IN
  // The export repeats case_id once per member (FastAPI Query(list)); a
  // URLSearchParams array would have flattened it into one comma value.
  const exportUrl = new URL(byPath['/appointments/group-roster/export'].url)
  assert.deepEqual(exportUrl.searchParams.getAll('case_id'), ['c1', 'c2'])
  assert.equal(exportUrl.searchParams.get('format'), 'csv')
  assert.equal(exportUrl.searchParams.get('post'), 'Beijing')
  // The Schengen stay history rides as JSON in the query the endpoint declares,
  // and the day count is asked for, never posted as a decision.
  const stayUrl = new URL(byPath['/travel/schengen-stay'].url)
  assert.deepEqual(JSON.parse(stayUrl.searchParams.get('stays')),
    [{ entry: '2026-01-02', exit: '2026-01-20' }])
  assert.equal(stayUrl.searchParams.get('as_of'), '2026-09-01')
  // The bulk-registration body carries the whole BulkRegistrationBody shape:
  // invalid rows are opt-IN, because USCIS rejects a batch that contains them.
  const bulk = JSON.parse(byPath['/h1b/orgs/org-1/bulk-registration'].body)
  assert.deepEqual(bulk, { case_ids: ['c1'], fiscal_year: null, include_invalid: false })
  // The RFE body defaults every optional field rather than sending undefined,
  // and no deadline is ever invented client-side.
  const rfe = JSON.parse(byPath['/h1b/cases/c1/rfe/assemble'].body)
  assert.deepEqual(rfe.issues, ['specialty_occupation'])
  assert.equal(rfe.response_due_date, '')
  assert.equal(rfe.generate_pdf, false)
  assert.deepEqual(JSON.parse(byPath['/h1b/cases/c1/paf/posting'].body),
    { method: 'hard_copy', locations: ['lobby', 'break room'],
      individual_direct_email: false })
})

test('the agent-channel booking client methods hit the pinned paths with the pinned bodies', async () => {
  const calls = []
  const realFetch = globalThis.fetch
  globalThis.fetch = async (url, opts = {}) => {
    calls.push({ url: String(url), method: opts.method || 'GET', body: opts.body })
    return { ok: true, status: 200, text: async () => '{}', blob: async () => ({}) }
  }
  try {
    const c = createVisaClient(newEmployerSession())
    await c.bookingCreate('c1', { route: 'us_b1b2', posts: ['Beijing'] })
    await c.bookingForCase('c1')
    await c.bookingPick('r1', 2, { post: 'Beijing', when: '2026-10-12T09:30' })
    await c.bookingCancel('r1')
    await c.bookingQueue()
    await c.bookingOfferSlots('r1', [{ post: 'Beijing', when: '2026-10-12' }])
    await c.bookingEvidence('r1', { name: 'c.png', mime: 'image/png', content_b64: 'AAA' })
    await c.bookingBooked('r1', { confirmation_number: 'X', evidence_document_id: 'd1' })
    await c.bookingFailed('r1', 'no slots')
    await c.bookingAgentReadSlots('r1')
    await c.bookingAgentBook('r1')
  } finally {
    globalThis.fetch = realFetch
  }
  const seen = calls.map((x) => `${x.method} ${new URL(x.url).pathname}`)
  assert.deepEqual(seen, [
    'POST /appointments/booking/cases/c1',
    'GET /appointments/booking/cases/c1',
    'POST /appointments/booking/r1/pick',
    'POST /appointments/booking/r1/cancel',
    'GET /appointments/booking/queue',
    'POST /appointments/booking/r1/offer-slots',
    'POST /appointments/booking/r1/evidence',
    'POST /appointments/booking/r1/booked',
    'POST /appointments/booking/r1/failed',
    'POST /appointments/booking/r1/agent/read-slots',
    'POST /appointments/booking/r1/agent/book'
  ])
  const byPath = {}
  for (const x of calls) byPath[`${x.method} ${new URL(x.url).pathname}`] = x
  // Pick echoes the SEEN slot (post + when) so consent binds to it, not a
  // position a concurrent re-offer could change under the applicant.
  assert.deepEqual(JSON.parse(byPath['POST /appointments/booking/r1/pick'].body),
    { index: 2, post: 'Beijing', when: '2026-10-12T09:30' })
  // Booked carries the full evidence pair; a bare call still defaults them so
  // the server never has to guess an undefined field.
  assert.deepEqual(JSON.parse(byPath['POST /appointments/booking/r1/booked'].body),
    { confirmation_number: 'X', evidence_document_id: 'd1', note: '' })
  assert.deepEqual(JSON.parse(byPath['POST /appointments/booking/r1/failed'].body),
    { reason: 'no slots' })
  // The create body defaults posts/windows/note rather than sending undefined.
  const created = JSON.parse(byPath['POST /appointments/booking/cases/c1'].body)
  assert.deepEqual(created, { route: 'us_b1b2', posts: ['Beijing'],
                             date_windows: [], note: '' })
})

test('filingCockpitView shows what is prepared with its source, and what is missing', () => {
  const view = filingCockpitView({
    form_key: 'eta-9141', filled_count: 22, total_mapped: 30,
    derived: [{ key: 'soc_title', label: 'Occupation title', value: 'Software Developers',
                source: 'the DOL SOC list' }],
    missing: [{ key: 'worksite_city', label: 'Worksite city',
                question: 'Which city will the worker actually work in?' }],
    human_only: [{ key: 'signature', label: 'Employer signature' }],
    preparation_notice: 'PREPARATION COPY — the real request is made in FLAG.'
  }, { sessionCaseId: 'child-1' })
  assert.equal(view.available, true)
  assert.equal(view.filledCount, 22)
  assert.equal(view.totalCount, 30)
  // Every prepared value names where it came from.
  assert.equal(view.prepared[0].source, 'the DOL SOC list')
  assert.equal(view.prepared[0].sourceKnown, true)
  // Each missing item carries the ONE input that clears it, in real words.
  assert.equal(view.missingCount, 1)
  assert.equal(view.missing[0].input, 'Which city will the worker actually work in?')
  assert.deepEqual(view.humanOnly, ['Employer signature'])
  assert.ok(view.notices.includes('PREPARATION COPY — the real request is made in FLAG.'))
  // A prepared value with no recorded source says so rather than inventing one.
  const noSource = filingCockpitView({ prepared: [{ key: 'fein', label: 'FEIN' }] })
  assert.equal(noSource.prepared[0].sourceKnown, false)
  assert.equal(noSource.prepared[0].source, '')
})

test('the cockpit’s single action opens a window and never performs a human act', () => {
  // The acts arrive IN the payload (app/filing_acts.py is the only author);
  // this is the shape the prepare endpoint actually sends.
  const ready = filingCockpitView({
    form_key: 'i-129', filled_count: 40, total_mapped: 50,
    human_acts: [
      { key: 'login', who: 'the signatory', why: 'personal account', non_delegable: true },
      { key: 'sign', who: 'the signatory', why: 'perjury', non_delegable: true },
      { key: 'pay', who: 'the petitioner', why: 'no card details' },
      { key: 'submit', who: 'the signatory', why: 'their legal act' }
    ]
  }, { sessionCaseId: 'child-1' })
  assert.equal(ready.action.enabled, true)
  assert.equal(ready.action.labelKey, 'cockpit.action.open')
  // Pinned: the action opens a window. It never signs, pays, or submits.
  assert.equal(ready.action.performsHumanAct, false)
  // The acts that remain are NAMED, so the surface can never read as "done".
  assert.deepEqual(ready.humanActs.map((a) => a.key), ['login', 'sign', 'pay', 'submit'])
  for (const a of ready.humanActs) assert.ok(HUMAN_ACT_KEYS.includes(a.key))
  // No child filing case yet: the button is honestly disabled with a reason,
  // never a control that cannot work.
  const notStarted = filingCockpitView({ form_key: 'i-129', filled_count: 1, total_mapped: 2 })
  assert.equal(notStarted.action.enabled, false)
  assert.equal(notStarted.action.reasonKey, 'cockpit.action.notStarted')
  // A payload that names no acts yields NONE: the curated per-form list this
  // client used to invent is gone, and the surface renders an honest unknown.
  assert.deepEqual(notStarted.humanActs, [])
  // The backend's own typed acts render with their server sentence.
  const fromServer = filingCockpitView({
    filled_count: 1, total_mapped: 2,
    human_acts: [{ act: 'submit the group appointment request', who: 'the coordinator',
                   ellis_does: 'assembles and validates the roster' }]
  }, { sessionCaseId: 'c1' })
  assert.equal(fromServer.humanActs.length, 1)
  assert.equal(fromServer.humanActs[0].who, 'the coordinator')
})

test('taps-to-done is the backend’s counted measure or an honest unknown, never a guess', () => {
  // The count arrives in the payload, counted server-side from the named
  // human acts (app/filing_acts.py). The old frontend table is gone.
  assert.deepEqual(
    tapsToDone({ taps_to_done: { known: true, min: 4, max: 4, basis: 'counted from acts' } }),
    { known: true, min: 4, max: 4, exact: 4, reason: 'counted from acts' })
  // A conditional act (a fee screen, a CAPTCHA) makes the honest answer a range.
  const range = tapsToDone({ taps_to_done: { known: true, min: 3, max: 5 } })
  assert.equal(range.exact, null)
  assert.equal(range.max, 5)
  // No count in the payload, an unknown one, or a half-stated one all read as
  // UNKNOWN rather than a number someone would quote back as a promise.
  assert.equal(tapsToDone({}).known, false)
  assert.equal(tapsToDone(null).known, false)
  assert.equal(tapsToDone({ taps_to_done: { known: false, reason: 'filed by mail' } }).known, false)
  assert.equal(tapsToDone({ taps_to_done: { known: false, reason: 'filed by mail' } }).reason,
    'filed by mail')
  assert.equal(tapsToDone({ taps_to_done: { known: true, min: 2 } }).known, false) // no max
  assert.equal(filingCockpitView({ form_key: 'eta-9141', filled_count: 1, total_mapped: 2 })
    .taps.known, false)
  // ...and the payload's own count flows through the cockpit view untouched.
  assert.equal(filingCockpitView({ form_key: 'i-129', filled_count: 1, total_mapped: 2,
    taps_to_done: { known: true, min: 4, max: 4 } }).taps.exact, 4)
})

test('the act-key vocabulary matches app/filing_acts.py exactly', async () => {
  // filing_acts.py promises every act key it emits is in the UI vocabulary
  // (a key outside it renders as a blank line). Pin the two lists to each
  // other across the language boundary so neither can drift alone.
  const py = await readFile(
    new URL('../../backend/app/filing_acts.py', import.meta.url), 'utf8')
  const m = py.match(/ACT_VOCABULARY = \(([^)]+)\)/)
  assert.ok(m, 'ACT_VOCABULARY not found in filing_acts.py')
  const backendVocab = [...m[1].matchAll(/"([a-z]+)"/g)].map((x) => x[1])
  assert.deepEqual(backendVocab.sort(), [...HUMAN_ACT_KEYS].sort())
})

test('the consular form payload parses into an available cockpit view', () => {
  // main.py get_consular_form speaks `filled`/`total`/`missing_required`; the
  // view must read that shape or the endpoint's acts and gaps never render.
  const view = filingCockpitView({
    available: true, form_key: 'schengen_uniform', filled: 12, total: 30,
    missing_required: [{ key: 'birth_place', label: 'Place of birth' }],
    human_acts: [
      { key: 'review', who: 'the applicant' },
      { key: 'sign', who: 'the applicant, by hand', non_delegable: true },
      { key: 'submit', who: 'the applicant, in person' }
    ],
    taps_to_done: { known: false, reason: 'lodged in person on paper' }
  }, { sessionCaseId: 'c1' })
  assert.equal(view.available, true)
  assert.equal(view.filledCount, 12)
  assert.equal(view.totalCount, 30)
  assert.equal(view.missing[0].label, 'Place of birth')
  assert.deepEqual(view.humanActs.map((a) => a.key), ['review', 'sign', 'submit'])
  assert.equal(view.taps.known, false)
  assert.equal(view.taps.reason, 'lodged in person on paper')
  // A `filled` LIST (the prepared-rows key on other payloads) is never
  // mistaken for a count.
  assert.equal(filingCockpitView({ filled: [{ label: 'FEIN' }] }).filledCount, null)
})

test('filingCockpitView honest-degrades and splits a PAF manifest by real status', () => {
  assert.equal(filingCockpitView({ available: false, reason: 'no lca step' }).available, false)
  assert.equal(filingCockpitView({}).available, false)
  assert.equal(filingCockpitView(null).available, false)
  assert.equal(filingCockpitView(null).action.enabled, false)
  // A 655.760(a) manifest: present items are prepared, and partial/missing/
  // unknown are all reported missing — an unanswered conditional item is never
  // quietly dropped.
  const paf = filingCockpitView({
    // The manifest names its one act itself (paf_api's envelope) — the client
    // no longer holds a per-form list to fall back on.
    human_acts: [{ key: 'review', who: 'the employer',
                   why: 'the file is the employer’s own record' }],
    taps_to_done: { known: false, reason: 'kept in the employer’s own records' },
    items: [
      { item_id: 'certified_lca', title: 'Certified LCA', status: 'present',
        citation: '20 CFR 655.760(a)(1)' },
      { item_id: 'wage_rate', title: 'Wage rate documentation', status: 'partial',
        next_action: 'Ellis holds the facts; the file still needs the document itself.' },
      { item_id: 'notice_documentation', title: 'Notice posting record', status: 'missing',
        next_action: 'record how the 655.734 notice was given' },
      { item_id: 'h1b_dependent', title: 'H-1B dependent attestations', status: 'unknown',
        condition_question: 'Is the employer H-1B dependent?' },
      { item_id: 'corporate', title: 'Corporate change documents', status: 'not_applicable' }
    ]
  }, { routeKey: 'paf' })
  assert.equal(paf.documents.length, 1)
  assert.equal(paf.documents[0].citation, '20 CFR 655.760(a)(1)')
  assert.deepEqual(paf.missing.map((m) => m.key),
    ['wage_rate', 'notice_documentation', 'h1b_dependent'])
  // Each gap carries the backend's own next action; an unanswered conditional
  // item asks its question rather than being dropped or assumed inapplicable.
  assert.match(paf.missing[0].input, /still needs the document itself/)
  assert.equal(paf.missing[2].input, 'Is the employer H-1B dependent?')
  assert.equal(paf.notApplicable[0].key, 'corporate')
  assert.deepEqual(paf.humanActs.map((a) => a.key), ['review'])
  // The public access file is never filed on a government site, so the single
  // action is honestly unavailable — not "not started yet", which would imply a
  // window is coming.
  assert.equal(paf.action.enabled, false)
  assert.equal(paf.action.reasonKey, 'cockpit.action.noPortal')
  assert.equal(filingCockpitView({ items: [{ label: 'x', status: 'present' }] },
    { routeKey: 'paf', sessionCaseId: 'c1' }).action.enabled, false)
})

test('appointment triage keeps every verdict a genuine tri-state', () => {
  // The real appt_eligibility.triage shape, inside appt_api's envelope: the
  // verdict block, the route-specific block, and the backend's own "unknown"
  // STRING, which must land as a null and never as a false.
  const schengen = appointmentTriageView({
    available: true,
    triage: { route: 'schengen', member_state: 'FRA',
      schengen: { required: false, within_59_months: true },
      submission_without_appearance_permitted: true,
      verdict: { in_person_required: false, biometrics_required: false,
                 agent_deliverable_end_to_end: true,
                 summary: 'Fingerprints can be reused, so an accredited agent can carry the whole filing.' },
      human_acts: [{ act: 'sign_mandate', label: 'Sign the mandate authorising the intermediary',
                     who: 'applicant', non_delegable: true, why: 'Art. 45 requires it.' }],
      open_questions: [] }
  })
  assert.equal(schengen.available, true)
  assert.equal(schengen.route, 'schengen')
  assert.equal(schengen.memberState, 'FRA')
  assert.equal(schengen.inPersonRequired, false)
  assert.equal(schengen.biometricsRequired, false)
  assert.equal(schengen.visBiometricsReusable, true)
  assert.equal(schengen.agentEndToEnd, true)
  assert.match(schengen.summary, /accredited agent/)
  // The act renders its server-localized SENTENCE, not its bare key, and keeps
  // the non-delegable flag.
  assert.match(schengen.humanActs[0].act, /Sign the mandate/)
  assert.equal(schengen.humanActs[0].nonDelegable, true)
  assert.equal(schengen.neverBooks, true)
  // First-time applicant: appearance is required and non-delegable, and that
  // is a REAL false on reuse.
  const firstTime = appointmentTriageView({ triage: { route: 'schengen',
    schengen: { required: true, within_59_months: false },
    verdict: { in_person_required: true, biometrics_required: true } } })
  assert.equal(firstTime.visBiometricsReusable, false)
  assert.equal(firstTime.biometricsRequired, true)
  assert.equal(firstTime.inPersonRequired, true)
  // A biometrics EXEMPTION is not 59-month reuse: "no fingerprints needed"
  // must never be reported as "fingerprints can be reused".
  const exempt = appointmentTriageView({ triage: { route: 'schengen',
    schengen: { required: false, within_59_months: null },
    verdict: { in_person_required: false, biometrics_required: false } } })
  assert.equal(exempt.biometricsRequired, false)
  assert.equal(exempt.visBiometricsReusable, null)
  // US: the interview waiver IS "no interview needed"; EVUS is its own answer.
  const us = appointmentTriageView({ triage: { route: 'us',
    us: { needed: false }, evus: { required: false },
    verdict: { in_person_required: false, interview_required: false } } })
  assert.equal(us.interviewWaiverEligible, true)
  assert.equal(us.evusRequired, false)
  const interview = appointmentTriageView({ triage: { route: 'us',
    us: { needed: true }, evus: { required: true } } })
  assert.equal(interview.interviewWaiverEligible, false)
  assert.equal(interview.evusRequired, true)
  // The backend's UNKNOWN string is an unknown, not a "no".
  const unknown = appointmentTriageView({ triage: { route: 'us',
    us: { needed: 'unknown', missing_facts: ['prior visa issue date'] },
    evus: { required: 'unknown' },
    verdict: { in_person_required: 'unknown' },
    open_questions: [{ question: 'Is the visa a 10-year B-1/B-2?',
                       resolve_with: 'the visa sticker’s validity dates' }] } })
  assert.equal(unknown.inPersonRequired, null)
  assert.equal(unknown.interviewWaiverEligible, null)
  assert.equal(unknown.evusRequired, null)
  assert.equal(unknown.visBiometricsReusable, null)
  // An all-unknown triage is still an ANSWER: it says what would settle it.
  assert.equal(unknown.available, true)
  assert.equal(unknown.openQuestions[0].resolveWith, 'the visa sticker’s validity dates')
  assert.equal(appointmentTriageView(null).available, false)
  assert.equal(appointmentTriageView(null).neverBooks, true)   // the note survives
})

test('the group roster enforces nothing itself: only the backend calls it submittable', () => {
  const members = Array.from({ length: 12 }, (_, i) => ({ case_id: `c${i}`, full_name: `T ${i}` }))
  const ok = groupRosterView({ roster: { members, submittable: true } },
    { groupKind: 'tour_group' })
  assert.equal(ok.count, 12)
  assert.equal(ok.submittable, true)
  assert.deepEqual(ok.preChecks, [])
  assert.equal(ok.coordinatorSubmits, true)
  assert.equal(ok.neverBooks, true)
  // Fewer than 10 travelling together: Ellis's own advisory pre-check fires.
  const few = groupRosterView({ roster: { members: members.slice(0, 4) } })
  assert.deepEqual(few.preChecks.map((c) => c.code), ['too_few'])
  assert.equal(few.preChecks[0].count, 4)
  assert.equal(GROUP_MIN_MEMBERS, 10)
  // Families and relatives are excluded from the channel by the consulate.
  const family = groupRosterView({ roster: { members } }, { groupKind: 'family' })
  assert.ok(family.preChecks.some((c) => c.code === 'family_excluded'))
  // A member missing what the consulate requires is named, not dropped.
  const gap = groupRosterView({ roster: { members: [
    { case_id: 'c1', full_name: 'A', missing: ['DS-160 confirmation number'] },
    { case_id: 'c2', full_name: 'B' }] } })
  assert.equal(gap.incompleteMembers.length, 1)
  assert.deepEqual(gap.incompleteMembers[0].missing, ['DS-160 confirmation number'])
  assert.ok(gap.preChecks.some((c) => c.code === 'members_incomplete'))
  // Silence is NOT a green light: an unstated submittable stays null.
  assert.equal(gap.submittable, null)
  assert.equal(groupRosterView(null).submittable, null)
  assert.equal(groupRosterView(null).available, false)
})

test('availability shows published estimates and only https official links', () => {
  // The real appt_availability.availability shape: snapshot metadata in
  // wait_time_data, the records in wait_times, the requested post in wait_time.
  const view = appointmentAvailabilityView({
    available: true,
    wait_time_data: { available: true, as_of: '2026-07-01' },
    wait_time: { post: 'Beijing', category: 'visitor', category_label: 'Visitor (B1/B2)',
                 wait_days: 88, known: true, as_of: '2026-07-01' },
    wait_times: [{ post: 'Shanghai', category: 'visitor', wait_days: 102,
                   as_of: '2026-07-01' }],
    source: 'U.S. Department of State published wait times',
    why_no_live_slots: 'No booking system publishes an availability feed.',
    official_links: [
      { kind: 'wait_times', label: 'Global visa wait times',
        url: 'https://travel.state.gov/content/travel/en/us-visas/wait-times.html' },
      { kind: 'appointment_system', label: 'US visa appointment service',
        url: 'https://www.usvisascheduling.com/' },
      { kind: 'insecure', label: 'Not https', url: 'http://insecure.example.com/slots' },
      { kind: 'other', label: 'Slot watcher', url: 'https://slot-sniper.example.com/china' },
      { kind: 'official_application_portal', label: 'Member state portal — not determined',
        url: '', description: 'Ellis has no verified official portal for this member state yet.' }
    ]
  })
  assert.equal(view.available, true)
  assert.equal(view.entries[0].post, 'Beijing')
  assert.equal(view.entries[0].waitDays, 88)
  assert.equal(view.entries[1].post, 'Shanghai')
  assert.equal(view.estimateOnly, true)
  assert.equal(view.liveSlotData, false)
  assert.equal(view.neverBooks, true)
  assert.match(view.whyNoLiveSlots, /no booking system publishes/i)
  // http:// is dropped entirely; https survives and is CLASSIFIED, not hidden;
  // and an unverified destination is kept, unlinked, rather than disappearing.
  assert.deepEqual(view.links.map((l) => l.kind),
    ['government', 'authorized_provider', 'insecure', 'unrecognized', 'not_determined'])
  assert.equal(view.links[2].url, '')          // insecure: kept, but never linked
  assert.equal(view.links[3].official, false)
  assert.equal(view.links[4].url, '')
  assert.match(view.links[4].description, /no verified official portal/)
  // A post with no published figure is unknown, never interpolated to a number.
  const partial = appointmentAvailabilityView({
    available: true, wait_time_data: { available: true },
    wait_times: [{ post: 'Wuhan', category: 'visitor', wait_days: null }] })
  assert.equal(partial.entries[0].waitDays, null)
  // Nothing published: an explicit unavailable, never an invented date.
  const none = appointmentAvailabilityView({
    available: false, wait_time_data: { available: false, reason: 'no snapshot placed' } })
  assert.equal(none.available, false)
  assert.equal(none.reason, 'no snapshot placed')
  assert.equal(none.neverBooks, true)
  // deepLinkView refuses anything that is not https.
  assert.equal(deepLinkView('javascript:alert(1)'), null)
  assert.equal(deepLinkView('data:text/html,<b>x'), null)
  assert.equal(deepLinkView('http://travel.state.gov'), null)
  assert.equal(deepLinkView(''), null)
  assert.equal(deepLinkView('https://ceac.state.gov/genniv/').kind, 'government')
  assert.equal(deepLinkView('https://visa.vfsglobal.com/chn/en/fra').kind, 'authorized_provider')
})

test('the pre-stage view never claims anything was filed, paid or booked', () => {
  // The real appt_appointments_prestage.prestage shape.
  const view = appointmentPrestageView({
    available: true,
    prestage: {
      route: 'us_b1b2',
      filled: [{ key: 'surname', label: 'Surname', value: 'CAO',
                 source: 'passport (machine-readable zone)', required: true }],
      missing: [
        { key: 'travel_dates', label: 'Intended travel dates', required: true,
          kind: 'form_answer', source: 'ask',
          how_to_resolve: 'Tell Ellis the dates you plan to travel.' },
        { key: 'previous_visits', label: 'Previous US visits', required: false,
          kind: 'form_answer', source: 'ask', how_to_resolve: 'Optional; answer if you have any.' }
      ],
      documents: [{ id: 'd1', label: 'Passport', status: 'submitted' }],
      fees: [{ key: 'mrv', label: 'MRV visa application fee (B1/B2)', amount: 185,
               currency: 'USD', per: 'applicant', payer: 'applicant',
               payment_channels: [{ label: 'China CITIC Bank' }],
               ellis_never: 'Ellis never enters card, bank or UnionPay details.' }],
      readiness: { filled: 12, form_fields: 20, missing_required: 1, missing_optional: 1 },
      human_acts: [{ act: 'pay_mrv', label: 'Pay the MRV fee', who: 'applicant',
                     non_delegable: true }],
      submitted: false
    }
  })
  assert.equal(view.available, true)
  assert.equal(view.nothingFiled, true)
  assert.equal(view.neverBooks, true)
  assert.equal(view.prepared[0].source, 'passport (machine-readable zone)')
  // The ONE input that clears each gap is the backend's own how-to-resolve.
  assert.equal(view.missing[0].input, 'Tell Ellis the dates you plan to travel.')
  // An optional question blocks nothing, so the blocking count is its own
  // number rather than a total that overstates what is in the way.
  assert.equal(view.missingCount, 2)
  assert.equal(view.missingRequiredCount, 1)
  assert.equal(view.filledCount, 12)
  assert.equal(view.totalCount, 20)
  assert.equal(view.documents[0].label, 'Passport')
  // The fee is exact, with its official channel, and carries the server's own
  // "Ellis never pays this" sentence.
  assert.equal(view.fees[0].amount, 185)
  assert.deepEqual(view.fees[0].channels, ['China CITIC Bank'])
  assert.match(view.fees[0].ellisNever, /never enters card/)
  assert.equal(view.humanActs[0].nonDelegable, true)
  assert.equal(appointmentPrestageView(null).available, false)
  assert.equal(appointmentPrestageView(null).nothingFiled, true)
})

test('every cockpit string exists in every locale and the single action never claims a submission', () => {
  const keys = [
    'cockpit.title', 'cockpit.sub', 'cockpit.open', 'cockpit.load', 'cockpit.loading',
    'cockpit.refresh', 'cockpit.unavailable', 'cockpit.notice.nothingFiled',
    'cockpit.prepared.title', 'cockpit.prepared.count', 'cockpit.prepared.empty',
    'cockpit.prepared.source', 'cockpit.prepared.sourceUnknown', 'cockpit.prepared.documents',
    'cockpit.prepared.narrative', 'cockpit.prepared.narrativeDraft', 'cockpit.prepared.wage',
    'cockpit.prepared.wageLine', 'cockpit.prepared.notApplicable', 'cockpit.missing.title',
    'cockpit.missing.count', 'cockpit.missing.none', 'cockpit.missing.input',
    'cockpit.humanOnly.title', 'cockpit.taps.title', 'cockpit.taps.exact', 'cockpit.taps.range',
    'cockpit.taps.unknown', 'cockpit.taps.note', 'cockpit.acts.title', 'cockpit.acts.never',
    'cockpit.acts.who', 'cockpit.acts.ellis', 'cockpit.action.open', 'cockpit.action.opening',
    'cockpit.action.close', 'cockpit.action.note', 'cockpit.action.disabled',
    'cockpit.action.notStarted', 'cockpit.action.noPortal', 'cockpit.noForm',
    'cockpit.filedAt', 'cockpit.employer.title',
    'cockpit.employer.sub', 'cockpit.paf.title', 'h1b.form.eta9141',
    'appt.open', 'appt.title', 'appt.sub', 'appt.neverBooks', 'appt.acts.title',
    'appt.acts.who', 'appt.acts.ellis', 'appt.triage.title', 'appt.triage.run',
    'appt.triage.checking', 'appt.triage.required', 'appt.triage.notRequired',
    'appt.triage.unknown', 'appt.triage.vis59.reuse', 'appt.triage.vis59.required',
    'appt.triage.vis59.unknown', 'appt.triage.waiver.eligible', 'appt.triage.waiver.notEligible',
    'appt.triage.waiver.unknown', 'appt.triage.evus.required', 'appt.triage.evus.notRequired',
    'appt.triage.evus.unknown', 'appt.prestage.title', 'appt.prestage.load',
    'appt.prestage.nothingDone', 'appt.prestage.ready', 'appt.prestage.missing',
    'appt.prestage.empty', 'appt.prestage.feeTitle', 'appt.prestage.feeLine',
    'appt.prestage.feeChannels', 'appt.prestage.readiness',
    'appt.triage.bio.required', 'appt.triage.bio.notRequired', 'appt.triage.bio.unknown',
    'appt.triage.questions', 'appt.triage.resolveWith', 'cockpit.acts.nonDelegable',
    'appt.group.title', 'appt.group.sub',
    'appt.group.caseIds', 'appt.group.name', 'appt.group.kind', 'appt.group.post',
    'appt.group.coordinator', 'appt.group.build', 'appt.group.building', 'appt.group.export',
    'appt.group.members', 'appt.group.preCheck', 'appt.group.tooFew',
    'appt.group.familyExcluded', 'appt.group.incomplete', 'appt.group.submittable',
    'appt.group.notSubmittable', 'appt.group.submittableUnknown', 'appt.group.coordinatorActs',
    'appt.avail.title', 'appt.avail.sub', 'appt.avail.load', 'appt.avail.post',
    'appt.avail.country', 'appt.avail.unavailable', 'appt.avail.asOf', 'appt.avail.waitDays',
    'appt.avail.officialLink', 'appt.avail.unofficialLink', 'appt.avail.estimate',
    'appt.avail.waitUnknown', 'appt.avail.linkNotDetermined', 'appt.avail.insecureLink'
  ]
  for (const lang of SUPPORTED) {
    for (const k of keys) assert.ok(STRINGS[lang][k], `${lang} missing ${k}`)
    for (const act of HUMAN_ACT_KEYS) {
      assert.ok(STRINGS[lang][`cockpit.act.${act}`], `${lang} cockpit.act.${act}`)
    }
    for (const kind of ['tour_group', 'company', 'school', 'family']) {
      assert.ok(STRINGS[lang][`appt.group.kind.${kind}`], `${lang} ${kind}`)
    }
    // THE button label: it opens a window. In no language does it claim to
    // submit, sign, pay, or file anything.
    const label = STRINGS[lang]['cockpit.action.open']
    assert.ok(!/submit|sign|pay\b|file it|files|book/i.test(label), `${lang}: ${label}`)
    assert.ok(!/提交|遞交|递交|付款|簽署|签署|預訂|预订/.test(label), `${lang}: ${label}`)
  }
  // The English copy says what it does and what it does not.
  assert.match(STRINGS.en['cockpit.action.open'], /open secure window/i)
  assert.match(STRINGS.en['cockpit.acts.never'], /never logs in, signs, pays, submits/i)
  assert.match(STRINGS.en['appt.neverBooks'], /never searches for or books/i)
  assert.match(STRINGS.en['cockpit.notice.nothingFiled'], /nothing on this screen has been filed/i)
  // The group pre-check names the consulate's own numbers, not Ellis's.
  assert.ok(STRINGS.en['appt.group.tooFew'].includes('{min}'))
  assert.ok(STRINGS.en['appt.group.tooFew'].includes('{count}'))
})

test('FilingCockpit renders the three sections and calls no signing, paying or submitting method', async () => {
  const src = await readFile(
    new URL('../../src/renderer/src/components/visa/FilingCockpit.jsx', import.meta.url), 'utf8')
  // The three things the spec allows on this screen, and nothing else.
  for (const id of ['cockpit-prepared', 'cockpit-missing', 'cockpit-action']) {
    assert.ok(src.includes(`data-testid="${id}"`), `missing section ${id}`)
  }
  // The taps target and the named human acts sit BESIDE the button.
  assert.ok(src.includes('data-testid="cockpit-taps"'))
  assert.ok(src.includes('data-testid="cockpit-human-acts"'))
  assert.ok(src.includes("t('cockpit.acts.never')"))
  assert.ok(src.includes("t('cockpit.notice.nothingFiled')"))
  // A payload that names no acts renders the honest unknown line — the
  // cockpit never falls back to a client-side list (there is none left).
  assert.ok(src.includes('data-testid="cockpit-acts-unknown"'))
  assert.ok(src.includes("t('cockpit.acts.unknown')"))
  // The backend's own no-tap-count reason renders when it gives one.
  assert.ok(src.includes('data-testid="cockpit-taps-reason"'))
  // The single action opens the secure window and nothing else: no client
  // method that signs, pays, submits, or resolves a personal handoff appears.
  for (const forbidden of ['completePayment', 'approvePayment', 'approvePaymentExact',
                           'providePaymentDetails', 'signAuthorization', 'signFinalReview',
                           'solveCaptcha', 'selectAppointment', 'recordAppointment',
                           'completeDeclaration']) {
    assert.ok(!src.includes(forbidden), `the cockpit must never call ${forbidden}`)
  }
  // It renders through the honest view model, never its own normalization.
  assert.ok(src.includes('filingCockpitView'))
})

test('AppointmentCockpit always shows the human-acts note and never books', async () => {
  const src = await readFile(
    new URL('../../src/renderer/src/components/visa/AppointmentCockpit.jsx', import.meta.url), 'utf8')
  // The never-books note is rendered UNCONDITIONALLY, at the top of the
  // surface's own return — before any payload arrives, and whether or not one
  // ever does.
  assert.ok(src.includes('data-testid="appt-never-books"'))
  const main = src.slice(src.indexOf('export default function AppointmentCockpit'))
  assert.match(main, /return \([\s\S]{0,500}<NeverBooksNote/,
    'the never-books note must render at the top of the cockpit, unconditionally')
  assert.ok(src.includes("t('appt.neverBooks')"))
  // The four surfaces, each wired to its pinned client method.
  for (const m of ['appointmentTriage', 'appointmentPrestage', 'appointmentGroupRoster',
                   'appointmentAvailability', 'downloadGroupRoster']) {
    assert.ok(src.includes(m), `missing client method ${m}`)
  }
  // Nothing here picks, holds, or confirms a slot.
  for (const forbidden of ['selectAppointment', 'recordAppointment', 'approveReschedule',
                           'solveCaptcha', 'providePaymentDetails']) {
    assert.ok(!src.includes(forbidden), `the appointment cockpit must never call ${forbidden}`)
  }
  // The three-way verdict component exists, so an unknown can never render as
  // a "no".
  assert.ok(src.includes('unknownKey'))
  assert.ok(src.includes('appt.triage.vis59.unknown'))
})

test('both cockpits are wired into the employer console and the H1B pipeline', async () => {
  const console_ = await readFile(
    new URL('../../src/renderer/src/screens/EmployerConsole.jsx', import.meta.url), 'utf8')
  assert.ok(console_.includes('FilingCockpit'), 'the employer console must mount the filing cockpit')
  assert.ok(console_.includes('AppointmentCockpit'),
    'the employer console must mount the appointment cockpit')
  // The existing petitioner branches survive untouched.
  assert.ok(console_.includes('<H1bPipeline'))
  assert.ok(console_.includes('<JobAnswersForm'))

  const pipeline = await readFile(
    new URL('../../src/renderer/src/components/visa/H1bPipeline.jsx', import.meta.url), 'utf8')
  assert.ok(pipeline.includes('<FilingCockpit'), 'each filing step must offer its cockpit')
  assert.ok(pipeline.includes('<AppointmentCockpit'),
    'the consular leg must offer the appointment cockpit')
  // The secure window belongs to the CHILD filing case; the parent petition is
  // a container with no portal session of its own.
  assert.match(pipeline, /sessionCaseId=\{step\.child_case_id \|\| ''\}/)
  // The ETA-9141 rides the LCA step beside the ETA-9035.
  assert.match(pipeline, /FORM_KEYS_BY_STEP = \{ lca: \['eta-9035', 'eta-9141'\]/)
  assert.ok(pipeline.includes('prepareEta9141'))
})
