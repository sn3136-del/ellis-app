// Appointment DEMO data + generators (Trip.com pitch demo, 2026-08-17).
//
// SIMULATED. Nothing here touches the real booking pipeline, the backend, or
// any government site: it is a self-contained script for showing the product
// end to end. The real feature (app/appt_booking*.py + AppointmentCockpit's
// BookingPanel) is evidence-gated and fails closed; this module exists so a
// demo can run without an operator account or a released adapter.
//
// Realism the demo relies on:
//   * centres are the REAL visa application centres / consulates, with real
//     street addresses and coordinates, so "nearest to you" is a true
//     great-circle sort from the address the viewer types;
//   * slot dates are generated on a business calendar with plausible wait
//     times per route, morning/afternoon blocks, and scarcity (some days
//     full), so the calendar never looks like a uniform grid.

// ---- Centres ---------------------------------------------------------------
// { id, name, kind, city, address, lat, lon, routes[] }
export const CENTRES = [
  // ---- US consular posts in Greater China (real posts + addresses) --------
  { id: 'us-bj', name: 'U.S. Embassy Beijing', kind: 'consulate', city: 'Beijing',
    address: '55 An Jia Lou Road, Chaoyang District, Beijing',
    lat: 39.9500, lon: 116.4667, routes: ['us'] },
  { id: 'us-sh', name: 'U.S. Consulate General Shanghai', kind: 'consulate', city: 'Shanghai',
    address: '1038 Nanjing West Road, Jing’an District, Shanghai',
    lat: 31.2286, lon: 121.4500, routes: ['us'] },
  { id: 'us-gz', name: 'U.S. Consulate General Guangzhou', kind: 'consulate', city: 'Guangzhou',
    address: '43 Hua Jiu Road, Zhujiang New Town, Tianhe District, Guangzhou',
    lat: 23.1180, lon: 113.3230, routes: ['us'] },
  { id: 'us-sy', name: 'U.S. Consulate General Shenyang', kind: 'consulate', city: 'Shenyang',
    address: '52 Shi Si Wei Road, Heping District, Shenyang',
    lat: 41.7860, lon: 123.4100, routes: ['us'] },
  { id: 'us-hk', name: 'U.S. Consulate General Hong Kong', kind: 'consulate', city: 'Hong Kong',
    address: '26 Garden Road, Central, Hong Kong',
    lat: 22.2780, lon: 114.1580, routes: ['us'] },

  // ---- Schengen visa application centres (VFS/TLS) in China --------------
  { id: 'sch-bj-fr', name: 'France Visa Application Centre — Beijing', kind: 'vac', city: 'Beijing',
    address: 'Room 1101, Tower A, Gateway Plaza, 18 Xiaguangli, Chaoyang District, Beijing',
    lat: 39.9630, lon: 116.4530, routes: ['schengen'], state: 'France' },
  { id: 'sch-bj-de', name: 'Germany Visa Application Centre — Beijing', kind: 'vac', city: 'Beijing',
    address: '2/F, Landmark Tower 2, 8 North Dongsanhuan Road, Chaoyang District, Beijing',
    lat: 39.9540, lon: 116.4610, routes: ['schengen'], state: 'Germany' },
  { id: 'sch-sh-fr', name: 'France Visa Application Centre — Shanghai', kind: 'vac', city: 'Shanghai',
    address: '3/F, Sun Tong Infoport Plaza, 55 Huai Hai West Road, Xuhui District, Shanghai',
    lat: 31.1900, lon: 121.4200, routes: ['schengen'], state: 'France' },
  { id: 'sch-sh-de', name: 'Germany Visa Application Centre — Shanghai', kind: 'vac', city: 'Shanghai',
    address: '9/F, Hongwell International Plaza, 1602 Zhongshan West Road, Shanghai',
    lat: 31.1830, lon: 121.4260, routes: ['schengen'], state: 'Germany' },
  { id: 'sch-gz-fr', name: 'France Visa Application Centre — Guangzhou', kind: 'vac', city: 'Guangzhou',
    address: '5/F, Ying Feng Plaza, 63 Ma Chang Road, Zhujiang New Town, Guangzhou',
    lat: 23.1230, lon: 113.3290, routes: ['schengen'], state: 'France' },
  { id: 'sch-cd-de', name: 'Germany Visa Application Centre — Chengdu', kind: 'vac', city: 'Chengdu',
    address: '10/F, Sino-Ocean Taikoo Li, 8 Zhongsha Road, Jinjiang District, Chengdu',
    lat: 30.6520, lon: 104.0810, routes: ['schengen'], state: 'Germany' },
  { id: 'sch-sz-fr', name: 'France Visa Application Centre — Shenzhen', kind: 'vac', city: 'Shenzhen',
    address: '26/F, Great China International Exchange Square, Fuhua Road, Futian, Shenzhen',
    lat: 22.5400, lon: 114.0570, routes: ['schengen'], state: 'France' },
]

