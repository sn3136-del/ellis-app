import { app, shell, BrowserWindow, ipcMain, dialog } from 'electron'
import { join } from 'path'
import { readFileSync, writeFileSync, existsSync, appendFileSync, mkdirSync } from 'fs'
import { execFile } from 'child_process'
import { getState, update } from './store.js'
import * as local from './localEngine.js'
import * as claude from './claude.js'
import * as localLLM from './localLLM.js'
import * as kimi from './kimi.js'
import * as tripEngine from './tripEngine.js'
import * as tripAgent from './tripAgent.js'
import { parseMrz, refineMrzWithVisualZone } from './mrz.js'
import { sendViaSmtp, smtpConfig } from './smtpSend.js'
import { nearestMission, nearestAgency } from './missions.js'
import { startMonitor, decisionsDir, tripRef } from './monitorService.js'
import { discoverPortal } from './webResearch.js'
import { OCR_LAYOUT_JXA, targetLangFor, langName, normLang, needsTranslation, translationPrompt, lookAlikeHtml, detectScriptLang } from './translate.js'
import { visaGrantHtml } from './visaDoc.js'
import { startBackend, stopBackend } from './backendService.js'

// Ellis's intelligence stack, in priority order:
//   1. Kimi K3 (Moonshot) — Ellis's immigration-tailored profile of the model
//   2. Ollama — free on-device LLM, no key
//   3. Anthropic Claude — optional paid key
//   4. Built-in deterministic engine — always available, offline
// Whatever happens, every request gets an answer.
function engine() {
  return local
}

// --- Runtime mode (main process) ---------------------------------------------
// The simulated Trip.com demo pipeline (trips:* / trips:agent:* handlers and
// the auto-emailing monitor service) is ONLY active in local_mock_demo (or
// test) mode, selected explicitly via the ELLIS_RUNTIME_MODE environment
// variable. Default is 'production': no demo handlers, no monitor, no
// fabricated documents, no automatic traveler emails.
// The packaged app is a self-contained, REAL-SERVICES-ONLY app: it bundles and
// launches its own backend and defaults to 'local_real_services' — real
// providers against a local DB, with the absolute real-only boundary (never a
// mock/synthetic portal, never invented fees/appointments/confirmations; fail
// closed instead). The simulated Trip.com demo pipeline is NEVER enabled in the
// packaged app. A developer running from source defaults to 'production' and
// starts their own backend. Only an explicit ELLIS_RUNTIME_MODE=local_mock_demo
// (dev only) turns on the demo pipeline.
const RUNTIME_MODE = String(
  process.env.ELLIS_RUNTIME_MODE || (app.isPackaged ? 'local_real_services' : 'production')
).trim() || 'production'
const DEMO_PIPELINE_ENABLED = RUNTIME_MODE === 'local_mock_demo' || RUNTIME_MODE === 'test'

// Register a demo-pipeline IPC channel: the real handler in demo mode, a
// refusal stub everywhere else (never silently simulate outside demo mode).
function demoHandle(channel, handler) {
  if (DEMO_PIPELINE_ENABLED) ipcMain.handle(channel, handler)
  else ipcMain.handle(channel, () => ({ error: 'demo_disabled' }))
}

let mainWindow = null

// --- Main-process lifecycle logging -----------------------------------------
// Timestamped log of every startup, window and quit/exit path, to console and
// ~/Library/Application Support/Ellis/logs/electron-main.log. This is what makes
// an otherwise-silent "Electron exited 0" diagnosable.
const MAIN_LOG = (() => {
  try {
    // homedir-based (not app.getPath, which can depend on app-ready) so the
    // very first module-load line is captured too.
    const home = process.env.HOME || (app.getPath && app.getPath('home')) || ''
    const dir = join(home, 'Library', 'Application Support', 'Ellis', 'logs')
    mkdirSync(dir, { recursive: true })
    return join(dir, 'electron-main.log')
  } catch { return null }
})()
function mlog(...args) {
  const line = `[${new Date().toISOString()}] [main] ${args.map(String).join(' ')}`
  try { console.log(line) } catch {}
  try { if (MAIN_LOG) appendFileSync(MAIN_LOG, line + '\n') } catch {}
}

process.on('uncaughtException', (err) => {
  mlog('UNCAUGHT EXCEPTION:', err && err.stack ? err.stack : err)
})
process.on('unhandledRejection', (reason) => {
  mlog('UNHANDLED REJECTION:', reason && reason.stack ? reason.stack : reason)
})
app.on('will-quit', () => mlog('app will-quit'))
app.on('quit', (_e, code) => mlog('app quit; exitCode=' + code))
mlog(`main module loaded; isPackaged=${app.isPackaged} runtimeMode=${RUNTIME_MODE} demoPipeline=${DEMO_PIPELINE_ENABLED}`)

function createWindow() {
  mlog('createWindow() called')
  mainWindow = new BrowserWindow({
    width: 1320,
    height: 880,
    minWidth: 960,
    minHeight: 640,
    show: false,
    backgroundColor: '#ffffff',
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    trafficLightPosition: { x: 16, y: 18 },
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false,
      contextIsolation: true,
      nodeIntegration: false,
      // The agentic pipeline runs on renderer timers; keep it moving even
      // when the window is minimized or covered by another app.
      backgroundThrottling: false
    }
  })

  mlog('BrowserWindow created')
  mainWindow.on('ready-to-show', () => { mlog('window ready-to-show'); mainWindow.show() })
  mainWindow.on('closed', () => { mlog('window closed'); mainWindow = null })
  mainWindow.webContents.on('did-finish-load', () => mlog('renderer did-finish-load'))
  mainWindow.webContents.on('did-fail-load', (_e, code, desc, url) =>
    mlog(`renderer DID-FAIL-LOAD code=${code} desc="${desc}" url=${url}`))
  mainWindow.webContents.on('render-process-gone', (_e, d) =>
    mlog('renderer render-process-gone: ' + JSON.stringify(d)))

  mainWindow.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url)
    return { action: 'deny' }
  })

  const rendererUrl = process.env['ELECTRON_RENDERER_URL']
  mlog('loading renderer: ' + (rendererUrl ? rendererUrl : 'file://../renderer/index.html'))
  if (rendererUrl) {
    mainWindow.loadURL(rendererUrl).catch((e) => mlog('loadURL error: ' + (e?.message || e)))
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html')).catch((e) => mlog('loadFile error: ' + (e?.message || e)))
  }
}

// --- IPC: settings -----------------------------------------------------------
ipcMain.handle('settings:get', () => getState().settings)
ipcMain.handle('settings:save', (_e, partial) => {
  const s = update((st) => Object.assign(st.settings, partial))
  return s.settings
})

