import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import { build } from 'esbuild'
import { createElement } from 'react'
import { act, create } from 'react-test-renderer'

// Mount the actual screen and fire its rendered controls. Only external
// services and locale chrome are replaced; request/answer logic is real.
const stubs = {
  'visaBackend.js': 'export const createVisaClient = () => globalThis.__requestTestClient',
  'visaSession.js': 'export const newSession = () => "request-order-test"',
  'locale.jsx': 'export const useLocale = () => ({lang:"en",t:(key)=>key})',
  'countryNames.js': 'export const useLocalizedCountries = () => []',
  'App.jsx': 'export const EllisMark = () => null',
  'ui.jsx': 'export const Loading = () => null',
}
const compiled = await build({
  entryPoints: ['src/renderer/src/screens/TravelDatabase.jsx'],
  bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic',
  external: ['react', 'react/jsx-runtime'], logLevel: 'silent',
  plugins: [{ name: 'service-fixtures', setup(builder) {
    builder.onResolve({ filter: /(?:visaBackend\.js|visaSession\.js|locale\.jsx|countryNames\.js|App\.jsx|ui\.jsx)$/ }, (args) => {
      const name = args.path.split('/').at(-1)
      return stubs[name] ? { path: name, namespace: 'fixture' } : null
    })
    builder.onLoad({ filter: /.*/, namespace: 'fixture' }, (args) => ({ contents: stubs[args.path] }))
  } }],
})
const module = { exports: {} }
new Function('require', 'module', 'exports', compiled.outputFiles[0].text)(
  createRequire(import.meta.url), module, module.exports)
const TravelDatabase = module.exports.default

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
function answer(label, nationality = 'HKG', destination = 'VNM', extra = {}) {
  return { route: { nationality, destination, travel_purpose: 'tourism', travel_document_type: 'ordinary_passport' },
    guidance: { disposition: 'VISA_REQUIRED', visa_category: label, requirement_detail: 'evisa',
      government_fee: { amount: 25, currency: 'USD' }, permitted_stay: '90 days', visa_products: [] }, ...extra }
}
async function screen(t, hash = '#database') {
  const saved = Object.fromEntries(['window', 'document', 'setTimeout', 'clearTimeout', '__requestTestClient'].map(k => [k, globalThis[k]]))
  const handlers = new Map(), timers = new Map()
  let timerId = 0
  globalThis.setTimeout = (fn, delay) => { timers.set(++timerId, { fn, delay }); return timerId }
  globalThis.clearTimeout = id => timers.delete(id)
  globalThis.window = { location: { hash }, history: { replaceState: (_state, _title, next) => { window.location.hash = next } },
    addEventListener: (name, fn) => handlers.set(name, fn), removeEventListener: name => handlers.delete(name) }
  globalThis.document = { addEventListener() {}, removeEventListener() {} }
  const asks = [], lookups = [], reports = []
  globalThis.__requestTestClient = {
    snapshotRegistries: async () => ({ countries: [], nationalities: [], travel_document_types: [] }),
    databaseAsk: (...args) => { const d = deferred(); asks.push({ ...d, args }); return d.promise },
    databaseLookup: body => { const d = deferred(); lookups.push({ ...d, body }); return d.promise },
    databaseReportIssue: body => { const d = deferred(); reports.push({ ...d, body }); return d.promise },
  }
  let renderer
  await act(async () => { renderer = create(createElement(TravelDatabase)); await Promise.resolve() })
  t.after(async () => {
    act(() => renderer.unmount())
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete globalThis[key]
      else globalThis[key] = value
    }
  })
  const find = id => renderer.root.findByProps({ 'data-testid': id })
  const has = id => renderer.root.findAllByProps({ 'data-testid': id }).length > 0
  const text = () => JSON.stringify(renderer.toJSON())
  return { renderer, asks, lookups, reports, find, has, text,
    async ask(question) {
      act(() => find('database-question').props.onChange({ target: { value: question } }))
      act(() => find('database-question').props.onKeyDown({ key: 'Enter' }))
      await act(async () => { await Promise.resolve() })
      return asks.at(-1)
    },
    async navigate(next) {
      await act(async () => { window.location.hash = next; handlers.get('hashchange')(); await Promise.resolve() })
      return lookups.at(-1)
    },
    async finish(request, response, fail = false) {
      await act(async () => { request[fail ? 'reject' : 'resolve'](response); await Promise.resolve() })
    },
    async timers(delay) {
      const due = [...timers].filter(([, timer]) => timer.delay === delay)
      await act(async () => { for (const [id, timer] of due) { timers.delete(id); timer.fn() } await Promise.resolve() })
    },
  }
}

