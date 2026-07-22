// Diplomatic-mission / visa-agency directory with nearest-location selection.
// The traveler enters their home address; Ellis geocodes it against a world
// city gazetteer and picks the closest embassy/consulate (or, for agency
// channels like Japan×China, the closest accredited agency) by great-circle
// distance. Curated mission networks cover the high-volume origin markets;
// every other pair falls back to the destination's embassy in the origin
// country's capital.

// --- World city gazetteer (city -> coordinates), used to geocode addresses ---
const CITIES = {
  // China
  beijing: [39.904, 116.407], shanghai: [31.230, 121.474], guangzhou: [23.129, 113.264],
  shenzhen: [22.543, 114.058], chengdu: [30.573, 104.067], chongqing: [29.563, 106.551],
  wuhan: [30.593, 114.306], "xi'an": [34.342, 108.940], xian: [34.342, 108.940],
  hangzhou: [30.274, 120.155], nanjing: [32.060, 118.797], qingdao: [36.067, 120.383],
  shenyang: [41.806, 123.432], dalian: [38.914, 121.615], tianjin: [39.343, 117.362],
  suzhou: [31.299, 120.585], kunming: [24.880, 102.833], changsha: [28.228, 112.939],
  zhengzhou: [34.747, 113.625], harbin: [45.803, 126.535], fuzhou: [26.074, 119.297],
  xiamen: [24.480, 118.089], jinan: [36.651, 117.120], hefei: [31.821, 117.227],
  // India
  delhi: [28.614, 77.209], 'new delhi': [28.614, 77.209], mumbai: [19.076, 72.878],
  bengaluru: [12.972, 77.594], bangalore: [12.972, 77.594], chennai: [13.083, 80.270],
  kolkata: [22.573, 88.364], hyderabad: [17.385, 78.487], ahmedabad: [23.023, 72.571],
  pune: [18.520, 73.857],
  // Southeast Asia
  hanoi: [21.028, 105.804], 'ho chi minh': [10.823, 106.630], 'ho chi minh city': [10.823, 106.630],
  saigon: [10.823, 106.630], bangkok: [13.756, 100.502], 'chiang mai': [18.788, 98.985],
  jakarta: [-6.208, 106.846], surabaya: [-7.257, 112.752], manila: [14.599, 120.984],
  cebu: [10.315, 123.885], 'kuala lumpur': [3.139, 101.687], singapore: [1.352, 103.820],
  'phnom penh': [11.556, 104.928], yangon: [16.840, 96.173], vientiane: [17.975, 102.633],
  // Americas
  'new york': [40.713, -74.006], 'los angeles': [34.052, -118.244], 'san francisco': [37.775, -122.419],
  chicago: [41.878, -87.630], houston: [29.760, -95.370], miami: [25.762, -80.192],
  washington: [38.907, -77.037], seattle: [47.606, -122.332], boston: [42.360, -71.059],
  toronto: [43.651, -79.347], vancouver: [49.283, -123.121], montreal: [45.502, -73.567],
  'mexico city': [19.433, -99.133], guadalajara: [20.660, -103.349],
  'sao paulo': [-23.551, -46.633], 'são paulo': [-23.551, -46.633], 'rio de janeiro': [-22.907, -43.173],
  brasilia: [-15.794, -47.882], 'buenos aires': [-34.604, -58.382], santiago: [-33.449, -70.669],
  lima: [-12.046, -77.043], bogota: [4.711, -74.072], bogotá: [4.711, -74.072],
  // Europe
  london: [51.507, -0.128], manchester: [53.481, -2.242], edinburgh: [55.953, -3.188],
  paris: [48.857, 2.352], lyon: [45.764, 4.836], marseille: [43.296, 5.370],
  berlin: [52.520, 13.405], munich: [48.135, 11.582], frankfurt: [50.110, 8.682],
  hamburg: [53.551, 9.994], madrid: [40.417, -3.704], barcelona: [41.385, 2.173],
  rome: [41.903, 12.496], milan: [45.464, 9.190], amsterdam: [52.368, 4.904],
  brussels: [50.850, 4.352], zurich: [47.377, 8.541], geneva: [46.205, 6.143],
  vienna: [48.208, 16.372], warsaw: [52.230, 21.011], prague: [50.075, 14.438],
  budapest: [47.498, 19.040], lisbon: [38.722, -9.139], athens: [37.984, 23.728],
  stockholm: [59.329, 18.069], oslo: [59.913, 10.752], copenhagen: [55.676, 12.568],
  helsinki: [60.170, 24.938], dublin: [53.349, -6.260], moscow: [55.756, 37.617],
  'st petersburg': [59.934, 30.335], kyiv: [50.450, 30.523], istanbul: [41.008, 28.978],
  ankara: [39.933, 32.860], izmir: [38.423, 27.143],
  // Middle East & Africa
  dubai: [25.204, 55.271], 'abu dhabi': [24.454, 54.377], doha: [25.286, 51.534],
  riyadh: [24.713, 46.675], jeddah: [21.485, 39.192], kuwait: [29.376, 47.977],
  amman: [31.955, 35.945], beirut: [33.894, 35.502], cairo: [30.044, 31.236],
  alexandria: [31.200, 29.919], casablanca: [33.573, -7.590], rabat: [34.021, -6.841],
  tunis: [36.806, 10.181], lagos: [6.524, 3.379], abuja: [9.077, 7.399],
  accra: [5.603, -0.187], nairobi: [-1.292, 36.822], 'addis ababa': [9.032, 38.742],
  johannesburg: [-26.204, 28.047], 'cape town': [-33.925, 18.424], pretoria: [-25.748, 28.229],
  dakar: [14.717, -17.468], 'tel aviv': [32.085, 34.782],
  // East Asia & Oceania
  tokyo: [35.677, 139.650], osaka: [34.694, 135.502], nagoya: [35.181, 136.906],
  seoul: [37.567, 126.978], busan: [35.180, 129.075], taipei: [25.033, 121.565],
  'hong kong': [22.319, 114.170], sydney: [-33.869, 151.209], melbourne: [-37.814, 144.963],
  perth: [-31.951, 115.860], auckland: [-36.849, 174.763], wellington: [-41.287, 174.776],
  // South Asia & Central Asia
  karachi: [24.861, 67.010], lahore: [31.520, 74.359], islamabad: [33.694, 73.056],
  dhaka: [23.811, 90.412], chittagong: [22.357, 91.783], colombo: [6.927, 79.861],
  kathmandu: [27.717, 85.324], almaty: [43.222, 76.851], tashkent: [41.299, 69.240],
  chandigarh: [30.733, 76.779],
  // Remaining capitals referenced by the fallback
  kabul: [34.526, 69.181], tehran: [35.689, 51.389], baghdad: [33.315, 44.366],
  havana: [23.113, -82.366], caracas: [10.480, -66.904], quito: [-0.180, -78.467],
  bucharest: [44.427, 26.102], sofia: [42.698, 23.322], zagreb: [45.815, 15.982],
  harare: [-17.829, 31.054], 'la paz': [-16.490, -68.134], 'santo domingo': [18.472, -69.902],
  'guatemala city': [14.634, -90.507], 'san salvador': [13.692, -89.218],
  tegucigalpa: [14.072, -87.192], 'port-au-prince': [18.547, -72.340],
  kingston: [17.971, -76.793], 'san jose': [9.928, -84.081], algiers: [36.754, 3.059],
  tirana: [41.328, 19.818], yaounde: [3.867, 11.516]
}

