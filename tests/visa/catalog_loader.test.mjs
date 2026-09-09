import test from 'node:test'
import assert from 'node:assert/strict'
import { catalogSignature, createCatalogLoader, createCatalogSelection } from '../../src/renderer/src/lib/catalogLoader.js'
import { STRINGS, hasDynamicCatalog, setDynamicCatalog } from '../../src/renderer/src/lib/i18n.js'

function memory() {
  const data = new Map()
  return { data, getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => data.set(key, value), removeItem: (key) => data.delete(key) }
}
function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
function harness({ entries = { title: 'Title' }, store = memory(), request } = {}) {
  const calls = [], installed = []
  const loader = createCatalogLoader({ entries, storage: () => store,
    install: (lang, values) => installed.push({ lang, values }),
    request: async (lang, source) => {
      calls.push({ lang, source })
      return request ? request(lang, source) : { status: 'ok', entries: { title: 'Titre' } }
    },
  })
  return { loader, calls, installed, store }
}

test('catalog signature uses every actual English value and ignores object key order', () => {
  assert.equal(catalogSignature({ b: 'Two', a: 'One' }), catalogSignature({ a: 'One', b: 'Two' }))
  const actual = STRINGS.en
  assert.notEqual(catalogSignature(actual), catalogSignature({ ...actual,
    'ops.stat.srcLine': 'Records cite an official page.' }))
  assert.notEqual(catalogSignature(actual), catalogSignature({ ...actual, newKey: 'New' }))
  const removed = { ...actual }; delete removed['ops.stat.srcLine']
  assert.notEqual(catalogSignature(actual), catalogSignature(removed))
})

test('legacy Chinese cache cannot keep an old source-coverage label', async () => {
  const store = memory()
  store.setItem('ellis.cat.v1.zh-CN', JSON.stringify({ 'ops.stat.srcLine': 'old official-link label' }))
  const h = harness({ entries: STRINGS.en, store, request: async () => ({ status: 'ok',
    entries: { 'ops.stat.srcLine': 'current source-support label' } }) })
  assert.equal(h.loader.needsLoad('zh-CN'), true)
  await h.loader.load('zh-CN')
  assert.equal(h.calls.length, 1)
  assert.equal(h.calls[0].source['ops.stat.srcLine'], STRINGS.en['ops.stat.srcLine'])
  assert.equal(store.getItem('ellis.cat.v1.zh-CN'), null)
  assert.equal(h.installed.at(-1).values['ops.stat.srcLine'], 'current source-support label')
})

test('current exact catalog rehydrates without another request, changed meaning refetches', async () => {
  const first = harness(); await first.loader.load('fr')
  const same = harness({ store: first.store })
  assert.equal(same.loader.needsLoad('fr'), false)
  await same.loader.load('fr')
  assert.equal(same.calls.length, 0)
  assert.deepEqual(same.installed.at(-1).values, { title: 'Titre' })
  const changed = harness({ store: first.store, entries: { title: 'A different meaning' } })
  assert.equal(changed.loader.needsLoad('fr'), true)
  await changed.loader.load('fr')
  assert.equal(changed.calls.length, 1)
  assert.deepEqual(changed.installed[0].values, {}) // remove any stale memory overlay too
})

test('same locale and catalog share one in-flight promise including Chinese', async () => {
  const d = deferred(), h = harness({ request: () => d.promise })
  const first = h.loader.load('zh-TW'), second = h.loader.load('zh-TW')
  assert.equal(first, second)
  await Promise.resolve(); assert.equal(h.calls.length, 1)
  d.resolve({ status: 'ok', entries: { title: '標題' } })
  assert.deepEqual(await Promise.all([first, second]), [true, true])
  await h.loader.load('zh-TW'); assert.equal(h.calls.length, 1)
})

test('failed or unavailable requests keep fallback and permit a real retry', async () => {
  let attempt = 0
  const h = harness({ request: async () => {
    if (++attempt === 1) throw new Error('network unavailable')
    if (attempt === 2) return { status: 'unavailable', entries: { title: 'Title' } }
    return { status: 'ok', entries: { title: 'Titre' } }
  } })
  assert.equal(await h.loader.load('fr'), false)
  assert.equal(await h.loader.load('fr'), false)
  assert.equal(h.store.getItem('ellis.cat.v2.fr'), null)
  assert.equal(await h.loader.load('fr'), true)
  assert.equal(h.calls.length, 3)
})