test('older chat answer cannot overwrite a newer route lookup or its URL', async t => {
  const s = await screen(t)
  const old = await s.ask('old Hong Kong Vietnam question')
  const latest = await s.navigate('#database/IDN/KOR/tourism/ordinary_passport')
  await s.finish(latest, answer('CURRENT KOREA', 'IDN', 'KOR'))
  await s.finish(old, answer('STALE VIETNAM'))
  assert.ok(s.text().includes('CURRENT KOREA'))
  assert.ok(!s.text().includes('STALE VIETNAM'))
  assert.match(window.location.hash, /IDN\/KOR/)
  assert.ok(!s.has('database-ask-slow'))
})

test('older lookup cannot overwrite a newer chat answer or leave lookup loading active', async t => {
  const s = await screen(t, '#database/HKG/VNM/tourism/ordinary_passport')
  const old = s.lookups[0]
  const latest = await s.ask('Indonesia to Korea')
  await s.finish(latest, answer('CURRENT CHAT KOREA', 'IDN', 'KOR'))
  await s.finish(old, answer('STALE LOOKUP VIETNAM'))
  assert.ok(s.text().includes('CURRENT CHAT KOREA'))
  assert.ok(!s.text().includes('STALE LOOKUP VIETNAM'))
  assert.equal(s.find('database-switch-doc').props.disabled, false)
  assert.match(window.location.hash, /IDN\/KOR/)
})

test('older chat completion/error cannot clear the newer chat loading state or append stale conversation', async t => {
  const s = await screen(t)
  const old = await s.ask('first question')
  const latest = await s.ask('second question')
  await s.finish(old, new Error('STALE ERROR'), true)
  assert.ok(s.has('database-ask-slow'))
  assert.ok(!s.text().includes('STALE ERROR'))
  await s.finish(latest, answer('CURRENT SECOND'))
  assert.ok(!s.has('database-ask-slow'))
  assert.ok(s.text().includes('CURRENT SECOND'))
})

test('in-flight detail poll cannot replace a newer chat answer', async t => {
  const s = await screen(t, '#database/HKG/VNM/tourism/ordinary_passport')
  await s.finish(s.lookups[0], answer('FIRST PARTIAL', 'HKG', 'VNM', { detail_pending: true }))
  await s.timers(2500)
  const oldPoll = s.lookups.at(-1)
  const latest = await s.ask('Indonesia to Korea now')
  await s.finish(latest, answer('CURRENT NEW CHAT', 'IDN', 'KOR'))
  await s.finish(oldPoll, answer('STALE POLLED DETAILS'))
  assert.ok(s.text().includes('CURRENT NEW CHAT'))
  assert.ok(!s.text().includes('STALE POLLED DETAILS'))
  const count = s.lookups.length
  await s.timers(2500)
  assert.equal(s.lookups.length, count)
})

test('current chat failures remain visible and release their loading state', async t => {
  const s = await screen(t)
  const current = await s.ask('question that fails')
  await s.finish(current, new Error('CURRENT SERVICE ERROR'), true)
  assert.ok(s.text().includes('CURRENT SERVICE ERROR'))
  assert.ok(!s.has('database-ask-slow'))
})

test('new search invalidates pending polls instead of reviving an old answer', async t => {
  const s = await screen(t, '#database/HKG/VNM/tourism/ordinary_passport')
  await s.finish(s.lookups[0], answer('OLD ROUTE', 'HKG', 'VNM', { detail_pending: true }))
  await s.timers(2500)
  const oldPoll = s.lookups.at(-1)
  const button = s.renderer.root.findAllByType('button').find(x => x.children.includes('db.newSearch'))
  assert.ok(button)
  act(() => button.props.onClick())
  await s.finish(oldPoll, answer('REVIVED OLD ROUTE'))
  assert.ok(!s.has('database-result'))
  assert.equal(window.location.hash, '#database')
})

