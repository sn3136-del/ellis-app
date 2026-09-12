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
  stdin: { contents: "export { RecordsTable, unitNameOf, NoteCell, sortQualityRecords, PublicationFilter, matchesPublicationFilter } from './src/renderer/src/screens/QualityConsole.jsx'",
    resolveDir: resolve('.'), sourcefile: 'quality-records-table-entry.jsx' },
  bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic',
  external: ['react', 'react/jsx-runtime'], logLevel: 'silent',
})
const module = { exports: {} }
new Function('require', 'module', 'exports', compiled.outputFiles[0].text)(
  createRequire(import.meta.url), module, module.exports)
const { RecordsTable, unitNameOf, sortQualityRecords, PublicationFilter, matchesPublicationFilter } = module.exports

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

test('publication sorting uses per-product access even when route and confidence differ', () => {
  const records = [
    record({ visa_type_name: 'held sibling', held: true, route_held: false, publication_state: 'withheld', confidence_level: 'High' }),
    record({ visa_type_name: 'published sibling', held: false, route_held: false, publication_state: 'published' }),
    record({ visa_type_name: 'published Low', held: false, publication_state: 'published', confidence_level: 'Low' }),
    record({ visa_type_name: 'state-only withheld', held: undefined, publication_state: 'withheld' }),
  ]
  const before = structuredClone(records)
  assert.deepEqual(sortQualityRecords(records, { key: 'publication', dir: 1 }).map(r => r.visa_type_name),
    ['published sibling', 'published Low', 'held sibling', 'state-only withheld'])
  assert.deepEqual(sortQualityRecords(records, { key: 'publication', dir: -1 }).map(r => r.visa_type_name),
    ['held sibling', 'state-only withheld', 'published sibling', 'published Low'])
  assert.deepEqual(records, before)
})

test('publication selector is accessible and retains column sorting choice', () => {
  const html = render([record()])
  assert.ok(html.includes('aria-label="Publication order"'))
  assert.ok(html.includes('value="published">Published first</option>'))
  assert.ok(html.includes('value="unpublished">Unpublished first</option>'))
  assert.ok(html.includes('value="columns" selected="">Column sorting</option>'))
})

test('publication option preserves existing fee and route sort behavior', () => {
  const records = [record({ travel_document_country: 'USA', visa_fee_amount: 10 }),
    record({ travel_document_country: 'AUS', visa_fee_amount: 20 })]
  assert.equal(sortQualityRecords(records, { key: 'fee', dir: -1 })[0].visa_fee_amount, 20)
  assert.equal(sortQualityRecords(records, { key: 'route', dir: 1 })[0].travel_document_country, 'AUS')
})

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

test('a long stay note is clamped by the cell and never rewritten', () => {
  const long = 'Actual permitted stay is determined by the e-Pass issued at entry. The Singapore Consulate-General in Hong Kong publishes visa-free social visits of up to 30 days for HKSAR passports. Depart by the last day stated on your e-Pass'
  const html = render([record({ max_stay_text: long,
    field_status: { max_stay_duration: 'not-published', max_stay_unit: 'not-applicable' } })])
  // The note is shown as the destination wrote it, clamped by the browser,
  // and the whole of it is one hover away. No figure is lifted out of it.
  assert.ok(html.includes('Actual permitted stay is determined'))
  assert.ok(html.includes('text-overflow:ellipsis'))
  assert.ok(html.includes('title="' + long + '"'))
  // The information button is a measured property (the text is cut off in
  // pixels), so static markup never carries one, whatever the length.
  assert.ok(!html.includes('aria-label="' + t('ops.noteOpen') + '"'))
  assert.ok(!html.includes('up to 30 days<'))
})

test('the cell cannot spill over its neighbour, whatever the note length', () => {
  for (const note of ['Up to 6 calendar months per visit', 'Visa T: single entry, 3 months validity, 1 month stay',
                      'x'.repeat(300)]) {
    const cell = stayCell(render([record({ max_stay_text: note,
      field_status: { max_stay_duration: 'filled', max_stay_unit: 'not-applicable' } })]))
    // max-width 0 is what makes a fixed table honour the clamp.
    assert.ok(cell.includes('max-width:0'), note.slice(0, 20))
    assert.ok(cell.includes('overflow:hidden'), note.slice(0, 20))
  }
})

// The stay cell alone, so an assertion cannot be satisfied by another column.
function stayCell(html) {
  const at = html.indexOf('data-label="' + t('ops.col.stay') + '"')
  assert.ok(at > 0, 'the stay cell is in the row')
  return html.slice(at, html.indexOf('</td>', at))
}

