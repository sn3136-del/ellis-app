// Live UI test: drives the running Ellis app over the Chrome DevTools Protocol.
// Covers: role select -> country -> login -> employer dashboard (graph, tools,
// capabilities) -> Bao Tran case -> Ask Ellis file+submit to USCIS -> Q&A.
const list = await (await fetch('http://127.0.0.1:9222/json')).json()
const ws = new WebSocket(list[0].webSocketDebuggerUrl)
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })

let id = 0
const pending = new Map()
ws.onmessage = (e) => {
  const m = JSON.parse(e.data)
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
}
function send(method, params) {
  return new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })) })
}
async function evaljs(expr) {
  const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })
  if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails))
  return r.result?.result?.value
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

let pass = 0, fail = 0
const ok = (c, n, x = '') => { c ? pass++ : fail++; console.log(c ? '  PASS' : '  FAIL', n, c ? '' : x) }

// Helpers injected once.
await evaljs(`
window.__q = (sel) => document.querySelector(sel);
window.__btn = (text) => [...document.querySelectorAll('button')].find(b => b.textContent.trim().toLowerCase().includes(text.toLowerCase()));
window.__set = (el, v) => { const proto = el instanceof HTMLTextAreaElement ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype; const s = Object.getOwnPropertyDescriptor(proto,'value').set; s.call(el, v); el.dispatchEvent(new Event('input',{bubbles:true})); };
true`)

const text = async () => (await evaljs('document.body.innerText')).toLowerCase()
const click = (t) => evaljs(`(()=>{const b=window.__btn(${JSON.stringify(t)}); if(!b) return false; b.click(); return true})()`)

console.log('== Onboarding flow ==')
for (let esc = 0; esc < 3; esc++) { if (!(await evaljs(`(()=>{const x=document.querySelector('.trip-back'); if(!x) return false; x.click(); return true})()`))) break; await sleep(700) }
if ((await text()).includes('switch role')) { await click('Switch role'); await sleep(700) }
let t = await text()
if (t.includes('skip')) { await click('Skip'); await sleep(600); t = await text() }
ok(t.includes('who is using ellis today?'), 'role select shown')
ok(await click('Employer'), 'click Employer')
await sleep(500)
t = await text()
ok(t.includes('where is your organization based?'), 'country question shown')
ok(await click('United States'), 'pick United States')
await sleep(500)
t = await text()
ok(t.includes('sign in') || t.includes('password'), 'login shown')
await evaljs(`(()=>{const ins=[...document.querySelectorAll('input')]; window.__set(ins[0],'admin'); window.__set(ins[1],'1234'); return true})()`)
await evaljs(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>/sign in|log in|continue/i.test(b.textContent)); b.click(); return true})()`)
await sleep(900)

console.log('== Employer dashboard ==')
t = await text()
ok(t.includes('workforce overview'), 'animated workforce graph card present')
ok(t.includes('active cases') && t.includes('open tasks') && t.includes('documents'), 'stats in graph card')
ok(t.includes('avg compliance'), 'compliance donut label')
ok(t.includes('bao tran'), 'Bao Tran visible on dashboard')
ok(t.includes('connect your tools'), 'tools section')
ok(t.includes('click to connect'), 'logo tiles present')
ok(t.includes('what ellis can do for you'), 'capabilities section')

// Tools: click first unconnected tile -> login modal
const toolName = await evaljs(`(()=>{const tiles=[...document.querySelectorAll('button')].filter(b=>b.textContent.includes('Click to connect')); if(!tiles.length) return null; tiles[0].click(); return tiles[0].textContent.replace('Click to connect','').trim()})()`)
ok(!!toolName, 'clicked tool tile: ' + toolName)
await sleep(400)
t = await text()
ok(t.includes('sign in to'), 'tool login modal opens')
await evaljs(`(()=>{const ins=[...document.querySelectorAll('.modal input')]; ins.forEach((el,i)=>window.__set(el, i===ins.length-1?'pass':'deep24-demo-'+i)); return true})()`)
ok(await click('Sign in & authorize Ellis'), 'submit tool login')
await sleep(2200)
t = await text()
ok(t.includes('connected'), 'tool authorization success screen')
await click('Done')
await sleep(600)
t = await text()
ok(t.includes('connected'), 'tile shows connected')

console.log('== Bao Tran case + Ask Ellis ==')
await evaljs(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.includes('Bao Tran')); b.click(); return true})()`)
await sleep(900)
t = await text()
ok(t.includes('bao tran') && (t.includes('deep24') || t.includes('H-1B')), 'case workspace open')
ok(await evaljs(`(()=>{const bs=[...document.querySelectorAll('main button')].filter(b=>b.textContent.trim()==='Ask Ellis'); if(!bs.length) return false; bs[bs.length-1].click(); return true})()`), 'open Ask Ellis tab')
await sleep(500)
await evaljs(`(()=>{const inp=[...document.querySelectorAll('input')].find(i=>/ask a question/i.test(i.placeholder||'')); window.__set(inp,'file the I-129 then submit it to USCIS on their behalf'); return true})()`)
await evaljs(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Send'); b.click(); return true})()`)
await sleep(2200)
t = await text()
ok(t.includes('step 1 done') && t.includes('i-129'), 'form filled (step 1)')
ok(t.includes('uscis portal'), 'USCIS portal login modal opened')
await evaljs(`(()=>{const ins=[...document.querySelectorAll('.modal input')]; window.__set(ins[0],'IOE0912345678'); window.__set(ins[1],'secret'); return true})()`)
ok(await click('Sign in & file with USCIS'), 'sign in to USCIS')
await sleep(7000)
t = await text()
ok(t.includes('filed with uscis') && t.includes('receipt number'), 'submission success with receipt')
await click('Done — Ellis will monitor the case')
await sleep(900)
t = await text()
ok(t.includes('uscis filing submitted') || t.includes('was submitted to uscis'), 'chat records the filed receipt')

// Q&A grounding
await evaljs(`(()=>{const inp=[...document.querySelectorAll('input')].find(i=>/ask a question/i.test(i.placeholder||'')); window.__set(inp,'when does his status expire?'); return true})()`)
await evaljs(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Send'); b.click(); return true})()`)
await sleep(60000)
t = await text()
ok(/2027/.test(t), 'answer cites real expiry year')
ok(t.includes('sources'), 'citations rendered')
ok(/uscis|egov/i.test(t), 'official source cited')

console.log(`\n==== UI: ${pass} passed, ${fail} failed ====`)
ws.close()
process.exit(fail ? 1 : 0)
