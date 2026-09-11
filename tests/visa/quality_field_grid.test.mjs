import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import { build } from 'esbuild'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { t } from '../../src/renderer/src/lib/i18n.js'

// Compile the actual component in memory, without a production bundle or DOM.
const compiled = await build({
  stdin: { contents: "export { FieldGrid, NextSweepCountdown } from './src/renderer/src/screens/QualityConsole.jsx'",
    resolveDir: resolve('.'), sourcefile: 'quality-field-grid-entry.jsx' },
  bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic',
  external: ['react', 'react/jsx-runtime'], logLevel: 'silent',
})
const module = { exports: {} }
new Function('require', 'module', 'exports', compiled.outputFiles[0].text)(
  createRequire(import.meta.url), module, module.exports)
const { FieldGrid, NextSweepCountdown } = module.exports

function record(text, extra = {}) {
  // The backend marks a stay stated in words as filled with the unit Not
  // applicable (owner rule, 11 September 2026).
  return { max_stay_duration: null, max_stay_unit: null, max_stay_text: text,
    field_status: { max_stay_duration: text ? 'filled' : 'missing', max_stay_unit: text ? 'not-applicable' : 'missing' },
    completeness: 0.8, source_check: 'reference', held: true, ...extra }
}
function render(rec, lang = 'en', tvv = value => value) {
  return renderToStaticMarkup(createElement(FieldGrid, { rec, t: key => t(lang, key), tvv }))
}

for (const text of [
  'Usually six calendar months, decided on arrival',
  'Up to 3 months per visit; April–June conditions remain separate',
]) test('drilldown retains calendar wording without inventing days: ' + text, () => {
  const rec = record(text)
  const before = structuredClone(rec)
  const html = render(rec, 'en', () => 'A translation must not replace the exact stored stay wording')
  assert.ok(html.includes(text))
  assert.ok(html.includes(t('en', 'ops.stayTextOnly')))
  assert.ok(html.includes('✓'))
  assert.doesNotMatch(html, /90 days|180 days|A translation must/)
  assert.deepEqual(rec, before)
})

test('calendar wording does not promote an unverified or held record', () => {
  const rec = record('6 months')
  Object.freeze(rec.field_status); Object.freeze(rec)
  const html = render(rec)
  assert.ok(html.includes('6 months'))
  assert.equal(rec.field_status.max_stay_duration, 'filled')
  assert.equal(rec.field_status.max_stay_unit, 'not-applicable')
  assert.equal(rec.completeness, 0.8)
  assert.equal(rec.source_check, 'reference')
  assert.equal(rec.held, true)
})

test('a genuinely missing stay keeps the gap mark even when stale wording rides along', () => {
  const rec = record('As above', { field_status: { max_stay_duration: 'missing', max_stay_unit: 'missing' } })
  const html = render(rec)
  assert.ok(html.includes(t('en', 'ops.notPublished')))
  assert.ok(!html.includes(t('en', 'ops.stayTextOnly')))
  assert.ok(html.includes('✗'))
})

test('a visa-free record shows Not applicable: the backend sends no validity wording for it', () => {
  const rec = validityRecord(null, {
    field_status: { validity_duration: 'not-applicable', validity_unit: 'not-applicable' } })
  const html = render(rec)
  assert.ok(html.includes(t('en', 'ops.notApplicable')))
  assert.ok(!html.includes(t('en', 'ops.validityTextOnly')))
})

test('inapplicability stated in the destination words is shown under the Not applicable verdict', () => {
  const rec = validityRecord('Not applicable. No ordinary travel is possible for US passports', {
    field_status: { validity_duration: 'not-applicable', validity_unit: 'not-applicable' } })
  const html = render(rec)
  assert.ok(html.includes('No ordinary travel is possible for US passports'))
  assert.ok(!html.includes('✓'))
})

test('a documented absence stated in the destination words shows that wording without a check', () => {
  // The backend only sends wording for a not-published cell when the
  // wording itself documents the absence ("Set by the mission").
  const rec = validityRecord('Set by the mission', {
    field_status: { validity_duration: 'not-published', validity_unit: 'not-applicable' } })
  const html = render(rec)
  assert.ok(html.includes('Set by the mission'))
  assert.ok(html.includes(t('en', 'ops.validityTextOnly')))
  assert.ok(!html.includes('✓'))
})

test('a label-only absence shows the label', () => {
  const rec = validityRecord(null, {
    field_status: { validity_duration: 'not-published', validity_unit: 'not-published' } })
  const html = render(rec)
  assert.ok(html.includes(t('en', 'ops.notPublished')))
  assert.ok(!html.includes(t('en', 'ops.validityTextOnly')))
})

