import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import { build } from 'esbuild'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { act, create } from 'react-test-renderer'
import { t as translate } from '../../src/renderer/src/lib/i18n.js'
import { formatChangeValue, changeDisplayEntries, changeOriginKind, translateChangeValue } from '../../src/renderer/src/lib/changeLogDisplay.js'

// The records list is the operator's first screen. It renders a real
// component, so a helper that is not in scope there crashes every row.
const compiled = await build({
  stdin: { contents: "export { RecordsTable, unitNameOf, NoteCell, sortQualityRecords, PublicationFilter, matchesPublicationFilter, qualityFilterQuery, NextSweepCountdown, QualityFilterField, CountryFilter, QualityPage, DeferredDetails } from './src/renderer/src/screens/QualityConsole.jsx'",
    resolveDir: resolve('.'), sourcefile: 'quality-records-table-entry.jsx' },
  bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic',
  external: ['react', 'react/jsx-runtime'], logLevel: 'silent',
})
const module = { exports: {} }
new Function('require', 'module', 'exports', compiled.outputFiles[0].text)(
  createRequire(import.meta.url), module, module.exports)
const { RecordsTable, unitNameOf, sortQualityRecords, PublicationFilter, matchesPublicationFilter, qualityFilterQuery, NextSweepCountdown, QualityFilterField, CountryFilter, QualityPage, DeferredDetails } = module.exports

const t = (key, vars) => translate('en', key, vars)

const hongKong = { value: 'HKG', label: 'Hong Kong', search: 'hkg hk hong kong' }
function countryField(countries, onCommit, label = 'Passport') {
  return createElement(QualityFilterField, { label }, createElement(CountryFilter, {
    value: '', placeholder: 'China or CHN', countries, onCommit,
  }))
}

test('country text survives the parent render when the country catalog arrives', () => {
  const committed = []; const commit = value => committed.push(value)
  let renderer
  try {
    act(() => { renderer = create(countryField([], commit)) })
    const input = renderer.root.findByType('input')
    act(() => { input.props.onFocus(); input.props.onChange({ target: { value: 'Hong Ko' } }) })
    act(() => { renderer.update(countryField([hongKong], commit)) })
    const updated = renderer.root.findByType('input')
    assert.ok(updated === input, 'The same input must remain mounted')
    assert.equal(updated.props.value, 'Hong Ko')
    assert.deepEqual(committed, [])
    act(() => { updated.props.onKeyDown({ key: 'Enter', preventDefault() {} }) })
    assert.deepEqual(committed, ['HKG'], 'Newly loaded suggestions resolve the retained text')
  } finally { if (renderer) act(() => renderer.unmount()) }
})

test('an unrelated parent render does not cancel the country auto-commit timer', async () => {
  const committed = []; const commit = value => committed.push(value)
  let renderer
  try {
    act(() => { renderer = create(countryField([hongKong], commit)) })
    const input = renderer.root.findByType('input')
    act(() => { input.props.onFocus(); input.props.onChange({ target: { value: 'HKG' } }) })
    act(() => { renderer.update(countryField([hongKong], commit, 'Passport country')) })
    assert.equal(renderer.root.findByType('input').props.value, 'HKG')
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 280)) })
    assert.deepEqual(committed, ['HKG'])
  } finally { if (renderer) act(() => renderer.unmount()) }
})

