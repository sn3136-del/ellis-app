// Live UI test of the redesigned case workspace:
//  - Overview: progress bar + stage timeline + stats + next actions
//  - Compliance: merged audit + risk flags with donut score
//  - Documents: card grid + required checklist + translation in preview
//  - Tabs: no separate Risk flags / Translation tabs
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
const ok = (c, n, extra) => { c ? pass++ : fail++; console.log(c ? '  PASS' : '  FAIL', n, extra || '') }
const click = (t) => evaljs(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim().toLowerCase().includes(${JSON.stringify(t.toLowerCase())})); if(!b) return false; b.click(); return true})()`)
const clickTab = (t) => evaljs(`(()=>{const b=[...document.querySelectorAll('.tab')].find(b=>b.textContent.trim().toLowerCase()===${JSON.stringify(t.toLowerCase())}); if(!b) return false; b.click(); return true})()`)
const text = async () => (await evaljs('document.body.innerText')).toLowerCase()

console.log('== Onboard as employer ==')
for (let esc = 0; esc < 3; esc++) { if (!(await evaljs(`(()=>{const x=document.querySelector('.trip-back'); if(!x) return false; x.click(); return true})()`))) break; await sleep(700) }
if ((await text()).includes('switch role')) { await click('Switch role'); await sleep(700) }
ok(await click('Employer'), 'pick Employer')
await sleep(500)
ok(await click('United States'), 'pick USA')
await sleep(600)
await evaljs(`(()=>{const ins=[...document.querySelectorAll('input')]; const set=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set; set.call(ins[0],'admin'); ins[0].dispatchEvent(new Event('input',{bubbles:true})); set.call(ins[1],'1234'); ins[1].dispatchEvent(new Event('input',{bubbles:true})); return true})()`)
await evaljs(`(()=>{[...document.querySelectorAll('button')].find(b=>/sign in|log in|continue/i.test(b.textContent)).click(); return true})()`)
await sleep(1400)

console.log('== Open Bao Tran case ==')
ok(await evaljs(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.includes('Bao Tran')); if(!b) return false; b.click(); return true})()`), 'open case')
await sleep(1200)

console.log('== Overview redesign ==')
let t = await text()
ok(t.includes('case progress'), 'progress card present')
ok(/\d+%/.test(t), 'progress percentage shown')
ok(t.includes('onboarding') && t.includes('evidence') && t.includes('filing') && t.includes('decision'), 'stage progression bar')
ok(t.includes('open tasks') && t.includes('completed') && t.includes('documents'), 'stat counters')
ok(t.includes('next actions'), 'next actions list')
ok(t.includes('case file'), 'compact case file card')
ok(t.includes('show completed') || !t.includes('completed ('), 'done tasks collapsed')
const hasBar = await evaljs(`!!document.querySelector('.tabpanel')`)
ok(hasBar, 'tabpanel animation wrapper')

console.log('== Tab list ==')
const tabNames = await evaljs(`[...document.querySelectorAll('.tab')].map(b=>b.textContent.trim())`)
ok(!tabNames.includes('Risk flags'), 'no separate Risk flags tab', JSON.stringify(tabNames))
ok(!tabNames.includes('Translation'), 'no Translation tab')
ok(tabNames.includes('Compliance'), 'Compliance tab present')

console.log('== Merged Compliance tab ==')
ok(await clickTab('Compliance'), 'open Compliance')
await sleep(1800)
t = await text()
ok(t.includes('one scan covering the compliance audit and every risk flag'), 'merged description')
ok(/in good standing|needs attention|at risk/.test(t), 'verdict headline')
ok(t.includes('to address') && t.includes('checks passing'), 'summary counters')
ok(await evaljs(`!!document.querySelector('svg circle')`), 'score donut rendered')
ok(t.includes('passing checks'), 'passing checks section')
ok(await click('Re-run scan'), 'rerun button works')
await sleep(1500)
ok((await text()).includes('updated'), 'rerun timestamp shown')

console.log('== Documents redesign ==')
ok(await clickTab('Documents'), 'open Documents')
await sleep(900)
t = await text()
ok(await evaljs(`document.querySelectorAll('.doccard').length >= 4`), 'document card grid')
ok(t.includes('required for'), 'required checklist panel')
ok(/\d+\/\d+/.test(t), 'collected counter')
ok(t.includes('reviewed') || t.includes('new'), 'review status chips')

// open a preview + translate from card icon buttons
ok(await evaljs(`(()=>{const c=document.querySelector('.doccard'); if(!c) return false; c.click(); return true})()`), 'open preview from card')
await sleep(700)
t = await text()
ok(t.includes('original document'), 'preview modal open')
ok(t.includes('translate to english'), 'translation available in preview')
await click('Translate to English')
await sleep(2500)
t = await text()
ok(t.includes('english translation') || t.includes('show original'), 'translation rendered inline')
await evaljs(`(()=>{const x=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='✕'); x && x.click(); return true})()`)
await sleep(400)

console.log('== Overview task run still works ==')
ok(await clickTab('Overview'), 'back to Overview')
await sleep(700)
const ranTask = await evaljs(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim().includes('Run')); if(!b) return false; b.click(); return true})()`)
ok(ranTask, 'run a next action')
await sleep(2500)
t = await text()
ok(t.includes('show completed'), 'task moved to completed')

console.log(`\n==== case redesign UI: ${pass} passed, ${fail} failed ====`)
ws.close(); process.exit(fail ? 1 : 0)
