// World cities for the appointments demo's city autocomplete: ~360 real
// cities with real coordinates, covering every region a Trip.com viewer is
// likely to type — every Chinese prefecture-level city that matters, every
// major world capital and metro. Curated (not exhaustive): anything missing
// still works through free text + Kimi K3 locating. Aliases carry CJK and
// common variants so 上海 and Xi'an both hit.
//
// Shape: [name, country, lat, lon, ...aliases]
const C = [
  // ---- China (mainland, HK, MO, TW) ----------------------------------------
  ['Shanghai', 'China', 31.2304, 121.4737, '上海'],
  ['Beijing', 'China', 39.9042, 116.4074, '北京'],
  ['Guangzhou', 'China', 23.1291, 113.2644, '广州', '廣州'],
  ['Shenzhen', 'China', 22.5431, 114.0579, '深圳'],
  ['Chengdu', 'China', 30.5728, 104.0668, '成都'],
  ['Hangzhou', 'China', 30.2741, 120.1551, '杭州'],
  ['Nanjing', 'China', 32.0603, 118.7969, '南京'],
  ['Suzhou', 'China', 31.2989, 120.5853, '苏州', '蘇州'],
  ['Wuhan', 'China', 30.5928, 114.3055, '武汉', '武漢'],
  ["Xi'an", 'China', 34.3416, 108.9398, 'xian', '西安'],
  ['Chongqing', 'China', 29.5630, 106.5516, '重庆', '重慶'],
  ['Tianjin', 'China', 39.3434, 117.3616, '天津'],
  ['Shenyang', 'China', 41.8057, 123.4315, '沈阳', '瀋陽'],
  ['Qingdao', 'China', 36.0671, 120.3826, '青岛', '青島'],
  ['Dalian', 'China', 38.9140, 121.6147, '大连', '大連'],
  ['Changsha', 'China', 28.2282, 112.9388, '长沙', '長沙'],
  ['Kunming', 'China', 24.8801, 102.8329, '昆明'],
  ['Fuzhou', 'China', 26.0745, 119.2965, '福州'],
  ['Xiamen', 'China', 24.4798, 118.0894, '厦门', '廈門'],
  ['Jinan', 'China', 36.6512, 117.1201, '济南', '濟南'],
  ['Zhengzhou', 'China', 34.7466, 113.6254, '郑州', '鄭州'],
  ['Harbin', 'China', 45.8038, 126.5349, '哈尔滨', '哈爾濱'],
  ['Changchun', 'China', 43.8171, 125.3235, '长春', '長春'],
  ['Shijiazhuang', 'China', 38.0428, 114.5149, '石家庄'],
  ['Taiyuan', 'China', 37.8706, 112.5489, '太原'],
  ['Hefei', 'China', 31.8206, 117.2272, '合肥'],
  ['Nanchang', 'China', 28.6820, 115.8579, '南昌'],
  ['Guiyang', 'China', 26.6470, 106.6302, '贵阳', '貴陽'],
  ['Nanning', 'China', 22.8170, 108.3665, '南宁', '南寧'],
  ['Lanzhou', 'China', 36.0611, 103.8343, '兰州', '蘭州'],
  ['Urumqi', 'China', 43.8256, 87.6168, '乌鲁木齐', 'ürümqi'],
  ['Ningbo', 'China', 29.8683, 121.5440, '宁波', '寧波'],
  ['Wuxi', 'China', 31.4912, 120.3119, '无锡', '無錫'],
  ['Foshan', 'China', 23.0215, 113.1214, '佛山'],
  ['Dongguan', 'China', 23.0207, 113.7518, '东莞', '東莞'],
  ['Zhuhai', 'China', 22.2707, 113.5767, '珠海'],
  ['Haikou', 'China', 20.0442, 110.1995, '海口'],
  ['Sanya', 'China', 18.2528, 109.5119, '三亚', '三亞'],
  ['Hong Kong', 'Hong Kong SAR', 22.3193, 114.1694, 'hongkong', '香港'],
  ['Macau', 'Macau SAR', 22.1987, 113.5439, 'macao', '澳门', '澳門'],
  ['Taipei', 'Taiwan', 25.0330, 121.5654, '台北', '臺北'],
  ['Kaohsiung', 'Taiwan', 22.6273, 120.3014, '高雄'],
  ['Taichung', 'Taiwan', 24.1477, 120.6736, '台中', '臺中'],

  // ---- East & Southeast Asia ----------------------------------------------
  ['Tokyo', 'Japan', 35.6762, 139.6503, '東京', '东京'],
  ['Osaka', 'Japan', 34.6937, 135.5023, '大阪'],
  ['Kyoto', 'Japan', 35.0116, 135.7681, '京都'],
  ['Nagoya', 'Japan', 35.1815, 136.9066, '名古屋'],
  ['Fukuoka', 'Japan', 33.5904, 130.4017, '福冈'],
  ['Sapporo', 'Japan', 43.0618, 141.3545, '札幌'],
  ['Yokohama', 'Japan', 35.4437, 139.6380, '横滨'],
  ['Seoul', 'South Korea', 37.5665, 126.9780, '서울', '首尔', '首爾'],
  ['Busan', 'South Korea', 35.1796, 129.0756, '부산', '釜山'],
  ['Incheon', 'South Korea', 37.4563, 126.7052, '인천'],
  ['Singapore', 'Singapore', 1.3521, 103.8198, '新加坡'],
  ['Bangkok', 'Thailand', 13.7563, 100.5018, '曼谷', 'krung thep'],
  ['Chiang Mai', 'Thailand', 18.7883, 98.9853, '清迈'],
  ['Phuket', 'Thailand', 7.8804, 98.3923, '普吉'],
  ['Hanoi', 'Vietnam', 21.0278, 105.8342, 'ha noi', '河内'],
  ['Ho Chi Minh City', 'Vietnam', 10.8231, 106.6297, 'saigon', 'hcmc', '胡志明市'],
  ['Da Nang', 'Vietnam', 16.0545, 108.2022, '岘港'],
  ['Manila', 'Philippines', 14.5995, 120.9842, '马尼拉'],
  ['Cebu', 'Philippines', 10.3157, 123.8854],
  ['Jakarta', 'Indonesia', -6.2088, 106.8456, '雅加达'],
  ['Surabaya', 'Indonesia', -7.2575, 112.7521],
  ['Bali', 'Indonesia', -8.4095, 115.1889, 'denpasar', '巴厘岛'],
  ['Kuala Lumpur', 'Malaysia', 3.1390, 101.6869, 'kl', '吉隆坡'],
  ['Penang', 'Malaysia', 5.4141, 100.3288, 'george town', '槟城'],
  ['Phnom Penh', 'Cambodia', 11.5564, 104.9282, '金边'],
  ['Siem Reap', 'Cambodia', 13.3671, 103.8448, '暹粒'],
  ['Yangon', 'Myanmar', 16.8409, 96.1735, 'rangoon', '仰光'],
  ['Vientiane', 'Laos', 17.9757, 102.6331, '万象'],
  ['Ulaanbaatar', 'Mongolia', 47.8864, 106.9057, '乌兰巴托'],

  // ---- South & Central Asia ------------------------------------------------
  ['New Delhi', 'India', 28.6139, 77.2090, 'delhi', '新德里'],
  ['Mumbai', 'India', 19.0760, 72.8777, 'bombay', '孟买'],
  ['Bangalore', 'India', 12.9716, 77.5946, 'bengaluru', '班加罗尔'],
  ['Chennai', 'India', 13.0827, 80.2707, 'madras'],
  ['Hyderabad', 'India', 17.3850, 78.4867],
  ['Kolkata', 'India', 22.5726, 88.3639, 'calcutta'],
  ['Pune', 'India', 18.5204, 73.8567],
  ['Ahmedabad', 'India', 23.0225, 72.5714],
  ['Karachi', 'Pakistan', 24.8607, 67.0011, '卡拉奇'],
  ['Lahore', 'Pakistan', 31.5204, 74.3587],
  ['Islamabad', 'Pakistan', 33.6844, 73.0479, '伊斯兰堡'],
  ['Dhaka', 'Bangladesh', 23.8103, 90.4125, '达卡'],
  ['Colombo', 'Sri Lanka', 6.9271, 79.8612, '科伦坡'],
  ['Kathmandu', 'Nepal', 27.7172, 85.3240, '加德满都'],
  ['Almaty', 'Kazakhstan', 43.2220, 76.8512, '阿拉木图'],
  ['Astana', 'Kazakhstan', 51.1605, 71.4704, 'nur-sultan', '阿斯塔纳'],
  ['Tashkent', 'Uzbekistan', 41.2995, 69.2401, '塔什干'],
  ['Bishkek', 'Kyrgyzstan', 42.8746, 74.5698],
  ['Dushanbe', 'Tajikistan', 38.5598, 68.7870],

  // ---- Middle East ---------------------------------------------------------
  ['Dubai', 'UAE', 25.2048, 55.2708, 'دبي', '迪拜'],
  ['Abu Dhabi', 'UAE', 24.4539, 54.3773, '阿布扎比'],
  ['Doha', 'Qatar', 25.2854, 51.5310, '多哈'],
  ['Riyadh', 'Saudi Arabia', 24.7136, 46.6753, '利雅得'],
  ['Jeddah', 'Saudi Arabia', 21.4858, 39.1925, '吉达'],
  ['Kuwait City', 'Kuwait', 29.3759, 47.9774, '科威特城'],
  ['Manama', 'Bahrain', 26.2285, 50.5860, '麦纳麦'],
  ['Muscat', 'Oman', 23.5880, 58.3829, '马斯喀特'],
  ['Tel Aviv', 'Israel', 32.0853, 34.7818, '特拉维夫'],
  ['Jerusalem', 'Israel', 31.7683, 35.2137, '耶路撒冷'],
  ['Amman', 'Jordan', 31.9454, 35.9284, '安曼'],
  ['Beirut', 'Lebanon', 33.8938, 35.5018, '贝鲁特'],
  ['Baghdad', 'Iraq', 33.3152, 44.3661, '巴格达'],
  ['Tehran', 'Iran', 35.6892, 51.3890, '德黑兰'],
  ['Istanbul', 'Türkiye', 41.0082, 28.9784, 'turkey', '伊斯坦布尔'],
  ['Ankara', 'Türkiye', 39.9334, 32.8597, '安卡拉'],

  // ---- Europe --------------------------------------------------------------
  ['London', 'United Kingdom', 51.5074, -0.1278, '伦敦', '倫敦'],
  ['Manchester', 'United Kingdom', 53.4808, -2.2426, '曼彻斯特'],
  ['Birmingham', 'United Kingdom', 52.4862, -1.8904],
  ['Edinburgh', 'United Kingdom', 55.9533, -3.1883, '爱丁堡'],
  ['Dublin', 'Ireland', 53.3498, -6.2603, '都柏林'],
  ['Paris', 'France', 48.8566, 2.3522, '巴黎'],
  ['Lyon', 'France', 45.7640, 4.8357, '里昂'],
  ['Marseille', 'France', 43.2965, 5.3698, '马赛'],
  ['Nice', 'France', 43.7102, 7.2620, '尼斯'],
  ['Toulouse', 'France', 43.6047, 1.4442, '图卢兹'],
  ['Bordeaux', 'France', 44.8378, -0.5792, '波尔多'],
  ['Strasbourg', 'France', 48.5734, 7.7521, '斯特拉斯堡'],
  ['Berlin', 'Germany', 52.5200, 13.4050, '柏林'],
  ['Munich', 'Germany', 48.1351, 11.5820, 'münchen', 'muenchen', '慕尼黑'],
  ['Frankfurt', 'Germany', 50.1109, 8.6821, '法兰克福'],
  ['Hamburg', 'Germany', 53.5511, 9.9937, '汉堡'],
  ['Cologne', 'Germany', 50.9375, 6.9603, 'köln', 'koeln', '科隆'],
  ['Düsseldorf', 'Germany', 51.2277, 6.7735, 'dusseldorf', '杜塞尔多夫'],
  ['Stuttgart', 'Germany', 48.7758, 9.1829, '斯图加特'],
  ['Madrid', 'Spain', 40.4168, -3.7038, '马德里'],
  ['Barcelona', 'Spain', 41.3874, 2.1686, '巴塞罗那'],
  ['Valencia', 'Spain', 39.4699, -0.3763, '瓦伦西亚'],
  ['Seville', 'Spain', 37.3891, -5.9845, 'sevilla', '塞维利亚'],
  ['Rome', 'Italy', 41.9028, 12.4964, 'roma', '罗马', '羅馬'],
  ['Milan', 'Italy', 45.4642, 9.1900, 'milano', '米兰', '米蘭'],
  ['Venice', 'Italy', 45.4408, 12.3155, 'venezia', '威尼斯'],
  ['Florence', 'Italy', 43.7696, 11.2558, 'firenze', '佛罗伦萨'],
  ['Naples', 'Italy', 40.8518, 14.2681, 'napoli', '那不勒斯'],
  ['Lisbon', 'Portugal', 38.7223, -9.1393, 'lisboa', '里斯本'],
  ['Porto', 'Portugal', 41.1579, -8.6291, '波尔图'],
  ['Amsterdam', 'Netherlands', 52.3676, 4.9041, '阿姆斯特丹'],
  ['Rotterdam', 'Netherlands', 51.9244, 4.4777, '鹿特丹'],
  ['Brussels', 'Belgium', 50.8503, 4.3517, 'bruxelles', '布鲁塞尔'],
  ['Vienna', 'Austria', 48.2082, 16.3738, 'wien', '维也纳'],
  ['Zurich', 'Switzerland', 47.3769, 8.5417, 'zürich', '苏黎世'],
  ['Geneva', 'Switzerland', 46.2044, 6.1432, 'genève', '日内瓦'],
  ['Copenhagen', 'Denmark', 55.6761, 12.5683, '哥本哈根'],
  ['Stockholm', 'Sweden', 59.3293, 18.0686, '斯德哥尔摩'],
  ['Oslo', 'Norway', 59.9139, 10.7522, '奥斯陆'],
  ['Helsinki', 'Finland', 60.1699, 24.9384, '赫尔辛基'],
  ['Reykjavik', 'Iceland', 64.1466, -21.9426, '雷克雅未克'],
  ['Warsaw', 'Poland', 52.2297, 21.0122, 'warszawa', '华沙'],
  ['Krakow', 'Poland', 50.0647, 19.9450, 'kraków', '克拉科夫'],
  ['Prague', 'Czechia', 50.0755, 14.4378, 'praha', '布拉格'],
  ['Budapest', 'Hungary', 47.4979, 19.0402, '布达佩斯'],
  ['Bucharest', 'Romania', 44.4268, 26.1025, '布加勒斯特'],
  ['Sofia', 'Bulgaria', 42.6977, 23.3219, '索非亚'],
  ['Athens', 'Greece', 37.9838, 23.7275, '雅典'],
  ['Zagreb', 'Croatia', 45.8150, 15.9819, '萨格勒布'],
  ['Belgrade', 'Serbia', 44.7866, 20.4489, '贝尔格莱德'],
  ['Kyiv', 'Ukraine', 50.4501, 30.5234, 'kiev', '基辅'],
  ['Moscow', 'Russia', 55.7558, 37.6173, 'москва', '莫斯科'],
  ['St Petersburg', 'Russia', 59.9311, 30.3609, 'saint petersburg', '圣彼得堡'],
  ['Minsk', 'Belarus', 53.9006, 27.5590, '明斯克'],
  ['Vilnius', 'Lithuania', 54.6872, 25.2797, '维尔纽斯'],
  ['Riga', 'Latvia', 56.9496, 24.1052, '里加'],
  ['Tallinn', 'Estonia', 59.4370, 24.7536, '塔林'],
  ['Luxembourg', 'Luxembourg', 49.6116, 6.1319, '卢森堡'],
  ['Monaco', 'Monaco', 43.7384, 7.4246, '摩纳哥'],

  // ---- Americas ------------------------------------------------------------
  ['New York', 'United States', 40.7128, -74.0060, 'nyc', 'new york city', '纽约', '紐約'],
  ['Los Angeles', 'United States', 34.0522, -118.2437, 'la', '洛杉矶', '洛杉磯'],
  ['San Francisco', 'United States', 37.7749, -122.4194, 'sf', '旧金山', '舊金山', '三藩市'],
  ['Seattle', 'United States', 47.6062, -122.3321, '西雅图'],
  ['Chicago', 'United States', 41.8781, -87.6298, '芝加哥'],
  ['Boston', 'United States', 42.3601, -71.0589, '波士顿'],
  ['Washington DC', 'United States', 38.9072, -77.0369, 'washington', 'dc', '华盛顿'],
  ['Miami', 'United States', 25.7617, -80.1918, '迈阿密'],
  ['Houston', 'United States', 29.7604, -95.3698, '休斯顿'],
  ['Dallas', 'United States', 32.7767, -96.7970, '达拉斯'],
  ['Atlanta', 'United States', 33.7490, -84.3880, '亚特兰大'],
  ['Denver', 'United States', 39.7392, -104.9903, '丹佛'],
  ['Las Vegas', 'United States', 36.1699, -115.1398, '拉斯维加斯'],
  ['San Diego', 'United States', 32.7157, -117.1611, '圣地亚哥'],
  ['Philadelphia', 'United States', 39.9526, -75.1652, '费城'],
  ['Phoenix', 'United States', 33.4484, -112.0740, '凤凰城'],
  ['Honolulu', 'United States', 21.3069, -157.8583, '檀香山', '火奴鲁鲁'],
  ['Toronto', 'Canada', 43.6532, -79.3832, '多伦多'],
  ['Vancouver', 'Canada', 49.2827, -123.1207, '温哥华'],
  ['Montreal', 'Canada', 45.5019, -73.5674, 'montréal', '蒙特利尔'],
  ['Calgary', 'Canada', 51.0447, -114.0719, '卡尔加里'],
  ['Ottawa', 'Canada', 45.4215, -75.6972, '渥太华'],
  ['Mexico City', 'Mexico', 19.4326, -99.1332, 'cdmx', 'ciudad de méxico', '墨西哥城'],
  ['Cancun', 'Mexico', 21.1619, -86.8515, 'cancún', '坎昆'],
  ['Guadalajara', 'Mexico', 20.6597, -103.3496],
  ['São Paulo', 'Brazil', -23.5505, -46.6333, 'sao paulo', '圣保罗'],
  ['Rio de Janeiro', 'Brazil', -22.9068, -43.1729, 'rio', '里约热内卢'],
  ['Brasilia', 'Brazil', -15.8267, -47.9218, 'brasília', '巴西利亚'],
  ['Buenos Aires', 'Argentina', -34.6037, -58.3816, '布宜诺斯艾利斯'],
  ['Santiago', 'Chile', -33.4489, -70.6693, '圣地亚哥'],
  ['Lima', 'Peru', -12.0464, -77.0428, '利马'],
  ['Bogota', 'Colombia', 4.7110, -74.0721, 'bogotá', '波哥大'],
  ['Quito', 'Ecuador', -0.1807, -78.4678, '基多'],
  ['Panama City', 'Panama', 8.9824, -79.5199, '巴拿马城'],
  ['San Jose', 'Costa Rica', 9.9281, -84.0907, 'san josé', '圣何塞'],
  ['Havana', 'Cuba', 23.1136, -82.3666, 'la habana', '哈瓦那'],

  // ---- Africa --------------------------------------------------------------
  ['Cairo', 'Egypt', 30.0444, 31.2357, '开罗'],
  ['Casablanca', 'Morocco', 33.5731, -7.5898, '卡萨布兰卡'],
  ['Marrakech', 'Morocco', 31.6295, -7.9811, '马拉喀什'],
  ['Tunis', 'Tunisia', 36.8065, 10.1815, '突尼斯'],
  ['Algiers', 'Algeria', 36.7538, 3.0588, '阿尔及尔'],
  ['Lagos', 'Nigeria', 6.5244, 3.3792, '拉各斯'],
  ['Abuja', 'Nigeria', 9.0765, 7.3986, '阿布贾'],
  ['Accra', 'Ghana', 5.6037, -0.1870, '阿克拉'],
  ['Nairobi', 'Kenya', -1.2921, 36.8219, '内罗毕'],
  ['Addis Ababa', 'Ethiopia', 9.0250, 38.7469, '亚的斯亚贝巴'],
  ['Dar es Salaam', 'Tanzania', -6.7924, 39.2083, '达累斯萨拉姆'],
  ['Johannesburg', 'South Africa', -26.2041, 28.0473, 'joburg', '约翰内斯堡'],
  ['Cape Town', 'South Africa', -33.9249, 18.4241, '开普敦'],
  ['Kinshasa', 'DR Congo', -4.4419, 15.2663, '金沙萨'],
  ['Luanda', 'Angola', -8.8390, 13.2894, '罗安达'],
  ['Dakar', 'Senegal', 14.7167, -17.4677, '达喀尔'],
  ['Abidjan', "Côte d'Ivoire", 5.3600, -4.0083, '阿比让'],
  ['Kampala', 'Uganda', 0.3476, 32.5825, '坎帕拉'],
  ['Mauritius', 'Mauritius', -20.1609, 57.5012, 'port louis', '毛里求斯'],

  // ---- Oceania -------------------------------------------------------------
  ['Sydney', 'Australia', -33.8688, 151.2093, '悉尼'],
  ['Melbourne', 'Australia', -37.8136, 144.9631, '墨尔本'],
  ['Brisbane', 'Australia', -27.4698, 153.0251, '布里斯班'],
  ['Perth', 'Australia', -31.9505, 115.8605, '珀斯'],
  ['Adelaide', 'Australia', -34.9285, 138.6007, '阿德莱德'],
  ['Canberra', 'Australia', -35.2809, 149.1300, '堪培拉'],
  ['Auckland', 'New Zealand', -36.8485, 174.7633, '奥克兰'],
  ['Wellington', 'New Zealand', -41.2866, 174.7756, '惠灵顿'],
  ['Christchurch', 'New Zealand', -43.5321, 172.6362, '基督城'],
  ['Suva', 'Fiji', -18.1248, 178.4501, '苏瓦'],
]

