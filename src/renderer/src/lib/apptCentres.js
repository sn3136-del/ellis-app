// Official appointment centres + the geography that finds the right one.
//
// REAL DATA, used by the real booking pipeline's UI (BookAppointment.jsx):
//   * CENTRES are the real visa application centres / consulates, with real
//     street addresses and coordinates — every address verified against
//     official sources (2026-08-17). Tokyo/Seoul carry NO Schengen VAC entry:
//     applications there go directly to the embassies, and a "VAC" would be
//     an invention.
//   * resolveAddress / nearestCentres are true great-circle geography from
//     the city the applicant names — no network call, never a dead end.
//
// What this module deliberately does NOT contain: slot availability. Open
// dates come only from the booking pipeline (app/appt_booking*.py) — read
// from the official calendar in an authorized session and stamped with who
// read them and when. Nothing here generates, guesses, or embellishes a slot.

import { WORLD_CITIES } from './worldCities.js'

// ---- Centres ---------------------------------------------------------------
// { id, name, kind, city, address, lat, lon, routes[] }
export const CENTRES = [
  // ---- US consular posts in Greater China (real posts + addresses) --------
  { id: 'us-bj', name: 'U.S. Embassy Beijing', kind: 'consulate', city: 'Beijing',
    address: '55 An Jia Lou Road, Chaoyang District, Beijing',
    lat: 39.9500, lon: 116.4667, routes: ['us'] },
  // Visa-section addresses (where the applicant actually goes), not always
  // the consulate compound — re-verified against the embassy's own pages
  // 2026-08-18.
  { id: 'us-sh', name: 'U.S. Consulate General Shanghai', kind: 'consulate', city: 'Shanghai',
    address: 'Westgate Mall 8/F, 1038 Nanjing West Road, Jing’an District, Shanghai',
    lat: 31.2286, lon: 121.4500, routes: ['us'] },
  { id: 'us-gz', name: 'U.S. Consulate General Guangzhou', kind: 'consulate', city: 'Guangzhou',
    address: '43 Hua Jiu Road, Zhujiang New Town, Tianhe District, Guangzhou (applicant entrance on Huaxia Road)',
    lat: 23.1180, lon: 113.3230, routes: ['us'] },
  { id: 'us-sy', name: 'U.S. Consulate General Shenyang', kind: 'consulate', city: 'Shenyang',
    address: 'Maoye Tiandi Shopping Mall 5/F, 185 Qingnian Street, Shenhe District, Shenyang',
    lat: 41.7770, lon: 123.4400, routes: ['us'] },
  { id: 'us-wh', name: 'U.S. Consulate General Wuhan', kind: 'consulate', city: 'Wuhan',
    address: 'Minsheng Bank Building, 396 Xinhua Road, Jiang’an District, Wuhan',
    lat: 30.5990, lon: 114.2860, routes: ['us'] },
  { id: 'us-hk', name: 'U.S. Consulate General Hong Kong', kind: 'consulate', city: 'Hong Kong',
    address: '26 Garden Road, Central, Hong Kong',
    lat: 22.2780, lon: 114.1580, routes: ['us'] },

  // ---- Schengen visa application centres (VFS/TLS) in China --------------
  { id: 'sch-bj-fr', name: 'France Visa Application Centre — Beijing', kind: 'vac', city: 'Beijing',
    address: 'Room 1101, Tower A, Gateway Plaza, 18 Xiaguangli, Chaoyang District, Beijing',
    lat: 39.9630, lon: 116.4530, routes: ['schengen'], state: 'France' },
  { id: 'sch-bj-de', name: 'Germany Visa Application Centre — Beijing', kind: 'vac', city: 'Beijing',
    address: 'East 101, B1/F, Building C, Guanghualu SOHO II, 9 Guanghua Road, Chaoyang, Beijing',
    lat: 39.9160, lon: 116.4540, routes: ['schengen'], state: 'Germany' },
  { id: 'sch-sh-fr', name: 'France Visa Application Centre — Shanghai', kind: 'vac', city: 'Shanghai',
    address: '2/F, SUN CITY, 299 Hengfeng Road, Jing’an District, Shanghai',
    lat: 31.2460, lon: 121.4560, routes: ['schengen'], state: 'France' },
  { id: 'sch-sh-de', name: 'Germany Visa Application Centre — Shanghai', kind: 'vac', city: 'Shanghai',
    address: '4/F, Jiushi Business Mansion, 213 Sichuan Middle Road, Huangpu District, Shanghai',
    lat: 31.2400, lon: 121.4860, routes: ['schengen'], state: 'Germany' },
  { id: 'sch-gz-fr', name: 'France Visa Application Centre — Guangzhou', kind: 'vac', city: 'Guangzhou',
    address: 'Room 02-03, 14/F, Pacific Finance Center, 32 Huaxia Road, Tianhe District, Guangzhou',
    lat: 23.1190, lon: 113.3220, routes: ['schengen'], state: 'France' },
  { id: 'sch-cd-de', name: 'Germany Visa Application Centre — Chengdu', kind: 'vac', city: 'Chengdu',
    address: 'Rooms 3201-3203, 32/F, Tower A, Maoye Center, 19 Dongyu Street, Jinjiang District, Chengdu',
    lat: 30.6550, lon: 104.0810, routes: ['schengen'], state: 'Germany' },
  { id: 'sch-sz-fr', name: 'France Visa Application Centre — Shenzhen', kind: 'vac', city: 'Shenzhen',
    address: 'Room 801A, CITIC International Building, 2001 Shennan Middle Road, Futian, Shenzhen',
    lat: 22.5430, lon: 114.0950, routes: ['schengen'], state: 'France' },

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
    address: 'Unit 03-05, 12/F, Prosperity Millennia Plaza, 663 King’s Road, Quarry Bay, Hong Kong',
    lat: 22.2880, lon: 114.2090, routes: ['schengen'] },
  { id: 'sch-taipei', name: 'Schengen Visa Application Centre — Taipei', kind: 'vac', city: 'Taipei',
    address: '7F, Room A, 97 Songren Road, Xinyi District, Taipei',
    lat: 25.0370, lon: 121.5670, routes: ['schengen'] },
  { id: 'sch-sg', name: 'Schengen Visa Application Centre — Singapore', kind: 'vac', city: 'Singapore',
    address: '#08-01, Philippine Airlines Building, 135 Cecil Street, Singapore',
    lat: 1.2790, lon: 103.8470, routes: ['schengen'] },
  { id: 'sch-bkk', name: 'Schengen Visa Application Centre — Bangkok', kind: 'vac', city: 'Bangkok',
    address: 'Unit 404, 4/F, The Plaza @ Chamchuri Square, Phayathai Road, Pathum Wan, Bangkok',
    lat: 13.7330, lon: 100.5290, routes: ['schengen'] },
  { id: 'sch-london', name: 'Schengen Visa Application Centre — London', kind: 'vac', city: 'London',
    address: '18 Ryeland Boulevard, Ram Quarter, Wandsworth, London SW18 1UN',
    lat: 51.4560, lon: -0.1920, routes: ['schengen'] },
  { id: 'sch-dubai', name: 'Schengen Visa Application Centre — Dubai', kind: 'vac', city: 'Dubai',
    address: 'First Floor, Phase 5 – Horus, Wafi Mall, Umm Hurair 2, Dubai',
    lat: 25.2290, lon: 55.3190, routes: ['schengen'] },
  { id: 'sch-delhi', name: 'Schengen Visa Application Centre — New Delhi', kind: 'vac', city: 'New Delhi',
    address: 'VFS Global House, 27 Kasturba Gandhi Marg, Connaught Place, New Delhi',
    lat: 28.6260, lon: 77.2220, routes: ['schengen'] },
  { id: 'sch-ny', name: 'Schengen Visa Application Centre — New York', kind: 'vac', city: 'New York',
    address: '145 W 45th Street, New York, NY', lat: 40.7570, lon: -73.9840, routes: ['schengen'] },
]

