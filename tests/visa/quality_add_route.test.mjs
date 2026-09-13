import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import { build } from 'esbuild'
import { createElement } from 'react'
import { act, create } from 'react-test-renderer'
import { readFileSync } from 'node:fs'
import { createQualityRouteAdder, qualityAddOutcome, manualRouteFields } from '../../src/renderer/src/lib/qualityAddRoute.js'
import { createLatestLoader } from '../../src/renderer/src/lib/qualityLoader.js'
import { t as translate } from '../../src/renderer/src/lib/i18n.js'
const route = { nationality: 'CHN', destination: 'JPN', travel_purpose: 'tourism', travel_document_type: 'prc_travel_document' }
function deferred() { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b }); return { promise, resolve, reject } }
function harness(client, read = async tab => ({ tab })) {
  const states = [], errors = [], stored = []
  let tab = 'records'
  const loader = createLatestLoader(read, { onStart() {}, onFinish() {}, onData(v) { states.push(v) }, onError(e) { errors.push(e.message) } })
  const add = createQualityRouteAdder({ client, loader, currentTab: () => tab, onStored(...args) { stored.push(args) } })
  return { add, loader, states, errors, stored, switchTab(v) { tab = v } }
}

test('duplicate clicks share one source request and exact document route', async () => {
  const post = deferred(), calls = []
  const h = harness({ post(path, body) { calls.push([path, body]); return post.promise } })
  const first = h.add(route), second = h.add(route)
  assert.equal(first, second)
  post.resolve({ record_present: true, route })
  await first
  assert.deepEqual(calls, [['/database/routes/research', route]])
  assert.equal(h.stored.length, 1)
})

test('a navigation during add reloads the current view, rejecting old records', async () => {
  const old = deferred(), post = deferred()
  const h = harness({ post: () => post.promise }, tab => tab === 'records' ? old.promise : Promise.resolve({ tab }))
  const stale = h.loader.run('records'), adding = h.add(route)
  old.resolve({ tab: 'old-records' }); await stale
  assert.deepEqual(h.states, [])
  h.switchTab('freshness'); await h.loader.run('freshness')
  post.resolve({ record_present: true }); await adding
  assert.deepEqual(h.states, [{ tab: 'freshness' }, { tab: 'freshness' }])
  assert.equal(h.stored[0][2], 'freshness')
})

test('an unconfirmed row never shows successful add even if HTTP and held look successful', async () => {
  for (const response of [{ ok: true, held: false }, { ok: false, record_present: false }, {}]) {
    const h = harness({ post: async () => response })
    await assert.rejects(h.add(route), error => error.code === 'add_unconfirmed')
    assert.equal(h.stored.length, 0)
    assert.deepEqual(h.states, [{ tab: 'records' }])
  }
})

test('network ambiguity reloads without retrying a possibly committed write', async () => {
  let writes = 0
  const failure = new Error('Network connection lost')
  const h = harness({ post: async () => { writes++; throw failure } })
  await assert.rejects(h.add(route), error => error === failure)
  assert.equal(writes, 1)
  assert.deepEqual(h.states, [{ tab: 'records' }])
})

test('manual edit success plus lookup failure reports saved fields without writing twice', async () => {
  const paths = []
  const h = harness({ post: async path => { paths.push(path); return { ok: true } }, databaseLookup: async () => { throw new Error('HTTP 503') } })
  await assert.rejects(h.add(route, { fields: { permitted_stay: '30 days' }, source_url: 'https://www.mofa.go.jp/', note: 'Official page' }), e => e.code === 'add_manual_partial')
  assert.deepEqual(paths, ['/database/records/edit'])
  assert.equal(h.stored.length, 0)
})

test('failed display reload cannot claim add completed or rerun model work', async () => {
  let writes = 0
  const h = harness({ post: async () => { writes++; return { record_present: true } } }, async () => { throw new Error('HTTP 503') })
  await assert.rejects(h.add(route), e => e.code === 'add_reload_failed')
  assert.equal(writes, 1)
})

test('a newer loader replaces the locale-owned loader before add completion', async () => {
  const post = deferred(), calls = []
  let current = { invalidate() {}, run: async () => { calls.push('old'); return { status: 'loaded' } } }
  const add = createQualityRouteAdder({ client: { post: () => post.promise }, loader: () => current, currentTab: () => 'records' })
  const pending = add(route)
  current = { run: async () => { calls.push('current'); return { status: 'loaded' } } }
  post.resolve({ record_present: true }); await pending
  assert.deepEqual(calls, ['current'])
})

