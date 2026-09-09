import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createLatestLoader, readQualityTab } from '../../src/renderer/src/lib/qualityLoader.js'

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
