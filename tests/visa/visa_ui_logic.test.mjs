// Hermetic unit tests for the Visa Console's pure logic (no backend / DOM).
import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  confidenceLevel, fieldRows, documentReady, defaultPreferences, formatFee,
  handoffCopy, formatSlot, isTerminal, dateToMs, msToDate, newSession, newAdminSession, newQualitySession, qualityRecordRoute, resultDisposition,
  isDocumentQuestion, splitQuestions, isValidDateShape, collectAnswers
} from '../../src/renderer/src/lib/visaSession.js'
import { HANDOFF_UI, HANDOFF_SIGNAL, HANDOFF_COPY } from '../../src/renderer/src/lib/visaBackend.js'
import { arrivalCardLines } from '../../src/renderer/src/lib/arrivalCard.js'
import { publishedStayText } from '../../src/renderer/src/lib/publishedStay.js'
import { applicationLane, applicationStepLinkIndex } from '../../src/renderer/src/lib/applicationLane.js'
import { t as translate, SUPPORTED } from '../../src/renderer/src/lib/i18n.js'

const ETA_APP = {
  disposition: 'ELECTRONIC_AUTHORIZATION_REQUIRED',
  requirement_detail: 'eta_electronic_authorization',
  application_channel: 'online_portal',
  official_portal_url: 'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601#HowTo',
  application_channel_detail: 'Apply using the Australian ETA app, outside Australia. Initial ETA applications are not filed through the ordinary ImmiAccount web form.',
}

test('ETA601 app guidance has one honest presentation for tile, forms and step links', () => {
  const before = structuredClone(ETA_APP)
  const lane = applicationLane(ETA_APP)
  assert.equal(lane.kind, 'australian_eta_app')
  assert.equal(lane.href, ETA_APP.official_portal_url)
  assert.equal(lane.linkKey, lane.inlineKey)
  assert.equal(translate('en', lane.channelKey), 'Apply using the Australian ETA app')
  assert.match(translate('en', lane.linkKey), /instructions/)
  assert.doesNotMatch(JSON.stringify(lane), /appstore|apps\.apple|play\.google|immiaccount/i)
  assert.deepEqual(ETA_APP, before) // presentation does not alter eligibility
})

test('all three locales describe an app and an instruction link, not web filing', () => {
  const lane = applicationLane(ETA_APP)
  for (const lang of SUPPORTED) {
    assert.match(translate(lang, lane.sectionKey), /ETA/)
    assert.match(translate(lang, lane.linkKey), /instructions|指南|指引/)
    assert.match(translate(lang, lane.channelKey), /app|应用|應用程式/)
    assert.notEqual(translate(lang, lane.linkKey), translate(lang, 'db.portalStart'))
  }
})

test('the ETA reference belongs to the app instruction, not a later conditional ImmiAccount request', () => {
  const lane = applicationLane(ETA_APP)
  assert.equal(applicationStepLinkIndex([
    'Download and open the official Australian ETA app.',
    'If requested after submitting, provide further information through ImmiAccount.',
  ], lane), 0)
  assert.equal(applicationStepLinkIndex(['If requested, respond online in ImmiAccount.'], lane), -1)
})

test('Visitor600 genuine web applications keep their own website and filing label', () => {
  const g = {...ETA_APP, disposition:'VISA_REQUIRED', requirement_detail:'evisa',
    visa_category:'Visitor (subclass 600)', official_portal_url:'https://online.immi.gov.au/lusc/login',
    application_channel_detail:'Apply online through ImmiAccount. Thai passport holders cannot use the Australian ETA app.'}
  const lane = applicationLane(g)
  assert.equal(lane.kind, 'standard')
  assert.equal(lane.href, g.official_portal_url)
  assert.equal(lane.linkKey, 'db.portalStart')
  assert.equal(applicationStepLinkIndex(['Create an ImmiAccount online.'], lane), 0)
})

for (const [name, change] of [
  ['optional mention', {application_channel_detail:'You may use the Australian ETA app for other products.'}],
  ['missing required instruction', {application_channel_detail:null}],
  ['visa-free verdict', {disposition:'VISA_EXEMPT'}],
  ['different product subtype', {requirement_detail:'evisa'}],
  ['Canada ETA', {official_portal_url:'https://www.canada.ca/en/immigration-refugees-citizenship/services/visit-canada/eta.html'}],
  ['Visitor600 reference', {official_portal_url:'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/visitor-600'}],
  ['lookalike host', {official_portal_url:'https://immi.homeaffairs.gov.au.example.com/visas/getting-a-visa/visa-listing/electronic-travel-authority-601'}],
  ['insecure reference', {official_portal_url:ETA_APP.official_portal_url.replace('https:', 'http:')}],
  ['credentials in reference', {official_portal_url:ETA_APP.official_portal_url.replace('https://', 'https://user@')}],
]) test(`app presentation does not infer an ETA lane from ${name}`, () => {
  assert.equal(applicationLane({...ETA_APP, ...change}).kind, 'standard')
})

