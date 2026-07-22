// Screenshots of the redesigned Trip.com portal: home, new application, detail.
import { writeFileSync } from 'fs'
const list = await (await fetch('http://127.0.0.1:9222/json')).json()
const ws = new WebSocket(list.find((t) => t.type === 'page').webSocketDebuggerUrl)
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
let id = 0
const pending = new Map()
ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) } }
const send = (method, params) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })) })
const evaljs = async (expr) => (await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
await send('Page.enable', {})
await send('Page.bringToFront', {})
async function shot(name) {
  const r = await send('Page.captureScreenshot', { format: 'png' })
  writeFileSync(`/tmp/trip-${name}.png`, Buffer.from(r.result.data, 'base64'))
  console.log('saved', name)
}
const click = (t) => evaljs(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim().toLowerCase().includes(${JSON.stringify(t)}.toLowerCase())); if(!b) return false; b.click(); return true})()`)

await shot('home')
await click('get started'); await sleep(700)
await shot('new')
await click('back'); await sleep(700)
await evaljs(`(()=>{const r=[...document.querySelectorAll('button.row')][0]; if(r) r.click(); return true})()`); await sleep(900)
await shot('detail')
ws.close()
