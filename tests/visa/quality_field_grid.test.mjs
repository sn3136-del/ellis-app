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
  return { max_stay_duration: null, max_stay_unit: null, max_stay_text: text,
    field_status: { max_stay_duration: 'missing', max_stay_unit: 'missing' },
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
  assert.ok(html.includes('✗'))
  assert.doesNotMatch(html, /90 days|180 days|A translation must/)
  assert.deepEqual(rec, before)
})

test('calendar wording does not promote an unverified or held record', () => {
  const rec = record('6 months')
  Object.freeze(rec.field_status); Object.freeze(rec)
  const html = render(rec)
  assert.ok(html.includes('✗'))
  assert.equal(rec.field_status.max_stay_duration, 'missing')
  assert.equal(rec.field_status.max_stay_unit, 'missing')
  assert.equal(rec.completeness, 0.8)
  assert.equal(rec.source_check, 'reference')
  assert.equal(rec.held, true)
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

test('an entirely unknown stay remains visibly missing', () => {
  const html = render(record(null))
  assert.ok(html.includes(t('en', 'ops.missingCounts')))
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

test('not-published field status is retained while stored calendar wording remains readable', () => {
  const rec = record('Up to 6 months at the officer’s discretion', {
    field_status: { max_stay_duration: 'not-published', max_stay_unit: 'not-published' },
  })
  const before = structuredClone(rec)
  const html = render(rec)
  assert.ok(html.includes('Up to 6 months at the officer’s discretion'))
  assert.deepEqual(rec, before)
  assert.ok(!html.includes('✓'))
})

for (const lang of ['en', 'zh-CN', 'zh-Hant']) {
  test('missing, authority-unpublished and optional-empty states have distinct copy: ' + lang, () => {
    const rec = { field_status: { required_documents: 'missing', info_validity: 'not-published',
      consulate_district: 'optional-empty' } }
    const before = structuredClone(rec)
    const html = render(rec, lang)
    const labels = ['ops.missingCounts', 'ops.notPublished', 'ops.optionalEmpty'].map(key => t(lang, key))
    assert.equal(new Set(labels).size, 3)
    for (const label of labels) assert.ok(html.includes(label))
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