test('current detail poll still fills the answer it belongs to', async t => {
  const s = await screen(t, '#database/HKG/VNM/tourism/ordinary_passport')
  await s.finish(s.lookups[0], answer('CURRENT PARTIAL', 'HKG', 'VNM', { detail_pending: true }))
  await s.timers(2500)
  await s.finish(s.lookups.at(-1), answer('CURRENT FULL DETAILS'))
  assert.ok(s.text().includes('CURRENT FULL DETAILS'))
  assert.ok(!s.text().includes('CURRENT PARTIAL'))
})

test('failed superseded switch cannot roll back the purpose selected by the new answer', async t => {
  const s = await screen(t, '#database/HKG/VNM/tourism/ordinary_passport')
  await s.finish(s.lookups[0], answer('FIRST ROUTE'))
  act(() => { s.find('database-switch-purpose').props.onChange({ target: { value: 'business' } }) })
  const oldSwitch = s.lookups.at(-1)
  const latest = await s.ask('what about studying in Korea')
  const fresh = answer('CURRENT STUDY', 'IDN', 'KOR')
  fresh.route.travel_purpose = 'study'
  await s.finish(latest, fresh)
  await s.finish(oldSwitch, new Error('STALE SWITCH ERROR'), true)
  assert.equal(s.find('database-switch-purpose').props.value, 'study')
  assert.ok(!s.has('database-switching'))
  assert.ok(!s.text().includes('STALE SWITCH ERROR'))
})

test('current failed switch keeps its previous answer, restores its control and clears loading', async t => {
  const s = await screen(t, '#database/HKG/VNM/tourism/ordinary_passport')
  await s.finish(s.lookups[0], answer('PRESERVED ANSWER'))
  act(() => { s.find('database-switch-purpose').props.onChange({ target: { value: 'business' } }) })
  await s.finish(s.lookups.at(-1), new Error('CURRENT SWITCH ERROR'), true)
  assert.equal(s.find('database-switch-purpose').props.value, 'tourism')
  assert.equal(s.find('database-switch-purpose').props.disabled, false)
  assert.ok(s.text().includes('PRESERVED ANSWER'))
  assert.ok(s.text().includes('CURRENT SWITCH ERROR'))
  assert.ok(!s.has('database-switching'))
})

test('identity replies still enter the current conversation and clear loading on early return', async t => {
  const s = await screen(t)
  const current = await s.ask('who are you')
  await s.finish(current, { identity: true, reply: 'CURRENT IDENTITY REPLY' })
  assert.ok(s.text().includes('CURRENT IDENTITY REPLY'))
  assert.ok(!s.has('database-ask-slow'))
})

test('back navigation after a chat route change reloads the URL route', async t => {
  const s = await screen(t, '#database/HKG/VNM/tourism/ordinary_passport')
  await s.finish(s.lookups[0], answer('FIRST VIETNAM'))
  await s.finish(await s.ask('Indonesia to Korea'), answer('SECOND KOREA', 'IDN', 'KOR'))
  const count = s.lookups.length
  const back = await s.navigate('#database/HKG/VNM/tourism/ordinary_passport')
  assert.equal(s.lookups.length, count + 1)
  await s.finish(back, answer('BACK TO VIETNAM'))
  assert.ok(s.text().includes('BACK TO VIETNAM'))
  assert.ok(!s.text().includes('SECOND KOREA'))
})

test('reopening a route after new search starts a fresh lookup', async t => {
  const hash = '#database/HKG/VNM/tourism/ordinary_passport'
  const s = await screen(t, hash)
  await s.finish(s.lookups[0], answer('ORIGINAL ANSWER'))
  act(() => s.find('database-again').props.onClick())
  const count = s.lookups.length
  const reopened = await s.navigate(hash)
  assert.equal(s.lookups.length, count + 1)
  await s.finish(reopened, answer('REOPENED ANSWER'))
  assert.ok(s.text().includes('REOPENED ANSWER'))
})
