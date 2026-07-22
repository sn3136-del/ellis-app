// Screenshots of the redesigned Overview / Compliance / Documents tabs (employer view).
import { writeFileSync } from 'node:fs'
const list = await (await fetch('http://127.0.0.1:9222/json')).json()
const page = list.find((p) => p.type === 'page') || list[0]
const ws = new WebSocket(page.webSocketDebuggerUrl)
await new Promise((res) => { ws.onopen = res })
let id = 0; const pending = new Map()
ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) } }
const send = (method, params) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })) })
const evaljs = async (expr) => (await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const click = (t) => evaljs(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim().toLowerCase().includes(${JSON.stringify(t.toLowerCase())})); if(!b) return false; b.click(); return true})()`)
const shot = async (name) => {
  const r = await send('Page.captureScreenshot', { format: 'png' })
  writeFileSync(`/tmp/${name}.png`, Buffer.from(r.result.data, 'base64'))
  console.log('saved /tmp/' + name + '.png')
}
const text = async () => (await evaljs('document.body.innerText')).toLowerCase()

for (let esc = 0; esc < 3; esc++) { if (!(await evaljs(`(()=>{const x=document.querySelector('.trip-back'); if(!x) return false; x.click(); return true})()`))) break; await sleep(700) }
if ((await text()).includes('switch role')) { await click('Switch role'); await sleep(700) }
await click('Employer'); await sleep(500)
await click('United States'); await sleep(600)
await evaljs(`(()=>{const ins=[...document.querySelectorAll('input')]; if(ins.length<2) return true; const set=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set; set.call(ins[0],'admin'); ins[0].dispatchEvent(new Event('input',{bubbles:true})); set.call(ins[1],'1234'); ins[1].dispatchEvent(new Event('input',{bubbles:true})); return true})()`)
await evaljs(`(()=>{const b=[...document.querySelectorAll('button')].find(x=>/sign in|log in|continue/i.test(x.textContent)); b && b.click(); return true})()`)
await sleep(1300)
await evaljs(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.includes('Bao Tran')); b && b.click(); return true})()`)
await sleep(1600)
await shot('case-overview')
await evaljs(`(()=>{[...document.querySelectorAll('.tab')].find(b=>b.textContent.trim()==='Compliance').click(); return true})()`)
await sleep(2200)
await shot('case-compliance')
await evaljs(`(()=>{[...document.querySelectorAll('.tab')].find(b=>b.textContent.trim()==='Documents').click(); return true})()`)
await sleep(1200)
await shot('case-documents')
ws.close(); process.exit(0)
