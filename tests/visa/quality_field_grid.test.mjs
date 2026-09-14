import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import { build } from 'esbuild'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { act, create } from 'react-test-renderer'
import { t } from '../../src/renderer/src/lib/i18n.js'
import { createVisaClient } from '../../src/renderer/src/lib/visaBackend.js'

// Compile the actual component in memory, without a production bundle or DOM.
const compiled = await build({
  stdin: { contents: "export { FieldGrid, FieldQuotes, FieldQuoteList } from './src/renderer/src/screens/QualityConsole.jsx'",
    resolveDir: resolve('.'), sourcefile: 'quality-field-grid-entry.jsx' },
  bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic',
  external: ['react', 'react/jsx-runtime'], logLevel: 'silent',
})
const module = { exports: {} }
new Function('require', 'module', 'exports', compiled.outputFiles[0].text)(
  createRequire(import.meta.url), module, module.exports)
const { FieldGrid, FieldQuotes, FieldQuoteList } = module.exports

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
function renderQuotes(rec, lang = 'en') {
  return renderToStaticMarkup(createElement(FieldQuoteList, { rec, evidence: rec.field_evidence || {},
    t: key => t(lang, key) }))
}

test('Quotes wait for evidence and display only fields with supporting quotations', () => {
  const rec = { field_status: { visa_fee_amount: 'filled', visa_fee_currency: 'filled',
    required_documents: 'missing', source_url: 'filled' }, visa_fee_amount: 25, visa_fee_currency: 'USD',
    source_url: 'https://immigration.example.gov/general',
    field_evidence: { visa_fee_amount: [{ quote: 'Single entry: USD 25.\nAgency fee is not included.',
      source_url: 'https://immigration.example.gov/fees' }] } }
  const before = structuredClone(rec)
  const closed = render(rec)
  assert.ok(!closed.includes('data-testid="ops-field-quotes"'))
  assert.ok(!closed.includes('<blockquote'))
  const html = renderQuotes(rec)
  assert.ok(html.includes('data-quote-field="visa_fee_amount"'))
  for (const field of ['visa_fee_currency', 'required_documents', 'source_url']) {
    assert.ok(!html.includes(`data-quote-field="${field}"`))
  }
  assert.ok(html.includes('Single entry: USD 25.\nAgency fee is not included.'))
  assert.ok(html.includes('href="https://immigration.example.gov/fees"'))
  assert.ok(!html.includes('No supporting quote recorded'))
  assert.deepEqual(rec, before)
})

test('Quotes never borrow a route source link or a sibling product quote as field proof', () => {
  const rec = { field_status: { visa_fee_amount: 'missing', required_documents: 'filled' },
    source_url: 'https://immigration.example.gov/general', field_evidence: {
      required_documents: [{ quote: 'Passport required.', source_url: 'https://immigration.example.gov/documents' }],
      visa_fee_amount: [{ quote: 'A fee without its supporting URL.' }],
    } }
  const html = renderQuotes(rec)
  assert.ok(!html.includes('data-quote-field="visa_fee_amount"'))
  assert.ok(!html.includes('No supporting quote recorded'))
  assert.ok(!html.includes('https://immigration.example.gov/general'))
  assert.ok(html.includes('data-quote-field="required_documents"'))
  assert.ok(html.includes('Passport required.'))
})

test('an empty Quotes response shows no field labels or replacement message', () => {
  const html = renderQuotes({ field_status: { visa_fee_amount: 'filled', required_documents: 'missing' } })
  assert.doesNotMatch(html, /data-quote-field|<dt|<dd|<blockquote|No supporting quote/)
  assert.equal(html.replace(/<[^>]*>/g, '').trim(), '')
})

