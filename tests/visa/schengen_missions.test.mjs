// RK-Termin lists its missions in GERMAN — the Beijing post is "Peking", the
// Guangzhou one is "Kanton", Hong Kong is "Hongkong". An applicant types the
// English or local name, so the picker must match on aliases as well as the
// official name, while always DISPLAYING the portal's own wording.
//
// Names below are real, read live from choose_locationList.do (196 missions,
// 2026-08-19).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const SRC = readFileSync(
  new URL('../../src/renderer/src/screens/SchengenVisa.jsx', import.meta.url), 'utf8')

// Lift the alias map and matcher out of the screen and exercise them directly.
function load() {
  const map = SRC.match(/const MISSION_ALIASES = \{[\s\S]*?\n\}/)[0]
  const fold = SRC.match(/function fold\(s\) \{[\s\S]*?\n\}/)[0]
  const match = SRC.match(/function missionMatches\(m, query\) \{[\s\S]*?\n\}/)[0]
  const mod = {}
  new Function('exports', `${map};${fold};${match};
    exports.MISSION_ALIASES = MISSION_ALIASES;
    exports.missionMatches = missionMatches;`)(mod)
  return mod
}

const REAL = [
  { code: 'peki', name: 'Peking' }, { code: 'shan', name: 'Shanghai' },
  { code: 'kant', name: 'Kanton' }, { code: 'cheng', name: 'Chengdu' },
  { code: 'shen', name: 'Shenyang' }, { code: 'hong', name: 'Hongkong' },
  { code: 'mosk', name: 'Moskau' }, { code: 'wien', name: 'Wien' },
  { code: 'kair', name: 'Kairo' }, { code: 'brue', name: 'Brüssel' },
  { code: 'kiew', name: 'Kyjiw' }, { code: 'newy', name: 'New York' }
]

test('English and local names find the German mission', () => {
  const { missionMatches } = load()
  const cases = [
    ['beijing', 'Peking'], ['Beijing', 'Peking'], ['北京', 'Peking'],
    ['guangzhou', 'Kanton'], ['canton', 'Kanton'],
    ['hong kong', 'Hongkong'], ['hongkong', 'Hongkong'],
    ['shanghai', 'Shanghai'], ['moscow', 'Moskau'], ['vienna', 'Wien'],
    ['cairo', 'Kairo'], ['kyiv', 'Kyjiw'], ['kiev', 'Kyjiw'],
    // Accent-folded: the applicant will not type the umlaut.
    ['brussel', 'Brüssel'], ['brussels', 'Brüssel'],
    // The official name itself must always still work.
    ['Peking', 'Peking'], ['new york', 'New York']
  ]
  for (const [typed, expect] of cases) {
    const hits = REAL.filter((m) => missionMatches(m, typed)).map((m) => m.name)
    assert.ok(hits.includes(expect),
      `"${typed}" should find ${expect}; got ${hits.join(', ') || 'nothing'}`)
  }
})

test('an empty query shows every mission (a browsable dropdown)', () => {
  const { missionMatches } = load()
  for (const q of ['', '   ']) {
    assert.equal(REAL.filter((m) => missionMatches(m, q)).length, REAL.length)
  }
})

test('the China posts are keyed by CODE, since their names are German', () => {
  // Matching those chips on /beijing|guangzhou/ against German names would
  // find nothing — the screen must select them by locationCode.
  assert.match(SRC, /const CHINA_CODES = \[[^\]]*'peki'[^\]]*'kant'[^\]]*\]/)
  const { MISSION_ALIASES } = load()
  for (const code of ['peki', 'shan', 'kant', 'cheng', 'shen', 'hong']) {
    assert.ok(MISSION_ALIASES[code]?.length, `${code} needs aliases`)
  }
})

test('the mission list is read AFTER the window exists, never raced', () => {
  // calendarMissions is read THROUGH the applicant's secure window; racing the
  // two with Promise.all 409s (no_secure_window) and the list silently arrives
  // empty — which is exactly what happened.
  assert.ok(!/Promise\.all\(\[\s*client\.createBrowserSession/.test(SRC),
    'the window must be created before the missions are read')
  const i = SRC.indexOf('createBrowserSession(made.id)')
  const j = SRC.indexOf('calendarMissions(made.id)')
  assert.ok(i > 0 && j > i, 'createBrowserSession must come before calendarMissions')
})
