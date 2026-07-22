// End-to-end OCR test: renders a synthetic passport data page (with a valid
// TD3 machine-readable zone) to a PNG using Cocoa, then feeds it through the
// app's real Apple Vision OCR pipeline and checks the parsed passport fields.
import { execFileSync } from 'child_process'
import { writeFileSync } from 'fs'

const IMG = '/tmp/ellis-test-passport.png'
const GEN = `
ObjC.import('Cocoa');
function run(argv) {
  const w = 1500, h = 460;
  const img = $.NSImage.alloc.initWithSize($.NSMakeSize(w, h));
  img.lockFocus;
  $.NSColor.whiteColor.setFill;
  $.NSBezierPath.fillRect($.NSMakeRect(0, 0, w, h));
  const font = $.NSFont.fontWithNameSize('Courier', 40);
  const attrs = $.NSMutableDictionary.dictionary;
  attrs.setObjectForKey(font, $.NSFontAttributeName);
  attrs.setObjectForKey($.NSColor.blackColor, $.NSForegroundColorAttributeName);
  $('REPUBLIC OF TUNISIA  PASSPORT').drawAtPointWithAttributes($.NSMakePoint(70, 370), attrs);
  $('P<TUNBENSALAH<<AMIRA<<<<<<<<<<<<<<<<<<<<<<<<').drawAtPointWithAttributes($.NSMakePoint(70, 150), attrs);
  $('X1234567<8TUN9502143F3001015<<<<<<<<<<<<<<04').drawAtPointWithAttributes($.NSMakePoint(70, 80), attrs);
  img.unlockFocus;
  const rep = $.NSBitmapImageRep.imageRepWithData(img.TIFFRepresentation);
  const png = rep.representationUsingTypeProperties($.NSBitmapImageFileTypePNG, $.NSDictionary.dictionary);
  png.writeToFileAtomically(argv[0], true);
  return 'ok';
}`
writeFileSync('/tmp/ellis-gen-passport.js', GEN)
execFileSync('osascript', ['-l', 'JavaScript', '/tmp/ellis-gen-passport.js', IMG])

const list = await (await fetch('http://127.0.0.1:9222/json')).json()
const ws = new WebSocket(list.find((t) => t.type === 'page').webSocketDebuggerUrl)
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
let id = 0
const pending = new Map()
ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) } }
const send = (method, params) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })) })
const evaljs = async (expr) => (await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value

let pass = 0, fail = 0
const ok = (c, n, x = '') => { c ? pass++ : fail++; console.log(c ? '  PASS' : '  FAIL', n, c ? '' : x) }

const r = await evaljs(`window.ellis.ocrDoc({ path: ${JSON.stringify(IMG)} })`)
ok(r?.ok, 'Vision OCR ran on the passport image', JSON.stringify(r).slice(0, 200))
ok(r?.mrzFound, 'MRZ detected and parsed', (r?.text || '').slice(0, 200))
const f = r?.fields || {}
ok(f.passportNumber === 'X1234567', 'passport number extracted', JSON.stringify(f))
ok(f.nationality === 'Tunisia', 'nationality resolved (TUN -> Tunisia)', f.nationality)
ok(/AMIRA/.test(f.fullName || ''), 'name extracted from MRZ', f.fullName)
ok(f.birthDate === '1995-02-14', 'birth date parsed', f.birthDate)
ok(f.expiryDate === '2030-01-01', 'expiry date parsed', f.expiryDate)

console.log(`\n${pass} passed, ${fail} failed`)
ws.close()
process.exit(fail ? 1 : 0)