test('arrival filing preserves conditions and exclusions when required is unknown', () => {
  const card={required:null,name:'TWAC',submission_window:'Within7 days',
    notes:'Required for multiple-entry permit holders; resident holders are excluded.'}
  assert.deepEqual(arrivalCardLines(card),['TWAC, Within7 days',card.notes])
  assert.deepEqual(arrivalCardLines({...card,required:false}),['TWAC',card.notes])
  assert.deepEqual(arrivalCardLines({required:null,name:'TWAC'}),[])
  assert.deepEqual(arrivalCardLines({required:true}),['Arrival card'])
  assert.deepEqual(arrivalCardLines({required:true},value=>value,'入境卡'),['入境卡'])
  assert.deepEqual(arrivalCardLines({...card,notes:[card.notes],note:card.notes},s=>'translated:'+s),
    ['translated:TWAC, translated:Within7 days','translated:'+card.notes])
})

test('operator navigation shares the current tab login without a privileged default', (t) => {
  const prior = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage')
  t.after(() => {
    if (prior) Object.defineProperty(globalThis, 'sessionStorage', prior)
    else delete globalThis.sessionStorage
  })
  const values = new Map()
  Object.defineProperty(globalThis, 'sessionStorage', {
    configurable: true, value: { getItem: (key) => values.get(key) || null }
  })
  assert.equal(newAdminSession().token, '')
  values.set('ellis_operator_access', 'test-only-operator-session')
  assert.equal(newAdminSession().token, 'test-only-operator-session')
  assert.equal(newSession().token, 'dev-token')
  assert.equal(newAdminSession({ token: 'explicit-test-key' }).token, 'explicit-test-key')
  values.delete('ellis_operator_access')
  assert.equal(newAdminSession().token, '')
  Object.defineProperty(globalThis, 'sessionStorage', {
    configurable: true, get() { throw new Error('storage access denied') }
  })
  assert.equal(newAdminSession().token, '')
})

test('quality control opens without a key and keeps one public browser attribution', (t) => {
  const prior = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage')
  t.after(() => {
    if (prior) Object.defineProperty(globalThis, 'sessionStorage', prior)
    else delete globalThis.sessionStorage
  })
  const values = new Map()
  Object.defineProperty(globalThis, 'sessionStorage', { configurable: true,
    value: { getItem: (k) => values.get(k), setItem: (k, v) => values.set(k, v) } })
  const first = newQualitySession()
  assert.equal(first.token, 'public-quality-control')
  assert.equal(first.orgId, 'platform')
  assert.equal(first.userId, newQualitySession().userId)
  assert.equal(values.has('ellis_operator_access'), false)
  Object.defineProperty(globalThis, 'sessionStorage', { configurable: true,
    get() { throw new Error('storage denied') } })
  assert.equal(newQualitySession().token, 'public-quality-control')
})

test('record actions preserve non-tourism and special-document scope', () => {
  assert.deepEqual(qualityRecordRoute({ travel_document_country: 'CAN',
    destination_country: 'JPN', travel_purpose: 'business',
    travel_document_type: 'diplomatic_passport' }), {
      nationality: 'CAN', destination: 'JPN', travel_purpose: 'business',
      travel_document_type: 'diplomatic_passport' })
})

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

test('additional_information handoff is fully mapped', () => {
  assert.equal(HANDOFF_UI.additional_information, 'AdditionalInfoModal')
  assert.equal(HANDOFF_SIGNAL.additional_information, 'provide_information')
  assert.equal(HANDOFF_COPY.additional_information[0], 'Additional information required')
  assert.ok(HANDOFF_COPY.additional_information[1].length > 0)
})

test('isDocumentQuestion recognizes document asks by kind and key prefix', () => {
  assert.equal(isDocumentQuestion({ key: 'document:photo', kind: 'document' }), true)
  assert.equal(isDocumentQuestion({ key: 'document:photo', kind: 'text' }), true)
  assert.equal(isDocumentQuestion({ key: 'religion', kind: 'text' }), false)
  assert.equal(isDocumentQuestion(null), false)
})