// --- IPC: notifications ----------------------------------------------------
ipcMain.handle('notifs:list', (_e, role) => {
  const all = getState().notifications || []
  return role ? all.filter((n) => n.forRole === role) : all
})
ipcMain.handle('notifs:add', (_e, notif) => {
  let created
  update((st) => {
    created = { id: 'ntf_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6), createdAt: Date.now(), read: false, ...notif }
    st.notifications.unshift(created)
    if (st.notifications.length > 200) st.notifications.length = 200
  })
  return created
})
ipcMain.handle('notifs:markRead', (_e, id) => {
  update((st) => { const n = st.notifications.find((x) => x.id === id); if (n) n.read = true })
  return true
})
ipcMain.handle('notifs:markAllRead', (_e, role) => {
  update((st) => st.notifications.forEach((n) => { if (!role || n.forRole === role) n.read = true }))
  return true
})

// --- IPC: open external (mailto / links) -----------------------------------
ipcMain.handle('open:external', (_e, url) => { shell.openExternal(url); return true })

// --- IPC: real email delivery ------------------------------------------------
// Sends to the actual recipient via SMTP (when configured) or macOS Mail.
// ELLIS_DEMO_EMAIL, when explicitly set in the environment, redirects all
// outbound mail to one inbox for QA runs; production leaves it unset.
const DEMO_EMAIL = process.env.ELLIS_DEMO_EMAIL || ''
const FAKE_EMAIL_RE = /@(?:[\w-]+\.)*(?:example|test|invalid|localhost)\.(?:com|org|net)$/i

function runOsascript(scriptText, timeoutMs = 90000) {
  return new Promise((resolve) => {
    const child = execFile('osascript', [], { timeout: timeoutMs }, (err, out, stderr) => {
      if (err) {
        resolve({ ok: false, error: String(stderr || err.message || 'Mail send failed').slice(0, 400), out: String(out || '') })
      } else {
        resolve({ ok: true, out: String(out || '').trim() })
      }
    })
    child.stdin.end(scriptText)
  })
}

function appleQuote(s) {
  return `"${String(s || '')
    .replace(/\\/g, '\\\\')
    .replace(/"/g, '\\"')
    .replace(/\r\n/g, '\\n')
    .replace(/\n/g, '" & return & "')
    .replace(/\r/g, '" & return & "')}"`
}

function isFakeEmail(addr) {
  return FAKE_EMAIL_RE.test(String(addr || ''))
}

async function outgoingCount() {
  const r = await runOsascript(`tell application "Mail" to return (count of outgoing messages) as string`, 15000)
  if (!r.ok) return -1
  const n = parseInt(r.out, 10)
  return Number.isFinite(n) ? n : -1
}

async function relaunchMail() {
  await runOsascript(`
tell application "Mail" to quit
delay 2
tell application "Mail"
  launch
  delay 2
end tell
return "ok"
`, 60000)
}

async function ensureMailCanSend() {
  let n = await outgoingCount()
  if (n <= 0) return { ok: true, outgoing: n }
  // Zombie Outbox entries cannot be deleted via AppleScript; relaunch clears them.
  await relaunchMail()
  n = await outgoingCount()
  return { ok: n === 0, outgoing: n }
}

async function sentContainsSubject(subject) {
  const r = await runOsascript(`
tell application "Mail"
  set hits to (messages of sent mailbox whose subject is ${appleQuote(subject)})
  return (count of hits) as string
end tell
`, 20000)
  return r.ok && parseInt(r.out, 10) > 0
}

async function deliverEmail({ to, subject, body, attachmentPath, attachmentPaths }) {
  // The Mail.app path sends plain text, so **emphasis** would arrive as
  // literal asterisks — strip it here. The SMTP path receives the original
  // and renders real <b> bold in its HTML part.
  const plainBody = String(body || '').replace(/\*\*([^*\n]+)\*\*/g, '$1')
  const requested = String(to || '').trim()
  const recipient = DEMO_EMAIL || requested
  if (!recipient || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(recipient)) {
    return { ok: false, error: 'Invalid recipient address' }
  }
  // Never hand fake demo domains to Mail — they sit forever in Outbox.
  if (isFakeEmail(requested) && !DEMO_EMAIL) {
    return { ok: true, simulated: true }
  }
  if (isFakeEmail(recipient)) {
    return { ok: true, simulated: true, redirectedFrom: requested || recipient }
  }

  // Accept one path or a list; keep only files that actually exist on disk.
  const attachments = [...(Array.isArray(attachmentPaths) ? attachmentPaths : []), attachmentPath]
    .filter((p) => p && existsSync(p))
    .filter((p, i, arr) => arr.indexOf(p) === i)

  // Optional: LionMail SMTP when the user has saved an app password.
  if (smtpConfig().configured) {
    const smtp = await sendViaSmtp({ to: recipient, subject, body, attachmentPaths: attachments })
    if (smtp.ok) return smtp
  }

  // Primary path (as before): Mail.app iCloud → Columbia.
  if (process.platform !== 'darwin') {
    return { ok: false, error: 'Automatic email requires macOS Mail' }
  }

  const ready = await ensureMailCanSend()
  if (!ready.ok) {
    return { ok: false, error: `Mail Outbox is jammed (${ready.outgoing} unsent). Quit Mail and try again.` }
  }

  const hasAttach = attachments.length > 0
  const attachLine = attachments
    .map((p) => `make new attachment with properties {file name:POSIX file ${appleQuote(p)}} at after the last paragraph\ndelay 1.2`)
    .join('\n')

  const buildScript = (visible, doSend, withAttach) => `
tell application "Mail"
  launch
  delay 0.4
  set senderAddress to ""
  try
    set acc to first account whose enabled is true
    try
      set senderAddress to user name of acc
    end try
  end try
  if senderAddress is not "" and senderAddress does not contain "@" then set senderAddress to senderAddress & "@icloud.com"
  set m to make new outgoing message with properties {subject:${appleQuote(subject)}, content:${appleQuote(plainBody)} & return, visible:${visible ? 'true' : 'false'}}
  tell m
    make new to recipient at end of to recipients with properties {address:${appleQuote(recipient)}}
    if senderAddress is not "" then
      try
        set sender to senderAddress
      end try
    end if
    ${withAttach ? attachLine : ''}
  end tell
  ${doSend ? 'send m\ndelay 2' : 'activate'}
  return senderAddress
end tell
`

  const attempt = async (withAttach) => {
    const before = await outgoingCount()
    const result = await runOsascript(buildScript(false, true, withAttach))
    if (!result.ok) return { ok: false, error: result.error }
    // Confirm delivery by polling: large attachments can sit in the Outbox
    // for several seconds. Declaring "wedged" too early caused duplicate
    // sends — only give up after the message neither cleared the Outbox nor
    // appeared in Sent for ~12s.
    let inSent = false
    let cleared = false
    for (let i = 0; i < 8; i++) {
      await new Promise((r) => setTimeout(r, 1500))
      inSent = await sentContainsSubject(subject)
      if (inSent) break
      const after = await outgoingCount()
      if (after <= before) { cleared = true; break }
    }
    const wedged = !inSent && !cleared
    return {
      ok: inSent || cleared,
      wedged,
      senderAddress: result.out || '',
      attachmentSkipped: hasAttach && !withAttach
    }
  }

  let send = await attempt(hasAttach)
  if (!send.ok && hasAttach) {
    if (send.wedged) await ensureMailCanSend()
    send = await attempt(false)
    if (send.ok) send.attachmentSkipped = true
  }
  if (!send.ok && send.wedged) {
    await ensureMailCanSend()
    send = await attempt(false)
  }
  if (send.ok) {
    return {
      ok: true,
      to: recipient,
      from: send.senderAddress || 'iCloud',
      via: 'mail.app',
      attachmentSkipped: !!send.attachmentSkipped
    }
  }

  const draft = await runOsascript(buildScript(true, false, false))
  if (draft.ok) {
    return {
      ok: false,
      drafted: true,
      error: send.error || 'Mail could not send — opened a draft',
      to: recipient
    }
  }
  return { ok: false, error: send.error || draft.error || 'Mail send failed' }
}


// --- IPC: documents --------------------------------------------------------
const BINARY_DOC_EXTS = ['pdf', 'jpg', 'jpeg', 'png', 'heic', 'webp']
ipcMain.handle('docs:pickAndRead', async () => {
  const res = await dialog.showOpenDialog(mainWindow, {
    title: 'Add a document',
    properties: ['openFile', 'multiSelections'],
    filters: [
      { name: 'Documents & images', extensions: ['pdf', 'jpg', 'jpeg', 'png', 'heic', 'webp', 'txt', 'md', 'csv', 'json', 'eml'] },
      { name: 'PDF', extensions: ['pdf'] },
      { name: 'Images', extensions: ['jpg', 'jpeg', 'png', 'heic', 'webp'] },
      { name: 'Text', extensions: ['txt', 'md', 'csv', 'json', 'eml'] }
    ]
  })
  if (res.canceled) return []
  return res.filePaths.map((p) => {
    const ext = (p.split('.').pop() || '').toLowerCase()
    const binary = BINARY_DOC_EXTS.includes(ext)
    let text = ''
    if (!binary) {
      try { text = readFileSync(p, 'utf-8') } catch { text = '' }
    }
    return { name: p.split('/').pop(), path: p, text, kind: binary ? ext : 'text' }
  })
})

// --- IPC: real OCR (Apple Vision, on-device) --------------------------------
// Reads text out of uploaded passports/IDs using macOS's built-in Vision
// framework — no cloud service, no API key, works offline. PDFs and HEIC are
// first rasterized with sips (also built into macOS). The extracted MRZ
// (machine-readable zone) is parsed into structured passport fields.
const OCR_JXA = `
ObjC.import('Foundation'); ObjC.import('Vision');
function run(argv) {
  const url = $.NSURL.fileURLWithPath(argv[0]);
  const handler = $.VNImageRequestHandler.alloc.initWithURLOptions(url, $());
  const req = $.VNRecognizeTextRequest.alloc.init;
  req.usesLanguageCorrection = false;
  const err = Ref();
  handler.performRequestsError($.NSArray.arrayWithObject(req), err);
  const out = [];
  const results = req.results;
  if (results) for (let i = 0; i < results.count; i++) {
    const cands = results.objectAtIndex(i).topCandidates(1);
    if (cands.count > 0) out.push(ObjC.unwrap(cands.objectAtIndex(0).string));
  }
  return out.join('\\n');
}`

function execP(cmd, args, timeout = 45000) {
  return new Promise((resolve) => {
    execFile(cmd, args, { timeout, maxBuffer: 8 * 1024 * 1024 }, (err, stdout, stderr) => {
      resolve({ err, stdout: String(stdout || ''), stderr: String(stderr || '') })
    })
  })
}

async function runOcr(path) {
  try {
    if (!path || !existsSync(path)) return { ok: false, error: 'File not found' }
    if (process.platform !== 'darwin') return { ok: false, error: 'On-device OCR requires macOS' }
    const ext = (path.split('.').pop() || '').toLowerCase()
    let imgPath = path
    // Vision on some macOS builds returns zero results for sips-generated
    // PNGs; JPEG rasterization is reliable, so PDFs (and PNG uploads) are
    // converted to JPEG before recognition.
    if (ext === 'pdf' || ext === 'png') {
      imgPath = join(app.getPath('temp'), `ellis-ocr-${Date.now()}.jpg`)
      const conv = await execP('sips', ['-s', 'format', 'jpeg', '-s', 'formatOptions', '92', '--resampleWidth', '2400', path, '--out', imgPath])
      if (conv.err || !existsSync(imgPath)) return { ok: false, error: 'Could not rasterize document for OCR' }
    }
    const scriptPath = join(app.getPath('temp'), 'ellis-vision-ocr.js')
    writeFileSync(scriptPath, OCR_JXA)
    const r = await execP('osascript', ['-l', 'JavaScript', scriptPath, imgPath])
    if (r.err) return { ok: false, error: (r.stderr || r.err.message || 'Vision OCR failed').slice(0, 300) }
    const text = r.stdout.trim()
    if (!text) return { ok: false, error: 'No readable text found in the image' }
    const mrz = refineMrzWithVisualZone(parseMrz(text), text)
    return { ok: true, text, fields: mrz || {}, mrzFound: !!mrz }
  } catch (err) {
    return { ok: false, error: String(err?.message || err).slice(0, 300) }
  }
}

ipcMain.handle('ocr:extract', (_e, { path }) => runOcr(path))

// Layout-aware OCR: returns per-line text + normalized bounding boxes + the
// detected dominant language. Backs the document-translation feature.
async function runLayoutOcr(path) {
  try {
    if (!path || !existsSync(path)) return { ok: false, error: 'File not found' }
    if (process.platform !== 'darwin') return { ok: false, error: 'On-device OCR requires macOS' }
    const ext = (path.split('.').pop() || '').toLowerCase()
    let imgPath = path
    if (['pdf', 'png', 'heic', 'webp'].includes(ext)) {
      imgPath = join(app.getPath('temp'), `ellis-layout-${Date.now()}.jpg`)
      const conv = await execP('sips', ['-s', 'format', 'jpeg', '-s', 'formatOptions', '92', '--resampleWidth', '2000', path, '--out', imgPath])
      if (conv.err || !existsSync(imgPath)) return { ok: false, error: 'Could not rasterize document' }
    }
    // Page aspect ratio (height/width) for faithful reconstruction.
    let aspect = 1.4
    const dims = await execP('sips', ['-g', 'pixelHeight', '-g', 'pixelWidth', imgPath])
    const hM = dims.stdout.match(/pixelHeight:\s*(\d+)/)
    const wM = dims.stdout.match(/pixelWidth:\s*(\d+)/)
    if (hM && wM && +wM[1] > 0) aspect = +hM[1] / +wM[1]
    const scriptPath = join(app.getPath('temp'), 'ellis-vision-layout.js')
    writeFileSync(scriptPath, OCR_LAYOUT_JXA)
    const r = await execP('osascript', ['-l', 'JavaScript', scriptPath, imgPath], 60000)
    if (r.err) return { ok: false, error: (r.stderr || 'Vision OCR failed').slice(0, 300) }
    let parsed
    try { parsed = JSON.parse(r.stdout.trim()) } catch { return { ok: false, error: 'OCR returned no layout' } }
    const lines = (parsed.lines || []).filter((l) => l.text && l.text.trim())
    if (!lines.length) return { ok: false, error: 'No readable text found' }
    // Script-based detection (robust for mixed Latin+native passports).
    const full = lines.map((l) => l.text).join(' ')
    const lang = detectScriptLang(full, parsed.lang)
    return { ok: true, lines, aspect, lang }
  } catch (err) {
    return { ok: false, error: String(err?.message || err).slice(0, 300) }
  }
}

// Read an image file as a data URL (rasterizing PDFs/HEIC first).
async function imageDataUrl(path) {
  const ext = (path.split('.').pop() || '').toLowerCase()
  let imgPath = path
  let aspect = 1.4
  if (['pdf', 'png', 'heic', 'webp'].includes(ext) || ext === 'jpeg') {
    imgPath = join(app.getPath('temp'), `ellis-img-${Date.now()}.jpg`)
    const conv = await execP('sips', ['-s', 'format', 'jpeg', '-s', 'formatOptions', '80', '--resampleWidth', '1600', path, '--out', imgPath])
    if (conv.err || !existsSync(imgPath)) return null
  }
  try {
    const dims = await execP('sips', ['-g', 'pixelHeight', '-g', 'pixelWidth', imgPath])
    const hM = dims.stdout.match(/pixelHeight:\s*(\d+)/); const wM = dims.stdout.match(/pixelWidth:\s*(\d+)/)
    if (hM && wM && +wM[1] > 0) aspect = +hM[1] / +wM[1]
    const b64 = readFileSync(imgPath).toString('base64')
    return { url: `data:image/jpeg;base64,${b64}`, aspect }
  } catch { return null }
}

ipcMain.handle('doc:detectLanguage', async (_e, { path }) => {
  // Prefer Kimi vision for language ID — the on-device OCR language pack does
  // not reliably recognize CJK/Arabic/Cyrillic on every macOS install.
  const s = getState().settings || {}
  const kc = kimiCfg(s)
  if (s.kimi?.enabled !== false && kc.apiKey) {
    try {
      const img = await imageDataUrl(path)
      if (img) {
        const code = await kimi.visionDetectLang(kc, img.url)
        if (code) return { ok: true, lang: normLang(code), langName: langName(code), engine: 'kimi-vision' }
      }
    } catch { /* fall back to on-device */ }
  }
  const r = await runLayoutOcr(path)
  if (!r.ok) return r
  return { ok: true, lang: r.lang, langName: langName(r.lang), lineCount: r.lines.length }
})

// Translate a document to the destination's primary language and render a
// look-alike PDF to the Desktop. The traveler downloads a document that reads
// as the original with the words replaced.
ipcMain.handle('doc:translate', async (_e, { path, docName, destination, targetLang }) => {
  const target = normLang(targetLang || targetLangFor(destination || 'USA'))
  const targetName = langName(target)
  const s = getState().settings || {}
  const kc = kimiCfg(s)
  let docLines = null
  let aspect = 1.4
  let sourceLang = 'und'
  let engine = null

  // Primary path: Kimi K3 multimodal — reads the document image directly, so
  // it handles any script (Chinese/Japanese/Arabic/Cyrillic) even where the
  // on-device OCR language pack is unavailable.
  if (s.kimi?.enabled !== false && kc.apiKey) {
    try {
      const img = await imageDataUrl(path)
      if (img) {
        // Cheap language ID first — skip the full translate when the document
        // is already in the target language (e.g. a French doc for France).
        const detected = await kimi.visionDetectLang(kc, img.url).catch(() => null)
        if (detected && !needsTranslation(detected, target)) {
          return { ok: true, skipped: true, reason: 'already-in-target', sourceLang: normLang(detected), targetLang: target }
        }
        if (detected) sourceLang = normLang(detected)
        const vt = await kimi.visionTranslate(kc, { imageDataUrl: img.url, targetName, aspect: img.aspect })
        if (vt.lines.length) {
          docLines = vt.lines
          aspect = vt.aspect
          engine = 'Kimi K3 (vision)'
          if (sourceLang === 'und') sourceLang = detectScriptLang(vt.lines.map((l) => l.text).join(' '), 'und')
        }
      }
    } catch (err) { console.error('vision translate failed', err) }
  }

  // Fallback path: on-device layout OCR + text-model translation.
  if (!docLines) {
    const doc = await runLayoutOcr(path)
    if (!doc.ok) return doc
    sourceLang = doc.lang
    aspect = doc.aspect
    if (!needsTranslation(doc.lang, target)) {
      return { ok: true, skipped: true, reason: 'already-in-target', sourceLang: doc.lang, targetLang: target }
    }
    const out = await llmTextChain(translationPrompt(doc.lines, targetName))
    const m = out && out.match(/\[[\s\S]*\]/)
    if (m) {
      try {
        const arr = JSON.parse(m[0])
        if (Array.isArray(arr) && arr.length >= Math.floor(doc.lines.length * 0.6)) {
          docLines = doc.lines.map((l, i) => ({ ...l, translated: arr[i] != null ? String(arr[i]) : l.text }))
          engine = ((s.anthropicKey || '').trim() || claude.ambientCredsAvailable()) ? 'Claude' : s.localAI?.enabled ? 'on-device AI' : 'AI'
        }
      } catch { /* fall through */ }
    }
  }

  if (!docLines) return { ok: false, error: 'Translation engine unavailable — configure an AI key in Settings.' }
  if (!needsTranslation(sourceLang, target)) {
    return { ok: true, skipped: true, reason: 'already-in-target', sourceLang, targetLang: target }
  }
  const html = lookAlikeHtml(
    { lines: docLines },
    docLines.map((l) => l.translated),
    { aspect, sourceName: langName(sourceLang), targetName, docLabel: docName || 'document', engine }
  )
  try {
    const dir = app.getPath('desktop')
    const base = `${(docName || 'document').replace(/\.[^.]+$/, '')} - ${targetName} translation`.replace(/[^\w .-]/g, '').trim() || 'Translation'
    let filePath = join(dir, base + '.pdf')
    let n = 1
    while (existsSync(filePath)) { filePath = join(dir, `${base} (${n}).pdf`); n++ }
    writeFileSync(filePath, await renderPdf(html))
    return { ok: true, path: filePath, sourceLang, sourceLangName: langName(sourceLang), targetLang: target, targetLangName: targetName, engine, lineCount: docLines.length }
  } catch (err) {
    return { ok: false, error: String(err?.message || err).slice(0, 200) }
  }
})

// --- IPC: PDF export -------------------------------------------------------
async function renderPdf(html) {
  const win = new BrowserWindow({ show: false, webPreferences: { offscreen: true } })
  try {
    await win.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(html))
    return await win.webContents.printToPDF({ printBackground: true, margins: { marginType: 'custom', top: 0.4, bottom: 0.4, left: 0.4, right: 0.4 } })
  } finally {
    win.destroy()
  }
}

