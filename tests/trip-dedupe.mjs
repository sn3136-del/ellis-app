const list = await (await fetch('http://127.0.0.1:9222/json')).json()
const ws = new WebSocket(list.find((t) => t.type === 'page').webSocketDebuggerUrl)
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
let id = 0
const pending = new Map()
ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) } }
const send = (method, params) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })) })
const ev = async (e) => (await send('Runtime.evaluate', { expression: e, awaitPromise: true, returnByValue: true })).result?.result?.value
const removed = await ev(`(async()=>{
  const ts = await window.ellis.listTrips()
  const seen = new Set(); const del = []
  for (const t of ts.sort((a,b)=>b.createdAt-a.createdAt)) {
    const k = t.name + '|' + t.destination
    if (seen.has(k) || t.status === 'processing') del.push(t.id); else seen.add(k)
  }
  for (const d of del) await window.ellis.deleteTrip(d)
  return del.length
})()`)
console.log('removed', removed)
process.exit(0)
