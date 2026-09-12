import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createLatestLoader, readQualityTab, refreshQualityRecord, publishQualityRecord, qualityRefreshOutcome } from '../../src/renderer/src/lib/qualityLoader.js'
import { createVisaClient } from '../../src/renderer/src/lib/visaBackend.js'

function deferred() { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b }); return { promise, resolve, reject } }
function harness(read, wait) {
  const state = { busy: false, data: null, errors: [] }
  const loader = createLatestLoader(read, {
    onStart() { state.busy = true }, onData(value) { state.data = value },
    onError(error) { state.errors.push(error.message) }, onFinish() { state.busy = false },
    ...(wait ? { wait } : {}),
  })
  return { loader, state }
}

test('late pre-correction records cannot replace a newer refreshed answer', async () => {
  const old = deferred(), fresh = deferred()
  const { loader, state } = harness(which => which === 'old' ? old.promise : fresh.promise)
  const a = loader.run('old'), b = loader.run('new')
  fresh.resolve({ visa: 'required' }); await b
  old.resolve({ visa: 'free' }); await a
  assert.deepEqual(state, { busy:false, data:{ visa:'required' }, errors:[] })
})

test('refresh waits for corrected records and rejects a late pre-refresh response', async () => {
  const old = deferred(), post = deferred(), updated = deferred()
  let reads = 0, writes = 0
  const { loader, state } = harness(() => ++reads === 1 ? old.promise : updated.promise)
  const oldRequest = loader.run('records')
  const route = { nationality: 'HKG', destination: 'VNM', travel_purpose: 'tourism', travel_document_type: 'ordinary_passport' }
  const refresh = refreshQualityRecord({ post: async (path, body) => {
    writes++; assert.equal(path, '/database/routes/research'); assert.deepEqual(body, route); return post.promise
  } }, route, loader, 'records')
  old.resolve({ verdict: 'visa-free' }); await oldRequest
  assert.equal(state.data, null)
  post.resolve({ research: { outcome: 'checked', renewed: true } })
  await Promise.resolve(); await Promise.resolve()
  updated.resolve({ verdict: 'visa-required', fee: 25 })
  const result = await refresh
  assert.equal(writes, 1); assert.equal(result.research.renewed, true)
  assert.deepEqual(state.data, { verdict: 'visa-required', fee: 25 })
})

test('a failed post-check QC reload cannot display successful refresh or replay the mutation', async () => {
  let writes = 0
  const { loader, state } = harness(async () => { throw new Error('HTTP 503') })
  state.data = { old: true }
  await assert.rejects(refreshQualityRecord({ post: async () => { writes++; return { research: { outcome: 'checked', renewed: true } } } },
    {}, loader, 'records'), error => error.code === 'quality_reload_failed')
  assert.equal(writes, 1); assert.deepEqual(state.data, { old: true })
  assert.deepEqual(state.errors, ['HTTP 503'])
})

test('a tab change during source refresh reloads the current view without resurrecting Records', async () => {
  const post = deferred(); let tab = 'records'; const reads = []
  const { loader, state } = harness(async which => { reads.push(which); return { view: which } })
  const pending = refreshQualityRecord({ post: () => post.promise }, {}, loader, () => tab)
  tab = 'freshness'; await loader.run(tab)
  post.resolve({ research: { outcome: 'checked', renewed: true } }); await pending
  assert.deepEqual(reads, ['freshness', 'freshness'])
  assert.deepEqual(state.data, { view: 'freshness' })
})

test('a newer tab load supersedes post-refresh reload without a false failure', async () => {
  const old = deferred(); let tab = 'records'
  const { loader, state } = harness(which => which === 'records' ? old.promise : Promise.resolve({ view: which }))
  const pending = refreshQualityRecord({ post: async () => ({ research: { outcome: 'checked' } }) }, {}, loader, () => tab)
  await Promise.resolve(); await Promise.resolve()
  tab = 'freshness'; await loader.run(tab)
  old.resolve({ view: 'records' })
  assert.equal((await pending).quality_reload, 'superseded')
  assert.deepEqual(state.data, { view: 'freshness' }); assert.deepEqual(state.errors, [])
})

test('partial checked/readable/provider failures never become full verification success', () => {
  for (const research of [
    { outcome: 'checked', consistent: true, renewed: false, verified_fields: ['government_fee'], unverified_fields: ['disposition'] },
    { outcome: 'checked', consistent: true },
    { outcome: 'page_not_relevant', source_reads: 1 },
    { outcome: 'provider_error', source_reads: 1 },
    { outcome: 'checked', provider_unavailable: true, renewed: false },
    { outcome: 'fetch_failed', source_reads: 0 }, null,
  ]) assert.equal(qualityRefreshOutcome(research).tone, 'warning')
  assert.equal(qualityRefreshOutcome({ outcome: 'checked', renewed: true }).kind, 'ok')
  assert.equal(qualityRefreshOutcome({ outcome: 'provider_error' }).kind, 'providerUnavailable')
  assert.equal(qualityRefreshOutcome({ outcome: 'checked', changed: ['fee'], renewed: false }).kind, 'correctedPartial')
  assert.equal(qualityRefreshOutcome({ outcome: 'checked', changed: ['fee'], disputed_fields: ['stay'] }).kind, 'correctedDisputed')
})