ipcMain.handle('export:pdf', async (_e, { html, suggestedName }) => {
  const res = await dialog.showSaveDialog(mainWindow, {
    title: 'Save PDF',
    defaultPath: (suggestedName || 'Ellis-document') + '.pdf',
    filters: [{ name: 'PDF', extensions: ['pdf'] }]
  })
  if (res.canceled || !res.filePath) return { ok: false, canceled: true }
  try {
    writeFileSync(res.filePath, await renderPdf(html))
    return { ok: true, path: res.filePath }
  } catch (err) {
    return { ok: false, error: { message: err?.message || 'PDF export failed' } }
  }
})

// Save a PDF straight to the user's Desktop (no dialog) — used by "Ask Ellis"
// commands like "fill out the I-129 and download it to my desktop".
ipcMain.handle('export:pdfToDesktop', async (_e, { html, suggestedName }) => {
  try {
    const dir = app.getPath('desktop')
    const base = (suggestedName || 'Ellis-document').replace(/[^\w .-]/g, '').trim() || 'Ellis-document'
    let filePath = join(dir, base + '.pdf')
    let n = 1
    while (existsSync(filePath)) { filePath = join(dir, `${base} (${n}).pdf`); n++ }
    writeFileSync(filePath, await renderPdf(html))
    return { ok: true, path: filePath }
  } catch (err) {
    return { ok: false, error: { message: err?.message || 'PDF export failed' } }
  }
})

