// Capture screenshots of the immigrant and employer dashboards via CDP.
const list = await (await fetch('http://127.0.0.1:9223/json')).json()
const ws = new WebSocket(list[0].webSocketDebuggerUrl)
await new Promise((res) => { ws.onopen = res })
let id = 0; const pending = new Map()
ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) } }
const send = (method, params) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })) })
const evaljs = async (expr) => (await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
import { writeFileSync } from 'fs'

async function shot(name) {
  const r = await send('Page.captureScreenshot', { format: 'png' })
  writeFileSync(`/tmp/${name}.png`, Buffer.from(r.result.data, 'base64'))
  console.log('saved', name)
}
const click = (t) => evaljs(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim().toLowerCase().includes(${JSON.stringify(t.toLowerCase())})); if(!b) return false; b.click(); return true})()`)

// Immigrant dashboard (current)
await evaljs(`window.scrollTo(0,0)`); await sleep(1400)
await shot('immigrant-home')

// Switch to employer
await click('Switch role'); await sleep(600)
await click('Employer'); await sleep(500)
await click('United States'); await sleep(500)
await evaljs(`(()=>{const ins=[...document.querySelectorAll('input')]; const set=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set; set.call(ins[0],'admin'); ins[0].dispatchEvent(new Event('input',{bubbles:true})); set.call(ins[1],'1234'); ins[1].dispatchEvent(new Event('input',{bubbles:true})); return true})()`)
await evaljs(`(()=>{[...document.querySelectorAll('button')].find(b=>/sign in|log in|continue/i.test(b.textContent)).click(); return true})()`)
await sleep(1600)
await shot('employer-home')
await evaljs(`document.querySelector('.main').scrollTo ? document.querySelector('.main').scrollTo(0,700) : window.scrollTo(0,700)`)
await sleep(600)
await shot('employer-tools')
ws.close(); process.exit(0)
