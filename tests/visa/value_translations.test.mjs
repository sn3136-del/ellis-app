import test from 'node:test'
import assert from 'node:assert/strict'
import { createValueTranslations, translationSegments } from '../../src/renderer/src/lib/valueTranslations.js'

function memory() {
  const data = new Map()
  return { getItem: k => data.get(k) ?? null, setItem: (k, v) => data.set(k, v) }
}
function deferred() {
  let resolve
  const promise = new Promise(r => { resolve = r })
  return { promise, resolve }
}
const translated = (entries, marker = '译') => ({ status: 'ok',
  entries: Object.fromEntries(Object.entries(entries).map(([k, v]) => [k, marker + v])) })

test('reader and QC share an exact-string flight and then need zero requests', async () => {
  const cache = createValueTranslations({ storage: () => memory() })
  const d = deferred(), calls = []
  const request = (lang, entries) => { calls.push({lang, entries}); return d.promise }
  const first = cache.load('zh-CN', ['Passport', 'Passport'], request)
  const second = cache.load('zh-CN', ['Passport'], request)
  await Promise.resolve()
  assert.equal(calls.length, 1)
  assert.equal(Object.keys(calls[0].entries).length, 1)
  d.resolve(translated(calls[0].entries))
  await Promise.all([first, second])
  assert.equal(cache.get('zh-CN', 'Passport'), '译Passport')
  await cache.load('zh-CN', ['Passport'], request)
  assert.equal(calls.length, 1)
})

test('warm translations rehydrate before any request; changed source requires its own proof', async () => {
  const store = memory(), first = createValueTranslations({ storage: () => store })
  await first.load('zh-CN', ['30 days'], async (_lang, entries) => translated(entries))
  const next = createValueTranslations({ storage: () => store })
  assert.equal(next.get('zh-CN', '30 days'), '译30 days')
  assert.equal(next.get('zh-CN', '90 days'), undefined)
  const requests = []
  await next.load('zh-CN', ['30 days', '90 days'], async (_lang, entries) => {
    requests.push(entries); return translated(entries)
  })
  assert.deepEqual(Object.values(requests[0]), ['90 days'])
})

test('late Simplified response never populates Traditional cache', async () => {
  const cache = createValueTranslations({ storage: () => memory() }), cn = deferred()
  const old = cache.load('zh-CN', ['Passport'], () => cn.promise)
  await cache.load('zh-Hant', ['Passport'], async (_lang, entries) => translated(entries, '繁'))
  cn.resolve({ status: 'ok', entries: { v0: '简护照' } }); await old
  assert.equal(cache.get('zh-Hant', 'Passport'), '繁Passport')
  assert.equal(cache.get('zh-CN', 'Passport'), '简护照')
})

test('partial fallback and malformed values are retried, successful strings retained', async () => {
  const cache = createValueTranslations({ storage: () => memory() })
  await cache.load('zh-CN', ['Good', 'Missing', 'Bad'], async () => ({status:'partial', entries:{v0:'正确',v1:'Missing',v2:{}}}))
  assert.equal(cache.get('zh-CN', 'Good'), '正确')
  assert.equal(cache.get('zh-CN', 'Missing'), undefined)
  const sent = []
  await cache.load('zh-CN', ['Good','Missing','Bad'], async (_lang, entries) => {sent.push(...Object.values(entries)); return translated(entries)})
  assert.deepEqual(sent, ['Missing', 'Bad'])
})

test('failure does not cache English as a translation or strand a flight', async () => {
  const cache = createValueTranslations({ storage: () => memory() })
  await cache.load('zh-CN', ['Failed'], async () => { throw Error('offline') })
  assert.equal(cache.get('zh-CN', 'Failed'), undefined)
  await cache.load('zh-CN', ['Failed'], async (_lang, entries) => translated(entries))
  assert.equal(cache.get('zh-CN', 'Failed'), '译Failed')
})