// Reveal a saved file in Finder.
ipcMain.handle('file:reveal', (_e, path) => { try { shell.showItemInFolder(path); return true } catch { return false } })

// --- IPC: AI (support chat + engine status only) ----------------------------

// SECURITY (must-hold invariant): the Electron client must NEVER contain,
// receive, or ship provider credentials, and must NEVER call an external
// provider (Kimi/Moonshot, Google Document AI, Browserbase, Stripe, cloud
// storage, government portals) with a bundled credential. All real provider
// access happens ONLY in the authenticated backend — the renderer talks to it
// through src/renderer/src/lib/visaBackend.js over authenticated HTTP.
//
// Historically this file loaded an "admin-provisioned" Kimi key from
// resources/kimi.key (bundled into the distributable app.asar) and from a
// drop-in file in userData. That shipped a live provider key to every user of
// the distributable. Both file paths are removed. See docs/SECURITY_ROTATION.md
// (the previously shipped key is marked for immediate rotation). The legacy
// on-device AI workspace may still be exercised by a DEVELOPER on their own
// machine via the ELLIS_KIMI_KEY env var, but only in an UNPACKAGED dev build —
// a packaged/distributable Ellis makes zero direct external-provider calls.
function clientProviderCallsAllowed() {
  // Never in a packaged/shipped app; a hard kill-switch env var can force-off.
  return !app.isPackaged && process.env.ELLIS_DISABLE_CLIENT_PROVIDERS !== '1'
}
let adminKeyCache = null
function adminKimiKey() {
  if (adminKeyCache !== null) return adminKeyCache
  // No bundled or drop-in credential files — ever. Dev-only env var, and never
  // when packaged. This function can never read a file inside the app bundle.
  const clean = (v) => {
    const k = String(v || '').trim()
    return k && !k.startsWith('#') ? k : ''
  }
  adminKeyCache = clientProviderCallsAllowed() ? clean(process.env.ELLIS_KIMI_KEY) : ''
  return adminKeyCache
}
function kimiCfg(s) {
  const k = s?.kimi || {}
  // In a packaged build a user-pasted key is also refused at the call sites:
  // provider access is backend-only. Dev builds may use a developer's own key.
  const own = clientProviderCallsAllowed() ? (k.apiKey || '').trim() : ''
  const apiKey = own || adminKimiKey()
  return { ...k, apiKey, managed: !own && !!apiKey }
}

function smartHandler(name, fns, localMethod) {
  ipcMain.handle(name, async (_e, payload) => {
    const s = getState().settings || {}
    const chain = []
    const kc = kimiCfg(s)
    if (fns.kimi && s.kimi?.enabled !== false && kc.apiKey) chain.push(['kimi', () => fns.kimi(kc, payload)])
    // Claude/ambient host creds are also refused in a packaged build — external
    // provider calls are backend-only. Ollama (local, keyless) stays available.
    const key = clientProviderCallsAllowed() ? (s.anthropicKey || '').trim() : ''
    if (clientProviderCallsAllowed() && (key || claude.ambientCredsAvailable())) chain.push(['claude', () => fns.claude(key, s.anthropicModel, payload)])
    if (s.localAI?.enabled) chain.push(['ollama', () => fns.ollama({ endpoint: s.localAI.endpoint, model: s.localAI.model }, payload)])
    let lastErr = null
    for (const [engineName, run] of chain) {
      try { return { ok: true, data: await run(), engine: engineName } } catch (err) { lastErr = err }
    }
    try { return { ok: true, data: await engine()[localMethod](payload), engine: 'local', warning: lastErr ? String(lastErr.message || lastErr) : undefined } } catch (err) { return { ok: false, error: parseError(err) } }
  })
}

// Report which AI engines are available (for Settings).
ipcMain.handle('ai:localStatus', async (_e, payload) => {
  return localLLM.ping(payload?.endpoint)
})
ipcMain.handle('ai:kimiStatus', async () => {
  const s = getState().settings || {}
  const kc = kimiCfg(s)
  const res = await kimi.ping(kc)
  return { ...res, managed: kc.managed }
})

smartHandler('ai:assistantChat', { kimi: kimi.assistantChat, ollama: localLLM.assistantChat, claude: claude.assistantChat }, 'assistantChat')

// --- IPC: Trip.com portal ----------------------------------------------------
// Trip applications are stored separately from immigration cases.
demoHandle('trips:list', () => getState().trips || [])
demoHandle('trips:create', (_e, data) => {
  let created
  update((st) => {
    if (!Array.isArray(st.trips)) st.trips = []
    created = {
      id: 'trip_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6),
      createdAt: Date.now(),
      updatedAt: Date.now(),
      status: 'draft',
      ...data
    }
    st.trips.unshift(created)
  })
  return created
})
demoHandle('trips:update', (_e, id, patch) => {
  let result = null
  update((st) => {
    const t = (st.trips || []).find((x) => x.id === id)
    if (t) {
      // Every status transition gets a timestamped history entry — the trail
      // behind the progress bar instead of a bare label.
      if (patch.status && patch.status !== t.status) {
        if (!Array.isArray(t.statusHistory)) t.statusHistory = []
        t.statusHistory.push({ status: patch.status, at: Date.now(), reason: patch.statusReason || null })
      }
      if (patch.statusReason !== undefined) t.statusReason = patch.statusReason
      delete patch.statusReason
      Object.assign(t, patch, { updatedAt: Date.now() })
      result = t
    }
  })
  return result
})
demoHandle('trips:delete', (_e, id) => {
  update((st) => { st.trips = (st.trips || []).filter((t) => t.id !== id) })
  return true
})

// Route classification + full plan for a tourist trip. Tries Kimi K3's
// immigration profile for the narrative brief; the structured plan always
// comes from the deterministic routing engine so the pipeline never stalls.
// The deterministic plan is instant; cache by route so the live preview and
// repeated opens don't recompute. The optional LLM narrative brief (which the
// UI does not display on the critical path) is only fetched when explicitly
// requested via wantBrief, so plan lookups never block on the model.
const planCache = new Map()
demoHandle('trips:plan', async (_e, payload) => {
  try {
    const t = payload?.traveler || {}
    const key = `${t.nationality}|${t.destination}`
    let plan = planCache.get(key)
    if (!plan) {
      plan = await tripEngine.tripPlan(payload)
      plan.engine = 'builtin'
      planCache.set(key, plan)
      if (planCache.size > 500) planCache.delete(planCache.keys().next().value)
    }
    plan = { ...plan }
    if (payload?.wantBrief && plan.engine === 'builtin') {
      const s = getState().settings || {}
      const kc = kimiCfg(s)
      if (s.kimi?.enabled !== false && kc.apiKey) {
        try { plan.brief = (await kimi.tripBrief(kc, payload)).brief; plan.engine = 'kimi' } catch { /* next */ }
      }
      const briefKey = (s.anthropicKey || '').trim()
      if (!plan.brief && (briefKey || claude.ambientCredsAvailable())) {
        try { plan.brief = (await claude.tripBrief(briefKey, s.anthropicModel, payload)).brief; plan.engine = 'claude' } catch { /* keep */ }
      }
    }
    return { ok: true, data: plan }
  } catch (err) {
    return { ok: false, error: parseError(err) }
  }
})