test('a validity figure inside a stay note is never promoted to the cell', () => {
  const note = 'Visa T: single entry, 3 months validity, 1 month stay'
  const cell = stayCell(render([record({ max_stay_text: note,
    field_status: { max_stay_duration: 'filled', max_stay_unit: 'not-applicable' } })]))
  // The note is rendered whole and nothing is lifted out of it.
  assert.ok(cell.includes(note))
  assert.ok(cell.includes('title="' + note + '"'))
  assert.ok(!/>\s*3 months\s*</.test(cell))
})

test('the stay cell itself carries the clamp, not merely some cell on the row', () => {
  const cell = stayCell(render([record({ max_stay_text: 'Up to 6 calendar months per visit',
    field_status: { max_stay_duration: 'filled', max_stay_unit: 'not-applicable' } })]))
  assert.ok(cell.includes('max-width:0'))
  assert.ok(cell.includes('overflow:hidden'))
  assert.ok(cell.includes('text-overflow:ellipsis'))
})

test('a numeric stay keeps its ellipsis and its tooltip too', () => {
  const cell = stayCell(render([record({ max_stay_duration: 90, max_stay_unit: 'Day',
    field_status: { max_stay_duration: 'filled', max_stay_unit: 'filled' } })]))
  assert.ok(cell.includes('90 days'))
  assert.ok(cell.includes('title="90 days"'))
  assert.ok(cell.includes('text-overflow:ellipsis'))
})

test('a short stay note is shown as it is, with no button', () => {
  const html = render([record({ max_stay_text: 'Up to 30 days',
    field_status: { max_stay_duration: 'filled', max_stay_unit: 'not-applicable' } })])
  assert.ok(html.includes('Up to 30 days'))
  assert.ok(!html.includes(t('ops.noteOpen')))
})



test('the stacked card layout undoes the table clamp on both clamped columns', async () => {
  const css = await import('node:fs').then((fs) => fs.readFileSync(
    'src/renderer/src/screens/QualityConsole.jsx', 'utf8'))
  // Several blocks share that width, so find the one that stacks the table.
  const card = css.slice(css.lastIndexOf('@media (max-width: 760px)', css.indexOf('.ops-rt thead { display: none; }')))
  const block = card.slice(0, card.indexOf('@media', 10))
  // Under 760px a cell has the whole width, so the clamp must be undone or
  // the value collapses to nothing on every row.
  assert.match(block, /\.ops-rt td \{[^}]*max-width: none !important/)
  assert.match(block, /overflow: visible !important/)
  assert.match(block, /\.ops-notetext \{[^}]*white-space: normal !important/)
})


test('publication filter separates mixed route products without changing grades or records', () => {
  const rows = [
    record({ visa_type_name: 'published Low', held: false, confidence_level: 'Low' }),
    record({ visa_type_name: 'held High sibling', held: true, route_held: false, publication_state: 'published' }),
    record({ visa_type_name: 'state only', held: undefined, publication_state: 'withheld' }),
  ]
  const before = structuredClone(rows)
  assert.deepEqual(rows.filter(r => matchesPublicationFilter(r, 'published')).map(r => r.visa_type_name), ['published Low'])
  assert.deepEqual(rows.filter(r => matchesPublicationFilter(r, 'unpublished')).map(r => r.visa_type_name), ['held High sibling', 'state only'])
  assert.equal(rows.filter(r => matchesPublicationFilter(r, '')).length, 3)
  assert.deepEqual(rows, before)
})

test('publication filter provides all three choices in each shipped language', () => {
  for (const lang of ['en', 'zh-CN', 'zh-TW']) {
    const translateHere = key => translate(lang, key)
    const html = renderToStaticMarkup(createElement(PublicationFilter, { value: 'unpublished', onChange() {}, t: translateHere }))
    assert.ok(html.includes('data-testid="ops-filter-publication"'))
    assert.ok(html.includes('value="unpublished" selected=""'))
    for (const key of ['ops.flt.publication', 'ops.publicationAll', 'ops.publicationPublished', 'ops.publicationUnpublished']) {
      assert.notEqual(translateHere(key), key)
      assert.ok(html.includes(translateHere(key)))
    }
  }
})


test('unpublished alternatives never display the published product badge', () => {
  const html = render([record({ held: true, route_held: false, publication_state: 'withheld', publication_reason: 'product_evidence_missing' })])
  assert.ok(!html.includes('data-testid="ops-published"'))
})
