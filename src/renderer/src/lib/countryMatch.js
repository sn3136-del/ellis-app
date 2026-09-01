// One resolver for "which country did the user mean", shared by every
// filter that turns free text or a committed code back into a country row.
//
// Why this exists: the quality console used to take the FIRST country whose
// search string merely CONTAINED the query. "KOR" is a substring of
// "Korea, Democratic People's Republic of", which sorts before
// "Korea, Republic of", so filtering South Korea returned North Korean
// records, and "IND" matched "British Indian Ocean Territory" before India,
// so an India filter matched nothing. Trip.com's 2026-08-31 evaluation
// called the first of these a serious error. Exactness must outrank
// position in the list, which is what the tiers below guarantee.
//
// Tiers, strongest first:
//   100  alpha-3 code, exactly ("KOR")
//    90  display name, exactly ("India", "Korea, Republic of")
//    80  a whole token of the search string ("kr", "korea", "韩国")
//    70  display name starts with the query ("korea, rep")
//    50  query appears anywhere in the search string ("south kor")
// Ties inside a tier go to the SHORTER display name, so plain "korea"
// resolves to Korea, Republic of, matching the ask box's alias table.

const strip = (s) => String(s || '').replace(/^[^\p{L}\p{N}]+/u, '').trim()

export function matchCountry(countries, raw) {
  const t = strip(raw).toLowerCase()
  if (!t) return null
  let best = null
  let bestScore = -1
  for (const c of countries || []) {
    const name = strip(c.label).toLowerCase()
    let tier = -1
    if (c.value.toLowerCase() === t) tier = 100
    else if (name === t) tier = 90
    else if (c.search.split(/\s+/).includes(t)) tier = 80
    else if (name.startsWith(t)) tier = 70
    else if (c.search.includes(t)) tier = 50
    if (tier < 0) continue
    const score = tier * 1000 - Math.min(name.length, 999)
    if (score > bestScore) { bestScore = score; best = { country: c, tier } }
  }
  return best
}

// The strict form filters use while text may still be half-typed. Each tier
// commits only when it names ONE country: an exact alpha-3, an exact display
// name, a token no other country carries (alpha-2 codes, 韩国), or the only
// substring match. Bare "korea" or "united" stays ambiguous here and the
// suggestion list disambiguates instead.
export function matchCountryStrict(countries, raw) {
  const t = strip(raw).toLowerCase()
  if (!t) return null
  const list = countries || []
  const vals = list.filter((c) => c.value.toLowerCase() === t)
  if (vals.length === 1) return vals[0]
  const names = list.filter((c) => strip(c.label).toLowerCase() === t)
  if (names.length === 1) return names[0]
  const tokens = list.filter((c) => c.search.split(/\s+/).includes(t))
  if (tokens.length === 1) return tokens[0]
  const pool = list.filter((c) => c.search.includes(t))
  return pool.length === 1 ? pool[0] : null
}