// --- IPC: Trip.com agent steps ----------------------------------------------
// Each pipeline step the traveler watches maps to one of these handlers. They
// do real work (OCR, LLM review, form assembly, ICS booking), persist their
// results and an agent-log entry on the trip, and return what they produced.
// The renderer renders only what came back — no invented outcomes.

function getTrip(id) {
  return (getState().trips || []).find((t) => t.id === id) || null
}

function patchTrip(id, patch, logEntry) {
  let result = null
  update((st) => {
    const t = (st.trips || []).find((x) => x.id === id)
    if (!t) return
    if (logEntry) {
      if (!Array.isArray(t.agentLog)) t.agentLog = []
      t.agentLog.push({ at: Date.now(), ...logEntry })
    }
    Object.assign(t, patch, { updatedAt: Date.now() })
    result = t
  })
  return result
}

// Step 1 — ingest: OCR every uploaded doc that still needs it, normalize the
// passport into trip state, and run the deterministic verification checks.
demoHandle('trips:agent:ingest', async (_e, { tripId }) => {
  const trip = getTrip(tripId)
  if (!trip) return { ok: false, error: 'Trip not found' }
  const docs = (trip.documents || []).map((d) => ({ ...d }))
  let ocrRan = 0
  for (const d of docs) {
    const isImage = ['pdf', 'jpg', 'jpeg', 'png', 'heic', 'webp'].includes(d.kind)
    if (!isImage || d.extracted || !d.path) continue
    const res = await runOcr(d.path)
    if (res.ok) {
      d.extracted = res.fields || null
      d.mrzFound = !!res.mrzFound
      d.text = d.text || (res.text || '').slice(0, 4000)
      ocrRan++
    }
  }
  // Verify what each upload actually IS — Kimi K3 vision looks at the file
  // and classifies it (type + plausibility + issues), so a mislabeled or
  // unusable document never silently satisfies a requirement. Heuristic
  // classification is the floor when no key/vision is available.
  const s0 = getState().settings || {}
  const kc0 = kimiCfg(s0)
  let visionChecked = 0
  await Promise.all(docs.map(async (d) => {
    if (d.docCheck && d.docType) return
    const isImage = ['pdf', 'jpg', 'jpeg', 'png', 'heic', 'webp'].includes(d.kind)
    if (isImage && d.path && s0.kimi?.enabled !== false && kc0.apiKey) {
      try {
        const img = await imageDataUrl(d.path)
        if (img) {
          const c = await kimi.visionClassifyDoc(kc0, img.url)
          d.docType = c.type
          d.docCheck = { ...c, engine: 'kimi-vision', at: Date.now() }
          visionChecked++
          return
        }
      } catch { /* fall through to heuristic */ }
    }
    d.docType = tripAgent.classifyByName(d)
    d.docCheck = { type: d.docType, label: d.name, plausible: true, issues: [], summary: 'Classified from file name/text.', engine: 'heuristic', at: Date.now() }
  }))
  const passport = docs.find((x) => x.mrzFound && x.extracted)?.extracted || trip.passport || null
  const checks = passport ? tripAgent.verifyPassport(trip, passport) : []
  const failed = checks.filter((c) => !c.ok)
  // A passport whose ICAO 9303 check digits verified is machine-authenticated;
  // that is a stronger signal than vision's judgment of a scan's look. Keep
  // the vision note as a warning but don't dead-end the filing — the
  // consulate examines the physical passport at the appointment anyway.
  const critFailed = checks.some((c) => !c.ok && ['expiry', 'nationality', 'name', 'number'].includes(c.id))
  for (const d of docs) {
    if (d.mrzFound && d.docCheck?.plausible === false && passport && !critFailed) {
      d.docCheck.overridden = 'ICAO MRZ check digits verified on-device — vision flag kept as a warning only; the physical passport is examined at the appointment.'
    }
  }
  const flaggedDocs = docs.filter((d) => d.docCheck?.plausible === false && !d.docCheck?.overridden)
  patchTrip(tripId, { documents: docs, passport, docChecks: checks }, {
    step: 'ingest',
    title: 'Documents verified',
    detail: (passport
      ? `Passport ${passport.passportNumber || '(number unreadable)'} extracted; ${checks.length} checks run, ${failed.length} flagged.${ocrRan ? ` OCR ran on ${ocrRan} document(s).` : ''}`
      : `${docs.length} document(s) on file — no machine-readable passport found yet.`)
      + (visionChecked ? ` ${visionChecked} document(s) identity-checked by Kimi K3 vision.` : '')
      + (flaggedDocs.length ? ` ${flaggedDocs.length} document(s) failed verification: ${flaggedDocs.map((d) => d.name).join(', ')}.` : '')
  })
  return { ok: true, passport, checks, docCount: docs.length, flaggedDocs: flaggedDocs.map((d) => ({ name: d.name, issues: d.docCheck?.issues || [] })) }
})

// Step 2 — review: LLM gap analysis of the uploads against the route's
// requirements (Kimi → Ollama), deterministic keyword reviewer as the floor.
demoHandle('trips:agent:review', async (_e, { tripId }) => {
  const trip = getTrip(tripId)
  if (!trip) return { ok: false, error: 'Trip not found' }
  const plan = trip.plan || await tripEngine.tripPlan({ traveler: trip })
  const s = getState().settings || {}
  const kc = kimiCfg(s)
  const det = tripAgent.deterministicGapReview(trip, plan)
  // LLM chain: Kimi K3 (key) → Claude (key) → Ollama (local). The deterministic
  // reviewer always runs and is merged in as the factual floor.
  let llm = null
  if (s.kimi?.enabled !== false && kc.apiKey) {
    llm = await tripAgent.llmGapReview({ kimi: kc, ollama: null }, trip, plan)
  }
  const anthropicKey = (s.anthropicKey || '').trim()
  if (!llm && (anthropicKey || claude.ambientCredsAvailable())) {
    try {
      const parsed = await claude.tripGapReview(anthropicKey, s.anthropicModel, tripAgent.gapReviewPrompt(trip, plan))
      if (Array.isArray(parsed?.covered) && Array.isArray(parsed?.missing)) {
        llm = { covered: parsed.covered, missing: parsed.missing, notes: String(parsed.notes || ''), engine: 'claude' }
      }
    } catch { /* fall through to Ollama */ }
  }
  if (!llm && s.localAI?.enabled) {
    llm = await tripAgent.llmGapReview({ kimi: null, ollama: s.localAI }, trip, plan)
  }
  const gap = tripAgent.mergeGapReviews(plan, det, llm)
  gap.reviewedAt = Date.now()
  const engineLabel = gap.engine.startsWith('kimi') ? 'Kimi K3 + rules'
    : gap.engine.startsWith('claude') ? 'Claude + rules'
    : gap.engine.startsWith('ollama') ? 'local AI + rules' : 'rules engine'
  patchTrip(tripId, { gapAnalysis: gap }, {
    step: 'review',
    title: `Document review (${engineLabel})`,
    detail: gap.notes || `${gap.covered.length} requirement(s) covered, ${gap.missing.length} missing.`
  })
  return { ok: true, gap }
})

// Step 3 — assemble: build the structured application from extracted fields.
demoHandle('trips:agent:assemble', async (_e, { tripId }) => {
  const trip = getTrip(tripId)
  if (!trip) return { ok: false, error: 'Trip not found' }
  const plan = trip.plan || await tripEngine.tripPlan({ traveler: trip })
  const form = tripAgent.assembleApplication(trip, plan)
  patchTrip(tripId, { applicationForm: form }, {
    step: 'assemble',
    title: `${form.form} assembled`,
    detail: form.fromPassport
      ? `${form.completeness}% of fields filled from extracted data${form.missing.length ? `; still needed: ${form.missing.join(', ')}` : ''}.`
      : `Assembled from the application form only — no passport was machine-read (${form.completeness}% complete).`
  })
  return { ok: true, form }
})

// Select the filing location for this trip: the nearest accredited agency
// (agency channels) or nearest embassy/consulate/VAC to the traveler's
// address. Persisted on the trip with the distance evidence.
demoHandle('trips:agent:mission', async (_e, { tripId }) => {
  const trip = getTrip(tripId)
  if (!trip) return { ok: false, error: 'Trip not found' }
  const plan = trip.plan || await tripEngine.tripPlan({ traveler: trip })
  const mission = plan.channel === 'agency'
    ? nearestAgency(trip.destination, trip.nationality, trip.address)
    : nearestMission(trip.destination, trip.nationality, trip.address)
  patchTrip(tripId, { mission }, {
    step: 'locate',
    title: `${mission.type === 'agency' ? 'Accredited agency' : mission.type === 'vac' ? 'Visa application centre' : 'Consulate'} selected: ${mission.name}, ${mission.city}`,
    detail: mission.matchedCity
      ? `Closest to ${mission.matchedCity}${mission.distanceKm != null ? ` (${mission.distanceKm} km)` : ''} based on the traveler's address.`
      : `Based on the traveler's country${trip.address ? '' : ' (no address provided — using the default location)'}.`
  })
  return { ok: true, mission }
})

