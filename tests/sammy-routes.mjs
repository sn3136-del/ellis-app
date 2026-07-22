// End-to-end Trip.com cases for Sammy Abdella across 5 routes.
import { writeFileSync } from 'node:fs'

const ROUTES = [
  { nat: 'China', dest: 'France', dep: '2026-11-01', ret: '2026-11-14', expectKind: 'embassy', emails: 3, appt: true, portal: /france-visas|tlscontact/i },
  { nat: 'China', dest: 'Japan', dep: '2026-11-01', ret: '2026-11-14', expectKind: 'evisa', emails: 2, appt: false, qr: true, portal: /accredited.*agency/i, channel: 'agency' },
  { nat: 'China', dest: 'South Korea', dep: '2026-11-01', ret: '2026-11-14', expectKind: 'embassy', emails: 3, appt: true, portal: /korean consulate/i },
  { nat: 'China', dest: 'USA', dep: '2026-12-01', ret: '2026-12-21', expectKind: 'embassy', emails: 3, appt: true, portal: /ceac/i },
  { nat: 'United States', dest: 'Japan', dep: '2026-11-01', ret: '2026-11-14', expectKind: 'free', emails: 2, appt: false, qr: true, portal: /visit japan web/i }
]

const list = await (await fetch('http://127.0.0.1:9222/json')).json()
const ws = new WebSocket(list.find((t) => t.type === 'page').webSocketDebuggerUrl)
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
let id = 0
const pending = new Map()
ws.onmessage = (e) => {
  const m = JSON.parse(e.data)
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
}
const send = (method, params) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })) })
await send('Runtime.enable', {})
async function evaljs(expr) {
  const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })
  if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 400))
  return r.result?.result?.value
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
await evaljs(`
window.__btn = (text) => [...document.querySelectorAll('button')].find(b => b.textContent.trim().toLowerCase().includes(text.toLowerCase()));
window.__set = (el, v) => { const proto = el instanceof HTMLTextAreaElement ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype; const s = Object.getOwnPropertyDescriptor(proto,'value').set; s.call(el, v); el.dispatchEvent(new Event('input',{bubbles:true})); };
true`)
const text = async () => (await evaljs('document.body.innerText')).toLowerCase()
const click = (t) => evaljs(`(()=>{const b=window.__btn(${JSON.stringify(t)}); if(!b) return false; b.click(); return true})()`)

let pass = 0, fail = 0
const ok = (c, n, x = '') => { c ? pass++ : fail++; console.log(c ? '  PASS' : '  FAIL', n, c ? '' : x) }
const report = []

async function ensureTripHome() {
  for (let attempt = 0; attempt < 6; attempt++) {
    let t = await text()
    if (t.includes('process your visa') && t.includes('get started')) return true
    if (t.includes('sign in')) {
      await evaljs(`(()=>{const ins=[...document.querySelectorAll('input')]; if(ins[0]) window.__set(ins[0],'admin'); if(ins[1]) window.__set(ins[1],'1234'); return true})()`)
      await evaljs(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>/sign in/i.test(b.textContent)); if(b) b.click(); return true})()`)
      await sleep(900)
      continue
    }
    if (t.includes('who is using ellis')) {
      await evaljs(`(()=>{const b=[...document.querySelectorAll('.rolecard')].find(x=>x.textContent.includes('Trip.com')); if(b) b.click(); return true})()`)
      await sleep(700)
      continue
    }
    if (t.includes('skip')) { await click('Skip'); await sleep(500); continue }
    if (t.includes('switch role')) { await click('Switch role'); await sleep(600); continue }
    const backed = await evaljs(`(()=>{const x=document.querySelector('.trip-back'); if(!x) return false; x.click(); return true})()`)
    if (!backed) {
      // force role select via switch if available in other workspaces
      await evaljs(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>/switch (role|workspace)/i.test(b.textContent)); if(b) b.click(); return true})()`)
    }
    await sleep(600)
  }
  return (await text()).includes('get started')
}

ok(await ensureTripHome(), 'Trip.com home ready')

