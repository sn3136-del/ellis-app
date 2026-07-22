// Capture full-window Welcome / User information / Confirmation for the Trip.com pitch deck.
import { writeFileSync, copyFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const OUT = join(ROOT, 'deck-assets')

const list = await (await fetch('http://127.0.0.1:9222/json')).json()
const page = list.find((t) => t.type === 'page') || list[0]
const ws = new WebSocket(page.webSocketDebuggerUrl)
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
let id = 0
const pending = new Map()
ws.onmessage = (e) => {
  const m = JSON.parse(e.data)
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
}
const send = (method, params = {}) => new Promise((res) => {
  const i = ++id
  pending.set(i, res)
  ws.send(JSON.stringify({ id: i, method, params }))
})
await send('Runtime.enable')
await send('Page.enable')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
async function evaljs(expr) {
  const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })
  return r.result?.result?.value
}

async function shot(name) {
  // Match product window; capture full surface (no CSS crop later)
  await send('Emulation.setDeviceMetricsOverride', {
    width: 1440, height: 900, deviceScaleFactor: 2, mobile: false
  })
  await sleep(350)
  // Prefer clipping the trip shell so we get the full product chrome
  const box = await evaljs(`(() => {
    const el = document.querySelector('.trip-shell') || document.body
    const r = el.getBoundingClientRect()
    return { x: r.x, y: r.y, width: r.width, height: r.height, dpr: window.devicePixelRatio || 1 }
  })()`)
  const clip = {
    x: Math.max(0, box.x),
    y: Math.max(0, box.y),
    width: Math.min(box.width, 1440),
    height: Math.min(box.height, 900),
    scale: 1
  }
  const r = await send('Page.captureScreenshot', {
    format: 'png',
    fromSurface: true,
    captureBeyondViewport: false,
    clip
  })
  const buf = Buffer.from(r.result.data, 'base64')
  const tmp = `/tmp/deck-${name}.png`
  writeFileSync(tmp, buf)
  copyFileSync(tmp, join(OUT, `deck-${name}.png`))
  console.log('saved', name, Math.round(clip.width) + 'x' + Math.round(clip.height))
}

await evaljs(`window.__btn=(t)=>[...document.querySelectorAll('button')].find(b=>b.textContent.trim().toLowerCase().includes(t.toLowerCase())); window.__set=(el,v)=>{const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;s.call(el,v);el.dispatchEvent(new Event('input',{bubbles:true}))}; true`)
const click = (t) => evaljs(`(()=>{const b=window.__btn(${JSON.stringify(t)}); if(!b) return false; b.click(); return true})()`)
const text = async () => (await evaljs('document.body.innerText')).toLowerCase()

async function ensureHome() {
  for (let i = 0; i < 10; i++) {
    let t = await text()
    if (t.includes('process your visa') && t.includes('get started')) return true
    if (t.includes('sign in')) {
      await evaljs(`(()=>{const ins=[...document.querySelectorAll('input')]; if(ins[0]) window.__set(ins[0],'admin'); if(ins[1]) window.__set(ins[1],'1234'); return true})()`)
      await evaljs(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>/sign in/i.test(b.textContent)); if(b) b.click(); return true})()`)
      await sleep(800); continue
    }
    if (t.includes('who is using ellis')) {
      await evaljs(`(()=>{const b=[...document.querySelectorAll('.rolecard')].find(x=>x.textContent.includes('Trip.com')); if(b) b.click(); return true})()`)
      await sleep(700); continue
    }
    if (t.includes('skip')) { await click('Skip'); await sleep(400); continue }
    if (t.includes('switch role')) { await click('Switch role'); await sleep(500); continue }
    await evaljs(`(()=>{const x=document.querySelector('.trip-back'); if(x) x.click(); return true})()`)
    await sleep(500)
  }
  return (await text()).includes('get started')
}

await ensureHome()
await shot('01-welcome')

await click('Get started')
await sleep(800)
await evaljs(`(()=>{
  const ins=[...document.querySelectorAll('input')];
  window.__set(ins[0],'Sammy Abdella');
  window.__set(ins[1],'sn3136@columbia.edu');
  window.__set(ins[2],'China');
  window.__set(ins[3],'Japan');
  window.__set(ins[4],'2026-11-01');
  window.__set(ins[5],'2026-11-14');
  return true})()`)
await sleep(1600)
await shot('02-userinfo')

console.log('continue', await click('Continue'))
let done = false
for (let w = 0; w < 90 && !done; w++) {
  await sleep(1000)
  const t = await text()
  done = t.includes('visa complete')
  if (w % 10 === 9) console.log('waiting', w + 1 + 's', 'complete=' + done)
}
if (!done) {
  await evaljs(`(()=>{const x=document.querySelector('.trip-back'); if(x) x.click(); return true})()`)
  await sleep(700)
  await evaljs(`(()=>{
    const rows=[...document.querySelectorAll('button.row, button')].filter(r=>/Sammy Abdella/i.test(r.textContent)&&/Japan/i.test(r.textContent));
    const issued = rows.find(r=>/issued|complete|ready/i.test(r.textContent)) || rows[0]
    if (issued) issued.click()
    return !!issued
  })()`)
  await sleep(1500)
}
await shot('03-confirmation')
console.log('final complete?', (await text()).includes('visa complete'))
process.exit(0)
