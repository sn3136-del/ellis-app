// Appointments-demo find-centre logic: ANY address must resolve to something
// actionable, the distance sort must be true geography, and the catalogue must
// stay internally valid. The demo's rule: the Find button never dead-ends.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  CENTRES, resolveAddress, distanceKm, nearestCentres, routeCatalogue,
  generateAvailability
} from '../../src/renderer/src/lib/apptDemoData.js'

// ---- resolveAddress: the many-address torture list -------------------------

const KNOWN = [
  // [typed address, expected label]
  ['88 Century Avenue, Pudong, Shanghai', 'Shanghai'],
  ['上海市浦东新区世纪大道88号', 'Shanghai'],
  ['北京市朝阳区建国路1号', 'Beijing'],
  ['No. 5 Tianhe Road, Guangzhou', 'Guangzhou'],
  ['深圳市福田区', 'Shenzhen'],
  ['71 rue Grande Fusterie, lyon', 'Lyon'],          // the reported bug
  ['71 Rue Grande Fusterie, LYON', 'Lyon'],          // case-insensitive
  ['10 Downing Street, London', 'London'],
  ['1600 Broadway, New York', 'New York'],
  ['Shinjuku, Tokyo', 'Tokyo'],
  ['東京都新宿区', 'Tokyo'],
  ['Gangnam-gu, Seoul', 'Seoul'],
  ['서울 강남구', 'Seoul'],
  ['Orchard Road, Singapore', 'Singapore'],
  ['Sukhumvit Road, Bangkok', 'Bangkok'],
  ['District 1, Ho Chi Minh City', 'Ho Chi Minh City'],
  ['Champs-Élysées, Paris', 'Paris'],
  ['Alexanderplatz, Berlin', 'Berlin'],
  ['Marienplatz, München', 'Munich'],
  ['Gran Vía, Madrid', 'Madrid'],
  ['Colosseo, Roma', 'Rome'],
  ['Bandra Kurla Complex, Mumbai', 'Mumbai'],
  ['Sheikh Zayed Road, Dubai', 'Dubai'],
  ['CBD, Sydney', 'Sydney'],
  ['Yonge St, Toronto', 'Toronto'],
  ['香港中环花园道', 'Hong Kong'],
  ['台北市內湖區', 'Taipei'],
]

test('every known-city address resolves with real coordinates', () => {
  for (const [typed, label] of KNOWN) {
    const o = resolveAddress(typed)
    assert.ok(o, `no resolution for: ${typed}`)
    assert.equal(o.label, label, `wrong city for: ${typed}`)
    assert.equal(typeof o.lat, 'number', `no lat for: ${typed}`)
    assert.equal(typeof o.lon, 'number', `no lon for: ${typed}`)
    assert.ok(!o.approx, `${typed} should be a confident match`)
  }
})

test('an unknown locality still resolves — approximate, never a dead end', () => {
  for (const typed of ['12 Rural Lane, Smalltownia', 'asdfgh qwerty',
                       '99 Nowhere Road']) {
    const o = resolveAddress(typed)
    assert.ok(o, `must not return null for: ${typed}`)
    assert.equal(o.approx, true)
    assert.ok(o.label.length > 0)
    assert.equal(o.lat, null)
  }
  // Only emptiness returns null (the button stays disabled until 3 chars).
  assert.equal(resolveAddress(''), null)
  assert.equal(resolveAddress('   '), null)
})

test('the approx label keeps the most city-like segment of the input', () => {
  assert.equal(resolveAddress('12 Foo Street, Bar District, Quuxville').label,
    'Quuxville')
})

// ---- geography: the nearest sort must be true --------------------------------

test('nearest centre from each world city is the geographically right one', () => {
  const cases = [
    // [address, route, expected first centre id]
    ['Pudong, Shanghai', 'us', 'us-sh'],
    ['Chaoyang, Beijing', 'us', 'us-bj'],
    ['Tianhe, Guangzhou', 'us', 'us-gz'],
    ['71 rue Grande Fusterie, Lyon', 'us', 'us-paris'],   // Lyon -> Paris embassy
    // Only REAL centres are listed; the nearest real Schengen VAC to Lyon
    // (a Schengen resident would not need one) is London's.
    ['71 rue Grande Fusterie, Lyon', 'schengen', 'sch-london'],
    ['Croydon, London', 'us', 'us-london'],
    ['Brooklyn, New York', 'schengen', 'sch-ny'],
    ['Roppongi, Tokyo', 'us', 'us-tokyo'],
    ['Gangnam, Seoul', 'us', 'us-seoul'],
    ['Jurong, Singapore', 'us', 'us-singapore'],
    ['Ba Dinh, Hanoi', 'us', 'us-hanoi'],
    ['District 3, Ho Chi Minh City', 'us', 'us-hcmc'],
    ['Andheri, Mumbai', 'us', 'us-mumbai'],
    ['Deira, Dubai', 'us', 'us-dubai'],
    ['Kreuzberg, Berlin', 'us', 'us-berlin'],
    // Paris has no VAC entry; London (344km) genuinely beats Lyon (392km).
    ['Le Marais, Paris', 'schengen', 'sch-london'],
  ]
  for (const [typed, route, expectFirst] of cases) {
    const o = resolveAddress(typed)
    const [first] = nearestCentres(route, o, 3)
    assert.equal(first.id, expectFirst,
      `${typed} (${route}) -> got ${first.id}, expected ${expectFirst}`)
    assert.equal(typeof first.km, 'number')
  }
})

