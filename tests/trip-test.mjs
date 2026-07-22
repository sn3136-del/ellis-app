// Live UI test: the Trip.com one-click agentic tourist-visa flow (v2 UI).
// Covers: Trip.com tile -> login -> branded portal (no Ellis header items,
// language switcher) -> new application (date limits, live route check) ->
// agent pipeline -> progress bar 100% -> per-step email confirmations ->
// visa PDF delivered. Then a second, hard consular scenario (China -> USA).
const list = await (await fetch('http://127.0.0.1:9222/json')).json()
const ws = new WebSocket(list.find((t) => t.type === 'page').webSocketDebuggerUrl)
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })

let id = 0
const pending = new Map()
ws.onmessage = (e) => {
  const m = JSON.parse(e.data)
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return }
  if (m.method === 'Runtime.exceptionThrown') console.log('  [renderer exception]', JSON.stringify(m.params.exceptionDetails).slice(0, 600))
  if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') console.log('  [console.error]', m.params.args.map((a) => a.value ?? a.description ?? '').join(' ').slice(0, 400))
}
function send(method, params) {
  return new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })) })
}
await send('Runtime.enable', {})
async function evaljs(expr) {
  const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })
  if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails))
  return r.result?.result?.value
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

let pass = 0, fail = 0
const ok = (c, n, x = '') => { c ? pass++ : fail++; console.log(c ? '  PASS' : '  FAIL', n, c ? '' : x) }

await evaljs(`
window.__btn = (text) => [...document.querySelectorAll('button')].find(b => b.textContent.trim().toLowerCase().includes(text.toLowerCase()));
window.__set = (el, v) => { const proto = el instanceof HTMLTextAreaElement ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype; const s = Object.getOwnPropertyDescriptor(proto,'value').set; s.call(el, v); el.dispatchEvent(new Event('input',{bubbles:true})); };
true`)

const text = async () => (await evaljs('document.body.innerText')).toLowerCase()
const click = (t) => evaljs(`(()=>{const b=window.__btn(${JSON.stringify(t)}); if(!b) return false; b.click(); return true})()`)

async function fillNewApp(name, email, nat, dest, dep, ret) {
  await evaljs(`(()=>{
    const ins=[...document.querySelectorAll('input')];
    window.__set(ins[0],${JSON.stringify(name)});
    window.__set(ins[1],${JSON.stringify(email)});
    window.__set(ins[2],${JSON.stringify(nat)});
    window.__set(ins[3],${JSON.stringify(dest)});
    window.__set(ins[4],${JSON.stringify(dep)});
    window.__set(ins[5],${JSON.stringify(ret)});
    return true})()`)
}

console.log('== Role select ==')
for (let esc = 0; esc < 3; esc++) { if (!(await evaljs(`(()=>{const x=document.querySelector('.trip-back'); if(!x) return false; x.click(); return true})()`))) break; await sleep(700) }
let t = await text()
if (t.includes('switch role')) { await click('Switch role'); await sleep(700); t = await text() }
if (t.includes('skip')) { await click('Skip'); await sleep(600); t = await text() }
ok(t.includes('who is using ellis today?'), 'role select shown')
ok(t.includes('trip.com'), 'Trip.com tile present')
ok(await evaljs(`(()=>{const b=[...document.querySelectorAll('.rolecard')].find(x=>x.textContent.includes('Trip.com')); if(!b) return false; b.click(); return true})()`), 'click Trip.com tile')
await sleep(600)