// Capital city (gazetteer key) per country — geocoding fallback + generic
// embassy placement when no curated mission network exists for the pair.
const CAPITAL = {
  Afghanistan: 'kabul', Albania: 'tirana', Algeria: 'algiers', Argentina: 'buenos aires',
  Australia: 'sydney', Austria: 'vienna', Bangladesh: 'dhaka', Belgium: 'brussels',
  Bolivia: 'la paz', Brazil: 'brasilia', Bulgaria: 'sofia', Cambodia: 'phnom penh',
  Cameroon: 'yaounde', Canada: 'toronto', Chile: 'santiago', China: 'beijing',
  Colombia: 'bogota', 'Costa Rica': 'san jose', Croatia: 'zagreb', Cuba: 'havana',
  Czechia: 'prague', Denmark: 'copenhagen', 'Dominican Republic': 'santo domingo',
  Ecuador: 'quito', Egypt: 'cairo', 'El Salvador': 'san salvador', Ethiopia: 'addis ababa',
  Finland: 'helsinki', France: 'paris', Germany: 'berlin', Ghana: 'accra', Greece: 'athens',
  Guatemala: 'guatemala city', Haiti: 'port-au-prince', Honduras: 'tegucigalpa',
  'Hong Kong': 'hong kong', Hungary: 'budapest', India: 'new delhi', Indonesia: 'jakarta',
  Iran: 'tehran', Iraq: 'baghdad', Ireland: 'dublin', Israel: 'tel aviv', Italy: 'rome',
  Jamaica: 'kingston', Japan: 'tokyo', Jordan: 'amman', Kazakhstan: 'almaty', Kenya: 'nairobi',
  'South Korea': 'seoul', Kuwait: 'kuwait', Lebanon: 'beirut', Malaysia: 'kuala lumpur',
  Mexico: 'mexico city', Morocco: 'rabat', Nepal: 'kathmandu', Netherlands: 'amsterdam',
  'New Zealand': 'wellington', Nigeria: 'abuja', Norway: 'oslo', Pakistan: 'islamabad',
  Peru: 'lima', Philippines: 'manila', Poland: 'warsaw', Portugal: 'lisbon', Qatar: 'doha',
  Romania: 'bucharest', Russia: 'moscow', 'Saudi Arabia': 'riyadh', Senegal: 'dakar',
  Singapore: 'singapore', 'South Africa': 'pretoria', Spain: 'madrid', 'Sri Lanka': 'colombo',
  Sweden: 'stockholm', Switzerland: 'zurich', Taiwan: 'taipei', Thailand: 'bangkok',
  Tunisia: 'tunis', Turkey: 'ankara', Ukraine: 'kyiv', 'United Arab Emirates': 'abu dhabi',
  'United Kingdom': 'london', 'United States': 'washington', USA: 'washington',
  Uzbekistan: 'tashkent', Venezuela: 'caracas', Vietnam: 'hanoi', Zimbabwe: 'harare'
}