for (const route of ROUTES) {
  const label = `${route.nat} → ${route.dest}`
  console.log(`\n== Sammy Abdella: ${label} ==`)
  ok(await ensureTripHome(), `${label}: back on home`)
  ok(await click('Get started'), `${label}: open new application`)
  await sleep(700)
  const filled = await evaljs(`(()=>{
    const ins=[...document.querySelectorAll('input')];
    if(ins.length < 6) return 'inputs='+ins.length;
    window.__set(ins[0],'Sammy Abdella');
    window.__set(ins[1],'sammy.abdella@trip.com');
    window.__set(ins[2],${JSON.stringify(route.nat)});
    window.__set(ins[3],${JSON.stringify(route.dest)});
    window.__set(ins[4],${JSON.stringify(route.dep)});
    window.__set(ins[5],${JSON.stringify(route.ret)});
    return 'ok'})()`)
  ok(filled === 'ok', `${label}: form filled`, filled)
  await sleep(1200)
  // Processing / validity day-only checks when shown
  const stats = await evaljs(`(()=>{
    const ks=[...document.querySelectorAll('.trip-stat3__k')].map(e=>e.textContent.trim().toLowerCase());
    const vs=[...document.querySelectorAll('.trip-stat3__v')].map(e=>e.textContent.trim());
    const map={}; ks.forEach((k,i)=>map[k]=vs[i]); return map;
  })()`)
  const dayRe = /^\d+(?:[–-]\d+)? days$/
  if (stats.processing) {
    ok(dayRe.test(stats.processing), `${label}: processing is day count only`, stats.processing)
  } else {
    ok(true, `${label}: processing box hidden (None)`)
  }
  if (stats.validity) {
    ok(dayRe.test(stats.validity), `${label}: validity is day count only`, stats.validity)
  }
  const continued = await click('Continue')
  ok(continued, `${label}: continue`)
  if (!continued) { report.push({ label, error: 'continue failed' }); continue }
  // Wait for agent to finish (embassy routes need more time)
  let done = false
  for (let w = 0; w < 60 && !done; w++) {
    await sleep(1000)
    done = (await text()).includes('visa complete!')
  }
  ok(done, `${label}: visa completed`)
  const trip = await evaljs(`window.ellis.listTrips().then(ts=>{
    const matches=ts.filter(t=>t.name==='Sammy Abdella' && t.destination===${JSON.stringify(route.dest)} && (t.nationality===${JSON.stringify(route.nat)} || (${JSON.stringify(route.nat)}==='United States' && (t.nationality==='USA'||t.nationality==='United States'))));
    matches.sort((a,b)=>(b.createdAt||0)-(a.createdAt||0));
    const x=matches[0];
    if(!x) return null;
    const log=x.emailLog||[];
    return {
      kind: x.plan?.kind,
      processing: x.plan?.processing,
      validity: x.plan?.validity,
      portal: x.plan?.portal,
      channel: x.plan?.channel,
      status: x.status,
      appt: !!x.appointmentAt,
      log: log.length,
      subjects: log.map(e=>e.subject || e.title),
      titles: log.map(e=>e.title).join(' | '),
      noEllis: !JSON.stringify(log).toLowerCase().includes('ellis')
    }
  })`)
  ok(!!trip, `${label}: trip persisted`)
  if (!trip) { report.push({ label, error: 'missing trip' }); continue }
  ok(trip.kind === route.expectKind, `${label}: kind ${route.expectKind}`, trip.kind)
  ok(trip.log === route.emails, `${label}: ${route.emails} emails`, JSON.stringify(trip.subjects))
  ok(trip.noEllis, `${label}: emails never mention Ellis`)
  if (route.portal) ok(route.portal.test(trip.portal || ''), `${label}: realistic portal`, trip.portal)
  if (route.channel) ok(trip.channel === route.channel, `${label}: channel ${route.channel}`, trip.channel)
  ok(trip.subjects.every((s) => String(s).length <= 40), `${label}: concise subjects`, JSON.stringify(trip.subjects))
  if (route.appt) {
    ok(trip.appt && trip.titles.toLowerCase().includes('appointment'), `${label}: appointment email sent`, trip.titles)
  } else {
    ok(!trip.appt && !trip.titles.toLowerCase().includes('appointment'), `${label}: no appointment email`, trip.titles)
  }
  if (route.qr) {
    ok(trip.titles.toLowerCase().includes('qr ready'), `${label}: QR email sent`, trip.titles)
  }
  if (trip.processing && trip.processing !== 'None') {
    ok(dayRe.test(trip.processing), `${label}: plan.processing day-only`, trip.processing)
  }
  ok(dayRe.test(trip.validity), `${label}: plan.validity day-only`, trip.validity)
  report.push({ label, ...trip })
}

console.log(`\nSammy Abdella routes: ${pass} passed, ${fail} failed`)
writeFileSync('/tmp/sammy-routes-report.json', JSON.stringify(report, null, 2))
console.log('report → /tmp/sammy-routes-report.json')
process.exit(fail ? 1 : 0)
