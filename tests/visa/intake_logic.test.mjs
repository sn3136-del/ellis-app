// Hermetic unit tests for the route-intake pure logic (no backend / DOM).
import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  conditionField, missingRequired, readinessMeta, checksSummary,
  validEmail, datesOrdered,
  researchStageMeta, researchTerminal, researchStatusMeta, RESEARCH_STEP_KEYS
} from '../../src/renderer/src/lib/intake.js'
import { STRINGS } from '../../src/renderer/src/lib/i18n.js'

// ---------------------------------------------------------------------------
// conditionField
// ---------------------------------------------------------------------------

const RESIDENCE_FIELD = {
  key: 'residence_status',
  required: false,
  condition: 'lawful_country_of_residence != passport_nationality'
}

test('conditionField: hidden when residence equals nationality', () => {
  assert.equal(conditionField(RESIDENCE_FIELD, {
    lawful_country_of_residence: 'USA', passport_nationality: 'USA'
  }), false)
})

test('conditionField: visible when residence differs from nationality', () => {
  assert.equal(conditionField(RESIDENCE_FIELD, {
    lawful_country_of_residence: 'FRA', passport_nationality: 'USA'
  }), true)
})

test('conditionField: hidden while either driving answer is still empty', () => {
  assert.equal(conditionField(RESIDENCE_FIELD, {}), false)
  assert.equal(conditionField(RESIDENCE_FIELD, { passport_nationality: 'USA' }), false)
  assert.equal(conditionField(RESIDENCE_FIELD, { lawful_country_of_residence: '' , passport_nationality: 'USA' }), false)
})

test('conditionField: no condition -> always visible', () => {
  assert.equal(conditionField({ key: 'age', required: true }, {}), true)
  assert.equal(conditionField({ key: 'age', condition: null }, {}), true)
})

test('conditionField: unknown/unsafe condition shapes default to visible', () => {
  assert.equal(conditionField({ key: 'x', condition: 'a == b' }, {}), true)
  assert.equal(conditionField({ key: 'x', condition: 'a != b != c' }, {}), true)
  assert.equal(conditionField({ key: 'x', condition: 'process.exit(1)' }, {}), true)
  assert.equal(conditionField(null, {}), true)
})

// ---------------------------------------------------------------------------
// missingRequired
// ---------------------------------------------------------------------------

const FIELDS = [
  { key: 'passport_nationality', required: true },
  { key: 'travel_document_type', required: true, default: 'ordinary_passport' },
  { key: 'visa_subtype', required: false },
  { key: 'email', required: true },
  { key: 'residence_status', required: true, condition: 'lawful_country_of_residence != passport_nationality' }
]

test('missingRequired: empty required fields are missing; defaults are not', () => {
  const missing = missingRequired(FIELDS, { passport_nationality: 'USA' })
  assert.ok(missing.includes('email'))
  assert.ok(!missing.includes('passport_nationality'))
  assert.ok(!missing.includes('travel_document_type'), 'a field with a default is never missing')
  assert.ok(!missing.includes('visa_subtype'), 'optional fields are never missing')
})

test('missingRequired: whitespace-only and empty-array answers count as missing', () => {
  const missing = missingRequired(
    [{ key: 'email', required: true }, { key: 'transit', required: true }],
    { email: '   ', transit: [] })
  assert.deepEqual(missing.sort(), ['email', 'transit'])
})

test('missingRequired: a hidden conditional field is not missing', () => {
  const missing = missingRequired(FIELDS, {
    passport_nationality: 'USA', lawful_country_of_residence: 'USA', email: 'a@b.co'
  })
  assert.ok(!missing.includes('residence_status'))
})

test('missingRequired: a visible conditional required field IS missing when empty', () => {
  const missing = missingRequired(FIELDS, {
    passport_nationality: 'USA', lawful_country_of_residence: 'FRA', email: 'a@b.co'
  })
  assert.ok(missing.includes('residence_status'))
})

test('missingRequired: tolerates empty/absent inputs', () => {
  assert.deepEqual(missingRequired(undefined, undefined), [])
  assert.deepEqual(missingRequired([], {}), [])
})

// ---------------------------------------------------------------------------
// readinessMeta
// ---------------------------------------------------------------------------