// --- Curated mission networks: destination -> origin country -> missions ---
// High-volume origin markets get the full consulate network with street
// addresses; each entry: [city, name, address]. Types: consulate unless noted.
const NETWORKS = {
  'South Korea': {
    China: [
      ['beijing', 'Embassy of the Republic of Korea', '20 Dongfang East Rd, Chaoyang District, Beijing'],
      ['shanghai', 'Consulate General of the Republic of Korea', '60 Wanshan Rd, Changning District, Shanghai'],
      ['guangzhou', 'Consulate General of the Republic of Korea', '18 Tiyu East Rd, Tianhe District, Guangzhou'],
      ['qingdao', 'Consulate General of the Republic of Korea', '101 Chunyang Rd, Chengyang District, Qingdao'],
      ['shenyang', 'Consulate General of the Republic of Korea', '37 Nanshisan Wei Rd, Heping District, Shenyang'],
      ['chengdu', 'Consulate General of the Republic of Korea', '19 Fourth Section, Renmin South Rd, Chengdu'],
      ['wuhan', 'Consulate General of the Republic of Korea', '4th Fl, Pojoin Bldg, 218 Xinhua Rd, Wuhan'],
      ["xi'an", 'Consulate General of the Republic of Korea', '37 Keji Rd, Yanta District, Xi\'an']
    ],
    India: [
      ['new delhi', 'Embassy of the Republic of Korea', '9 Chandragupta Marg, Chanakyapuri, New Delhi'],
      ['chennai', 'Consulate General of the Republic of Korea', '5th Fl, Bannari Amman Towers, 29 Dr Radhakrishnan Salai, Chennai'],
      ['mumbai', 'Consulate General of the Republic of Korea', 'Kanchenjunga Bldg, 72 Peddar Rd, Mumbai']
    ],
    Vietnam: [
      ['hanoi', 'Embassy of the Republic of Korea', '28th Fl, Lotte Center, 54 Lieu Giai St, Hanoi'],
      ['ho chi minh city', 'Consulate General of the Republic of Korea', '107 Nguyen Du St, District 1, Ho Chi Minh City']
    ]
  },
  Japan: {
    China: [
      ['beijing', 'Embassy of Japan', '1 Liangmaqiao East St, Chaoyang District, Beijing'],
      ['shanghai', 'Consulate-General of Japan', '8 Wanshan Rd, Changning District, Shanghai'],
      ['guangzhou', 'Consulate-General of Japan', 'Garden Tower, 368 Huanshi East Rd, Guangzhou'],
      ['chongqing', 'Consulate-General of Japan', '42nd Fl, Metropolitan Tower, 68 Zourong Rd, Chongqing'],
      ['shenyang', 'Consulate-General of Japan', '50 Shisi Wei Rd, Heping District, Shenyang'],
      ['qingdao', 'Consulate-General of Japan', '59 Xianggang Middle Rd, Qingdao'],
      ['dalian', 'Consulate-General of Japan (Dalian office)', 'Senmao Bldg, 147 Zhongshan Rd, Dalian'],
      ['hong kong', 'Consulate-General of Japan', '46-47F One Exchange Square, 8 Connaught Place, Central, Hong Kong']
    ],
    India: [
      ['new delhi', 'Embassy of Japan', 'Plot 4&5, 50-G Shantipath, Chanakyapuri, New Delhi'],
      ['mumbai', 'Consulate-General of Japan', '1 M.L. Dahanukar Marg, Cumballa Hill, Mumbai'],
      ['chennai', 'Consulate-General of Japan', '12/1 Cenotaph Rd, Teynampet, Chennai'],
      ['bengaluru', 'Consulate-General of Japan', '1st Fl, Prestige Nebula, 8-14 Cubbon Rd, Bengaluru'],
      ['kolkata', 'Consulate-General of Japan', '55 M.N. Sen Lane, Tollygunge, Kolkata']
    ]
  },
  USA: {
    China: [
      ['beijing', 'U.S. Embassy', '55 Anjialou Rd, Chaoyang District, Beijing'],
      ['shanghai', 'U.S. Consulate General', '1469 Huaihai Middle Rd, Xuhui District, Shanghai'],
      ['guangzhou', 'U.S. Consulate General', '43 Huajiu Rd, Zhujiang New Town, Guangzhou'],
      ['shenyang', 'U.S. Consulate General', '52 Shisi Wei Rd, Heping District, Shenyang'],
      ['wuhan', 'U.S. Consulate General', '568 Jianshe Ave, New World Intl Trade Tower, Wuhan'],
      ['hong kong', 'U.S. Consulate General', '26 Garden Rd, Central, Hong Kong']
    ],
    India: [
      ['new delhi', 'U.S. Embassy', 'Shantipath, Chanakyapuri, New Delhi'],
      ['mumbai', 'U.S. Consulate General', 'C-49, G Block, Bandra Kurla Complex, Mumbai'],
      ['chennai', 'U.S. Consulate General', '220 Anna Salai, Gemini Circle, Chennai'],
      ['hyderabad', 'U.S. Consulate General', 'Survey No 115/1, Financial District, Nanakramguda, Hyderabad'],
      ['kolkata', 'U.S. Consulate General', '5/1 Ho Chi Minh Sarani, Kolkata']
    ],
    Brazil: [
      ['brasilia', 'U.S. Embassy', 'SES Av. das Nações, Quadra 801, Lote 3, Brasília'],
      ['sao paulo', 'U.S. Consulate General', 'Rua Henri Dunant 500, Chácara Santo Antônio, São Paulo'],
      ['rio de janeiro', 'U.S. Consulate General', 'Av. Presidente Wilson 147, Castelo, Rio de Janeiro']
    ],
    Nigeria: [
      ['abuja', 'U.S. Embassy', '1075 Diplomatic Drive, Central District, Abuja'],
      ['lagos', 'U.S. Consulate General', '2 Walter Carrington Crescent, Victoria Island, Lagos']
    ]
  },
  France: {
    China: [
      ['beijing', 'Embassy of France (visa: TLScontact Beijing)', 'TLScontact, 12th Fl, Tower A, U-Town Office Bldg, Chaoyang District, Beijing'],
      ['shanghai', 'Consulate General of France (visa: TLScontact Shanghai)', 'TLScontact, 2nd Fl, Site A, 211 Shimen Yi Rd, Jing\'an District, Shanghai'],
      ['guangzhou', 'Consulate General of France (visa: TLScontact Guangzhou)', 'TLScontact, Room 201, 2nd Fl, Fuli Yingtong Bldg, 30 Huaxia Rd, Guangzhou'],
      ['chengdu', 'Consulate General of France (visa: TLScontact Chengdu)', 'TLScontact, 6th Fl, Square One, 18 Dongyu St, Jinjiang District, Chengdu'],
      ['wuhan', 'Consulate General of France (visa: TLScontact Wuhan)', 'TLScontact, 30th Fl, Wuhan Tiandi A1, 1628 Zhongshan Ave, Wuhan'],
      ['shenyang', 'Consulate General of France (visa: TLScontact Shenyang)', 'TLScontact, 11th Fl, Tower A, Chamber of Commerce Headquarters Bldg, Shenyang']
    ],
    India: [
      ['new delhi', 'Embassy of France (visa: VFS Global New Delhi)', 'VFS Global, Shivaji Stadium Metro Station, Baba Kharak Singh Marg, New Delhi'],
      ['mumbai', 'Consulate General of France (visa: VFS Global Mumbai)', 'VFS Global, Trade Centre, Bandra Kurla Complex, Mumbai'],
      ['bengaluru', 'Consulate General of France (visa: VFS Global Bengaluru)', 'VFS Global, Global Tech Park, O Shaughnessy Rd, Bengaluru']
    ]
  },
  Germany: {
    China: [
      ['beijing', 'German Embassy (visa: VFS Global Beijing)', 'VFS Global, Vantone Center, 6 Chaowai St, Chaoyang District, Beijing'],
      ['shanghai', 'German Consulate General (visa: VFS Global Shanghai)', 'VFS Global, 3rd Fl, Yunnan Rd 567, Huangpu District, Shanghai'],
      ['guangzhou', 'German Consulate General (visa: VFS Global Guangzhou)', 'VFS Global, 4th Fl, R&F Profit Plaza, 76 Huangpu Ave West, Guangzhou'],
      ['chengdu', 'German Consulate General (visa: VFS Global Chengdu)', 'VFS Global, 5th Fl, La Defense Bldg, 1480 Tianfu Ave North, Chengdu']
    ],
    India: [
      ['new delhi', 'German Embassy (visa: VFS Global New Delhi)', 'VFS Global, Shivaji Stadium Metro Station, New Delhi'],
      ['mumbai', 'German Consulate General (visa: VFS Global Mumbai)', 'VFS Global, Trade Centre, Bandra Kurla Complex, Mumbai']
    ]
  },
  'United Kingdom': {
    China: [
      ['beijing', 'UK Visa Application Centre (VFS Global)', '2nd Fl, Tower B, Gemdale Center, 91 Jianguo Rd, Chaoyang District, Beijing'],
      ['shanghai', 'UK Visa Application Centre (VFS Global)', '5th Fl, Sail Tower, 266 Hankou Rd, Huangpu District, Shanghai'],
      ['guangzhou', 'UK Visa Application Centre (VFS Global)', '2nd Fl, Chen Ju Bldg, 248 Yanjiang Middle Rd, Guangzhou'],
      ['chongqing', 'UK Visa Application Centre (VFS Global)', '32nd Fl, Metropolitan Tower, 68 Zourong Rd, Chongqing']
    ],
    India: [
      ['new delhi', 'UK Visa Application Centre (VFS Global)', 'Shivaji Stadium Metro Station, New Delhi'],
      ['mumbai', 'UK Visa Application Centre (VFS Global)', 'Trade Centre, Bandra Kurla Complex, Mumbai'],
      ['chennai', 'UK Visa Application Centre (VFS Global)', 'Fagun Towers, 74 Ethiraj Salai, Egmore, Chennai']
    ]
  },
  Canada: {
    China: [
      ['beijing', 'Canada Visa Application Centre (VFS Global)', '8th Fl, Tower B, Gemdale Center, Chaoyang District, Beijing'],
      ['shanghai', 'Canada Visa Application Centre (VFS Global)', '4th Fl, 555 West Nanjing Rd, Jing\'an District, Shanghai'],
      ['guangzhou', 'Canada Visa Application Centre (VFS Global)', '3rd Fl, R&F Profit Plaza, 76 Huangpu Ave West, Guangzhou']
    ],
    India: [
      ['new delhi', 'Canada Visa Application Centre (VFS Global)', 'Shivaji Stadium Metro Station, New Delhi'],
      ['chandigarh', 'Canada Visa Application Centre (VFS Global)', 'SCO 186-187, Sector 8-C, Chandigarh']
    ]
  },
  China: {
    'United States': [
      ['washington', 'Embassy of the People\'s Republic of China', '3505 International Place NW, Washington, DC'],
      ['new york', 'Consulate General of the PRC', '520 12th Ave, New York, NY'],
      ['san francisco', 'Consulate General of the PRC', '1450 Laguna St, San Francisco, CA'],
      ['los angeles', 'Consulate General of the PRC', '443 Shatto Place, Los Angeles, CA'],
      ['chicago', 'Consulate General of the PRC', '1 East Erie St, Chicago, IL']
    ]
  }
}

