// Trip.com-only conversion guard.
//
// Proves the legacy three-role product (Immigrant / Employer / Counsel) is
// gone from the renderer: the deleted files stay deleted, no remaining source
// (or built renderer bundle, when out/renderer exists) carries the legacy
// product's markers, the repo-root stale bundle is gone, the new App shell has
// no role selection, and the simulated-portal banner string is pinned.
//
// This test prints only file paths and marker names — never file contents.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync, readdirSync } from 'node:fs'
import { join, dirname, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const RENDERER_SRC = join(ROOT, 'src/renderer/src')

// Every file the Trip.com-only conversion deleted. None may come back.
const DELETED = [
  'src/renderer/src/screens/RoleSelect.jsx',
  'src/renderer/src/screens/CountrySelect.jsx',
  'src/renderer/src/screens/Login.jsx',
  'src/renderer/src/screens/Dashboard.jsx',
  'src/renderer/src/screens/Cases.jsx',
  'src/renderer/src/screens/CaseWorkspace.jsx',
  'src/renderer/src/screens/Assistant.jsx',
  'src/renderer/src/screens/Notifications.jsx',
  'src/renderer/src/components/OnboardingDemo.jsx',
  'src/renderer/src/components/Tour.jsx',
  'src/renderer/src/components/TourPicker.jsx',
  'src/renderer/src/components/Integrations.jsx',
  'src/renderer/src/components/PortalModal.jsx',
  'src/renderer/src/components/charts.jsx',
  'src/renderer/src/lib/tour.js',
  'src/main/seed.js',
  'src/main/ai.js'
]

// Markers of the deleted product. Word-boundary, case-insensitive for the
// role names so prose like "immigration" or "employment" never false-hits.
const FORBIDDEN = [
  { name: 'RoleSelect welcome copy', re: /Who is using Ellis today/i },
  { name: 'RoleSelect', re: /RoleSelect/ },
  { name: 'CountrySelect', re: /CountrySelect/ },
  { name: 'CaseWorkspace', re: /CaseWorkspace/ },
  { name: 'OnboardingDemo', re: /OnboardingDemo/ },
  { name: 'role name: Immigrant', re: /\bimmigrant\b/i },
  // 'role name: Employer' and 'role name: Counsel' retired 2026-08-10: the
  // H1B edition (docs/H1B_ARCHITECTURE.md, Surfaces) makes the employer
  // PETITIONER a first-class product party — EmployerConsole.jsx, the
  // '#employer' persona, and pinned copy like "Waiting on the employer"
  // legitimately use the word — and the backend pins its RFE/narrative/
  // evidence endpoints under /h1b/cases/{id}/counsel/* (h1b/counsel_api.py),
  // so the typed client necessarily carries that word too. Both appear in
  // renderer source AND the built bundle, which a per-file whitelist cannot
  // cover (bundle names are hashed). The legacy Employer/Counsel ROLE product
  // stays guarded by the deleted-file list, the RoleSelect/CaseWorkspace/
  // OnboardingDemo markers, and the no-role-selection App.jsx test below.
]

// Explicit whitelist for legitimate remaining uses of a forbidden marker.
// Empty by design: the conversion left none. Add entries only with a comment
// explaining why the hit is genuinely required.
const WHITELIST = [
  // { file: 'src/renderer/src/example.jsx', marker: 'role name: Employer', reason: '...' }
]

function walk(dir, out = []) {
  let entries
  try { entries = readdirSync(dir, { withFileTypes: true }) } catch { return out }
  for (const e of entries) {
    const p = join(dir, e.name)
    if (e.isDirectory()) walk(p, out)
    else if (e.isFile()) out.push(p)
  }
  return out
}

function scanFiles(files, label) {
  const hits = []
  for (const f of files) {
    let text
    try { text = readFileSync(f, 'utf-8') } catch { continue }
    for (const { name, re } of FORBIDDEN) {
      if (!re.test(text)) continue
      const rel = relative(ROOT, f)
      if (WHITELIST.some((w) => w.file === rel && w.marker === name)) continue
      hits.push(`${name} in ${rel}`)
    }
  }
  assert.deepEqual(hits, [], `${label} must carry no legacy-product markers:\n  ${hits.join('\n  ')}`)
}

test('every legacy-product file stays deleted', () => {
  const present = DELETED.filter((p) => existsSync(join(ROOT, p)))
  assert.deepEqual(present, [], `Deleted files must not come back:\n  ${present.join('\n  ')}`)
})

test('renderer source carries no legacy-product markers', () => {
  const files = walk(RENDERER_SRC).filter((f) => !/\.(png|jpg|jpeg|svg|ico|woff2?)$/i.test(f))
  assert.ok(files.length > 10, 'renderer source tree should exist')
  scanFiles(files, 'src/renderer/src')
})

test('built renderer bundle carries no legacy-product markers (when built)', (t) => {
  const outRenderer = join(ROOT, 'out/renderer')
  if (!existsSync(outRenderer)) {
    t.diagnostic('out/renderer absent — run `npm run build` first for the packaged-renderer scan; skipping cleanly')
    return
  }
  const files = walk(outRenderer).filter((f) => /\.js$/i.test(f))
  assert.ok(files.length > 0, 'out/renderer exists but contains no JS bundles')
  scanFiles(files, 'out/renderer')
})

test('the stale repo-root bundle (index.js) is gone', () => {
  assert.ok(!existsSync(join(ROOT, 'index.js')), 'repo-root index.js (built artifact) must be deleted')
  const pkg = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf-8'))
  assert.equal(pkg.main, './out/main/index.js')
})

test('App.jsx imports no deleted screens and has no role selection', () => {
  const app = readFileSync(join(RENDERER_SRC, 'App.jsx'), 'utf-8')
  for (const name of ['RoleSelect', 'CountrySelect', 'Login', 'Dashboard', 'Cases.jsx', 'CaseWorkspace', 'Assistant', 'Notifications', 'OnboardingDemo', 'TourPicker', 'Tour.jsx', 'tour.js', 'charts']) {
    assert.ok(!app.includes(`screens/${name}`) && !app.includes(`components/${name}`) && !app.includes(`lib/${name}`), `App.jsx must not import ${name}`)
  }
  // No role state / role routing of any kind (onSwitchRole, TripPortal's
  // legacy prop name, is not a standalone word and is allowed).
  assert.ok(!/\brole\b/i.test(app), "App.jsx must not reference 'role' selection")
  assert.ok(!/\broles\b/i.test(app), "App.jsx must not reference 'roles'")
  // Boots INTO the Schengen lane (the Trip.com evaluation's front door —
  // Germany's account-free RK-Termin calendar, live). The deleted
  // appointments SIMULATION must stay gone.
  assert.match(app, /useState\('schengen'\)/)
  assert.ok(!app.includes('AppointmentsDemo'), 'the appointments demo screen must stay deleted')
})

test('the demo banner string exists in the i18n en locale exactly once', () => {
  const i18n = readFileSync(join(RENDERER_SRC, 'lib/i18n.js'), 'utf-8')
  const BANNER = 'SIMULATED PORTAL — NO REAL APPLICATION, PAYMENT, APPOINTMENT OR SUBMISSION'
  const count = i18n.split(BANNER).length - 1
  assert.equal(count, 1, `banner string must appear exactly once in i18n.js (en locale); found ${count}`)
})