test('stale request failure cannot clear the current loading state or show an old error', async () => {
  const old=deferred(), fresh=deferred()
  const { loader,state }=harness(which => which==='old' ? old.promise : fresh.promise)
  const a=loader.run('old'), b=loader.run('new')
  old.reject(new Error('old server failure')); await a
  assert.equal(state.busy,true); assert.deepEqual(state.errors,[])
  fresh.resolve('current'); await b
  assert.equal(state.data,'current'); assert.equal(state.busy,false)
})

test('a retry waiting on a network error cannot restart after a newer tab load', async () => {
  const delay=deferred(); const calls=[]
  const {loader,state}=harness(async which => { calls.push(which); if(which==='old') throw new Error('network'); return 'new data' }, () => delay.promise)
  const old=loader.run('old'); await Promise.resolve()
  await loader.run('new'); delay.resolve(); await old
  assert.deepEqual(calls,['old','new']); assert.equal(state.data,'new data')
})

test('background freshness polling yields to a manual refresh', async () => {
  const pending=deferred(); const calls=[]
  const {loader,state}=harness(which => { calls.push(which); return pending.promise })
  const request=loader.run('manual'); await loader.run('poll',{quiet:true})
  assert.deepEqual(calls,['manual']); assert.equal(state.busy,true)
  pending.resolve('refreshed'); await request
  assert.equal(state.busy,false)
})

test('unmount or tab cleanup invalidates a queued response', async () => {
  const pending=deferred(); const {loader,state}=harness(() => pending.promise)
  const request=loader.run('records'); loader.invalidate(); pending.resolve('old'); await request
  assert.equal(state.data,null)
})

test('Freshness remains readable when its optional tiles fail', async () => {
  const result=await readQualityTab({get:async path => {
    if(path==='/database/freshness') return { attempted:4, verified:1 }
    throw new Error('optional service unavailable')
  }},'freshness')
  assert.deepEqual(result,{freshness:{attempted:4,verified:1}})
})

test('record polling preserves pagination while reading the canonical list', async () => {
  const paths=[]
  const result=await readQualityTab({get:async path => { paths.push(path); return {records:[]} }},'records-poll')
  assert.deepEqual(paths,['/database/records'])
  assert.equal(result.resetShown,false)
})

test('database client bypasses a previously stored browser response', async () => {
  const original=globalThis.fetch; const options=[]
  globalThis.fetch=async (_url,init) => { options.push(init); return {ok:true,text:async ()=>'{}'} }
  try {
    const client=createVisaClient({token:'public-quality-control',orgId:'platform',userId:'test'})
    await client.get('/database/records'); await client.databaseLookup({nationality:'IDN',destination:'KOR'})
    assert.equal(options.length,2)
    assert.ok(options.every(init=>init.cache==='no-store'))
  } finally {globalThis.fetch=original}
})

test('a stalled optional response body cannot indefinitely hide completed Freshness data', async () => {
  const original=globalThis.fetch; let stalledSignal
  globalThis.fetch=async (url, init) => {
    if(url.endsWith('/health/uptime')) {
      stalledSignal=init.signal
      return {ok:true,text:()=>new Promise(()=>{})}
    }
    return {ok:true,text:async()=>url.endsWith('/database/freshness') ? '{"verified":4}' : '[]'}
  }
  try {
    const real=createVisaClient({token:'public-quality-control'})
    const client={get:(path,options)=>real.get(path, options ? {timeoutMs:5} : undefined)}
    assert.deepEqual(await readQualityTab(client,'freshness'), {freshness:{verified:4},issues:[]})
    assert.equal(stalledSignal.aborted,true)
  } finally {globalThis.fetch=original}
})

test('a stalled primary QC read exits loading with an error and permits a later refresh', async () => {
  const original=globalThis.fetch; let stalledSignal
  globalThis.fetch=(_url,init)=>{stalledSignal=init.signal; return new Promise(()=>{})}
  try {
    const client=createVisaClient({token:'public-quality-control'})
    const {loader,state}=harness(()=>client.get('/database/records',{timeoutMs:5}))
    await loader.run('records')
    assert.equal(state.busy,false); assert.match(state.errors[0],/timed out/)
    assert.equal(stalledSignal.aborted,true)
    globalThis.fetch=async()=>({ok:true,text:async()=>'{}'})
    await loader.run('records')
    assert.deepEqual(state.data,{}); assert.equal(state.busy,false)
  } finally {globalThis.fetch=original}
})