test('pending or partially checked records never claim official confirmation', () => {
  assert.equal(qualityAddOutcome({ record_present: true, detail_pending: true }).key, 'ops.add.pending')
  for (const research of [{ consistent: true, outcome: 'checked', renewed: false }, { consistent: true, outcome: 'checked' }, { outcome: 'provider_error' }, { outcome: 'research_error' }]) {
    assert.equal(qualityAddOutcome({ record_present: true, research }).tone, 'warning')
  }
  assert.equal(qualityAddOutcome({ record_present: true, research: { outcome: 'checked', renewed: true } }).key, 'ops.refresh.ok')
})

test('manual product entry does not guess entries or validity and retains explicit zero', () => {
  const fields = manualRouteFields({ visa_type: 'Visitor', validity: '90 days', fee_amount: '0', fee_currency: 'usd' })
  assert.equal(fields.visa_products[0].entry, null)
  assert.equal(fields.government_fee.amount, 0)
  assert.equal(manualRouteFields({ visa_type: 'Visitor', entries_sel: 'multiple' }).visa_products[0].validity, null)
  for (const fee_amount of ['bad', 'Infinity', '-2']) assert.throws(() => manualRouteFields({ fee_amount }))
})

const compiled = await build({
  stdin: { contents: "export { AddRouteCard } from './src/renderer/src/screens/QualityConsole.jsx'", resolveDir: resolve('.'), sourcefile: 'quality-add-entry.jsx' },
  bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic',
  external: ['react', 'react/jsx-runtime'], logLevel: 'silent',
})
const mod = { exports: {} }
new Function('require', 'module', 'exports', compiled.outputFiles[0].text)(createRequire(import.meta.url), mod, mod.exports)
const { AddRouteCard } = mod.exports
const t = (key, vars) => translate('en', key, vars)

test('actual Add form offers every backend document type and submits the selected document', () => {
  let component; const submissions = []
  act(() => { component = create(createElement(AddRouteCard, { countries: [], t, onAdd(...args) { submissions.push(args) }, onManualAdd() {} })) })
  const root = component.root
  const documents = JSON.parse(readFileSync('data/reference/travel_document_types.json')).entries.map(x => x.code)
  const selects = root.findAllByType('select')
  const document = selects.find(s => s.props.value === 'ordinary_passport')
  assert.deepEqual(document.findAllByType('option').map(x => x.props.value).sort(), documents.sort())
  const countries = root.findAll(node => node.type?.name === 'CountryFilter')
  act(() => { countries[0].props.onCommit('CHN'); countries[1].props.onCommit('JPN'); document.props.onChange({ target: { value: 'prc_travel_document' } }) })
  act(() => root.findByProps({ 'data-testid': 'ops-add-research-btn' }).props.onClick())
  assert.deepEqual(submissions[0].slice(0, 4), ['CHN', 'JPN', 'tourism', 'prc_travel_document'])
  act(() => component.unmount())
})

test('actual Add form displays partial and pending status rather than green confirmation', () => {
  for (const response of [{ record_present: true, detail_pending: true }, { record_present: true, research: { outcome: 'checked', consistent: true, renewed: false } }]) {
    let component
    act(() => { component = create(createElement(AddRouteCard, { countries: [], t, onAdd() {}, onManualAdd() {}, addMsg: { ok: true, response } })) })
    const status = component.root.findByProps({ role: 'status' })
    assert.equal(status.props.style.color, '#9b6800')
    assert.ok(!status.children.join('').includes('confirmed against'))
    act(() => component.unmount())
  }
})

test('unmount during add cannot reactivate an invalidated loader', async () => {
  let mounted = true, reads = 0, changedFilters = 0
  const post = deferred()
  const add = createQualityRouteAdder({ client: { post: () => post.promise },
    loader: { invalidate() {}, run: async () => { reads++; return { status: 'loaded' } } },
    currentTab: () => 'records', isActive: () => mounted, onStored() { changedFilters++ } })
  const pending = add(route)
  mounted = false
  post.resolve({ record_present: true })
  assert.equal((await pending).quality_reload, 'superseded')
  assert.equal(reads, 0); assert.equal(changedFilters, 0)
})

test('actual Add form freezes document and country controls while saving', () => {
  let component
  act(() => { component = create(createElement(AddRouteCard, { countries: [], t, adding: true, onAdd() {}, onManualAdd() {} })) })
  assert.equal(component.root.findByType('fieldset').props.disabled, true)
  assert.equal(component.root.findByProps({ 'data-testid': 'ops-add-research-btn' }).props.disabled, true)
  act(() => component.unmount())
})