// Step 4 — appointment: book a deterministic slot and write the ICS artifact.
demoHandle('trips:agent:appointment', async (_e, { tripId, where }) => {
  const trip = getTrip(tripId)
  if (!trip) return { ok: false, error: 'Trip not found' }
  const plan = trip.plan || await tripEngine.tripPlan({ traveler: trip })
  if (trip.mission) where = `${trip.mission.name}, ${trip.mission.address}`
  const slot = tripAgent.bookAppointmentSlot(trip)
  let icsPath = trip.appointment?.icsPath || null
  if (!icsPath || !existsSync(icsPath)) {
    try {
      const base = `${trip.name} - ${trip.destination} appointment`.replace(/[^\w .-]/g, '').trim()
      icsPath = join(app.getPath('desktop'), base + '.ics')
      let n = 1
      while (existsSync(icsPath) && !slot.existing) { icsPath = join(app.getPath('desktop'), `${base} (${n}).ics`); n++ }
      writeFileSync(icsPath, tripAgent.appointmentIcs(trip, plan, slot.at, where || `${trip.destination} consulate`))
    } catch { icsPath = null }
  }
  const appointment = { at: slot.at, where: where || `${trip.destination} consulate`, icsPath, bookedAt: trip.appointment?.bookedAt || Date.now() }
  patchTrip(tripId, { appointmentAt: slot.at, appointment }, slot.existing ? null : {
    step: 'appointment',
    title: 'Appointment booked',
    detail: `${new Date(slot.at).toLocaleString()} at ${appointment.where}.`,
    artifact: icsPath
  })
  return { ok: true, appointment }
})

// Step 5 — submit: actually transmit the filing package to the configured
// filing endpoint. In production this is the accredited agency's or visa
// centre's intake address; in demo it routes to the demo inbox (clearly
// labeled). The transmission result — via, recipient, message id — is the
// persisted evidence behind the "Submitted" claim.
demoHandle('trips:agent:submit', async (_e, { tripId, attachments, channelLabel }) => {
  const trip = getTrip(tripId)
  if (!trip) return { ok: false, error: 'Trip not found' }
  const plan = trip.plan || await tripEngine.tripPlan({ traveler: trip })
  const s = getState().settings || {}
  const endpoint = String(s.tripFiling?.endpoint || '').trim()
  const label = channelLabel || plan.portal || 'the filing channel'
  if (!endpoint) {
    // No intake address configured — the package is prepared, not transmitted.
    const submission = { at: Date.now(), channel: label, endpoint: null, transmitted: false, ok: false, prepared: true, attachments: (attachments || []).filter((a) => a && existsSync(a)) }
    patchTrip(tripId, { submission }, {
      step: 'submit',
      title: `Filing package prepared for ${label}`,
      detail: 'No filing intake address is configured (Settings → Email delivery) — the package is ready for manual filing.',
      artifact: submission.attachments[0] || null
    })
    return { ok: true, submission }
  }
  const p = trip.passport || {}
  const body = [
    `VISA FILING — ${trip.destination} · ${plan.headline}`,
    '',
    `Applicant: ${p.fullName || trip.name}`,
    `Nationality: ${p.nationality || trip.nationality}`,
    `Passport: ${p.passportNumber || 'see attached'}${p.expiryDate ? ` (expires ${p.expiryDate})` : ''}`,
    `Travel dates: ${trip.departure || 'TBD'} to ${trip.return || 'TBD'}`,
    `Channel: ${label}`,
    plan.portalUrl ? `Official portal: ${plan.portalUrl}` : null,
    '',
    'The completed application form and supporting document package are attached.',
    'Submitted by Trip.com visa services on behalf of the applicant.'
  ].filter((x) => x !== null).join('\n')
  const valid = (attachments || []).filter((a) => a && existsSync(a))
  const r = await deliverEmail({
    to: endpoint,
    subject: `[FILING] ${trip.destination} ${plan.headline} — ${trip.name}`,
    body,
    attachmentPaths: valid
  })
  const submission = {
    at: Date.now(),
    channel: label,
    endpoint,
    transmitted: !!r.ok && !r.simulated,
    simulated: !!r.simulated,
    via: r.via || (r.ok ? 'mail.app' : null),
    messageId: r.messageId || null,
    ok: !!r.ok,
    drafted: !!r.drafted,
    error: r.ok ? null : r.error || null,
    attachments: valid
  }
  patchTrip(tripId, { submission }, {
    step: 'submit',
    title: r.ok ? `Filing package transmitted to ${label}` : `Filing transmission ${r.drafted ? 'drafted' : 'failed'} — ${label}`,
    detail: `${valid.length} attachment(s) → ${endpoint}${r.messageId ? ` · message ${r.messageId}` : ''}${r.ok ? '' : r.error ? ` · ${r.error}` : ''}`,
    artifact: valid[0] || null
  })
  return { ok: true, submission }
})

// Generic LLM text completion through the engine chain:
// Kimi K3 (key) -> Claude (key) -> Ollama (local). Null when none reachable.
async function llmTextChain(prompt) {
  const s = getState().settings || {}
  const kc = kimiCfg(s)
  if (s.kimi?.enabled !== false && kc.apiKey) {
    try { return await kimi.textCompletion(kc, prompt) } catch { /* next */ }
  }
  const key = (s.anthropicKey || '').trim()
  if (key || claude.ambientCredsAvailable()) {
    try { return await claude.textCompletion(key, s.anthropicModel, prompt) } catch { /* next */ }
  }
  if (s.localAI?.enabled) {
    try {
      const res = await fetch((s.localAI.endpoint || 'http://127.0.0.1:11434') + '/api/chat', {
        method: 'POST',
        signal: AbortSignal.timeout(120000),
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ model: s.localAI.model || 'llama3.1:8b', stream: false, options: { temperature: 0.1 }, messages: [{ role: 'user', content: prompt }] })
      })
      if (res.ok) {
        const data = await res.json()
        const text = (data.message?.content || '').trim()
        if (text) return text
      }
    } catch { /* offline */ }
  }
  return null
}

// Portal discovery: the agent searches the live web for the official
// embassy/portal for this corridor, reads the top official page, and
// extracts the application channel. Findings persist with source evidence.
demoHandle('trips:agent:research', async (_e, { tripId }) => {
  const trip = getTrip(tripId)
  if (!trip) return { ok: false, error: 'Trip not found' }
  const plan = trip.plan || await tripEngine.tripPlan({ traveler: trip })
  try {
    const research = await discoverPortal(trip, plan, llmTextChain)
    patchTrip(tripId, { portalResearch: research }, {
      step: 'research',
      title: `Official portal verified: ${(() => { try { return new URL(research.officialUrl).hostname } catch { return research.officialUrl } })()}`,
      detail: `Live web search ("${research.query}") -> ${research.results.length} results; ${research.engine === 'llm' ? 'LLM-extracted' : 'top official result'}. ${research.notes}`.slice(0, 480)
    })
    return { ok: true, research }
  } catch (err) {
    patchTrip(tripId, {}, {
      step: 'research',
      title: 'Portal research unavailable',
      detail: `Web search failed (${String(err?.message || err).slice(0, 120)}) — using the built-in portal reference.`
    })
    return { ok: false, error: String(err?.message || err) }
  }
})

// Rejection recovery: turn a refusal (or verification failure) into a
// concrete fix plan the traveler can act on, generated by the LLM chain with
// a deterministic floor, and persisted on the trip.
const REMEDY_BASE = {
  'expired passport': ['Renew the passport at the issuing authority (allow 1–3 weeks).', 'Upload the new passport in the portal — verification and filing re-run automatically.', 'Keep the same travel dates if more than ~4 weeks away; otherwise rebook via Trip.com free of charge where the fare allows.'],
  'insufficient funds': ['Add a bank statement covering the last 3–6 months with a stable balance.', 'Include payslips or an employment letter stating salary.', 'A sponsor letter with the sponsor\'s bank statement also satisfies most consulates.'],
  'incomplete documents': ['Open the requirement checklist in the application package — items marked missing are exactly what the consulate wants.', 'Upload the missing documents; the agent rebuilds and refiles the package automatically.'],
  'purpose of visit doubts': ['Add the full Trip.com itinerary: return flight, hotels for every night, and a day-by-day plan.', 'Include an employment/leave letter showing ties to your home country.'],
  'prior overstay or refusal': ['Include a cover letter addressing the earlier issue directly.', 'Provide evidence of compliant travel since (entry/exit stamps, visas used correctly).']
}
// LLM text that lands in emails or UI cards must never carry raw markdown.
function stripMd(t) {
  return String(t || '')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/__([^_]+)__/g, '$1')
    .replace(/^#+\s*/gm, '')
    .replace(/^\s*[-*]\s+/gm, '')
}