test('readinessMeta maps all 5 statuses to a tone + i18n key', () => {
  assert.deepEqual(readinessMeta('NOT_READY'), { tone: 'blocked', i18nKey: 'readiness.NOT_READY' })
  assert.deepEqual(readinessMeta('PREPARATION_ONLY'), { tone: 'info', i18nKey: 'readiness.PREPARATION_ONLY' })
  assert.deepEqual(readinessMeta('APPLICANT_HANDOFF_READY'), { tone: 'info', i18nKey: 'readiness.APPLICANT_HANDOFF_READY' })
  assert.deepEqual(readinessMeta('LIVE_SANDBOX_READY'), { tone: 'warn', i18nKey: 'readiness.LIVE_SANDBOX_READY' })
  assert.deepEqual(readinessMeta('LIVE_PRODUCTION_READY'), { tone: 'ok', i18nKey: 'readiness.LIVE_PRODUCTION_READY' })
})

test('readinessMeta fails safe to blocked on unknown/missing statuses', () => {
  for (const bad of ['SOMETHING_NEW', '', null, undefined, 42]) {
    const meta = readinessMeta(bad)
    assert.equal(meta.tone, 'blocked', `status ${String(bad)} must fail safe`)
    assert.equal(meta.i18nKey, 'readiness.UNKNOWN')
  }
})

// ---------------------------------------------------------------------------
// checksSummary
// ---------------------------------------------------------------------------

const EXPECTED_KEYS = ['requirements', 'sources', 'conflicts', 'fee', 'jurisdiction', 'portal', 'passportRule', 'adapter']

test('checksSummary: a fully verified route yields ok rows', () => {
  const rows = checksSummary({
    snapshot_resolution: { matched: 'route', disposition: 'VERIFIED', research_status: 'verified' },
    source_evidence: { verified_count: 3, authorities: ['immigration_gov'], sample_urls: [] },
    conflicts: { open_for_destination: 0, route_conflicted: false },
    fees: { versions: 1, verified: true },
    consular_jurisdiction: { required: true, resolved: true, status: 'resolved', competent_post: 'Embassy X' },
    official_portal: { verified_count: 1, portals: [{ kind: 'evisa', url: 'https://x', operator_kind: 'government' }] },
    passport_validity_rule: { rule_kind: 'six_months_beyond_stay', note: '' },
    adapter: { records: 1, production_active: 1, sandbox_ready: 1 },
    readiness_reasoning: { requirements_verified: true }
  })
  assert.deepEqual(rows.map((r) => r.key), EXPECTED_KEYS)
  for (const r of rows) assert.equal(r.status, 'ok', `${r.key} should be ok`)
  const jur = rows.find((r) => r.key === 'jurisdiction')
  assert.match(jur.detail, /Embassy X/)
})

test('checksSummary: an unresearched route yields pending rows (never invented ok)', () => {
  const rows = checksSummary({
    snapshot_resolution: { matched: 'matrix_entry', disposition: 'RESEARCH_INCOMPLETE', research_status: 'not_researched' },
    source_evidence: { verified_count: 0, authorities: [] },
    conflicts: { open_for_destination: 0, route_conflicted: false },
    fees: { versions: 0, verified: false },
    consular_jurisdiction: { required: false, resolved: false, status: 'not_required', competent_post: null },
    official_portal: { verified_count: 0, portals: [] },
    passport_validity_rule: { rule_kind: 'unknown', note: 'no verified rule in snapshot — never guessed' },
    adapter: { records: 0, production_active: 0, sandbox_ready: 0 },
    readiness_reasoning: { requirements_verified: false }
  })
  const byKey = Object.fromEntries(rows.map((r) => [r.key, r]))
  assert.equal(byKey.requirements.status, 'pending')
  assert.equal(byKey.sources.status, 'pending')
  assert.equal(byKey.fee.status, 'pending')
  assert.equal(byKey.portal.status, 'pending')
  assert.equal(byKey.passportRule.status, 'pending')
  assert.equal(byKey.adapter.status, 'pending')
  // no conflicts + jurisdiction not required are genuinely fine
  assert.equal(byKey.conflicts.status, 'ok')
  assert.equal(byKey.jurisdiction.status, 'ok')
})

