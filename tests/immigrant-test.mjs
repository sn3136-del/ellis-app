// Live UI test of the immigrant home page: single pending case card with
// animated progress, stage timeline, connect card, capability rows.
const list = await (await fetch('http://127.0.0.1:9222/json')).json()
const ws = new WebSocket(list[0].webSocketDebuggerUrl)
await new Promise((res) => { ws.onopen = res })
let id = 0; const pending = new Map()
ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) } }
const send = (method, params) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })) })
const evaljs = async (expr) => {
  const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })
  if (r.result?.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description || 'err')
  return r.result?.result?.value
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
let pass = 0, fail = 0
const ok = (c, n) => { c ? pass++ : fail++; console.log(c ? '  PASS' : '  FAIL', n) }
const click = (t) => evaljs(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim().toLowerCase().includes(${JSON.stringify(t.toLowerCase())})); if(!b) return false; b.click(); return true})()`)
const text = async () => (await evaljs('document.body.innerText')).toLowerCase()

ok(await click('Switch role'), 'switch role')
await sleep(700)
ok(await click('Immigrant'), 'pick Immigrant')
await sleep(500)
let t = await text()
ok(t.includes('where are you from?'), 'origin question')
ok(t.includes('vietnam'), 'Vietnam origin option present')
ok(await click('Vietnam'), 'pick Vietnam')
await sleep(500)
ok(await click('United States'), 'pick USA')
await sleep(500)
ok(await click('Work'), 'pick Work')
await sleep(600)
await evaljs(`(()=>{const ins=[...document.querySelectorAll('input')]; const set=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set; set.call(ins[0],'admin'); ins[0].dispatchEvent(new Event('input',{bubbles:true})); set.call(ins[1],'1234'); ins[1].dispatchEvent(new Event('input',{bubbles:true})); return true})()`)
await evaljs(`(()=>{[...document.querySelectorAll('button')].find(b=>/sign in|log in|continue/i.test(b.textContent)).click(); return true})()`)
await sleep(1200)
t = await text()
ok(t.includes('your case'), 'single-case hero card')
ok(t.includes('bao tran'), 'Bao Tran is the immigrant case')
ok(t.includes('case progress') || t.includes('% complete'), 'animated progress ring')
ok(t.includes('onboarding') && t.includes('filing') && t.includes('decision'), 'stage timeline steps')
ok(t.includes('open my case'), 'open case button')
ok(t.includes('let ellis file & track for you'), 'connect card')
ok(t.includes('what ellis can do for you'), 'capability rows')
ok(t.includes('one case at a time') || t.includes('other cases on your profile'), 'single-pending-case messaging')
console.log(`\n==== immigrant UI: ${pass} passed, ${fail} failed ====`)
ws.close(); process.exit(fail ? 1 : 0)