test('Quotes preserve distinct supporting pages, remove duplicates and reject unsafe links', () => {
  const source = { quote: 'Published fee: USD 25.', source_url: 'https://immigration.example.gov/fees' }
  const rec = { field_status: { visa_fee_amount: 'filled' }, visa_fee_amount: 25, field_evidence: {
    visa_fee_amount: [source, { ...source },
      { quote: 'Consular confirmation.', source_url: 'https://embassy.example.gov/fees' },
      ...['javascript:alert(1)', 'data:text/html,unsafe', '//example.gov/fees', 'https://user:password@example.gov/'].map(source_url => ({ quote: 'Unsafe link.', source_url })),
      { quote: '', source_url: 'https://example.gov/' }, { quote: {}, source_url: 'https://example.gov/' }, null],
  } }
  const html = renderQuotes(rec)
  assert.equal((html.match(/<blockquote/g) || []).length, 2)
  assert.equal((html.match(/Published fee: USD 25\./g) || []).length, 1)
  assert.ok(html.includes('rel="noopener noreferrer"'))
  assert.ok(!html.includes('Unsafe link.'))
  assert.ok(!html.includes('javascript:'))
})

for (const lang of ['en', 'zh-CN', 'zh-Hant']) {
  test('Quotes localize controls while preserving exact source wording: ' + lang, () => {
    const html = renderQuotes({ field_status: { required_documents: 'filled', visa_fee_amount: 'missing' },
      field_evidence: { required_documents: [{ quote: 'Passport & itinerary\nKeep the exact wording.',
        source_url: 'https://immigration.example.gov/documents' }] } }, lang)
    assert.ok(html.includes(t(lang, 'ops.fx.required_documents')))
    assert.ok(!html.includes('data-quote-field="visa_fee_amount"'))
    assert.ok(!html.includes('No supporting quote recorded'))
    assert.ok(html.includes('Passport &amp; itinerary\nKeep the exact wording.'))
    assert.ok(!html.includes('translated value'))
  })
}

function quoteRecord(extra = {}) {
  return { cache_key: 'CAN|CAN|VNM|tourism|default|unknown|v6', product_index: 0,
    evidence_revision: 'record-v1', field_status: { visa_fee_amount: 'filled' }, ...extra }
}
function quoteResponse(rec, quote = 'Single entry: USD 25.') {
  return { cache_key: rec.cache_key, product_index: rec.product_index, revision: rec.evidence_revision,
    field_evidence: { visa_fee_amount: [{ quote, source_url: 'https://immigration.example.gov/fees' }] } }
}
function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
async function quoteScreen(test, rec = quoteRecord()) {
  const reads = []
  const onEvidence = record => { const request = deferred(); reads.push({ ...request, record }); return request.promise }
  const props = record => ({ rec: record, t: key => t('en', key), onEvidence })
  let renderer
  await act(async () => { renderer = create(createElement(FieldQuotes, props(rec))) })
  test.after(() => act(() => renderer.unmount()))
  return { reads, renderer, text: () => JSON.stringify(renderer.toJSON()),
    async update(next) { await act(async () => { renderer.update(createElement(FieldQuotes, props(next))) }) },
    async resolve(index, response) { await act(async () => { reads[index].resolve(response) }) },
  }
}

test('expanding a record checks evidence once and shows a collapsed Quotes dropdown only after proof arrives', async test => {
  const rec = quoteRecord(), s = await quoteScreen(test, rec)
  assert.equal(s.reads.length, 1)
  assert.equal(s.renderer.toJSON(), null)
  await s.resolve(0, quoteResponse(rec))
  assert.ok(s.text().includes('Single entry: USD 25.'))
  const dropdown = s.renderer.root.findByProps({ 'data-testid': 'ops-field-quotes' })
  assert.equal(dropdown.type, 'details')
  assert.notEqual(dropdown.props.open, true)
  assert.ok(s.text().includes(t('en', 'ops.quotes.title')))
  assert.equal(s.reads.length, 1)
})