for (const [duration, unit, label] of [[30, 'Day', '30 days'], [12, 'Hour', '12 hours']]) {
  test('numeric stay keeps its usual rendering: ' + label, () => {
    const html = render(record('unneeded fallback', { max_stay_duration: duration, max_stay_unit: unit,
      field_status: { max_stay_duration: 'filled', max_stay_unit: 'filled' } }))
    assert.ok(html.includes(label))
    assert.ok(html.includes('✓'))
    assert.ok(!html.includes(t('en', 'ops.stayTextOnly')))
    assert.ok(!html.includes('unneeded fallback'))
  })
}

test('an entirely unknown stay reads Not publicly available and keeps the gap mark', () => {
  // Owner rule (11 September 2026): never "Missing information" in a cell.
  const html = render(record(null))
  assert.ok(html.includes(t('en', 'ops.notPublished')))
  assert.ok(!html.includes(t('en', 'ops.missingCounts')))
  assert.ok(html.includes('✗'))
  assert.ok(!html.includes(t('en', 'ops.stayTextOnly')))
})

for (const lang of ['en', 'zh-CN', 'zh-Hant']) {
  test('calendar explanation is localized without changing stored evidence: ' + lang, () => {
    const html = render(record('6 calendar months'), lang)
    assert.ok(html.includes('6 calendar months'))
    assert.ok(html.includes(t(lang, 'ops.stayTextOnly')))
    assert.ok(!html.includes('ops.stayTextOnly'))
  })
}

test('not-published field status shows the stored wording the backend chose to send, without a check', () => {
  const rec = record('Up to 6 months at the officer’s discretion', {
    field_status: { max_stay_duration: 'not-published', max_stay_unit: 'not-applicable' },
  })
  const before = structuredClone(rec)
  const html = render(rec)
  assert.ok(html.includes('Up to 6 months at the officer’s discretion'))
  assert.deepEqual(rec, before)
  assert.ok(!html.includes('✓'))
})

for (const lang of ['en', 'zh-CN', 'zh-Hant']) {
  test('gaps read as one of the two owner labels only: ' + lang, () => {
    const rec = { field_status: { required_documents: 'missing', info_validity: 'not-published',
      consulate_district: 'optional-empty', visa_fee_amount: 'not-applicable' } }
    const before = structuredClone(rec)
    const html = render(rec, lang)
    assert.ok(html.includes(t(lang, 'ops.notPublished')))
    assert.ok(html.includes(t(lang, 'ops.notApplicable')))
    for (const key of ['ops.missingCounts', 'ops.optionalEmpty']) assert.ok(!html.includes(t(lang, key)))
    assert.deepEqual(rec, before)
  })
}

function renderSweep(run, lang = 'en') {
  return renderToStaticMarkup(createElement(NextSweepCountdown, {
    at: null, t: key => t(lang, key), summary: {
      scheduler: { status: 'inactive' }, canonical_total: 10,
      attempted_target: 8, overdue_attempts: 2, verified_target: 3, last_run: run,
    },
  }))
}
const currentRun = {
  status: 'running', started_at: '2026-09-09T15:00:00+00:00',
  attempted: 2, selected: 5, cycle_unattempted: 3, verified: 1, partial: 1,
  unreadable: 0, errors: 0, read: 2, source_reads: 3, insufficient_evidence: 1,
  model_comparisons: 2, model_comparisons_reused: 1, source_fetch_failures: 0,
  provider_failed: 0, no_official_source: 0,
}

for (const lang of ['en', 'zh-CN', 'zh-Hant']) {
  test('actual Freshness component explains continuation without adding prior counts: ' + lang, () => {
    const run = { ...currentRun, cycle_started_at: '2026-09-09T12:00:00+00:00',
      resumed_from_started_at: '2026-09-09T14:00:00+00:00', prior_attempt_results: 7 }
    const before = structuredClone(run)
    const html = renderSweep(run, lang)
    assert.match(html, /data-testid="ops-fresh-continuation"/)
    assert.ok(html.includes(t(lang, 'ops.fresh.runContinuation')
      .replace('{start}', run.cycle_started_at).replace('{prior}', '7')))
    assert.ok(html.includes(t(lang, 'ops.fresh.runCoverage')
      .replace('{attempted}', '2').replace('{selected}', '5').replace('{unfinished}', '3')))
    assert.ok(html.includes(t(lang, 'ops.fresh.runEvidence')
      .replace('{verified}', '1').replace('{partial}', '1').replace('{unreadable}', '0').replace('{errors}', '0')))
    assert.deepEqual(run, before)
  })
}