test('long official guidance is segmented within endpoint limits without dropping its tail', async () => {
  const text = 'Passport must be valid. '.repeat(70) + 'The fee is JPY 6000.'
  const pieces = translationSegments(text)
  assert.ok(pieces.length > 1)
  assert.ok(pieces.every(p => p.length <= 900))
  assert.equal(pieces.join(' '), text)
  const cache = createValueTranslations({ storage: () => memory() })
  await cache.load('zh-CN', [text], async (_lang, entries) => {
    assert.ok(Object.values(entries).every(v => v.length <= 900))
    return translated(entries)
  })
  assert.ok(cache.get('zh-CN', text).endsWith('The fee is JPY 6000.'))
})

test('equal 400-character prefixes never substitute different visa conditions', async () => {
  const a = 'A'.repeat(400) + ' visa required', b = 'A'.repeat(400) + ' visa exempt'
  const cache = createValueTranslations({ storage: () => memory() })
  await cache.load('zh-CN', [a], async (_lang, entries) => translated(entries))
  assert.equal(cache.get('zh-CN', b), undefined)
})

test('bulk requests are bounded and preserve every distinct string', async () => {
  const cache = createValueTranslations({storage:()=>memory()}), sent=[]
  const texts=Array.from({length:250},(_,i)=>`Official condition ${i}`)
  const result=await cache.load('zh-CN',texts,async (_lang,entries)=>{sent.push(Object.keys(entries).length);return translated(entries)})
  assert.deepEqual(sent,[120,120,10]);assert.equal(Object.keys(result).length,250)
})

test('blocked storage still supports instant same-session reuse', async () => {
  const cache=createValueTranslations({storage:()=>{throw Error('blocked')}})
  await cache.load('zh-CN',['Travel'],async (_lang,entries)=>translated(entries))
  assert.equal(cache.get('zh-CN','Travel'),'译Travel')
})

test('oversized source URLs and identifiers remain intact and are never submitted in pieces', async () => {
  const url = 'https://www.mofa.go.jp/visa/' + 'official-12345'.repeat(100) + '?fee=6000&stay=90'
  const token = '1234567890'.repeat(100)
  const text = `Official source ${url} Identifier ${token} Fee JPY 6000.`
  const cache = createValueTranslations({ storage: () => memory() }), sent = []
  assert.ok(translationSegments(text).includes(url))
  assert.ok(translationSegments(text).includes(token))
  await cache.load('zh-CN', [text, url], async (_lang, entries) => {
    sent.push(...Object.values(entries)); return translated(entries)
  })
  assert.ok(sent.every(value => value.length <= 900 && !value.includes('https:') && !value.includes('12345')))
  assert.equal(cache.get('zh-CN', url), url)
  assert.ok(cache.get('zh-CN', text).includes(url))
  assert.ok(cache.get('zh-CN', text).includes(token))
  assert.ok(cache.get('zh-CN', text).endsWith('Fee JPY 6000.'))
})

test('at most two batches run across simultaneous callers and languages; queued strings share flights', async () => {
  const cache = createValueTranslations({ storage: () => memory() }), calls = []
  const texts = Array.from({length: 500}, (_, i) => `Rule ${i}`)
  let active = 0, peak = 0
  const request = (lang, entries) => {
    const d = deferred()
    active += 1; peak = Math.max(peak, active)
    calls.push({lang, entries, finish() { active -= 1; d.resolve(translated(entries)) }})
    return d.promise
  }
  const first = cache.load('zh-CN', texts, request)
  const second = cache.load('zh-CN', [texts.at(-1)], request)
  const third = cache.load('zh-Hant', ['Passport'], request)
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(calls.length, 2)
  calls[0].finish()
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(cache.get('zh-CN', texts[0]), '译' + texts[0])
  assert.equal(calls.length, 3)
  calls[1].finish()
  for (let i = 2; i < 6; i += 1) {
    await new Promise(resolve => setImmediate(resolve))
    calls[i].finish()
  }
  await Promise.all([first, second, third])
  assert.equal(peak, 2)
  assert.equal(calls.length, 6)
  assert.equal(Object.keys(cache.snapshot('zh-CN', texts)).length, 500)
  assert.equal(cache.get('zh-Hant', 'Passport'), '译Passport')
})