for (const field_evidence of [{}, { visa_fee_amount: [] }, {
  visa_fee_amount: [{ quote: 'Quote with no safe source.', source_url: 'javascript:alert(1)' }],
}, { unrecognized_field: [{ quote: 'Not a displayed field.', source_url: 'https://example.gov/' }] }]) {
  test('a route without usable field quotations has no Quotes dropdown: ' + JSON.stringify(field_evidence), async test => {
    const rec = quoteRecord(), s = await quoteScreen(test, rec)
    await s.resolve(0, { ...quoteResponse(rec), field_evidence })
    assert.equal(s.renderer.toJSON(), null)
    assert.equal(s.reads.length, 1)
  })
}

test('late Quotes response cannot replace evidence for a refreshed product', async test => {
  const prior = quoteRecord(), current = quoteRecord({ product_index: 1, evidence_revision: 'record-v2' })
  const s = await quoteScreen(test, prior)
  await s.update(current)
  assert.equal(s.renderer.toJSON(), null)
  assert.equal(s.reads.length, 2)
  await s.resolve(1, quoteResponse(current, 'Current product quote.'))
  await s.resolve(0, quoteResponse(prior, 'Stale product quote.'))
  assert.ok(s.text().includes('Current product quote.'))
  assert.ok(!s.text().includes('Stale product quote.'))
})

test('Quotes reject mismatched identity and allow explicit retry without automatic requests', async test => {
  const rec = quoteRecord(), s = await quoteScreen(test, rec)
  await s.resolve(0, { ...quoteResponse(rec), revision: 'stale-revision' })
  assert.ok(s.text().includes(t('en', 'ops.quotes.failed')))
  assert.ok(!s.text().includes('Single entry: USD 25.'))
  assert.equal(s.reads.length, 1)
  await act(async () => { s.renderer.root.findByType('button').props.onClick(); await Promise.resolve() })
  assert.equal(s.reads.length, 2)
  await s.resolve(1, quoteResponse(rec))
  assert.ok(s.text().includes('Single entry: USD 25.'))
})

test('evidence API sends product and revision with the existing public QC session and no browser cache', async () => {
  const original = globalThis.fetch, requests = []
  globalThis.fetch = async (url, options) => { requests.push({ url, options }); return { ok: true, text: async () => '{}' } }
  try {
    const client = createVisaClient({ token: 'public-quality-control', orgId: 'platform', userId: 'tester' })
    await client.databaseRecordEvidence('CAN|CAN|VNM|tourism|default|unknown|v6', 0, 'record-v1')
    await client.databaseRecordEvidence('CAN|CAN|VNM|tourism|default|unknown|v6', null)
    const url = new URL(requests[0].url)
    assert.equal(url.pathname, '/database/record-evidence')
    assert.equal(url.searchParams.get('product_index'), '0')
    assert.equal(url.searchParams.get('revision'), 'record-v1')
    assert.equal(url.searchParams.get('cache_key'), 'CAN|CAN|VNM|tourism|default|unknown|v6')
    assert.equal(requests[0].options.method, 'GET')
    assert.equal(requests[0].options.cache, 'no-store')
    assert.equal(requests[0].options.headers.authorization, 'Bearer public-quality-control')
    assert.equal(new URL(requests[1].url).searchParams.has('product_index'), false)
  } finally { globalThis.fetch = original }
})

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

test('published processing wording fills its own tile without inventing a numeric minimum', () => {
  const rec = { processing_min_days: null, processing_unit: null,
    processing_text: 'Within 24 hours after receiving a complete application.',
    field_status: { processing_min_days: 'missing', processing_unit: 'missing' },
    held: true, confidence_level: 'Low', completeness: 0.8 }
  const before = structuredClone(rec)
  const html = render(rec)
  assert.ok(html.includes(rec.processing_text))
  assert.ok(!html.includes(t('en', 'ops.notPublished')))
  assert.ok(html.includes('✗'))
  assert.deepEqual(rec, before)
})

