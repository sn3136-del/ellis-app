import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import { build } from 'esbuild'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { t as translate } from '../../src/renderer/src/lib/i18n.js'

// The records list is the operator's first screen. It renders a real
// component, so a helper that is not in scope there crashes every row.
const compiled = await build({
  stdin: { contents: "export { RecordsTable, unitNameOf } from './src/renderer/src/screens/QualityConsole.jsx'",
    resolveDir: resolve('.'), sourcefile: 'quality-records-table-entry.jsx' },
  bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic',
  external: ['react', 'react/jsx-runtime'], logLevel: 'silent',
})
const module = { exports: {} }
new Function('require', 'module', 'exports', compiled.outputFiles[0].text)(
  createRequire(import.meta.url), module, module.exports)
const { RecordsTable, unitNameOf } = module.exports

const t = (key, vars) => translate('en', key, vars)

function record(extra = {}) {
  return { travel_document_country: 'AUS', destination_country: 'RUS', travel_purpose: 'tourism',
    travel_document_type: 'ordinary_passport', collected_at: '2026-09-11', source_url: 'https://australia.mid.ru/',
    visa_requirement: 'Visa Required in Advance', visa_type_name: 'Ordinary tourist visa',
    cache_key: 'AUS|AUS|RUS|tourism|default|unknown|v6', confidence_level: 'High', source_check: 'ai-quote',
    completeness: 1, field_status: {}, held: false, publication_state: 'published',
    max_stay_duration: null, max_stay_unit: null, max_stay_text: null,
    visa_fee_amount: null, visa_fee_currency: null, ...extra }
}
function render(records) {
  return renderToStaticMarkup(createElement(RecordsTable, {
    records, total: records.length, onFlag: () => {}, onRelease: () => {}, onEdit: () => {},
    onRefresh: () => {}, t, flagOf: () => null }))
}

test('a numeric stay renders with its own unit name, in every unit', () => {
  for (const [duration, unit, label] of [[30, 'Day', 'days'], [12, 'Hour', 'hours'], [6, 'Month', 'months'], [2, 'Year', 'years']]) {
    const html = render([record({ max_stay_duration: duration, max_stay_unit: unit,
      field_status: { max_stay_duration: 'filled', max_stay_unit: 'filled' } })])
    assert.ok(html.includes(`${duration} ${label}`), `${duration} ${unit}`)
  }
})

test('a stay stated in words renders as stored', () => {
  const html = render([record({ max_stay_text: 'Up to 6 calendar months per visit',
    field_status: { max_stay_duration: 'filled', max_stay_unit: 'not-applicable' } })])
  assert.ok(html.includes('Up to 6 calendar months per visit'))
})

test('a labelled stay renders its label, never Missing information', () => {
  const gap = render([record({ field_status: { max_stay_duration: 'missing', max_stay_unit: 'missing' } })])
  assert.ok(gap.includes(t('ops.notPublished')))
  assert.ok(!gap.includes(t('ops.missingCounts')))
  const na = render([record({ field_status: { max_stay_duration: 'not-applicable', max_stay_unit: 'not-applicable' } })])
  assert.ok(na.includes(t('ops.notApplicable')))
})

test('a table of many records renders every row', () => {
  const html = render([
    record({ max_stay_duration: 30, max_stay_unit: 'Day', field_status: { max_stay_duration: 'filled' } }),
    record({ travel_document_country: 'HKG', destination_country: 'VNM', cache_key: 'HKG|HKG|VNM|tourism|default|unknown|v6',
      max_stay_text: 'Up to 45 days per entry', field_status: { max_stay_duration: 'filled', max_stay_unit: 'not-applicable' } }),
    record({ travel_document_country: 'USA', destination_country: 'PRK', cache_key: 'USA|USA|PRK|tourism|default|unknown|v6',
      field_status: { max_stay_duration: 'not-applicable', max_stay_unit: 'not-applicable' } }),
  ])
  assert.ok(html.includes('30 days') && html.includes('Up to 45 days per entry') && html.includes(t('ops.notApplicable')))
})

test('the unit helper is exported and shared', () => {
  assert.equal(unitNameOf(t, 'Working Day'), t('ops.u.workDay'))
  assert.equal(unitNameOf(t, null), '')
  assert.equal(unitNameOf(t, 'Fortnight'), 'Fortnight')
})