test('distances are plausible great-circle numbers', () => {
  const sh = resolveAddress('Shanghai')
  const [first] = nearestCentres('us', sh, 3)
  assert.equal(first.id, 'us-sh')
  assert.ok(first.km < 30, `Shanghai centre should be near, got ${first.km}km`)
  const lyonToParis = distanceKm(resolveAddress('Lyon'), { lat: 48.867, lon: 2.321 })
  assert.ok(lyonToParis > 350 && lyonToParis < 500,
    `Lyon->Paris should be ~390km, got ${lyonToParis}`)
})

test('an approx origin yields centres with NO distance claimed', () => {
  const o = resolveAddress('somewhere unrecognizable')
  const list = nearestCentres('us', o, 3)
  assert.equal(list.length, 3)
  for (const c of list) assert.equal(c.km, null)   // never a fabricated km
})

// ---- catalogue integrity -----------------------------------------------------

test('the centre catalogue is valid: unique ids, real coords, routed', () => {
  const ids = new Set()
  for (const c of CENTRES) {
    assert.ok(c.id && !ids.has(c.id), `duplicate/missing id ${c.id}`)
    ids.add(c.id)
    assert.ok(c.name && c.city && c.address, `incomplete centre ${c.id}`)
    assert.ok(Math.abs(c.lat) <= 90 && Math.abs(c.lon) <= 180, `bad coords ${c.id}`)
    assert.ok(Array.isArray(c.routes) && c.routes.length > 0, `unrouted ${c.id}`)
  }
  // Both routes have enough coverage for a worldwide demo.
  assert.ok(routeCatalogue('us').length >= 20)
  assert.ok(routeCatalogue('schengen').length >= 15)
})

// ---- city autocomplete -------------------------------------------------------

test('city autocomplete finds any world city by prefix, substring, or CJK alias', async () => {
  const { searchCities, findCity, WORLD_CITIES } =
    await import('../../src/renderer/src/lib/worldCities.js')
  // Prefix, ranked first.
  assert.equal(searchCities('lyo')[0].name, 'Lyon')
  assert.equal(searchCities('LYON')[0].name, 'Lyon')
  assert.equal(searchCities('shang')[0].name, 'Shanghai')
  // CJK aliases.
  assert.equal(searchCities('上海')[0].name, 'Shanghai')
  assert.equal(searchCities('里昂')[0].name, 'Lyon')
  assert.equal(searchCities('紐約')[0].name, 'New York')
  // Diacritic-insensitive.
  assert.equal(searchCities('munchen')[0].name, 'Munich')
  assert.equal(searchCities('sao paulo')[0].name, 'São Paulo')
  // Common abbreviations.
  assert.equal(searchCities('nyc')[0].name, 'New York')
  assert.equal(searchCities('hcmc')[0].name, 'Ho Chi Minh City')
  // Bounded, and empty input yields nothing.
  assert.ok(searchCities('a').length <= 8)
  assert.deepEqual(searchCities(''), [])
  // Committed values look up exactly.
  assert.equal(findCity('Lyon, France').name, 'Lyon')
  assert.equal(findCity('上海').name, 'Shanghai')
  assert.equal(findCity('not-a-city'), null)
  // The list is broad (every region) and internally valid.
  assert.ok(WORLD_CITIES.length >= 200, `only ${WORLD_CITIES.length} cities`)
  const names = new Set()
  for (const c of WORLD_CITIES) {
    assert.ok(!names.has(c.name + c.country), `duplicate ${c.name}`)
    names.add(c.name + c.country)
    assert.ok(Math.abs(c.lat) <= 90 && Math.abs(c.lon) <= 180, `bad coords ${c.name}`)
  }
})

test('every world city resolves through resolveAddress with real coordinates', async () => {
  const { WORLD_CITIES } = await import('../../src/renderer/src/lib/worldCities.js')
  for (const c of WORLD_CITIES) {
    const o = resolveAddress(`123 Some Street, ${c.name}`)
    assert.ok(o && o.lat != null, `no coords for ${c.name}`)
    assert.ok(!o.approx, `${c.name} resolved only approximately`)
  }
})

test('slot generation is deterministic per centre and never empty', () => {
  const centre = CENTRES.find((c) => c.id === 'us-paris')
  const a = generateAvailability('us', centre)
  const b = generateAvailability('us', centre)
  assert.deepEqual(a, b)                     // re-render must not reshuffle
  assert.ok(a.length >= 4)
  for (const d of a) assert.ok(d.times.length > 0)
})
