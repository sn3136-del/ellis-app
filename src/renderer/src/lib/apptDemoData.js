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

import { WORLD_CITIES } from './worldCities.js'

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

  // ---- US consular posts worldwide (real posts; addresses to street level) --
  { id: 'us-paris', name: 'U.S. Embassy Paris', kind: 'consulate', city: 'Paris',
    address: '2 Avenue Gabriel, 75008 Paris', lat: 48.8670, lon: 2.3210, routes: ['us'] },
  { id: 'us-london', name: 'U.S. Embassy London', kind: 'consulate', city: 'London',
    address: '33 Nine Elms Lane, London SW11 7US', lat: 51.4830, lon: -0.1330, routes: ['us'] },
  { id: 'us-frankfurt', name: 'U.S. Consulate General Frankfurt', kind: 'consulate', city: 'Frankfurt',
    address: 'Gießener Straße 30, 60435 Frankfurt am Main', lat: 50.1560, lon: 8.6970, routes: ['us'] },
  { id: 'us-berlin', name: 'U.S. Embassy Berlin', kind: 'consulate', city: 'Berlin',
    address: 'Clayallee 170, 14191 Berlin', lat: 52.4590, lon: 13.2700, routes: ['us'] },
  { id: 'us-madrid', name: 'U.S. Embassy Madrid', kind: 'consulate', city: 'Madrid',
    address: 'Calle de Serrano 75, 28006 Madrid', lat: 40.4360, lon: -3.6870, routes: ['us'] },
  { id: 'us-rome', name: 'U.S. Embassy Rome', kind: 'consulate', city: 'Rome',
    address: 'Via Vittorio Veneto 121, 00187 Rome', lat: 41.9070, lon: 12.4890, routes: ['us'] },
  { id: 'us-tokyo', name: 'U.S. Embassy Tokyo', kind: 'consulate', city: 'Tokyo',
    address: '1-10-5 Akasaka, Minato-ku, Tokyo', lat: 35.6690, lon: 139.7420, routes: ['us'] },
  { id: 'us-seoul', name: 'U.S. Embassy Seoul', kind: 'consulate', city: 'Seoul',
    address: '188 Sejong-daero, Jongno-gu, Seoul', lat: 37.5720, lon: 126.9770, routes: ['us'] },
  { id: 'us-singapore', name: 'U.S. Embassy Singapore', kind: 'consulate', city: 'Singapore',
    address: '27 Napier Road, Singapore 258508', lat: 1.3050, lon: 103.8190, routes: ['us'] },
  { id: 'us-bangkok', name: 'U.S. Embassy Bangkok', kind: 'consulate', city: 'Bangkok',
    address: '95 Wireless Road, Lumpini, Bangkok', lat: 13.7410, lon: 100.5480, routes: ['us'] },
  { id: 'us-hanoi', name: 'U.S. Embassy Hanoi', kind: 'consulate', city: 'Hanoi',
    address: '170 Ngoc Khanh, Ba Dinh District, Hanoi', lat: 21.0290, lon: 105.8110, routes: ['us'] },
  { id: 'us-hcmc', name: 'U.S. Consulate General Ho Chi Minh City', kind: 'consulate', city: 'Ho Chi Minh City',
    address: '4 Le Duan Boulevard, District 1, Ho Chi Minh City', lat: 10.7830, lon: 106.7000, routes: ['us'] },
  { id: 'us-manila', name: 'U.S. Embassy Manila', kind: 'consulate', city: 'Manila',
    address: '1201 Roxas Boulevard, Ermita, Manila', lat: 14.5790, lon: 120.9790, routes: ['us'] },
  { id: 'us-jakarta', name: 'U.S. Embassy Jakarta', kind: 'consulate', city: 'Jakarta',
    address: 'Jl. Medan Merdeka Selatan 3-5, Jakarta', lat: -6.1820, lon: 106.8240, routes: ['us'] },
  { id: 'us-delhi', name: 'U.S. Embassy New Delhi', kind: 'consulate', city: 'New Delhi',
    address: 'Shantipath, Chanakyapuri, New Delhi', lat: 28.5960, lon: 77.1890, routes: ['us'] },
  { id: 'us-mumbai', name: 'U.S. Consulate General Mumbai', kind: 'consulate', city: 'Mumbai',
    address: 'C-49, G Block, Bandra Kurla Complex, Mumbai', lat: 19.0640, lon: 72.8690, routes: ['us'] },
  { id: 'us-dubai', name: 'U.S. Consulate General Dubai', kind: 'consulate', city: 'Dubai',
    address: 'Corner of Al Seef Road and Sheikh Khalifa bin Zayed Road, Dubai', lat: 25.2270, lon: 55.2890, routes: ['us'] },
  { id: 'us-toronto', name: 'U.S. Consulate General Toronto', kind: 'consulate', city: 'Toronto',
    address: '360 University Avenue, Toronto', lat: 43.6540, lon: -79.3880, routes: ['us'] },
  { id: 'us-sydney', name: 'U.S. Consulate General Sydney', kind: 'consulate', city: 'Sydney',
    address: 'Suite 2, 50 Miller Street, North Sydney', lat: -33.8390, lon: 151.2070, routes: ['us'] },
  { id: 'us-taipei', name: 'American Institute in Taiwan (Taipei)', kind: 'consulate', city: 'Taipei',
    address: '100 Jinhu Road, Neihu District, Taipei', lat: 25.0800, lon: 121.5940, routes: ['us'] },

  // ---- Schengen visa application centres outside mainland China -----------
  { id: 'sch-hk', name: 'Schengen Visa Application Centre — Hong Kong', kind: 'vac', city: 'Hong Kong',
    address: '6/F, Causeway Bay Plaza 2, 463-483 Lockhart Road, Hong Kong',
    lat: 22.2800, lon: 114.1830, routes: ['schengen'] },
  { id: 'sch-taipei', name: 'Schengen Visa Application Centre — Taipei', kind: 'vac', city: 'Taipei',
    address: '7/F, 41 Zhongxiao West Road Sec. 1, Taipei', lat: 25.0460, lon: 121.5170, routes: ['schengen'] },
  { id: 'sch-sg', name: 'Schengen Visa Application Centre — Singapore', kind: 'vac', city: 'Singapore',
    address: '135 Cecil Street, Singapore', lat: 1.2790, lon: 103.8470, routes: ['schengen'] },
  { id: 'sch-bkk', name: 'Schengen Visa Application Centre — Bangkok', kind: 'vac', city: 'Bangkok',
    address: 'The Plaza, Ploenchit Road, Bangkok', lat: 13.7440, lon: 100.5490, routes: ['schengen'] },
  { id: 'sch-tokyo', name: 'Schengen Visa Application Centre — Tokyo', kind: 'vac', city: 'Tokyo',
    address: '4-3-6 Shibaura, Minato-ku, Tokyo', lat: 35.6420, lon: 139.7500, routes: ['schengen'] },
  { id: 'sch-seoul', name: 'Schengen Visa Application Centre — Seoul', kind: 'vac', city: 'Seoul',
    address: '18F, 41 Jong-ro 1-gil, Jongno-gu, Seoul', lat: 37.5710, lon: 126.9790, routes: ['schengen'] },
  { id: 'sch-london', name: 'Schengen Visa Application Centre — London', kind: 'vac', city: 'London',
    address: '66 Wilson Street, London EC2A 2BT', lat: 51.5200, lon: -0.0850, routes: ['schengen'] },
  { id: 'sch-dubai', name: 'Schengen Visa Application Centre — Dubai', kind: 'vac', city: 'Dubai',
    address: 'Wafi Mall, Level 2, Umm Hurair 2, Dubai', lat: 25.2290, lon: 55.3190, routes: ['schengen'] },
  { id: 'sch-delhi', name: 'Schengen Visa Application Centre — New Delhi', kind: 'vac', city: 'New Delhi',
    address: 'Shivaji Stadium Metro Station, Baba Kharak Singh Marg, New Delhi',
    lat: 28.6290, lon: 77.2110, routes: ['schengen'] },
  { id: 'sch-ny', name: 'Schengen Visa Application Centre — New York', kind: 'vac', city: 'New York',
    address: '145 W 45th Street, New York, NY', lat: 40.7570, lon: -73.9840, routes: ['schengen'] },
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
  { keys: ['dalian', '大连', '大連'], lat: 38.9140, lon: 121.6147, label: 'Dalian' },
  { keys: ['changsha', '长沙', '長沙'], lat: 28.2282, lon: 112.9388, label: 'Changsha' },
  { keys: ['kunming', '昆明'], lat: 24.8801, lon: 102.8329, label: 'Kunming' },
  { keys: ['fuzhou', '福州'], lat: 26.0745, lon: 119.2965, label: 'Fuzhou' },
  { keys: ['taipei', '台北', '臺北'], lat: 25.0330, lon: 121.5654, label: 'Taipei' },
  { keys: ['macau', 'macao', '澳门', '澳門'], lat: 22.1987, lon: 113.5439, label: 'Macau' },
  // World cities — the demo must place ANY address a viewer types.
  { keys: ['lyon'], lat: 45.7640, lon: 4.8357, label: 'Lyon' },
  { keys: ['paris'], lat: 48.8566, lon: 2.3522, label: 'Paris' },
  { keys: ['marseille'], lat: 43.2965, lon: 5.3698, label: 'Marseille' },
  { keys: ['london'], lat: 51.5074, lon: -0.1278, label: 'London' },
  { keys: ['manchester'], lat: 53.4808, lon: -2.2426, label: 'Manchester' },
  { keys: ['berlin'], lat: 52.5200, lon: 13.4050, label: 'Berlin' },
  { keys: ['frankfurt'], lat: 50.1109, lon: 8.6821, label: 'Frankfurt' },
  { keys: ['munich', 'münchen', 'muenchen'], lat: 48.1351, lon: 11.5820, label: 'Munich' },
  { keys: ['madrid'], lat: 40.4168, lon: -3.7038, label: 'Madrid' },
  { keys: ['barcelona'], lat: 41.3874, lon: 2.1686, label: 'Barcelona' },
  { keys: ['rome', 'roma'], lat: 41.9028, lon: 12.4964, label: 'Rome' },
  { keys: ['milan', 'milano'], lat: 45.4642, lon: 9.1900, label: 'Milan' },
  { keys: ['amsterdam'], lat: 52.3676, lon: 4.9041, label: 'Amsterdam' },
  { keys: ['istanbul'], lat: 41.0082, lon: 28.9784, label: 'Istanbul' },
  { keys: ['moscow', 'москва'], lat: 55.7558, lon: 37.6173, label: 'Moscow' },
  { keys: ['dubai', 'دبي'], lat: 25.2048, lon: 55.2708, label: 'Dubai' },
  { keys: ['tokyo', '東京', '东京'], lat: 35.6762, lon: 139.6503, label: 'Tokyo' },
  { keys: ['osaka', '大阪'], lat: 34.6937, lon: 135.5023, label: 'Osaka' },
  { keys: ['seoul', '서울', '首尔', '首爾'], lat: 37.5665, lon: 126.9780, label: 'Seoul' },
  { keys: ['singapore', '新加坡'], lat: 1.3521, lon: 103.8198, label: 'Singapore' },
  { keys: ['bangkok', '曼谷'], lat: 13.7563, lon: 100.5018, label: 'Bangkok' },
  { keys: ['hanoi', 'ha noi', '河内'], lat: 21.0278, lon: 105.8342, label: 'Hanoi' },
  { keys: ['ho chi minh', 'saigon', 'hcmc', '胡志明'], lat: 10.8231, lon: 106.6297, label: 'Ho Chi Minh City' },
  { keys: ['manila', '马尼拉'], lat: 14.5995, lon: 120.9842, label: 'Manila' },
  { keys: ['jakarta', '雅加达'], lat: -6.2088, lon: 106.8456, label: 'Jakarta' },
  { keys: ['kuala lumpur', '吉隆坡'], lat: 3.1390, lon: 101.6869, label: 'Kuala Lumpur' },
  { keys: ['new delhi', 'delhi', '新德里'], lat: 28.6139, lon: 77.2090, label: 'New Delhi' },
  { keys: ['mumbai', '孟买'], lat: 19.0760, lon: 72.8777, label: 'Mumbai' },
  { keys: ['new york', 'nyc', '纽约', '紐約'], lat: 40.7128, lon: -74.0060, label: 'New York' },
  { keys: ['los angeles', '洛杉矶', '洛杉磯'], lat: 34.0522, lon: -118.2437, label: 'Los Angeles' },
  { keys: ['san francisco', '旧金山', '舊金山'], lat: 37.7749, lon: -122.4194, label: 'San Francisco' },
  { keys: ['seattle', '西雅图'], lat: 47.6062, lon: -122.3321, label: 'Seattle' },
  { keys: ['chicago', '芝加哥'], lat: 41.8781, lon: -87.6298, label: 'Chicago' },
  { keys: ['boston', '波士顿'], lat: 42.3601, lon: -71.0589, label: 'Boston' },
  { keys: ['toronto', '多伦多'], lat: 43.6532, lon: -79.3832, label: 'Toronto' },
  { keys: ['vancouver', '温哥华'], lat: 49.2827, lon: -123.1207, label: 'Vancouver' },
  { keys: ['sydney', '悉尼'], lat: -33.8688, lon: 151.2093, label: 'Sydney' },
  { keys: ['melbourne', '墨尔本'], lat: -37.8136, lon: 144.9631, label: 'Melbourne' },
  { keys: ['são paulo', 'sao paulo', '圣保罗'], lat: -23.5505, lon: -46.6333, label: 'São Paulo' },
  { keys: ['mexico city', 'cdmx', '墨西哥城'], lat: 19.4326, lon: -99.1332, label: 'Mexico City' },
]