test('processing ranges and qualifications remain visible beside an extracted minimum', () => {
  const text = '5–10 working days; public holidays and additional checks may extend processing.'
  const rec = { processing_min_days: 5, processing_unit: 'Working Day', processing_text: text,
    field_status: { processing_min_days: 'filled', processing_unit: 'filled' } }
  for (const lang of ['en', 'zh-CN', 'zh-Hant']) {
    const html = render(rec, lang, () => 'shortened translation')
    assert.ok(html.includes(text))
    assert.ok(!html.includes('shortened translation'))
  }
})

test('processing wording never resurrects inapplicable or unpublished values', () => {
  for (const status of ['not-applicable', 'not-published', 'optional-empty']) {
    const html = render({ processing_min_days: null, processing_unit: null,
      processing_text: 'Stale processing words', field_status: { processing_min_days: status, processing_unit: status } })
    assert.ok(!html.includes('Stale processing words'))
    assert.ok(html.includes(t('en', status === 'not-applicable' ? 'ops.notApplicable' : 'ops.notPublished')))
  }
})

test('processing still uses its numeric value when no qualified wording is supplied', () => {
  const html = render({ processing_min_days: 5, processing_unit: 'Working Day', processing_text: null,
    field_status: { processing_min_days: 'filled', processing_unit: 'filled' } })
  assert.ok(html.includes('5 working days'))
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
  // The note under a documented absence says so, not "shown as stored".
  assert.ok(html.includes(t('en', 'ops.wordingAbsence')))
  assert.ok(!html.includes(t('en', 'ops.validityTextOnly')))
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

// Every QC access cell describes its own product publication status or
// offers the publish action. The quality cell shows the tier label alone.
test('QC access cell: published label or publish button, never an empty cell', async () => {
  const { readFileSync } = await import('node:fs')
  const src = readFileSync(new URL('../../src/renderer/src/screens/QualityConsole.jsx', import.meta.url), 'utf8')
  assert.match(src, /\{!held && \(\s*<span data-testid="ops-published"/)
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

test('reviewer brands are replaced in record fields in all UI languages without rewriting evidence', () => {
  for (const name of ['Codex root official-source review', 'codex-guard_review-20260913',
    'OpenAI official source review', 'ChatGPT', 'ClaudeAI', 'claude-source-review-20260913', 'agent_codex_review']) {
    const rec = record(null, { data_source: name, source_url: 'https://example.gov/codex-fees',
      field_status: { data_source: 'filled', source_url: 'filled' } })
    for (const lang of ['en', 'zh-CN', 'zh-Hant']) {
      const html = render(rec, lang)
      assert.ok(html.includes(t(lang, 'ops.origin.aiReview')))
      assert.ok(!html.includes(name))
      assert.ok(html.includes('https://example.gov/codex-fees'))
      assert.equal(rec.data_source, name, 'Presentation must not mutate the audit identity')
    }
  }
})

test('Ellis display messages normalize provider names and preserve embedded source URLs', async () => {
  const { reviewDisplayText, reviewAttributionLabel } = await import('../../src/renderer/src/lib/reviewDisplay.js')
  assert.equal(reviewDisplayText('CodexAI, ChatGPT, OPENAI, Claude and claude-source-review completed checks.'),
    'AI, AI, AI, AI and AI completed checks.')
  assert.equal(reviewDisplayText('Claude checked https://example.gov/OpenAI/fees?by=codex'),
    'AI checked https://example.gov/OpenAI/fees?by=codex')
  assert.equal(reviewAttributionLabel('Ministry of Foreign Affairs'), 'Ministry of Foreign Affairs')
  assert.equal(reviewDisplayText(null), null)
  assert.equal(reviewAttributionLabel(' www.example.gov/claude'), ' www.example.gov/claude')
  assert.equal(reviewAttributionLabel('medicalcodex'), 'medicalcodex')
})