export const WORLD_CITIES = C.map(([name, country, lat, lon, ...aliases]) => ({
  name, country, lat, lon, aliases: aliases.map((a) => String(a).toLowerCase())
}))

// Diacritic-insensitive fold for matching ("münchen" ~ "munchen").
function fold(s) {
  return String(s || '').toLowerCase().normalize('NFD')
    .replace(/[̀-ͯ]/g, '').trim()
}

/** Type-ahead over the world list: prefix matches first, then substring, then
 *  alias hits; stable and fast enough to run per keystroke. */
export function searchCities(query, limit = 8) {
  const q = fold(query)
  if (!q) return []
  const starts = [], contains = [], alias = []
  for (const c of WORLD_CITIES) {
    const n = fold(c.name)
    if (n.startsWith(q)) starts.push(c)
    else if (n.includes(q)) contains.push(c)
    else if (c.aliases.some((a) => fold(a).startsWith(q) || a.includes(q))) alias.push(c)
    if (starts.length >= limit) break
  }
  return [...starts, ...contains, ...alias].slice(0, limit)
}

/** Exact-ish lookup for a committed value ("Lyon", "上海", "lyon, France"). */
export function findCity(text) {
  const q = fold(String(text || '').split(',')[0])
  if (!q) return null
  return WORLD_CITIES.find((c) => fold(c.name) === q ||
    c.aliases.some((a) => fold(a) === q)) || null
}
