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
  stdin: { contents: "export { FieldGrid } from './src/renderer/src/screens/QualityConsole.jsx'",
    resolveDir: resolve('.'), sourcefile: 'quality-field-grid-entry.jsx' },
  bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic',
  external: ['react', 'react/jsx-runtime'], logLevel: 'silent',
})
const module = { exports: {} }
new Function('require', 'module', 'exports', compiled.outputFiles[0].text)(
  createRequire(import.meta.url), module, module.exports)
const { FieldGrid } = module.exports

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