test('publishing waits for current records and rejects pre-approval reads', async () => {
  const old = deferred(), post = deferred(), fresh = deferred()
  let reads = 0, writes = 0
  const { loader, state } = harness(() => ++reads === 1 ? old.promise : fresh.promise)
  const original = loader.run('records')
  const payload = { cache_key: 'THA|THA|CHN|tourism|default|unknown|v6' }
  const pending = publishQualityRecord({ databaseApprove(body) {
    writes++; assert.deepEqual(body, payload); return post.promise
  } }, payload, loader, 'records')
  old.resolve({ held: true }); await original
  assert.equal(state.data, null)
  post.resolve({ ok: true, published: true, held: false })
  await Promise.resolve(); await Promise.resolve()
  fresh.resolve({ held: false }); await pending
  assert.deepEqual(state.data, { held: false }); assert.equal(writes, 1)
})

test('a blocked publication retains the actionable backend reason and reloads without retrying', async () => {
  const blocked = Object.assign(new Error('Resolve the conflicting visa requirement in the Correction queue.'), {
    detail: { code: 'publication_blocked', published: false, held: true },
  })
  let writes = 0
  const { loader, state } = harness(async () => ({ held: true }))
  await assert.rejects(publishQualityRecord({ databaseApprove: async () => { writes++; throw blocked } },
    {}, loader, 'records'), error => error === blocked)
  assert.equal(writes, 1); assert.deepEqual(state.data, { held: true })
})

test('legacy approval acknowledgements cannot be reported as publication', async () => {
  for (const response of [{ ok: true }, { ok: true, published: false, held: true }, { published: true, held: true }]) {
    const { loader } = harness(async () => ({ held: true }))
    await assert.rejects(publishQualityRecord({ databaseApprove: async () => response }, {}, loader, 'records'),
      error => error.code === 'publication_unconfirmed')
  }
})

test('publication never retries an ambiguous POST or a committed write whose reload failed', async () => {
  for (const ambiguous of [true, false]) {
    let writes = 0
    const { loader } = harness(async () => { throw new Error('HTTP 503') })
    await assert.rejects(publishQualityRecord({ databaseApprove: async () => {
      writes++
      if (ambiguous) throw new Error('Network failed while publishing')
      return { published: true, held: false }
    } }, {}, loader, 'records'), error => ambiguous
      ? error.message === 'Network failed while publishing' : error.code === 'publication_reload_failed')
    assert.equal(writes, 1)
  }
})

test('publication reloads the newly selected tab and cannot resurrect a stale record view', async () => {
  const post = deferred(); let tab = 'records'; const reads = []
  const { loader, state } = harness(async which => { reads.push(which); return { view: which } })
  const pending = publishQualityRecord({ databaseApprove: () => post.promise }, {}, loader, () => tab)
  tab = 'issues'; await loader.run(tab)
  post.resolve({ published: true, held: false }); await pending
  assert.deepEqual(reads, ['issues', 'issues']); assert.deepEqual(state.data, { view: 'issues' })
})

test('a newer visible view may supersede publication reload without a false error', async () => {
  const old = deferred()
  const { loader, state } = harness(which => which === 'records' ? old.promise : Promise.resolve({ view: which }))
  const pending = publishQualityRecord({ databaseApprove: async () => ({ published: true, held: false }) }, {}, loader, 'records')
  await Promise.resolve(); await Promise.resolve()
  await loader.run('freshness'); old.resolve({ view: 'records' })
  assert.equal((await pending).quality_reload, 'superseded')
  assert.deepEqual(state.data, { view: 'freshness' })
})

test('locale or client changes cannot reactivate an old loader after publish or refresh', async () => {
  for (const operation of [publishQualityRecord, refreshQualityRecord]) {
    const post = deferred(), newerRead = deferred(); let oldReads = 0
    const prior = harness(async () => { oldReads++; return { locale: 'old' } })
    const newer = harness(async () => newerRead.promise)
    let current = prior.loader
    const pending = operation({ databaseApprove: () => post.promise, post: () => post.promise },
      {}, () => current, () => 'records')
    prior.loader.invalidate(); current = newer.loader
    post.resolve({ published: true, held: false, research: { outcome: 'checked' } })
    await Promise.resolve(); await Promise.resolve()
    newerRead.resolve({ locale: 'new', held: false }); await pending
    assert.equal(oldReads, 0); assert.equal(prior.state.data, null)
    assert.deepEqual(newer.state.data, { locale: 'new', held: false })
  }
})