test('malformed response cannot install arbitrary or blank values as a ready catalog', async () => {
  const h = harness({ request: async () => ({ status: 'ok', entries: { unknown: 'Bad', title: {} } }) })
  assert.equal(await h.loader.load('fr'), false)
  assert.equal(h.loader.needsLoad('fr'), true)
  assert.deepEqual(h.installed, [{ lang: 'fr', values: {} }])
})

test('partial valid response preserves supported strings and ordinary fallback behavior', async () => {
  const h = harness({ entries: { title: 'Title', next: 'Next' }, request: async () => ({
    status: 'partial', entries: { title: 'Titre', next: 'Next', alien: 'Ignore' },
  }) })
  assert.equal(await h.loader.load('fr'), true)
  assert.deepEqual(h.installed.at(-1).values, { title: 'Titre', next: 'Next' })
})

test('storage failure does not erase the loaded memory catalog or repeat its request', async () => {
  const blocked = { getItem() { throw new Error('storage denied') },
    removeItem() { throw new Error('storage denied') }, setItem() { throw new Error('quota') } }
  const h = harness({ store: blocked })
  assert.equal(await h.loader.load('fr'), true)
  assert.equal(await h.loader.load('fr'), true)
  assert.equal(h.calls.length, 1)
})

function selectionHarness(loader) {
  const changes = [], pending = [], ready = []
  const selection = createCatalogSelection({ loader,
    isSupported: (lang) => ['en', 'fr', 'es', 'zh-CN', 'zh-TW'].includes(lang),
    hasStaticFallback: (lang) => ['en', 'zh-CN', 'zh-TW'].includes(lang),
    onLanguage: (lang) => changes.push(lang), onPending: (value) => pending.push(value),
    onReady: () => ready.push(true),
  })
  return { selection, changes, pending, ready }
}

test('late failure of old language cannot reset a newer selection or its spinner', async () => {
  const fr = deferred(), es = deferred()
  const s = selectionHarness({ needsLoad: () => true, load: (lang) => lang === 'fr' ? fr.promise : es.promise })
  const first = s.selection.select('fr'), second = s.selection.select('es')
  fr.resolve(false); await first
  assert.deepEqual(s.changes, ['fr', 'es'])
  assert.equal(s.pending.at(-1), true)
  assert.equal(s.ready.length, 0)
  es.resolve(true); await second
  assert.deepEqual(s.changes, ['fr', 'es'])
  assert.equal(s.pending.at(-1), false)
  assert.equal(s.ready.length, 1)
})

test('latest unavailable dynamic language falls back to English; Chinese keeps static text', async () => {
  const s = selectionHarness({ needsLoad: () => true, load: async () => false })
  await s.selection.select('fr')
  assert.deepEqual(s.changes, ['fr', 'en'])
  await s.selection.select('zh-CN')
  assert.deepEqual(s.changes, ['fr', 'en', 'zh-CN'])
  assert.equal(s.pending.at(-1), false)
  await s.selection.select('invented')
  assert.equal(s.changes.length, 3)
})

test('unmount or StrictMode cancellation cannot apply a stale completion', async () => {
  const d = deferred(), h = harness({ request: () => d.promise })
  const s = selectionHarness(h.loader)
  const old = s.selection.select('fr')
  s.selection.cancel()
  const current = s.selection.select('fr')
  await Promise.resolve(); assert.equal(h.calls.length, 1)
  d.resolve({ status: 'ok', entries: { title: 'Titre' } })
  await Promise.all([old, current])
  assert.equal(s.ready.length, 1)
  assert.deepEqual(s.pending, [true, true, false])
})


test('clearing a stale RTL overlay does not mark untranslated English fallback ready', async () => {
  const d = deferred()
  setDynamicCatalog('ar', { 'ops.stat.srcLine': 'old translation' })
  const loader = createCatalogLoader({ entries: STRINGS.en, storage: () => memory(),
    install: setDynamicCatalog, request: () => d.promise })
  const result = loader.load('ar')
  assert.equal(hasDynamicCatalog('ar'), false)
  d.resolve({ status: 'ok', entries: { 'ops.stat.srcLine': 'ترجمة جديدة' } })
  assert.equal(await result, true)
  assert.equal(hasDynamicCatalog('ar'), true)
  setDynamicCatalog('ar', {})
  assert.equal(hasDynamicCatalog('ar'), false)
})