test('splitQuestions separates typed questions from document asks', () => {
  const { inputQuestions, documentQuestions } = splitQuestions([
    { key: 'religion', kind: 'text', mandatory: true },
    { key: 'document:portrait_photo', kind: 'document', mandatory: true },
    { key: 'dob', kind: 'date', mandatory: true },
    { key: '' },      // no key -> dropped
    null              // junk -> dropped
  ])
  assert.deepEqual(inputQuestions.map((q) => q.key), ['religion', 'dob'])
  assert.deepEqual(documentQuestions.map((q) => q.key), ['document:portrait_photo'])
  // A missing/absent questions payload never throws.
  assert.deepEqual(splitQuestions(undefined), { inputQuestions: [], documentQuestions: [] })
})

test('isValidDateShape accepts MM/DD/YYYY and rejects obvious slips', () => {
  assert.equal(isValidDateShape('08/01/2026'), true)
  assert.equal(isValidDateShape('8/1/2026'), true)
  assert.equal(isValidDateShape('2026-08-01'), false)   // ISO is typed as US here
  assert.equal(isValidDateShape('13/01/2026'), false)   // month out of range
  assert.equal(isValidDateShape('08/32/2026'), false)   // day out of range
  assert.equal(isValidDateShape('soon'), false)
  assert.equal(isValidDateShape(''), false)
})

test('collectAnswers blocks empty mandatory fields and bad dates', () => {
  const questions = [
    { key: 'religion', kind: 'text', mandatory: true },
    { key: 'entry_date', kind: 'date', mandatory: true },
    { key: 'note', kind: 'text', mandatory: false }
  ]
  const { answers, errors } = collectAnswers(questions, { entry_date: 'not-a-date' })
  assert.equal(errors.religion, 'addinfo.errRequired')
  assert.equal(errors.entry_date, 'addinfo.errDate')
  assert.equal('note' in errors, false)          // optional + empty -> fine
  assert.deepEqual(answers, {})                  // nothing valid to send
})

test('collectAnswers returns ONLY answered keys, trimmed', () => {
  const questions = [
    { key: 'religion', kind: 'text', mandatory: true },
    { key: 'entry_date', kind: 'date', mandatory: true },
    { key: 'note', kind: 'text', mandatory: false },
    { key: 'document:photo', kind: 'document', mandatory: true } // never typed
  ]
  const { answers, errors } = collectAnswers(questions, {
    religion: '  None  ', entry_date: '08/01/2026', note: '', 'document:photo': 'x'
  })
  assert.deepEqual(errors, {})
  assert.deepEqual(answers, { religion: 'None', entry_date: '08/01/2026' })
})