// MOFA-accredited Chinese travel agencies handling Japan tourist visas —
// the real filing channel for the China → Japan corridor.
const JAPAN_AGENCIES_CN = [
  ['beijing', 'CITS (China International Travel Service) — Japan visa desk', '1 Yongan Dongli, Jianguomenwai Ave, Beijing'],
  ['shanghai', 'Shanghai CYTS — Japan visa center', '100 Yan\'an East Rd, Huangpu District, Shanghai'],
  ['guangzhou', 'GZL International Travel — Japan visa desk', '10 Yuexiu South Rd, Guangzhou'],
  ['hangzhou', 'Zhejiang CITS — Japan visa desk', '1 Shihan Rd, Hangzhou'],
  ['chengdu', 'Sichuan CITS — Japan visa desk', '65 Renmin South Rd Section 2, Chengdu'],
  ['shenyang', 'Liaoning CITS — Japan visa desk', '113 Huanghe South St, Shenyang'],
  ['qingdao', 'Qingdao CITS — Japan visa desk', '73 Xianggang Middle Rd, Qingdao']
]

function haversineKm([lat1, lon1], [lat2, lon2]) {
  const R = 6371
  const toRad = (x) => (x * Math.PI) / 180
  const dLat = toRad(lat2 - lat1)
  const dLon = toRad(lon2 - lon1)
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2
  return Math.round(2 * R * Math.asin(Math.sqrt(a)))
}