// Well-known city anchors so a typed address resolves without any network
// call (a demo must never depend on a geocoding service). Matching is by
// substring on the city name, in English or Chinese.
const CITY_ANCHORS = [
  { keys: ['beijing', '北京'], lat: 39.9042, lon: 116.4074, label: 'Beijing' },
  { keys: ['shanghai', '上海'], lat: 31.2304, lon: 121.4737, label: 'Shanghai' },
  { keys: ['guangzhou', '广州', '廣州'], lat: 23.1291, lon: 113.2644, label: 'Guangzhou' },
  { keys: ['shenzhen', '深圳'], lat: 22.5431, lon: 114.0579, label: 'Shenzhen' },
  { keys: ['chengdu', '成都'], lat: 30.5728, lon: 104.0668, label: 'Chengdu' },
  { keys: ['hangzhou', '杭州'], lat: 30.2741, lon: 120.1551, label: 'Hangzhou' },
  { keys: ['nanjing', '南京'], lat: 32.0603, lon: 118.7969, label: 'Nanjing' },
  { keys: ['suzhou', '苏州', '蘇州'], lat: 31.2989, lon: 120.5853, label: 'Suzhou' },
  { keys: ['wuhan', '武汉', '武漢'], lat: 30.5928, lon: 114.3055, label: 'Wuhan' },
  { keys: ['xian', "xi'an", '西安'], lat: 34.3416, lon: 108.9398, label: 'Xi’an' },
  { keys: ['chongqing', '重庆', '重慶'], lat: 29.5630, lon: 106.5516, label: 'Chongqing' },
  { keys: ['tianjin', '天津'], lat: 39.3434, lon: 117.3616, label: 'Tianjin' },
  { keys: ['shenyang', '沈阳', '瀋陽'], lat: 41.8057, lon: 123.4315, label: 'Shenyang' },
  { keys: ['qingdao', '青岛', '青島'], lat: 36.0671, lon: 120.3826, label: 'Qingdao' },
  { keys: ['hong kong', 'hongkong', '香港'], lat: 22.3193, lon: 114.1694, label: 'Hong Kong' },
]

/** Resolve a typed address to a coordinate, offline. Returns null when no
 *  known city appears in the text — the UI then asks rather than guessing. */
export function resolveAddress(text) {
  const s = String(text || '').toLowerCase().trim()
  if (!s) return null
  for (const a of CITY_ANCHORS) {
    if (a.keys.some((k) => s.includes(k))) {
      return { lat: a.lat, lon: a.lon, label: a.label }
    }
  }
  return null
}

/** Great-circle distance in km. */
export function distanceKm(a, b) {
  const R = 6371
  const toRad = (d) => (d * Math.PI) / 180
  const dLat = toRad(b.lat - a.lat)
  const dLon = toRad(b.lon - a.lon)
  const la1 = toRad(a.lat), la2 = toRad(b.lat)
  const h = Math.sin(dLat / 2) ** 2 +
    Math.cos(la1) * Math.cos(la2) * Math.sin(dLon / 2) ** 2
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)))
}

/** The centres serving a route, nearest first from the resolved origin. */
export function nearestCentres(route, origin, limit = 3) {
  const pool = CENTRES.filter((c) => c.routes.includes(route))
  if (!origin) return pool.slice(0, limit).map((c) => ({ ...c, km: null }))
  return pool
    .map((c) => ({ ...c, km: Math.round(distanceKm(origin, c)) }))
    .sort((a, b) => a.km - b.km)
    .slice(0, limit)
}

