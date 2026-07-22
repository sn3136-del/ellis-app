// Step-by-step debug of the Ask Ellis -> USCIS submit flow in the live app.
const list = await (await fetch('http://127.0.0.1:9223/json')).json()
const ws = new WebSocket(list[0].webSocketDebuggerUrl)
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

// Ask the file+submit command
await evaljs(`(()=>{const inp=[...document.querySelectorAll('input')].find(i=>/ask a question/i.test(i.placeholder||'')); const proto=window.HTMLInputElement.prototype; Object.getOwnPropertyDescriptor(proto,'value').set.call(inp,'file the I-129 then submit it to USCIS on their behalf'); inp.dispatchEvent(new Event('input',{bubbles:true})); return true})()`)
await evaljs(`(()=>{[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Send').click(); return true})()`)
await sleep(2500)
console.log('modal present:', await evaljs(`!!document.querySelector('.modal')`))
console.log('modal inputs:', await evaljs(`[...document.querySelectorAll('.modal input')].map(i=>i.type+':'+(i.placeholder||''))`))
// Fill credentials
await evaljs(`(()=>{const ins=[...document.querySelectorAll('.modal input')]; const set=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set; ins.forEach((el,i)=>{set.call(el, i? 'secretpw':'IOE0912345678'); el.dispatchEvent(new Event('input',{bubbles:true}))}); return ins.length})()`)
await sleep(300)
const btn = await evaljs(`(()=>{const b=[...document.querySelectorAll('.modal button')].find(b=>/sign in & file/i.test(b.textContent)); return b ? {disabled:b.disabled, text:b.textContent.trim()} : null})()`)
console.log('submit button:', JSON.stringify(btn))
await evaljs(`(()=>{const b=[...document.querySelectorAll('.modal button')].find(b=>/sign in & file/i.test(b.textContent)); b.click(); return true})()`)
for (let s = 0; s < 8; s++) {
  await sleep(1000)
  console.log(`t=${s + 1}s:`, await evaljs(`(document.querySelector('.modal')?.innerText||'NO MODAL').replace(/\\n+/g,' | ').slice(0,140)`))
}
const done = await evaljs(`(()=>{const b=[...document.querySelectorAll('.modal button')].find(b=>/done/i.test(b.textContent)); if(!b) return false; b.click(); return true})()`)
console.log('clicked Done:', done)
await sleep(1200)
const chat = await evaljs(`document.body.innerText.toLowerCase()`)
console.log('chat has receipt:', /receipt number: (ioe|w)/.test(chat), '| filing submitted:', chat.includes('uscis filing submitted'))
console.log('facts updated:', chat.includes('filing'))
ws.close(); process.exit(0)