// Match the free-text address against the gazetteer. Longest city-name match
// wins so "Ho Chi Minh City" beats "Chi". Falls back to the origin capital.
export function geocodeAddress(address, originCountry) {
  const hay = ` ${String(address || '').toLowerCase().replace(/[,，、.\-]/g, ' ').replace(/\s+/g, ' ')} `
  let best = null
  for (const [city, coords] of Object.entries(CITIES)) {
    if (hay.includes(` ${city} `) || hay.includes(city)) {
      if (!best || city.length > best.city.length) best = { city, coords }
    }
  }
  if (best) return { city: best.city, coords: best.coords, matched: true }
  const cap = CAPITAL[originCountry]
  if (cap && CITIES[cap]) return { city: cap, coords: CITIES[cap], matched: false }
  return null
}

const titleCase = (s) => String(s).replace(/\b\w/g, (c) => c.toUpperCase())

// Nearest mission of `destination` for a traveler of `originCountry` living
// at `address`. Returns { name, address, city, distanceKm, type, matchedCity }.
export function nearestMission(destination, originCountry, address) {
  const dest = destination === 'United States' ? 'USA' : destination
  const geo = geocodeAddress(address, originCountry)
  const curated = NETWORKS[dest]?.[originCountry === 'USA' ? 'United States' : originCountry]
  let candidates = (curated || []).map(([city, name, addr]) => ({
    city: titleCase(city), name, address: addr, coords: CITIES[city] || null, type: /visa application centre|vfs|tls|bls/i.test(name) ? 'vac' : /embassy/i.test(name) ? 'embassy' : 'consulate'
  }))
  if (!candidates.length) {
    const cap = CAPITAL[originCountry]
    const capCity = cap && CITIES[cap] ? titleCase(cap) : null
    candidates = [{
      city: capCity || 'the capital',
      name: `Embassy of ${destination === 'USA' ? 'the United States' : destination}`,
      address: capCity ? `${capCity}, ${originCountry}` : originCountry,
      coords: cap ? CITIES[cap] || null : null,
      type: 'embassy'
    }]
  }
  let best = candidates[0]
  let bestD = null
  if (geo) {
    for (const c of candidates) {
      if (!c.coords) continue
      const d = haversineKm(geo.coords, c.coords)
      if (bestD === null || d < bestD) { bestD = d; best = c }
    }
  }
  return {
    name: best.name,
    address: best.address,
    city: best.city,
    type: best.type,
    distanceKm: bestD,
    matchedCity: geo?.matched ? titleCase(geo.city) : null
  }
}

// Nearest accredited agency for agency-channel filings (Japan × China).
export function nearestAgency(destination, originCountry, address) {
  if (destination === 'Japan' && originCountry === 'China') {
    const geo = geocodeAddress(address, originCountry)
    let best = JAPAN_AGENCIES_CN[0]
    let bestD = null
    if (geo) {
      for (const a of JAPAN_AGENCIES_CN) {
        const coords = CITIES[a[0]]
        if (!coords) continue
        const d = haversineKm(geo.coords, coords)
        if (bestD === null || d < bestD) { bestD = d; best = a }
      }
    }
    return {
      name: best[1], address: best[2], city: titleCase(best[0]), type: 'agency',
      distanceKm: bestD, matchedCity: geo?.matched ? titleCase(geo.city) : null
    }
  }
  // Other agency-channel corridors: nearest mission stands in as the filing point.
  return nearestMission(destination, originCountry, address)
}