console.log('== Login (Trip.com skips country step) ==')
t = await text()
ok(t.includes('sign in'), 'login shown directly (no country step)')
await evaljs(`(()=>{const ins=[...document.querySelectorAll('input')]; window.__set(ins[0],'admin'); window.__set(ins[1],'1234'); return true})()`)
await evaljs(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>/sign in/i.test(b.textContent)); b.click(); return true})()`)
await sleep(900)

console.log('== Branded portal header ==')
t = await text()
ok(t.includes('process your visa, in one click'), 'minimal hero shown')
const header = await evaljs(`(()=>{const h=document.querySelector('.trip-head'); return { text: h.innerText, imgs: [...h.querySelectorAll('img')].map(i=>i.alt), hasSwitch: /switch role/i.test(h.innerText), hasEllis: [...h.querySelectorAll('img')].some(i=>/ellis/i.test(i.alt)) } })()`)
ok(header.imgs.includes('Trip.com') && !header.hasEllis, 'header shows Trip.com logo only (no Ellis)')
ok(!header.hasSwitch && !/powered by ellis/i.test(header.text), 'no Switch role / Ellis chip in header')
ok(/EN/.test(header.text) && header.text.includes('简体中文') && !header.text.includes('粵語'), 'language switcher EN / Mandarin only')
const blueBtn = await evaljs(`(()=>{const b=window.__btn('get started'); return getComputedStyle(b).backgroundColor})()`)
ok(/rgb\(40, 125, 250\)/.test(blueBtn), 'buttons use Trip.com brand blue #287dfa', blueBtn)

console.log('== Language switching ==')
await evaljs(`(()=>{[...document.querySelectorAll('.trip-lang')].find(b=>b.textContent.includes('简体中文')).click(); return true})()`)
await sleep(400)
t = await text()
ok(t.includes('一键办理您的签证'), 'Mandarin hero rendered')
await evaljs(`(()=>{[...document.querySelectorAll('.trip-lang')].find(b=>b.textContent.trim()==='EN').click(); return true})()`)
await sleep(400)

console.log('== New application: date limits + route check ==')
await click('Get started')
await sleep(500)
const today = new Date().toISOString().slice(0, 10)
const mins = await evaljs(`(()=>{const ds=[...document.querySelectorAll('input[type=date]')]; return ds.map(d=>d.min)})()`)
ok(mins[0] === today, 'departure min = today (no past dates)', JSON.stringify(mins))
await fillNewApp('Li Wei', 'liwei@example.com', 'China', 'Japan', '2026-09-01', '2026-09-14')
await sleep(300)
const retMin = await evaljs(`(()=>{const ds=[...document.querySelectorAll('input[type=date]')]; return ds[1].min})()`)
ok(retMin === '2026-09-01', 'return min = departure date')
await sleep(1000)
t = await text()
ok(t.includes('short-stay') || t.includes('japan'), 'route classified (China->Japan short-stay via agency)')
ok(await evaljs(`!!document.querySelector('.trip-ready__bar')`), 'route graph with coverage bar shown (no text wall)')
ok(!t.includes('covered automatically'), 'no coverage label under the bar')
ok(!t.includes('new visa application'), 'no page title — minimal layout')
ok(t.includes('pdf or jpg'), 'PDF/JPG upload accepted (hint shown)')
const comboDrops = await evaljs(`(()=>{const c=[...document.querySelectorAll('.trip-combo')][0]; const i=c.querySelector('input'); i.dispatchEvent(new FocusEvent('focusin',{bubbles:true})); return new Promise(r=>setTimeout(()=>{const l=c.querySelector('.trip-combo__list'); if(!l) return r(null); const cs=getComputedStyle(l); i.dispatchEvent(new FocusEvent('focusout',{bubbles:true})); r({ below: l.getBoundingClientRect().top > i.getBoundingClientRect().top, scrolls: cs.overflowY==='auto', maxH: cs.maxHeight }); }, 250))})()`)
ok(comboDrops && comboDrops.below && comboDrops.scrolls, 'country dropdown opens downward and scrolls', JSON.stringify(comboDrops))
await evaljs(`document.activeElement && document.activeElement.blur(); true`)
await sleep(300)
ok(await click('Continue'), 'continue to agent')
await sleep(900)

console.log('== Agentic pipeline + progress + task list ==')
await sleep(800)
t = await text()
ok(t.includes('documents processed') && t.includes('visa completed'), 'summarized task list shown')
const flagsRow = await evaljs(`(()=>{const r=document.querySelector('.trip-detail__route'); return r ? r.innerText : ''})()`)
ok(flagsRow.includes('\u{1F1E8}\u{1F1F3}') && flagsRow.includes('\u{1F1EF}\u{1F1F5}'), 'flag route title CN -> JP', flagsRow)
ok(!t.includes('ellis agent pipeline') && !t.includes('progress'), 'no progress/pipeline text — bar only')
ok(t.includes('working on your visa') || t.includes('processing'), 'processing starts automatically after Continue')
await sleep(15000)
t = await text()
ok(t.includes('visa complete!'), 'completion state: Visa complete!')
ok(t.includes('confirmation email has been sent'), 'confirmation email note shown')
ok(await evaljs(`!!document.querySelector('.trip-complete__img')`), 'on-theme completion illustration shown')
ok(t.includes('submitted via'), 'submitted-via-portal task named')
ok(!t.includes('appointment scheduled on'), 'China→Japan agency route has no consulate appointment task')
const p1 = await evaljs(`document.querySelector('.trip-progress__fill').style.width`)
ok(p1 === '100%', 'progress bar reached 100%', p1)
ok(!t.includes('email confirmations') && !t.includes('documents on file') && !t.includes('delivered'), 'delivered/docs/email boxes removed')
ok(!t.includes('open official portal'), 'no user-facing portal link (agent handles it)')
const trip1 = await evaljs(`window.ellis.listTrips().then(ts=>{const x=ts.find(t=>t.name==='Li Wei'); const log=x?.emailLog||[]; return { s:x?.status, log:log.length, sent:log.every(e=>e.sent===true), p:!!x?.visaPath, appt:!!x?.appointmentAt, portal:x?.plan?.portal, channel:x?.plan?.channel, processing:x?.plan?.processing, validity:x?.plan?.validity, titles:log.map(e=>e.title).join(' | ').toLowerCase(), noEllis:!JSON.stringify(log).toLowerCase().includes('ellis') }})`)
ok(trip1.s === 'issued', 'trip persisted as issued', JSON.stringify(trip1))
ok(/accredited.*agency/i.test(trip1.portal || ''), 'China→Japan uses MOFA-accredited agency channel', trip1.portal)
ok(trip1.channel === 'agency', 'plan marked as agency channel')
ok(trip1.processing === '5–7 days' && trip1.validity === '15–30 days', 'China→Japan processing/validity match MOFA practice', `${trip1.processing} / ${trip1.validity}`)
ok(trip1.log === 2, 'exactly 2 emails: submission + Japan QR', JSON.stringify(trip1))
ok(trip1.titles.includes('japan submitted') || trip1.titles.includes('submitted'), 'concise submission subject', trip1.titles)
ok(trip1.titles.includes('qr ready'), 'second email is Japan QR ready', trip1.titles)
ok(trip1.noEllis, 'emails never mention Ellis')
ok(!trip1.appt, 'no consulate appointment for agency Japan route')
ok(trip1.sent, 'all emails dispatched through the mail bridge')
ok(trip1.p, 'visa PDF path recorded')

console.log('== Ask Ellis chat in the trip flow ==')
ok(t.includes('hello li'), 'chat welcome greets traveler by name + stage')
ok(t.includes('24/7 support'), 'anonymous 24/7 support chatbox present (no Ask Ellis name)')
ok(!t.includes('ask ellis') && !t.includes('kimi k3 —'), 'no AI branding on the chatbox')
await evaljs(`(()=>{const i=document.querySelector('.trip-chat__inputrow input'); window.__set(i,'Do I need travel insurance for this trip?'); return true})()`)
await evaljs(`(()=>{document.querySelector('.trip-chat__inputrow .btn').click(); return true})()`)
let replied = false
for (let w = 0; w < 20 && !replied; w++) { await sleep(1000); replied = (await evaljs(`document.querySelectorAll('.trip-chat__msg--ai').length`)) >= 2 }
ok(replied, 'Ellis chat answered the question')

console.log('== Hard case: China -> USA consular B-2 ==')
ok(await evaljs(`(()=>{const b=document.querySelector('.trip-back'); if(!b) return false; b.click(); return true})()`), 'back to list via arrow')
await sleep(600)
await click('Get started')
await sleep(500)
await fillNewApp('Zhang Min', 'zhangmin@example.com', 'China', 'USA', '2026-12-01', '2026-12-21')
await sleep(1200)
t = await text()
ok(t.includes('b-2 visitor visa'), 'B-2 visa name shown (no classification chip)')
ok(!t.includes('embassy / consular'), 'classification chip removed above fee boxes')
ok(await click('Continue'), 'continue hard case')
await sleep(1200)
t = await text()
ok(t.includes('scheduling the earliest appointment') || t.includes('appointment scheduled on'), 'appointment task in list (consular)')
ok(t.includes('ds-160'), 'DS-160 form named in Forms completed task')
ok(t.includes('ceac') && !t.includes('%20'), 'CEAC portal named cleanly (no %20 bug)')
await sleep(2000)
t = await text()
ok(t.includes('turn back a step'), 'turn-back control available while processing')
ok(await click('Turn back a step'), 'turn back one step')
await sleep(20000)
t = await text()
ok(t.includes('visa complete!'), 'hard case: visa completed end-to-end (after turning back)')
ok(t.includes('appointment scheduled on'), 'appointment task shows scheduled date')
const trip2 = await evaljs(`window.ellis.listTrips().then(ts=>{const x=ts.find(t=>t.name==='Zhang Min'); const log=x?.emailLog||[]; return { s:x?.status, log:log.length, appt:!!x?.appointmentAt, titles:log.map(e=>e.title).join(' | ').toLowerCase(), noEllis:!JSON.stringify(log).toLowerCase().includes('ellis') }})`)
ok(trip2.s === 'issued' && trip2.log === 3, 'hard case: 3 emails (submit + appointment + visa)', JSON.stringify(trip2))
ok(trip2.appt, 'interview appointment scheduled in UI')
ok(/usa submitted/i.test(trip2.titles) && /usa appointment/i.test(trip2.titles) && /usa visa ready/i.test(trip2.titles), 'concise email subjects for USA consular', trip2.titles)
ok(trip2.noEllis, 'hard-case emails never mention Ellis')
t = await text()
ok(!t.includes('turn back a step'), 'turn-back hidden once visa issued')

console.log('== Home list reflects both applications ==')
ok(await evaljs(`(()=>{const b=document.querySelector('.trip-back'); if(!b) return false; b.click(); return true})()`), 'back to list again via arrow')
await sleep(600)
t = await text()
ok(t.includes('li wei') && t.includes('zhang min'), 'both applications listed')
const bars = await evaljs(`document.querySelectorAll('.trip-minibar').length`)
ok(bars >= 2, 'progress mini-bars on list rows')

console.log('== Right-click delete ==')
const targetId = await evaljs(`window.ellis.listTrips().then(ts=>ts.find(t=>t.name==='Zhang Min')?.id)`)
const before = await evaljs(`window.ellis.listTrips().then(ts=>ts.length)`)
await evaljs(`(()=>{const r=[...document.querySelectorAll('button.row')].find(x=>x.textContent.includes('Zhang Min')); r.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:420,clientY:380})); return true})()`)
await sleep(300)
ok(await evaljs(`!!document.querySelector('.trip-ctx')`), 'context menu appears on right-click')
await evaljs(`document.querySelector('.trip-ctx__item').click()`)
await sleep(600)
const after = await evaljs(`window.ellis.listTrips().then(ts=>ts.length)`)
ok(after === before - 1, 'Delete case removes the application', `${before} -> ${after}`)
const stillThere = await evaljs(`window.ellis.listTrips().then(ts=>ts.some(t=>t.id===${JSON.stringify(targetId)}))`)
ok(!stillThere, 'deleted case gone from store')

console.log(`\n${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