test('regular Freshness cycle has no continuation note', () => {
  const html = renderSweep({ ...currentRun, cycle_started_at: currentRun.started_at })
  assert.doesNotMatch(html, /ops-fresh-continuation|Continuation of the cycle/)
  assert.ok(html.includes('Attempts recorded for 2/5 selected routes'))
})

test('zero prior attempts remain zero, while unavailable counts are not invented', () => {
  const resumed = { ...currentRun, cycle_started_at: '2026-09-09T12:00:00Z',
    resumed_from_started_at: '2026-09-09T14:00:00Z' }
  assert.ok(renderSweep({ ...resumed, prior_attempt_results: 0 }).includes('Previous run: 0 attempts.'))
  for (const count of [undefined, -1, '7', true]) {
    assert.ok(renderSweep({ ...resumed, prior_attempt_results: count }).includes('Previous run: — attempts.'))
  }
})

test('malformed continuation timestamps cannot imply a recorded prior cycle', () => {
  for (const extra of [
    { cycle_started_at: 'invalid', resumed_from_started_at: currentRun.started_at },
    { cycle_started_at: currentRun.started_at, resumed_from_started_at: 'invalid' },
  ]) assert.doesNotMatch(renderSweep({ ...currentRun, ...extra }), /ops-fresh-continuation/)
})

// Owner requests of 11 September 2026 on the QC access and quality cells,
// pinned at source level: the green "Published to travelers" label stands in
// whenever the publish button is absent (including product-only holds, which
// also name the product under review), and the quality cell shows the tier
// label alone, never a completeness percentage.
test('QC access cell: published label or publish button, never an empty cell', async () => {
  const { readFileSync } = await import('node:fs')
  const src = readFileSync(new URL('../../src/renderer/src/screens/QualityConsole.jsx', import.meta.url), 'utf8')
  assert.match(src, /\{\(!held \|\| productWithheld\) && \(\s*<span data-testid="ops-published"/)
  assert.match(src, /\{held && productWithheld && \(\s*<span data-testid="ops-product-withheld"/)
  assert.match(src, /\{held && !productWithheld && \(\s*<button[^]*?data-testid="ops-release"/)
  assert.ok(!src.includes('pctDone'), 'the quality cell must not print a completeness percentage')
})


// A validity the source states in words rides in the validity cell the same
// way a stay in words does (owner finding, Australia to Russia, 11 September 2026).
function validityRecord(text, extra = {}) {
  return { validity_duration: null, validity_unit: null, validity_text: text,
    field_status: { validity_duration: 'filled', validity_unit: 'not-applicable' },
    completeness: 1, source_check: 'ai-quote', held: false, ...extra }
}

test('validity wording is shown as stored with its explanation', () => {
  const text = 'Up to 3 months for a single or double entry visa, up to 6 months for a multiple entry visa'
  const rec = validityRecord(text)
  const before = structuredClone(rec)
  const html = render(rec, 'en', () => 'A translation must not replace the exact stored validity wording')
  assert.ok(html.includes(text))
  assert.ok(html.includes(t('en', 'ops.validityTextOnly')))
  assert.ok(!html.includes(t('en', 'ops.notPublished')))
  assert.doesNotMatch(html, /A translation must/)
  assert.deepEqual(rec, before)
})

test('a numeric validity keeps its usual rendering and no wording note', () => {
  const html = render(validityRecord('unneeded fallback', { validity_duration: 6, validity_unit: 'Month',
    field_status: { validity_duration: 'filled', validity_unit: 'filled' } }))
  assert.ok(!html.includes(t('en', 'ops.validityTextOnly')))
  assert.ok(!html.includes('unneeded fallback'))
})

for (const lang of ['en', 'zh-CN', 'zh-Hant']) {
  test('validity explanation is localized: ' + lang, () => {
    const html = render(validityRecord('2 years or until the linked passport expires, whichever is sooner'), lang)
    assert.ok(html.includes('2 years or until the linked passport expires, whichever is sooner'))
    assert.ok(html.includes(t(lang, 'ops.validityTextOnly')))
    assert.ok(!html.includes('ops.validityTextOnly'))
  })
}


test('a disputed wording cell still shows the stored wording under the pending mark', () => {
  const rec = validityRecord('Up to 3 months for a single or double entry visa', {
    field_status: { validity_duration: 'pending-review', validity_unit: 'not-applicable' } })
  const html = render(rec)
  assert.ok(html.includes('Up to 3 months for a single or double entry visa'))
})