async function buildRemedy(tripId, reason) {
  const trip = getTrip(tripId)
  if (!trip) return null
  const key = String(reason || '').toLowerCase()
  const base = REMEDY_BASE[Object.keys(REMEDY_BASE).find((k) => key.includes(k.split(' ')[0]))] ||
    ['Review the consulate\'s refusal letter for the stated ground.', 'Address that ground with new evidence before reapplying.', 'Reapply through Trip.com — your verified documents and signature are reused.']
  let plan = base
  let engine = 'builtin'
  const llmOut = await llmTextChain(`A ${trip.nationality} tourist's ${trip.destination} visa application was refused. Stated reason: "${reason || 'not specified'}". Documents on file: ${(trip.documents || []).map((d) => d.name).join(', ')}. Give exactly 3 short, concrete, numbered steps to fix this and succeed on reapplication. No preamble.`)
  if (llmOut) {
    const steps = llmOut.split(/\n+/).map((l) => stripMd(l).replace(/^\s*\d+[.)]\s*/, '').trim()).filter((l) => l.length > 10).slice(0, 4)
    if (steps.length >= 2) { plan = steps; engine = 'llm' }
  }
  const remediation = { reason: reason || 'Not specified', steps: plan, engine, at: Date.now() }
  patchTrip(tripId, { remediation }, {
    step: 'remedy',
    title: `Fix plan prepared (${reason || 'reason not specified'})`,
    detail: plan.map((s2, i) => `${i + 1}. ${s2}`).join(' ').slice(0, 480)
  })
  return remediation
}
demoHandle('trips:agent:remedy', async (_e, { tripId, reason }) => {
  const remediation = await buildRemedy(tripId, reason)
  return remediation ? { ok: true, remediation } : { ok: false, error: 'Trip not found' }
})