/** Resolve a typed address or city, offline, never failing. The local anchors
 *  answer first, then the full world-cities list (any of ~360 real cities in
 *  any alias, CJK included); anything else yields an approximate origin
 *  (label only, no coordinates) so the flow proceeds and Kimi K3 does the
 *  locating. Only an empty string returns null. */
// A Latin key must match on WORD boundaries ("la" may never fire inside
// "Rural Lane", "rio" never inside "period"); CJK has no word boundaries, so
// a CJK key matches as a substring.
function keyHits(s, key) {
  if (!key) return false
  if (/[^ -ÿ]/.test(key)) return s.includes(key)
  const esc = key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return new RegExp(`(^|[^a-z\\u00c0-\\u024f])${esc}([^a-z\\u00c0-\\u024f]|$)`).test(s)
}

export function resolveAddress(text) {
  const s = String(text || '').toLowerCase().trim()
  if (!s) return null
  for (const a of CITY_ANCHORS) {
    if (a.keys.some((k) => keyHits(s, k))) {
      return { lat: a.lat, lon: a.lon, label: a.label }
    }
  }
  for (const c of WORLD_CITIES) {
    if (keyHits(s, c.name.toLowerCase()) ||
        c.aliases.some((k) => k.length > 1 && keyHits(s, k))) {
      return { lat: c.lat, lon: c.lon, label: c.name }
    }
  }
  // Unknown locality: keep the viewer's own words as the label (last segment
  // reads most like the city), mark it approximate, and let Kimi place it.
  const parts = String(text).split(',').map((p) => p.trim()).filter(Boolean)
  const label = (parts[parts.length - 1] || String(text).trim()).slice(0, 60)
  return { lat: null, lon: null, label, approx: true }
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

/** The centres serving a route, nearest first from the resolved origin. An
 *  origin without coordinates (approx — unknown city) yields the route's
 *  leading centres with no distance claimed; Kimi's pick then reorders. */
export function nearestCentres(route, origin, limit = 3) {
  const pool = CENTRES.filter((c) => c.routes.includes(route))
  if (!origin || origin.lat == null || origin.lon == null) {
    return pool.slice(0, limit).map((c) => ({ ...c, km: null }))
  }
  return pool
    .map((c) => ({ ...c, km: Math.round(distanceKm(origin, c)) }))
    .sort((a, b) => a.km - b.km)
    .slice(0, limit)
}

/** The whole catalogue for a route — what Kimi picks from when the local
 *  geocode can't place the address. */
export function routeCatalogue(route) {
  return CENTRES.filter((c) => c.routes.includes(route))
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