test('collectAnswers with only document questions yields no answers, no errors', () => {
  const { answers, errors } = collectAnswers(
    [{ key: 'document:portrait_photo', kind: 'document', mandatory: true }], {})
  assert.deepEqual(answers, {})
  assert.deepEqual(errors, {})
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

// Same formatter for the route headline, product table and record detail.
import { publishedFeeText } from '../../src/renderer/src/lib/publishedFee.js'
test('published visa fees preserve a from qualifier and existing fixed/free formats', () => {
  assert.equal(publishedFeeText({ amount: 250, currency: 'AUD', qualifier: 'from' }), 'From 250 AUD')
  assert.equal(publishedFeeText({ amount: 250, currency: 'AUD' }), '250 AUD')
  assert.equal(publishedFeeText({ amount: 0, currency: 'AUD' }), 'None')
  assert.equal(publishedFeeText({ amount: 0, currency: 'AUD' }, { zeroLabel: 'Free' }), 'Free')
  assert.equal(publishedFeeText({ amount: 0, currency: 'AUD', qualifier: 'from' }), 'From 0 AUD')
  assert.equal(publishedFeeText({ amount: 250, currency: 'AUD', qualifier: 'from' }, { fromLabel: '起价' }), '起价 250 AUD')
  for (const amount of [null, undefined, NaN, Infinity, -1, false, '']) {
    assert.equal(publishedFeeText({ amount, currency: 'AUD' }), null)
  }
})


test('India long-validity products preserve the calendar-year limit over the bare 180-day number', () => {
  const product = { type: '1-year e-Tourist Visa', max_stay_days: 180,
    permitted_stay: 'Cumulative stay must not exceed 180 days per calendar year; follow the issued permission for each visit.' }
  const before = structuredClone(product)
  assert.equal(publishedStayText(product), product.permitted_stay)
  assert.deepEqual(product, before)
})

test('qualified product stay retains entry conditions, discretion and explicit uncertainty', () => {
  for (const permitted_stay of [
    'Up to 30 days only if holding a valid qualifying residence permit.',
    'Maximum 6 weeks granted on entry, at the immigration officer’s discretion.',
    'Permitted stay is not yet confirmed; check the issued permission.',
  ]) assert.equal(publishedStayText({permitted_stay, max_stay_days: 180}), permitted_stay)
})

test('all supported locales render qualified text through the catalog and numeric fallback through local strings', () => {
  const source = 'Cumulative stay must not exceed 180 days per calendar year.'
  const localized = [source, '每个日历年累计停留不得超过180天。', '每個曆年累計停留不得超過180天。']
  for (const [index, lang] of SUPPORTED.entries()) {
    const seen = []
    assert.equal(publishedStayText({permitted_stay: source, max_stay_days: 180}, {
      translate: value => { seen.push(value); return localized[index] },
      formatDays: n => translate(lang, 'db.upToDays', {n}),
    }), localized[index])
    assert.deepEqual(seen, [source])
    assert.equal(publishedStayText({max_stay_days: 30}, {
      formatDays: n => translate(lang, 'db.upToDays', {n}),
    }), translate(lang, 'db.upToDays', {n: 30}))
  }
})

test('missing translation preserves the full qualified stay rather than dropping to a number', () => {
  const permitted_stay = 'Up to 30 days if the entry conditions are met.'
  assert.equal(publishedStayText({permitted_stay, max_stay_days: 30}, {translate: () => ''}), permitted_stay)
})

test('numeric-only and blank-text products retain their own day fallback', () => {
  for (const permitted_stay of [null, undefined, '', '  '])
    assert.equal(publishedStayText({permitted_stay, max_stay_days: 30}), 'Up to 30 days')
  assert.equal(publishedStayText({max_stay_days: '14'}), 'Up to 14 days')
})

test('missing or malformed numeric stays remain unknown without a parent or sibling fallback', () => {
  for (const max_stay_days of [null, undefined, 0, -1, false, true, '', 'bad', Infinity])
    assert.equal(publishedStayText({max_stay_days}, {unknownLabel: 'Unknown stay'}), 'Unknown stay')
  assert.equal(publishedStayText(null, {unknownLabel: 'Unknown stay'}), 'Unknown stay')
})


import fs from 'node:fs'
import { checkRequirements } from '../../src/renderer/src/lib/checkRequirements.js'
const checkRoute = { passport_nationality: 'HKG', destination_country: 'JPN',
  travel_purpose: 'tourism', travel_document_type: 'ordinary_passport' }

for (const field of ['biometrics_required', 'interview_required', 'appointment_required']) {
  for (const value of [true, false]) {
    test(`${field}=${value} without backend stage never becomes a border or application decision`, () => {
      const g = { [field]: value }; const before = structuredClone(g)
      const [r] = checkRequirements(g)
      assert.equal(r.valueKey, 'db.checkRequirementUnknown')
      assert.equal(r.stageKey, 'db.checkStageUnknown')
      assert.equal(r.tone, null)
      assert.deepEqual(g, before)
    })
  }
}

for (const value of [null, undefined, 0, 1, 'false', [], {}]) {
  test(`missing/malformed flag ${String(value)} never becomes a known requirement`, () => {
    assert.deepEqual(checkRequirements({biometrics_required: value}), [])
  })
}

test('claimed proof metadata cannot create a second frontend scope-verification contract', () => {
  const p = { status: 'reviewed', verifier: 'ai', source_url: 'https://www.mofa.go.jp/visa/',
    verified_at: '2026-09-10', quote: 'Source statement.', subject: checkRoute,
    requirement_stage: 'visa_application', reviewed_value: false }
  for (const proof of [undefined, p, { ...p, requirement_stage: 'border_entry' },
    { ...p, status: 'partial' }, { ...p, status: 'unknown' },
    { ...p, subject: { ...checkRoute, product_type: 'Optional long-stay paper visa' } },
    { ...p, source_url: 'https://unrelated.example/' },
    { ...p, verified_at: 'invalid' }, { ...p, verified_at: '2099-01-01' }]) {
    for (const value of [true, false]) {
      const [r] = checkRequirements({biometrics_required: value},
        {field_provenance: {biometrics_required: proof}}, checkRoute)
      assert.equal(r.scopeConfirmed, false)
      assert.equal(r.valueKey, 'db.checkRequirementUnknown')
    }
  }
})

test('visa exemption and a no-application channel never manufacture scope', () => {
  const [r] = checkRequirements({biometrics_required: false,
    disposition: 'VISA_EXEMPT', application_channel: 'not_required'})
  assert.equal(r.valueKey, 'db.checkRequirementUnknown')
  assert.equal(r.stageKey, 'db.checkStageUnknown')
})

test('original flags, actual border instructions and application workflow are retained unchanged', () => {
  const g = {biometrics_required: false, interview_required: true, appointment_required: false,
    required_documents: ['Submit fingerprints at the visa application centre.'],
    entry_requirements: ['Fingerprinting and photograph checks apply at the border, subject to exemptions.'],
    submission_process: ['Attend the visa appointment.']}
  const before = structuredClone(g)
  assert.equal(checkRequirements(g).length, 3)
  assert.deepEqual(g, before)
})

test('all locale labels describe unconfirmed scope and never a border exemption', () => {
  const keys = ['db.checksAndAppointments', 'db.checkStageUnknown',
    'db.checkRequirementUnknown', 'db.checkScopeExplanation']
  for (const lang of SUPPORTED) {
    for (const key of keys) assert.notEqual(translate(lang, key), key)
    assert.notEqual(translate(lang, 'db.checkRequirementUnknown'), translate(lang, 'db.notRequired'))
  }
})

test('renderer isolates legacy checks from entry facts and preserves existing stay/prose consumers', () => {
  const source = fs.readFileSync(new URL('../../src/renderer/src/screens/TravelDatabase.jsx', import.meta.url), 'utf8')
  const entry = source.slice(source.indexOf('  const entryFacts ='), source.indexOf('  const documents ='))
  assert.ok(!entry.includes('g.biometrics_required') && !entry.includes('g.interview_required') && !entry.includes('g.appointment_required'))
  assert.match(entry, /g\.insurance_required/)
  assert.match(source, /checkRequirements\(g\)/)
  assert.match(source, /Section title=\{t\('db.checksAndAppointments'\)\}/)
  assert.match(source, /publishedStayText/)
  assert.match(source, /itemsOf\(g\.required_documents\)/)
  assert.match(source, /itemsOf\(g\.submission_process\)/)
})

import { entryInstructionTexts, publishedEntryInstructions } from '../../src/renderer/src/lib/entryInstructions.js'

const AIR = 'For international arrivals by air, the health declaration is mandatory before immigration clearance. Complete it before boarding, as requested; the form can be completed 24 hours before arrival. Land/sea scope has not been established.'
const LAND = 'Air and sea arrivals use All Indonesia. Land arrivals use ECD Bea Cukai. Follow the applicable declaration instructions at https://beacukai.go.id/ecd.'

test('route instructions preserve mode, mandatory/requested timing and exact URLs', () => {
  for (const text of [AIR, LAND, '陆路旅客按海关规定提交；不把提前窗口改成截止期限。']) {
    assert.deepEqual(publishedEntryInstructions({entry_requirements:text}), {route:[text], products:[]})
  }
})
test('equal product instructions are shown once while differing product scope remains named', () => {
  const input={entry_requirements:AIR,visa_products:[
    {type:'e-Visa',entry_requirements:AIR},
    {type:'Border permission',entry_requirements:LAND},
    {type:'Unreviewed alternative'},
  ]}
  const before=structuredClone(input)
  assert.deepEqual(publishedEntryInstructions(input),{route:[AIR],products:[{index:1,name:'Border permission',texts:[LAND]}]})
  assert.deepEqual(input,before)
})
test('product rules are not copied to the default or a sibling when route prose is absent', () => {
  assert.deepEqual(publishedEntryInstructions({visa_products:[{type:'A',entry_requirements:LAND},{type:'B'}]}),
    {route:[],products:[{index:0,name:'A',texts:[LAND]}]})
})
test('missing held guidance and malformed values cannot create new instructions', () => {
  for(const value of [null,undefined,{},true,42]) assert.deepEqual(publishedEntryInstructions(value),{route:[],products:[]})
  assert.deepEqual(entryInstructionTexts([null,false,{},'  ',AIR]),[AIR])
  assert.deepEqual(publishedEntryInstructions({entry_requirements:{required:false},visa_products:{type:'A'}}),{route:[],products:[]})
})
test('condition lists retain order and partial overlap is not mistaken for equivalent product scope', () => {
  assert.deepEqual(publishedEntryInstructions({entry_requirements:[AIR],visa_products:[{type:'Permit',entry_requirements:[AIR,LAND]}]}),
    {route:[AIR],products:[{index:0,name:'Permit',texts:[AIR,LAND]}]})
})
