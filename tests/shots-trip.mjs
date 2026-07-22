// Capture screenshots of the Trip.com portal: role select, home, new form, detail.
const list = await (await fetch('http://127.0.0.1:9222/json')).json()
const ws = new WebSocket(list[0].webSocketDebuggerUrl)
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
let id = 0
const pending = new Map()
ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) } }
const send = (method, params) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })) })
const evaljs = async (expr) => (await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
import { writeFileSync } from 'fs'
async function shot(name) {
  const r = await send('Page.captureScreenshot', { format: 'png' })
  writeFileSync(`/tmp/${name}.png`, Buffer.from(r.result.data, 'base64'))
  console.log('saved /tmp/' + name + '.png')
}
const click = (t) => evaljs(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim().toLowerCase().includes(${JSON.stringify(t.toLowerCase())})); if(!b) return false; b.click(); return true})()`)
const text = async () => (await evaljs('document.body.innerText')).toLowerCase()

if ((await text()).includes('switch role')) { await click('switch role'); await sleep(700) }
await shot('trip-roleselect')
await evaljs(`(()=>{const b=[...document.querySelectorAll('.rolecard')].find(x=>x.textContent.includes('Trip.com')); b.click(); return true})()`)
await sleep(600)
await evaljs(`(()=>{const ins=[...document.querySelectorAll('input')]; const s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set; s.call(ins[0],'admin'); ins[0].dispatchEvent(new Event('input',{bubbles:true})); s.call(ins[1],'1234'); ins[1].dispatchEvent(new Event('input',{bubbles:true})); return true})()`)
await evaljs(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>/sign in/i.test(b.textContent)); b.click(); return true})()`)
await sleep(900)
await shot('trip-home')
await click('start a visa application')
await sleep(500)
await evaljs(`(()=>{const ins=[...document.querySelectorAll('input')]; const s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set; const set=(el,v)=>{s.call(el,v); el.dispatchEvent(new Event('input',{bubbles:true}))}; set(ins[0],'Ananya Rao'); set(ins[1],'ananya@example.com'); set(ins[2],'India'); set(ins[3],'France'); set(ins[4],'2026-10-02'); set(ins[5],'2026-10-16'); return true})()`)
await sleep(1200)
await shot('trip-new')
// open the existing issued application
await click('back')
await sleep(500)
await evaljs(`(()=>{const b=[...document.querySelectorAll('.row')].find(x=>x.textContent.includes('Li Wei')); if(b) b.click(); return !!b})()`)
await sleep(800)
await shot('trip-detail')
process.exit(0)