test('checksSummary: warns on conflicts, unresolved jurisdiction, sandbox-only adapter', () => {
  const rows = checksSummary({
    conflicts: { open_for_destination: 2, route_conflicted: true },
    consular_jurisdiction: { required: true, resolved: false, status: 'unresolved' },
    adapter: { records: 1, production_active: 0, sandbox_ready: 1 }
  })
  const byKey = Object.fromEntries(rows.map((r) => [r.key, r]))
  assert.equal(byKey.conflicts.status, 'warn')
  assert.equal(byKey.jurisdiction.status, 'warn')
  assert.equal(byKey.adapter.status, 'warn')
})

test('checksSummary never throws and never claims ok on missing/garbage input', () => {
  for (const input of [undefined, null, {}, { source_evidence: 'nope' }, { adapter: 17 }, []]) {
    const rows = checksSummary(input)
    assert.equal(rows.length, EXPECTED_KEYS.length)
    assert.deepEqual(rows.map((r) => r.key), EXPECTED_KEYS)
    for (const r of rows) {
      assert.ok(['ok', 'warn', 'pending'].includes(r.status))
      assert.equal(typeof r.labelKey, 'string')
      assert.equal(typeof r.detail, 'string')
    }
    const byKey = Object.fromEntries(rows.map((r) => [r.key, r]))
    // Nothing verifiable may present as ok without affirmative evidence…
    for (const k of ['requirements', 'sources', 'fee', 'portal', 'passportRule', 'adapter']) {
      assert.equal(byKey[k].status, 'pending', `${k} must stay pending on empty input`)
    }
    // …while absence of conflicts / no jurisdiction requirement is honestly ok.
    assert.equal(byKey.conflicts.status, 'ok')
    assert.equal(byKey.jurisdiction.status, 'ok')
  }
})

// ---------------------------------------------------------------------------
// researchStageMeta — 18 backend pipeline stages onto 9 applicant steps
// ---------------------------------------------------------------------------

// The full pipeline in execution order, with the expected applicant step.
const STAGES_IN_ORDER = [
  ['RECEIVE_ROUTE', 0], ['NORMALIZE_ROUTE', 0], ['CHECK_STORED_POLICY', 0],
  ['DISCOVER_OFFICIAL_SOURCES', 1], ['VERIFY_SOURCE_DOMAINS', 1], ['FETCH_SOURCE_CONTENT', 1],
  ['KIMI_EXTRACT_AND_TRANSLATE', 2], ['NORMALIZE_REQUIREMENTS', 2],
  ['RESOLVE_CONSULAR_JURISDICTION', 3],
  ['VERIFY_OFFICIAL_PORTAL', 4],
  ['VERIFY_ROUTE_FEES', 5],
  ['DETECT_CONFLICTS', 6],
  ['CREATE_IMMUTABLE_POLICY_VERSION', 7], ['LINK_ROUTE', 7], ['IMPORT_TO_POSTGRESQL', 7], ['EXPORT_TO_JSONL', 7],
  ['EVALUATE_ADAPTER_READINESS', 8], ['RETURN_ROUTE_STATUS', 8]
]

test('researchStageMeta: all 18 stages map to the expected applicant step', () => {
  assert.equal(STAGES_IN_ORDER.length, 18)
  assert.equal(RESEARCH_STEP_KEYS.length, 9)
  for (const [stage, order] of STAGES_IN_ORDER) {
    const meta = researchStageMeta(stage)
    assert.equal(meta.order, order, `${stage} should map to step ${order}`)
    assert.equal(meta.i18nKey, RESEARCH_STEP_KEYS[order], `${stage} i18n key`)
  }
})

test('researchStageMeta: unknown/missing stages fail safe to the FIRST step', () => {
  for (const bad of ['SOMETHING_NEW', '', null, undefined, 42]) {
    const meta = researchStageMeta(bad)
    assert.equal(meta.order, 0, `stage ${String(bad)} must map to the first step`)
    assert.equal(meta.i18nKey, RESEARCH_STEP_KEYS[0])
  }
})

test('researchStageMeta: ordering is monotone across the pipeline and covers all 9 steps', () => {
  let prev = -1
  const seen = new Set()
  for (const [stage] of STAGES_IN_ORDER) {
    const { order } = researchStageMeta(stage)
    assert.ok(order >= prev, `${stage} order ${order} must not go backwards (prev ${prev})`)
    assert.ok(order === prev || order === prev + 1, `${stage} must advance by at most one step`)
    prev = order
    seen.add(order)
  }
  assert.equal(researchStageMeta(STAGES_IN_ORDER[0][0]).order, 0)
  assert.equal(prev, 8, 'the pipeline must end on the last applicant step')
  assert.deepEqual([...seen].sort((a, b) => a - b), [0, 1, 2, 3, 4, 5, 6, 7, 8])
})

