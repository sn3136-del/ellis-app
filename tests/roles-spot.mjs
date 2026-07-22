// Spot-check counsel + immigrant case workspaces after the redesign.
const list = await (await fetch('http://127.0.0.1:9222/json')).json()
const page = list.find((p) => p.type === 'page') || list[0]
const ws = new WebSocket(page.webSocketDebuggerUrl)
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
const ok = (c, n, x) => { c ? pass++ : fail++; console.log(c ? '  PASS' : '  FAIL', n, x || '') }
const click = (t) => evaljs(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim().toLowerCase().includes(${JSON.stringify(t.toLowerCase())})); if(!b) return false; b.click(); return true})()`)
const text = async () => (await evaljs('document.body.innerText')).toLowerCase()
const login = async () => {
  await evaljs(`(()=>{const ins=[...document.querySelectorAll('input')]; if(ins.length<2) return false; const set=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set; set.call(ins[0],'admin'); ins[0].dispatchEvent(new Event('input',{bubbles:true})); set.call(ins[1],'1234'); ins[1].dispatchEvent(new Event('input',{bubbles:true})); return true})()`)
  await evaljs(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>/sign in|log in|continue/i.test(b.textContent)); b && b.click(); return true})()`)
  await sleep(1300)
}

console.log('== Counsel case workspace ==')
for (let esc = 0; esc < 3; esc++) { if (!(await evaljs(`(()=>{const x=document.querySelector('.trip-back'); if(!x) return false; x.click(); return true})()`))) break; await sleep(700) }
if ((await text()).includes('switch role')) { await click('Switch role'); await sleep(700) }
ok(await click('Counsel'), 'pick Counsel')
await sleep(500)
await click('United States'); await sleep(600)
await login()
ok(await evaljs(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.includes('Bao Tran')); if(!b) return false; b.click(); return true})()`), 'open case as counsel')
await sleep(1100)
let tabs = await evaljs(`[...document.querySelectorAll('.tab')].map(b=>b.textContent.trim())`)
ok(!tabs.includes('Risk flags') && !tabs.includes('Translation'), 'counsel tabs cleaned', JSON.stringify(tabs))
ok(tabs.includes('Compliance') && tabs.includes('Authenticity'), 'counsel keeps compliance + authenticity')
let t = await text()
ok(t.includes('case progress') && /\d+%/.test(t), 'counsel sees progression bar')
ok(!t.includes('notify counsel'), 'no Notify counsel button for counsel role')

console.log('== Immigrant case workspace ==')
await click('Switch role'); await sleep(700)
await click('Immigrant'); await sleep(500)
await click('Vietnam'); await sleep(500)
await click('United States'); await sleep(500)
await click('Work'); await sleep(600)
await login()
await evaljs(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.toLowerCase().includes('open my case')); b && b.click(); return true})()`)
await sleep(1100)
tabs = await evaljs(`[...document.querySelectorAll('.tab')].map(b=>b.textContent.trim())`)
ok(!tabs.includes('Translation'), 'immigrant Translation tab removed', JSON.stringify(tabs))
ok(tabs.includes('Travel') && tabs.includes('Documents'), 'immigrant keeps travel + documents')
t = await text()
ok(t.includes('case progress') && /\d+%/.test(t), 'immigrant sees progression bar')
ok(t.includes('notify counsel'), 'immigrant can notify counsel')

console.log(`\n==== roles spot: ${pass} passed, ${fail} failed ====`)
ws.close(); process.exit(fail ? 1 : 0)