// Well-known city anchors so a typed address resolves without any network
// call (centre-finding must never depend on a geocoding service). Matching is
// by substring on the city name, in English or Chinese.
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
  // World cities — any address an applicant types must place.
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
  if (/[^ -ÿ]/.test(key)) return s.includes(key)
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
  // Unknown locality: keep the applicant's own words as the label (last
  // segment reads most like the city), mark it approximate, and let Kimi
  // place it.
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

/** Best-effort match from an operator-recorded post string back to a known
 *  centre — so a real offered slot can be drawn on a real map. Matches by
 *  exact name, then by the centre's city appearing in the post text. Returns
 *  null rather than guessing across cities. */
export function centreForPost(route, post) {
  const p = String(post || '').toLowerCase().trim()
  if (!p) return null
  const pool = routeCatalogue(route)
  return pool.find((c) => c.name.toLowerCase() === p)
    || pool.find((c) => p.includes(c.name.toLowerCase()))
    || pool.find((c) => p.includes(c.city.toLowerCase()))
    || null
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

/** Split a recorded slot's free-form `when` into a date part and the rest —
 *  "2026-09-14 09:30" -> { date: '2026-09-14', time: '09:30' }. A `when` that
 *  does not start with an ISO date keeps its whole text as `time` and no
 *  date, so nothing recorded by a person is ever dropped or reformatted into
 *  a claim they did not make. */
export function splitWhen(when) {
  const s = String(when || '').trim()
  const m = s.match(/^(\d{4}-\d{2}-\d{2})[T ]?(.*)$/)
  if (!m) return { date: '', time: s }
  return { date: m[1], time: (m[2] || '').trim() }
}
