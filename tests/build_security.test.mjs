// Build-security guardrails for the Ellis Electron distributable.
//
// The Electron client must NEVER contain, receive, or ship provider credentials
// and must NEVER bundle private/personal data. These tests fail the build if a
// provider key, private passport fixture, .env file, Google ADC file, or any
// credential-like resource is present where it could be packaged, or if the
// electron-builder packaging config would allow such a file through.
//
// This test prints only file NAMES and match COUNTS — never secret contents.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync, readdirSync, statSync } from 'node:fs'
import { join, dirname, basename, extname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { execFileSync } from 'node:child_process'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')

// Directories that must be scanned for a shippable build (never node_modules of
// third-party SDKs, whose source files legitimately contain the word
// "credentials"). We scan the app's own source, resources, and build output.
const SCAN_DIRS = ['resources', 'out', 'build', 'src']

// Credential / private-data FILE patterns that must never sit in a scan dir.
const FORBIDDEN_FILE = [
  /\.key$/i,
  /\.pem$/i,
  /\.p12$/i,
  /\.pfx$/i,
  /^id_rsa/i,
  /^\.env$/i,
  /^\.env\./i,
  /\.env$/i,
  /application_default_credentials\.json$/i,
  /^service-account.*\.json$/i,
  /\.passport$/i
]

// Private passport fixture basenames (consented, live ONLY outside the repo).
// Their names must never appear as files inside the repo working tree.
const PRIVATE_FIXTURE_NAMES = [
  'fixture_us.jpg', 'fixture_sg.jpg', 'fixture_sg.pdf',
  'fixture_cn.jpg', 'fixture_cn_2.jpg', 'fixture_pdf_1.pdf'
]

// Secret VALUE patterns (high signal, low false-positive) for scanning bundled
// text output. We deliberately do NOT match the word "credentials" or file
// paths — only things that look like an actual live secret.
const SECRET_VALUE = [
  { name: 'Moonshot/OpenAI-style key', re: /\bsk-[A-Za-z0-9_\-]{20,}\b/ },
  { name: 'AWS access key id', re: /\bAKIA[0-9A-Z]{16}\b/ },
  { name: 'Google API key', re: /\bAIza[0-9A-Za-z_\-]{30,}\b/ },
  { name: 'PEM private key block', re: /-----BEGIN [A-Z ]*PRIVATE KEY-----/ },
  { name: 'Service-account private_key field', re: /"private_key"\s*:\s*"-----BEGIN/ },
  { name: 'Moonshot key assignment', re: /MOONSHOT_API_KEY\s*=\s*sk-/ },
  { name: 'Browserbase key assignment', re: /BROWSERBASE_API_KEY\s*=\s*[A-Za-z0-9]/ },
  { name: 'Browserbase Live View URL', re: /browserbase\.com\/v1\/sessions\/[A-Za-z0-9-]+\/(live|debug)/ }
]

function walk(dir, out = []) {
  let entries
  try { entries = readdirSync(dir, { withFileTypes: true }) } catch { return out }
  for (const e of entries) {
    const p = join(dir, e.name)
    if (e.isDirectory()) {
      if (e.name === 'node_modules' || e.name === '.git') continue
      walk(p, out)
    } else if (e.isFile()) {
      out.push(p)
    }
  }
  return out
}

function scannableFiles() {
  const files = []
  for (const d of SCAN_DIRS) {
    const abs = join(ROOT, d)
    if (existsSync(abs)) walk(abs, files)
  }
  return files
}

test('no credential-like files in shippable directories', () => {
  const offenders = []
  for (const f of scannableFiles()) {
    const name = basename(f)
    if (FORBIDDEN_FILE.some((re) => re.test(name))) {
      offenders.push(f.replace(ROOT + '/', ''))
    }
  }
  assert.deepEqual(
    offenders, [],
    `Credential/secret files must not sit in a shippable dir:\n  ${offenders.join('\n  ')}`
  )
})

test('no private passport fixtures anywhere in the repo tree', () => {
  const offenders = []
  const all = walk(ROOT, [])
  for (const f of all) {
    if (f.includes('/.venv/') || f.includes('/backend/.venv/')) continue
    if (PRIVATE_FIXTURE_NAMES.includes(basename(f))) offenders.push(f.replace(ROOT + '/', ''))
    if (f.includes('/private_fixtures/')) offenders.push(f.replace(ROOT + '/', ''))
  }
  assert.deepEqual(offenders, [], `Private fixtures must live OUTSIDE the repo:\n  ${offenders.join('\n  ')}`)
})

test('no live secret VALUES in bundled build output (out/)', () => {
  const outDir = join(ROOT, 'out')
  if (!existsSync(outDir)) return // nothing built yet; nothing to leak
  const hits = []
  for (const f of walk(outDir, [])) {
    // Only scan text-ish bundles; skip images/binaries by extension.
    if (/\.(png|jpg|jpeg|gif|icns|ico|woff2?|ttf|otf|asar|node|map)$/i.test(f)) continue
    let text
    try { text = readFileSync(f, 'utf-8') } catch { continue }
    for (const { name, re } of SECRET_VALUE) {
      if (re.test(text)) hits.push(`${name} in ${f.replace(ROOT + '/', '')}`)
    }
  }
  assert.deepEqual(hits, [], `Live secret values must never be bundled:\n  ${hits.join('\n  ')}`)
})

test('electron-builder packaging config excludes credential/private patterns', () => {
  const pkg = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf-8'))
  const files = (pkg.build && pkg.build.files) || []
  const required = [
    '!**/*.key', '!**/*.pem', '!**/.env', '!**/.env.*',
    '!**/application_default_credentials.json', '!**/private_fixtures/**'
  ]
  const missing = required.filter((r) => !files.includes(r))
  assert.deepEqual(missing, [], `package.json build.files must exclude:\n  ${missing.join('\n  ')}`)
})

test('the Electron main process never reads a bundled/drop-in credential file', () => {
  const main = readFileSync(join(ROOT, 'src/main/index.js'), 'utf-8')
  // These are the exact bundled/drop-in credential-file reads that were removed.
  assert.ok(!/resourcesPath[^\n]*kimi\.key/.test(main), 'must not read resourcesPath/kimi.key')
  assert.ok(!/getPath\(['"]userData['"]\)[^\n]*kimi\.key/.test(main), 'must not read userData/kimi.key')
  assert.ok(!/join\([^)]*['"]resources['"][^)]*kimi\.key/.test(main), 'must not read resources/kimi.key')
  // And the packaged-build guard must exist.
  assert.ok(/clientProviderCallsAllowed/.test(main), 'must gate provider calls behind clientProviderCallsAllowed()')
  assert.ok(/app\.isPackaged/.test(main), 'gate must consult app.isPackaged')
})

test('any packaged app under release/ must not bundle credential files', () => {
  const releaseDir = join(ROOT, 'release')
  if (!existsSync(releaseDir)) return
  // Find app.asar archives and list their entries (names only).
  const asars = walk(releaseDir, []).filter((f) => basename(f) === 'app.asar')
  for (const asar of asars) {
    let listing = ''
    try {
      listing = execFileSync('npx', ['--no-install', 'asar', 'list', asar], { encoding: 'utf-8' })
    } catch {
      // asar tool unavailable — fall back to a raw byte scan for the filename.
      const buf = readFileSync(asar)
      assert.ok(!buf.includes('kimi.key'), `release asar appears to bundle kimi.key: ${asar}`)
      continue
    }
    const bad = listing.split('\n').filter((line) => {
      const n = basename(line.trim())
      if (!n) return false
      // Ignore third-party SDK source files literally named *credentials*.
      if (line.includes('/node_modules/')) return false
      return FORBIDDEN_FILE.some((re) => re.test(n))
    })
    assert.deepEqual(bad, [], `packaged app ${basename(dirname(dirname(asar)))} bundles credentials:\n  ${bad.join('\n  ')}`)
  }
})

test('packaged app.asar contents carry no secret VALUES (deep extract scan)', () => {
  const releaseDir = join(ROOT, 'release')
  if (!existsSync(releaseDir)) return
  const asars = walk(releaseDir, []).filter((f) => basename(f) === 'app.asar')
  const tmpBase = join(ROOT, 'release', '.asar_scan_tmp')
  const hits = []
  for (const asar of asars) {
    const dest = join(tmpBase, basename(dirname(dirname(asar))))
    try {
      execFileSync('npx', ['--no-install', 'asar', 'extract', asar, dest], { stdio: 'ignore' })
    } catch { continue } // asar tool unavailable — the filename test above still applies
    for (const f of walk(dest, [])) {
      if (f.includes('/node_modules/')) continue
      if (!/\.(js|mjs|json|map|html|txt|env)$/i.test(f) && basename(f) !== '.env') continue
      let text
      try { text = readFileSync(f, 'utf-8') } catch { continue }
      for (const { name, re } of SECRET_VALUE) {
        if (re.test(text)) hits.push(`${name} in ${f.replace(dest, basename(dirname(dirname(asar))) + ':')}`)
      }
    }
  }
  try { execFileSync('rm', ['-rf', tmpBase]) } catch { /* best effort */ }
  assert.deepEqual(hits, [], `packaged app bundles live secret values:\n  ${hits.join('\n  ')}`)
})

test('packaged main process enforces backend-only provider calls', () => {
  const built = join(ROOT, 'out/main/index.js')
  if (!existsSync(built)) return // nothing built yet
  const src = readFileSync(built, 'utf-8')
  assert.ok(/clientProviderCallsAllowed/.test(src), 'built main must gate provider calls')
  assert.ok(/isPackaged/.test(src), 'built main must consult app.isPackaged')
  assert.ok(!/resourcesPath[^\n]{0,40}kimi\.key/.test(src), 'built main must not read a bundled kimi.key')
})