// ---- Slot generation -------------------------------------------------------
// Deterministic per (centre, day) so re-renders don't reshuffle the calendar,
// but varied enough to look like a real, uneven booking calendar.
function hash(str) {
  let h = 2166136261
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return (h >>> 0) / 4294967295
}

const TIMES_AM = ['08:00', '08:15', '08:30', '08:45', '09:00', '09:15', '09:30',
                  '09:45', '10:00', '10:30', '11:00']
const TIMES_PM = ['13:00', '13:15', '13:30', '14:00', '14:15', '14:30', '15:00',
                  '15:30']

function pad(n) { return String(n).padStart(2, '0') }
export function isoDate(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** Realistic typical wait before the first opening, per route + centre. */
function leadDays(route, centre) {
  const base = route === 'us' ? 24 : 11
  const jitter = Math.floor(hash(centre.id + ':lead') * 14)
  return base + jitter
}

/**
 * A month of availability for one centre: business days only, a realistic
 * lead time before anything opens, some days fully booked, and a varying
 * number of morning/afternoon slots per open day.
 */
export function generateAvailability(route, centre, { from = new Date(), days = 45 } = {}) {
  const out = []
  const lead = leadDays(route, centre)
  for (let i = lead; i < lead + days; i++) {
    const d = new Date(from.getFullYear(), from.getMonth(), from.getDate() + i)
    const dow = d.getDay()
    if (dow === 0 || dow === 6) continue           // consulates close weekends
    const key = `${centre.id}:${isoDate(d)}`
    const r = hash(key)
    if (r < 0.42) continue                          // fully booked that day
    const amCount = Math.floor(hash(key + ':am') * 4)      // 0-3
    const pmCount = Math.floor(hash(key + ':pm') * 3)      // 0-2
    if (amCount + pmCount === 0) continue
    const times = []
    for (let k = 0; k < amCount; k++) {
      times.push(TIMES_AM[Math.floor(hash(key + ':a' + k) * TIMES_AM.length)])
    }
    for (let k = 0; k < pmCount; k++) {
      times.push(TIMES_PM[Math.floor(hash(key + ':p' + k) * TIMES_PM.length)])
    }
    const uniq = [...new Set(times)].sort()
    if (!uniq.length) continue
    out.push({ date: isoDate(d), times: uniq })
  }
  return out
}

/** Human date label, e.g. "Tue, 16 Sep 2026". */
export function prettyDate(iso) {
  const [y, m, d] = String(iso).split('-').map(Number)
  const dt = new Date(y, (m || 1) - 1, d || 1)
  return dt.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric',
                                          month: 'short', year: 'numeric' })
}

/** Days from today to an ISO date. */
export function daysAway(iso, from = new Date()) {
  const [y, m, d] = String(iso).split('-').map(Number)
  const dt = new Date(y, (m || 1) - 1, d || 1)
  const base = new Date(from.getFullYear(), from.getMonth(), from.getDate())
  return Math.round((dt - base) / 86400000)
}

/** A plausible official confirmation number for the route. */
export function confirmationNumber(route, seed) {
  const n = Math.floor(hash(String(seed) + ':conf') * 9_000_000) + 1_000_000
  return route === 'us' ? `AA00${n}` : `VFS-CN-${n}`
}

// The steps Ellis's agent narrates while "working" the official site. Each is
// a real step of the live pipeline, so the demo mirrors the true flow.
export function agentSteps(route, centre) {
  const site = route === 'us' ? 'ais.usvisa-info.com' : 'visa.vfsglobal.com'
  return [
    { key: 'session', label: `Opening the operator's signed-in session on ${site}` },
    { key: 'navigate', label: `Navigating to the ${centre.city} scheduling calendar` },
    { key: 'read', label: 'Reading the open dates from the official calendar' },
    { key: 'relay', label: 'Relaying what it found to Trip.com' },
  ]
}

export function bookingSteps(route, centre, slot) {
  const site = route === 'us' ? 'ais.usvisa-info.com' : 'visa.vfsglobal.com'
  return [
    { key: 'reopen', label: `Re-opening ${site} in the operator's session` },
    { key: 'select', label: `Selecting ${prettyDate(slot.date)} at ${slot.time}` },
    { key: 'confirm', label: `Confirming the appointment at ${centre.name}` },
    { key: 'capture', label: 'Capturing the official confirmation as evidence' },
  ]
}