test('researchStageMeta: every step i18n key exists in every locale', () => {
  for (const key of RESEARCH_STEP_KEYS) {
    for (const locale of Object.keys(STRINGS)) {
      assert.ok(typeof STRINGS[locale][key] === 'string' && STRINGS[locale][key].length > 0,
        `${locale}:${key} must be defined`)
    }
  }
})

// ---------------------------------------------------------------------------
// researchTerminal
// ---------------------------------------------------------------------------

test('researchTerminal: every terminal status stops polling', () => {
  for (const s of ['complete', 'research_incomplete', 'conflicted', 'timed_out', 'failed']) {
    assert.equal(researchTerminal(s), true, `${s} is terminal`)
  }
})

test('researchTerminal: queued/running keep polling', () => {
  assert.equal(researchTerminal('queued'), false)
  assert.equal(researchTerminal('running'), false)
})

test('researchTerminal: unknown statuses are terminal (never poll forever)', () => {
  for (const bad of ['SOMETHING_NEW', 'RUNNING', '', null, undefined, 42]) {
    assert.equal(researchTerminal(bad), true, `status ${String(bad)} must stop polling`)
  }
})

// ---------------------------------------------------------------------------
// researchStatusMeta
// ---------------------------------------------------------------------------

test('researchStatusMeta maps every status to the honest tone', () => {
  assert.deepEqual(researchStatusMeta('complete'), { tone: 'ok', i18nKey: 'research.status.complete' })
  assert.deepEqual(researchStatusMeta('running'), { tone: 'info', i18nKey: 'research.status.running' })
  assert.deepEqual(researchStatusMeta('queued'), { tone: 'info', i18nKey: 'research.status.queued' })
  assert.deepEqual(researchStatusMeta('research_incomplete'), { tone: 'warn', i18nKey: 'research.status.research_incomplete' })
  assert.deepEqual(researchStatusMeta('timed_out'), { tone: 'warn', i18nKey: 'research.status.timed_out' })
  assert.deepEqual(researchStatusMeta('conflicted'), { tone: 'blocked', i18nKey: 'research.status.conflicted' })
  assert.deepEqual(researchStatusMeta('failed'), { tone: 'blocked', i18nKey: 'research.status.failed' })
})

test('researchStatusMeta fails safe to blocked on unknown statuses', () => {
  for (const bad of ['SOMETHING_NEW', 'COMPLETE', '', null, undefined, 42]) {
    const meta = researchStatusMeta(bad)
    assert.equal(meta.tone, 'blocked', `status ${String(bad)} must fail safe`)
    assert.equal(meta.i18nKey, 'research.status.UNKNOWN')
  }
})

test('researchStatusMeta: every status i18n key exists in every locale', () => {
  const keys = ['complete', 'running', 'queued', 'research_incomplete', 'timed_out', 'conflicted', 'failed']
    .map((s) => researchStatusMeta(s).i18nKey)
    .concat([researchStatusMeta('__unknown__').i18nKey])
  for (const key of keys) {
    for (const locale of Object.keys(STRINGS)) {
      assert.ok(typeof STRINGS[locale][key] === 'string' && STRINGS[locale][key].length > 0,
        `${locale}:${key} must be defined`)
    }
  }
})

// ---------------------------------------------------------------------------
// small helpers
// ---------------------------------------------------------------------------

test('validEmail accepts plausible addresses and rejects junk', () => {
  assert.equal(validEmail('a@b.co'), true)
  assert.equal(validEmail('  a@b.co '), true)
  assert.equal(validEmail('a@b'), false)
  assert.equal(validEmail('not-an-email'), false)
  assert.equal(validEmail(''), false)
  assert.equal(validEmail(null), false)
})

test('datesOrdered requires departure strictly after arrival when both set', () => {
  assert.equal(datesOrdered('2026-09-01', '2026-09-10'), true)
  assert.equal(datesOrdered('2026-09-10', '2026-09-01'), false)
  assert.equal(datesOrdered('2026-09-01', '2026-09-01'), false)
  assert.equal(datesOrdered('', '2026-09-01'), true, 'incomplete pairs are not an error yet')
  assert.equal(datesOrdered('2026-09-01', ''), true)
})