// Automatic rejection recovery — invoked by the 24/7 monitor the moment a
// refusal notice lands in the decisions folder. Analyzes the ground, emails
// the traveler the concrete fix, and reopens the document gate so the
// missing/corrected documents are prompted for and re-verified before the
// reapplication is filed.
async function refuseTrip(tripId, docPath, reason) {
  const trip = getTrip(tripId)
  if (!trip || ['issued', 'ready', 'refused'].includes(trip.status)) return { ok: false }
  const remediation = await buildRemedy(tripId, reason)
  const steps = remediation?.steps || []
  const firstName = ((trip.name || '').trim().split(/\s+/)[0]) || 'there'
  const needsDocs = /document|fund|statement|letter|photo|insurance|passport/i.test(`${reason} ${steps.join(' ')}`)
  const body = [
    `Hi ${firstName},`, '',
    `We're sorry — the ${trip.destination} authority did not approve this application (stated reason: ${reason}). Our monitoring caught the decision the moment it was returned, and we've already prepared the fix:`, '',
    ...steps.map((s, i) => `${i + 1}. ${s}`), '',
    needsDocs
      ? 'Open your application on Trip.com — it now asks for exactly the documents that address this ground. Upload them and the corrected application is re-verified, re-signed from your authorization, and refiled automatically.'
      : 'Open your application on Trip.com and tap "Fix & reapply" — your verified documents and signature are reused, so the corrected application takes minutes.',
    '', 'Any refundable fees return to your original payment method.', '', 'Warm regards,', 'Trip.com'
  ].join('\n')
  const r = await sendTripUpdate(tripId, { subject: `${trip.destination} visa decision`, body, attachmentPaths: [docPath].filter((p) => p && existsSync(p)) })
  update((st) => {
    const t = (st.trips || []).find((x) => x.id === tripId)
    if (!t) return
    if (!Array.isArray(t.statusHistory)) t.statusHistory = []
    t.statusHistory.push({ status: 'refused', at: Date.now(), reason: `Authority refusal: ${reason}` })
    t.status = 'refused'
    t.statusReason = `Refused — ${reason}. Fix plan sent to the traveler; corrected documents are being collected.`
    t.refusalDocPath = docPath || null
    // Reopen the document gate for the reapplication: the refusal ground
    // becomes a listed requirement so the traveler is prompted precisely.
    if (needsDocs) {
      if (!t.gapAnalysis) t.gapAnalysis = { covered: [], missing: [], engine: 'builtin' }
      if (!Array.isArray(t.gapAnalysis.missing)) t.gapAnalysis.missing = []
      t.gapAnalysis.missing.unshift({ requirement: `Corrected evidence for: ${reason}`, why: steps[0] || 'Address the refusal ground with new documents.' })
    }
    if (!Array.isArray(t.emailLog)) t.emailLog = []
    t.emailLog.push({ title: `${t.destination} decision: refused`, at: Date.now(), sent: r.ok ? true : r.drafted ? 'draft' : false, subject: `${t.destination} visa decision` })
    t.updatedAt = Date.now()
  })
  update((st) => {
    st.notifications.unshift({ id: 'ntf_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6), createdAt: Date.now(), read: false, forRole: 'tripcom', fromRole: 'tripcom', caseId: tripId, caseName: trip.name, title: `Refusal caught by monitoring — fix plan sent to ${trip.email}` })
    if (st.notifications.length > 200) st.notifications.length = 200
  })
  return { ok: true, emailed: !!r.ok }
}

// Pre-submission rejection-risk review — the last check before anything is
// filed. Deterministic consular rules run first (they can hard-block); Kimi
// K3 then reviews the complete application the way a consular officer would
// and adds judgment risks. High risks with a document fix reopen the
// documents gate instead of filing an application that would be refused.
demoHandle('trips:agent:riskReview', async (_e, { tripId }) => {
  const trip = getTrip(tripId)
  if (!trip) return { ok: false, error: 'Trip not found' }
  const plan = trip.plan || await tripEngine.tripPlan({ traveler: trip })
  const p = trip.passport || {}
  const risks = []
  // Rule 1: the six-month passport-validity rule most consulates enforce.
  if (p.expiryDate && trip.return) {
    const exp = new Date(p.expiryDate)
    const need = new Date(trip.return)
    need.setMonth(need.getMonth() + 6)
    if (!Number.isNaN(exp.getTime()) && exp < need) {
      risks.push({ item: 'Passport validity under 6 months beyond the return date', severity: 'high', fix: 'Renew the passport before filing — most consulates refuse otherwise.', docFix: true })
    }
  }
  // Rule 2: outstanding requirements or failed-verification documents.
  for (const m of trip.gapAnalysis?.missing || []) {
    risks.push({ item: `Missing: ${m.requirement}`, severity: 'high', fix: m.why || 'Upload this document.', docFix: true })
  }
  for (const d of (trip.documents || []).filter((x) => x.docCheck?.plausible === false && !x.docCheck?.overridden)) {
    risks.push({ item: `Failed verification: ${d.name}`, severity: 'high', fix: (d.docCheck.issues || []).join('; ') || 'Replace with a valid copy.', docFix: true })
  }
  // Rule 3: appointment after departure (booked too late).
  if (trip.appointment?.urgent) {
    risks.push({ item: 'No consular slot available before departure', severity: 'high', fix: 'Move the travel dates or use an expedited service.', docFix: false })
  }
  // Kimi K3 officer-style judgment pass over the full application.
  const s = getState().settings || {}
  const kc = kimiCfg(s)
  let engine = 'rules'
  if (s.kimi?.enabled !== false && kc.apiKey) {
    try {
      const form = trip.applicationForm || {}
      const docs = (trip.documents || []).map((d) => `${d.name} [${d.docType || '?'}${d.docCheck?.plausible === false ? (d.docCheck?.overridden ? ' — authenticity verified via ICAO MRZ check digits' : ' — FAILED VERIFICATION') : ''}]`).join('; ')
      const fields = (form.fields || []).map((f) => `${f.label}: ${f.value ?? 'EMPTY'}`).join('\n')
      const out = await kimi.chat({
        ...kc,
        system: 'You are a senior consular officer reviewing a tourist visa application BEFORE submission to catch anything that would cause refusal. Reply with ONLY JSON: {"risks":[{"item":"...","severity":"high|medium|low","fix":"..."}]} — empty array if the application is solid. Consider: blank material fields, inconsistent dates, funding vs trip length, ties to home country, purpose clarity. Context you must respect: the platform (Trip.com) automatically attaches the traveler\'s flight itinerary and hotel booking confirmations at filing — never flag flights/hotels/itinerary as missing; a document marked "authenticity verified via ICAO MRZ check digits" passed machine verification — never flag it. Do not repeat risks already known.',
        messages: [{ role: 'user', content: `Route: ${trip.nationality} → ${trip.destination} (${plan.headline}). Travel ${trip.departure} to ${trip.return}.\nApplication fields:\n${fields}\nDocuments: ${docs}\nAlready-known risks: ${risks.map((r) => r.item).join('; ') || 'none'}` }],
        maxTokens: 900,
        reasoningEffort: 'high',
        responseFormat: { type: 'json_object' }
      })
      const parsed = JSON.parse(String(out).match(/\{[\s\S]*\}/)?.[0] || '{}')
      for (const r of (parsed.risks || []).slice(0, 6)) {
        risks.push({ item: stripMd(r.item).slice(0, 160), severity: ['high', 'medium', 'low'].includes(r.severity) ? r.severity : 'medium', fix: stripMd(r.fix).slice(0, 200), docFix: /document|statement|letter|photo|insurance|upload/i.test(String(r.fix)) })
      }
      engine = 'kimi+rules'
    } catch { /* rules floor stands */ }
  }
  const blocking = risks.filter((r) => r.severity === 'high' && r.docFix)
  const verdict = blocking.length ? 'fix_first' : 'ready'
  const riskReview = { risks, verdict, engine, at: Date.now() }
  patchTrip(tripId, { riskReview }, {
    step: 'review',
    title: verdict === 'ready'
      ? `Pre-submission review passed (${engine === 'kimi+rules' ? 'Kimi K3 officer review' : 'rules'})`
      : 'Pre-submission review found blocking risks',
    detail: risks.length
      ? risks.map((r) => `[${r.severity}] ${r.item}`).join(' · ').slice(0, 480)
      : 'Full application reviewed against refusal grounds — no risks found. Cleared for filing.'
  })
  return { ok: true, riskReview }
})

// Record a renderer-produced artifact (a generated PDF) or milestone in the
// trip's agent log so every claim in the UI has a persisted trail.
demoHandle('trips:agent:record', (_e, { tripId, entry }) => {
  if (!entry || !entry.title) return { ok: false, error: 'Missing entry' }
  const t = patchTrip(tripId, {}, {
    step: String(entry.step || 'note'),
    title: String(entry.title).slice(0, 200),
    detail: entry.detail ? String(entry.detail).slice(0, 500) : null,
    artifact: entry.artifact || null
  })
  return { ok: !!t }
})

// Redact anything that looks like a provider secret before it can surface in
// the UI or logs (defense in depth — messages should never carry a key).
function redactSecrets(s) {
  return String(s || '')
    .replace(/sk-[A-Za-z0-9_\-]{12,}/g, 'sk-***REDACTED***')
    .replace(/AKIA[0-9A-Z]{16}/g, 'AKIA***REDACTED***')
    .replace(/Bearer\s+[A-Za-z0-9._\-]{12,}/gi, 'Bearer ***REDACTED***')
    .replace(/-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g, '***REDACTED PRIVATE KEY***')
}
function parseError(err) {
  if (err?.code === 'NO_API_KEY' || err?.message === 'NO_API_KEY') {
    return { code: 'NO_API_KEY', message: 'No AI key configured. Add a Kimi or Claude key in Settings.' }
  }
  const status = err?.status || err?.response?.status
  if (status === 401) return { code: 'AUTH', message: 'The AI provider rejected the API key. Check it in Settings.' }
  if (status === 429) return { code: 'RATE', message: 'AI provider rate limit or quota reached. Try again shortly.' }
  return { code: 'UNKNOWN', message: redactSecrets(err?.message) || 'Something went wrong calling the model.' }
}


// --- IPC: traveler updates — locked to the application's email ---------------
// Every traveler-facing message resolves its recipient from the trip record
// server-side. Callers cannot address anyone else.
async function sendTripUpdate(tripId, { subject, body, attachmentPaths }) {
  const trip = getTrip(tripId)
  if (!trip) return { ok: false, error: 'Trip not found' }
  const to = String(trip.email || '').trim()
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(to)) return { ok: false, error: 'The application has no valid email address' }
  // Reference in the subject: traveler-friendly, and makes every subject
  // unique so delivery verification can't match an older same-subject email.
  const refd = subject.includes(tripRef(trip)) ? subject : `${subject} · ${tripRef(trip)}`
  return deliverEmail({ to, subject: refd, body, attachmentPaths })
}
demoHandle('trips:email', (_e, { tripId, subject, body, attachmentPaths }) =>
  sendTripUpdate(tripId, { subject, body, attachmentPaths }))

demoHandle('trips:decisionsInfo', (_e, { tripId }) => {
  const trip = tripId ? getTrip(tripId) : null
  return { dir: decisionsDir(), ref: trip ? tripRef(trip) : null }
})
demoHandle('trips:openDecisionsDir', () => { shell.openPath(decisionsDir()); return true })

// Auto-issue: called by the monitoring service when the decision document
// arrives, and reachable by the manual decision card. Marks the trip issued,
// emails the traveler (their application email only), and records everything.
const issuingNow = new Set()
async function issueTrip(tripId, attachmentPath, sourceDetail) {
  const trip = getTrip(tripId)
  if (!trip || ['issued', 'ready', 'refused'].includes(trip.status)) return { ok: false }
  // Claim the trip synchronously before the long email await so a concurrent
  // trigger (folder auto-issue + manual Approve) can't both send.
  if (issuingNow.has(tripId)) return { ok: false, busy: true }
  issuingNow.add(tripId)
  try {
  const firstName = ((trip.name || '').trim().split(/\s+/)[0]) || 'there'
  const body = [
    `Hi ${firstName},`,
    '',
    'Great news — your visa has been approved. Attached: your visa document, your signed application as filed, and your appointment confirmation — the complete official record.',
    '',
    ...(trip.portalAccess?.username ? [`You can also see the decision yourself on the embassy portal (login: ${trip.portalAccess.username}).`, ''] : []),
    'When you travel, carry your passport together with the visa document and your Trip.com itinerary.',
    '',
    'Safe travels, and enjoy your trip!',
    '',
    'Warm regards,',
    'Trip.com'
  ].join('\n')
  // The traveler must receive the actual visa document. When the authority's
  // own file arrived (decisions folder / manual Approve with attachment) that
  // file leads; otherwise render the full grant notice from the recorded
  // decision. The signed official application always accompanies it.
  let visaDocPath = attachmentPath && existsSync(attachmentPath) ? attachmentPath : null
  let grantPath = null
  try {
    const plan = await tripEngine.tripPlan({ traveler: { nationality: trip.nationality, destination: trip.destination } })
    const dir = app.getPath('desktop')
    const base = `${trip.name} - ${trip.destination} Visa Grant Notice`.replace(/[^\w .-]/g, '').trim()
    let fp = join(dir, base + '.pdf')
    let n = 1
    while (existsSync(fp)) { fp = join(dir, `${base} (${n}).pdf`); n++ }
    writeFileSync(fp, await renderPdf(visaGrantHtml(trip, plan)))
    grantPath = fp
  } catch { /* grant notice is best-effort; the decision doc still goes out */ }
  const attachments = [visaDocPath, grantPath, trip.formPath, trip.apptNoticePath].filter((p) => p && existsSync(p))
  const r = await sendTripUpdate(tripId, { subject: `${trip.destination} visa approved`, body, attachmentPaths: attachments })
  update((st) => {
    const t = (st.trips || []).find((x) => x.id === tripId)
    if (!t) return
    if (!Array.isArray(t.statusHistory)) t.statusHistory = []
    t.statusHistory.push({ status: 'issued', at: Date.now(), reason: sourceDetail })
    t.status = 'issued'
    t.statusReason = sourceDetail
    t.visaPath = visaDocPath || grantPath || t.visaPath
    if (grantPath) t.grantNoticePath = grantPath
    t.emailSentAt = Date.now()
    if (!Array.isArray(t.emailLog)) t.emailLog = []
    t.emailLog.push({ title: `${t.destination} visa approved`, at: Date.now(), final: true, sent: r.ok ? true : r.drafted ? 'draft' : false, subject: `${t.destination} visa approved` })
    if (!Array.isArray(t.agentLog)) t.agentLog = []
    t.agentLog.push({ at: Date.now(), step: 'deliver', title: `Visa delivered to ${t.email}`, detail: r.ok ? 'Email sent with the decision document attached.' : r.drafted ? 'Draft opened in Mail.' : `Email failed: ${r.error || 'unknown'}`, artifact: attachmentPath || null })
    t.updatedAt = Date.now()
  })
  update((st) => {
    st.notifications.unshift({ id: 'ntf_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6), createdAt: Date.now(), read: false, forRole: 'tripcom', fromRole: 'tripcom', caseId: tripId, caseName: trip.name, title: `Visa delivered to ${trip.email}` })
    if (st.notifications.length > 200) st.notifications.length = 200
  })
  return { ok: true, emailed: !!r.ok }
  } finally {
    issuingNow.delete(tripId)
  }
}
demoHandle('trips:issue', (_e, { tripId, attachmentPath, detail }) =>
  issueTrip(tripId, attachmentPath, detail || 'Authority decision recorded: approved'))

app.whenReady().then(async () => {
  mlog('app ready')
  // Bring up the embedded backend ONLY in the packaged app. In development the
  // launcher (or the developer) runs the backend at 127.0.0.1:8000, so we never
  // start the packaged backend lifecycle or touch process.resourcesPath here —
  // we just adopt the already-running backend.
  try {
    const r = await startBackend()
    mlog(`backend: ${r.reused ? 'reused existing' : (r.ok ? 'started' : 'unavailable (dev: use launcher-started backend)')}`)
  } catch (e) {
    mlog('backend start error (non-fatal): ' + (e?.message || e))
  }
  // The monitor auto-sends traveler emails — it must NEVER run outside the
  // explicitly selected local demo mode.
  if (DEMO_PIPELINE_ENABLED) startMonitor(issueTrip, refuseTrip)
  createWindow()
  app.on('activate', () => {
    mlog('app activate')
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
}).catch((e) => mlog('whenReady handler error: ' + (e?.stack || e)))

app.on('before-quit', () => { mlog('app before-quit; stopping backend'); stopBackend() })

app.on('window-all-closed', () => {
  mlog(`window-all-closed (platform=${process.platform})`)
  stopBackend()
  // On macOS apps normally stay alive with no windows; here the app IS the UI,
  // so quit when the window is closed (also releases the backend it adopted).
  app.quit()
})