function record(extra = {}) {
  return { travel_document_country: 'AUS', destination_country: 'RUS', travel_purpose: 'tourism',
    travel_document_type: 'ordinary_passport', collected_at: '2026-09-11', source_url: 'https://australia.mid.ru/',
    visa_requirement: 'Visa Required in Advance', visa_type_name: 'Ordinary tourist visa',
    cache_key: 'AUS|AUS|RUS|tourism|default|unknown|v6', confidence_level: 'High', source_check: 'ai-quote',
    completeness: 1, field_status: {}, held: false, publication_state: 'published',
    max_stay_duration: null, max_stay_unit: null, max_stay_text: null,
    visa_fee_amount: null, visa_fee_currency: null, ...extra }
}
function render(records, extra = {}) {
  return renderToStaticMarkup(createElement(RecordsTable, {
    records, total: records.length, onFlag: () => {}, onRelease: () => {}, onEdit: () => {},
    onRefresh: () => {}, t, flagOf: () => null, ...extra }))
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

test('records header hides publication order while retaining normal table columns', () => {
  const html = render([record()])
  assert.ok(!html.includes('ops-publication-sort'))
  assert.ok(!html.includes('Publication order'))
  assert.ok(html.includes(t('ops.col.route')))
  assert.ok(html.includes(t('ops.col.fee')))
})

test('freshness retains the next scheduled countdown while the current refresh is running', () => {
  const realNow = Date.now
  Date.now = () => Date.parse('2026-09-13T19:15:00Z')
  try {
    for (const lang of ['en', 'zh-CN', 'zh-Hant']) {
      const localized = key => translate(lang, key)
      const html = renderToStaticMarkup(createElement(NextSweepCountdown, {
        at: '2026-09-14T00:20:00Z',
        summary: { scheduler: { status: 'active' }, last_run: { running: true } },
        t: localized,
      }))
      assert.ok(html.includes('05:05:00'))
      assert.ok(html.includes(localized('ops.fresh.nextTitle')))
      assert.ok(!html.includes(localized('ops.fresh.awaitingRun')))
      assert.ok(!html.includes(localized('ops.fresh.schedulerUnavailable')))
    }
  } finally { Date.now = realNow }
})

test('explicitly paused refresh shows a localized pause and ignores an old countdown', () => {
  for (const lang of ['en', 'zh-CN', 'zh-Hant']) {
    const localized = key => translate(lang, key)
    const html = renderToStaticMarkup(createElement(NextSweepCountdown, {
      at: '2099-09-14T00:20:00Z', summary: { scheduler: { status: 'paused' } }, t: localized,
    }))
    assert.ok(html.includes(localized('ops.fresh.paused')))
    assert.ok(!html.includes(localized('ops.fresh.awaitingRun')))
    assert.ok(!html.includes(localized('ops.fresh.schedulerUnavailable')))
    assert.doesNotMatch(html, /\d{2}:\d{2}:\d{2}/)
  }
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
  const html = render([record({ held: true, route_held: false, publication_state: 'withheld', publication_reason: 'product_evidence_low' })])
  assert.ok(!html.includes('data-testid="ops-published"'))
  assert.ok(html.includes('Unpublished alternative'))
})


test('Excel receives the same publication, product and missing-field selections as QC', () => {
  const params = new URLSearchParams(qualityFilterQuery({ nationality: 'IND', destination: 'JPN', visaType: 'Multiple-entry', fieldMissing: 'visa_fee_amount', publication: 'unpublished', confidence: '' }))
  assert.deepEqual(Object.fromEntries(params), { nationality: 'IND', destination: 'JPN', visa_type: 'Multiple-entry', field_missing: 'visa_fee_amount', publication: 'unpublished' })
})

test('publish pending state disables every sibling button for the same canonical route', () => {
  const a = record({ held: true }), b = record({ held: true, visa_type_name: 'Multiple-entry tourist visa' })
  const html = render([a, b], { releaseStates: { [a.cache_key]: { pending: true } } })
  assert.equal((html.match(/data-testid="ops-release" disabled="" aria-busy="true"/g) || []).length, 2)
  assert.equal((html.match(/Publishing…/g) || []).length, 2)
  assert.ok(!html.includes('data-testid="ops-published"'))
})

test('publish rejection is visible beside its route instead of a silent unchanged button', () => {
  const a = record({ held: true })
  const html = render([a], { releaseStates: { [a.cache_key]: {
    pending: false, error: true, message: 'Resolve the conflicting visa requirement in the Correction queue.',
  } } })
  assert.ok(html.includes('role="alert" data-testid="ops-release-message"'))
  assert.ok(html.includes('Resolve the conflicting visa requirement'))
  assert.ok(!html.includes('data-testid="ops-published"'))
  assert.ok(!html.includes('disabled=""'))
})


test('change log renders source metadata as words and retains equal-count evidence updates', () => {
  const from = { disposition: { source_url: 'https://example.gov/old', verifier: 'human', quote: 'Earlier policy' } }
  const to = { disposition: { source_url: 'https://example.gov/new', status: 'reviewed', verifier: 'ai', quote: 'Current policy' } }
  const rows = changeDisplayEntries({ field_provenance: { from, to } }, 'modify', { t })
  assert.equal(rows.length, 1)
  assert.equal(rows[0][2], 'Updated: Field source records: 1 · Source pages: 1')
  assert.equal(rows[0][3], true)
  assert.doesNotMatch(rows[0][2], /[{}]|source_url|verifier/)
  assert.equal(changeDisplayEntries({ field_provenance: { from: to, to } }, 'modify', { t }).length, 0)
})

test('change log keeps changed same-length lists, zero fees and qualified fees', () => {
  const rows = changeDisplayEntries({ required_documents: { from: ['Passport', 'Ticket'], to: ['Passport', 'Photo'] } }, 'modify', { t })
  assert.equal(rows[0][1], 'Passport · Ticket')
  assert.equal(rows[0][2], 'Passport · Photo')
  assert.equal(formatChangeValue('government_fee', { amount: 0, currency: 'USD' }, { t }), '0 USD')
  assert.equal(formatChangeValue('government_fee', { amount: 250, currency: 'AUD', qualifier: 'from', note: 'Agency charges excluded' }, { t }), 'From 250 AUD · Agency charges excluded')
})

test('structured conditions and encoded object values stay readable without losing false', () => {
  const text = formatChangeValue('arrival_card', { required: false, condition: 'Only when requested', submission_method: 'online_portal' }, { t })
  assert.equal(text, 'Required: No · Condition: Only when requested · Submission Method: Online Portal')
  assert.doesNotMatch(text, /[{}]|required.*false|online_portal/)
  assert.equal(formatChangeValue('fee', '{"amount":25,"currency":"USD"}', { t }), '25 USD')
  assert.equal(formatChangeValue('notes', 'Bring {original} passport', { t }), 'Bring {original} passport')
})

test('change attribution uses only this change new evidence and never infers a person from origin or source_kind', () => {
  const change = { origin: 'operator-edit', source_kind: 'human-verified 2026-09-13', changes: { field_provenance: {
    from: { fee: { verifier: 'human' } }, to: { fee: { verifier: 'ai' } },
  } } }
  assert.equal(changeOriginKind(change), 'aiReview')
  assert.equal(changeOriginKind({ origin: 'operator-edit', source_kind: 'human-verified 2026-09-13' }), 'qc')
  change.changes.field_provenance.to.docs = { verifier: 'human' }
  assert.equal(changeOriginKind(change), 'qc')
  assert.equal(changeOriginKind({ origin: 'grounded_recheck' }), 'recheck')
  assert.equal(changeOriginKind({ origin: 'engine' }), 'engine')
})

for (const lang of ['en', 'zh-CN', 'zh-Hant']) {
  test(`change log summaries and attribution have static ${lang} labels`, () => {
    const tr = (key, vars) => translate(lang, key, vars)
    const summary = formatChangeValue('field_provenance', { fee: { source_url: 'https://example.gov/fee' } }, { t: tr })
    assert.doesNotMatch(summary, /ops\.chg|source_url|\{fields\}|\{sources\}/)
    for (const key of ['ops.chg.evidenceLabel', 'ops.origin.aiReview', 'ops.origin.qc', 'ops.originAiReviewTip', 'ops.originQcTip']) assert.notEqual(tr(key), key)
  })
}


test('an unpublished fee amount does not conceal a changed published currency', () => {
  const rows = changeDisplayEntries({ government_fee: { from: { amount: null, currency: 'USD' }, to: { amount: null, currency: 'EUR' } } }, 'modify', { t })
  assert.equal(rows.length, 1)
  assert.equal(rows[0][1], 'Currency: USD')
  assert.equal(rows[0][2], 'Currency: EUR')
})

test('change log reviewer identities are neutral while source quote and URL values stay exact', () => {
  for (const value of ['CodexAI', 'OpenAI', 'CHATGPT', 'claude-fill_review-20260913']) {
    assert.equal(formatChangeValue('data_source', value, { t }), 'AI source review')
    assert.equal(formatChangeValue('verified_by', value, { t }), 'AI source review')
  }
  assert.equal(formatChangeValue('note', 'Reviewed by Claude; checked with Codex.', { t }),
    'Reviewed by AI; checked with AI.')
  assert.equal(formatChangeValue('source_url', 'https://example.gov/openai', { t }), 'https://example.gov/openai')
  assert.equal(formatChangeValue('quote', 'Codex is literal wording in this test quote.', { t }),
    'Codex is literal wording in this test quote.')
})


test('the final change chip translation path leaves literal evidence wording unchanged', () => {
  const transform = value => value.replace(/Codex/g, 'AI')
  assert.equal(translateChangeValue('quote', 'Codex in literal evidence', transform), 'Codex in literal evidence')
  assert.equal(translateChangeValue('source_url', 'https://example.gov/Codex', transform), 'https://example.gov/Codex')
  assert.equal(translateChangeValue('note', 'Checked by Codex', transform), 'Checked by AI')
})


test('large QC histories render one page and retain every record through Show more', () => {
  const items=Array.from({length:121},(_,i)=>i);let renderer
  try{
    act(()=>{renderer=create(createElement(QualityPage,{items,t},visible=>visible.map(i=>createElement('article',{key:i},String(i)))))})
    assert.equal(renderer.root.findAllByType('article').length,50)
    act(()=>renderer.root.findByProps({'data-testid':'ops-history-more'}).props.onClick())
    assert.equal(renderer.root.findAllByType('article').length,100)
    act(()=>renderer.root.findByProps({'data-testid':'ops-history-more'}).props.onClick())
    assert.deepEqual(renderer.root.findAllByType('article').map(x=>Number(x.children[0])),items)
    assert.equal(renderer.root.findAllByProps({'data-testid':'ops-history-more'}).length,0)
  }finally{if(renderer)act(()=>renderer.unmount())}
})

test('closed QC histories and hidden change fields do no rendering until opened', () => {
  let calls=0,renderer
  try{
    act(()=>{renderer=create(createElement(DeferredDetails,{summary:'Resolved (500)'},()=>{calls++;return createElement('article',{},'Complete history')}))})
    assert.equal(calls,0);assert.equal(renderer.root.findAllByType('article').length,0)
    act(()=>renderer.root.findByType('details').props.onToggle({currentTarget:{open:true}}))
    assert.equal(calls,1);assert.equal(renderer.root.findByType('article').children[0],'Complete history')
    act(()=>renderer.root.findByType('details').props.onToggle({currentTarget:{open:false}}))
    assert.equal(renderer.root.findAllByType('article').length,0)
  }finally{if(renderer)act(()=>renderer.unmount())}
})
