// Embassy / portal discovery agent. For each corridor the agent searches the
// live web for the destination's official embassy or visa portal serving the
// traveler's country, opens the top official result, and has the LLM extract
// the application URL and registration steps. The findings — with the source
// URLs as evidence — are persisted on the trip and drive the "official
// portal" link in the app.
//
// Boundary: the agent finds, enters, and prepares; it never submits an
// application into a government site itself (no APIs, CAPTCHA-gated, and
// filing test data with a government would be a false statement).

const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Safari/605.1.15 EllisVisaAgent/1.0'

// Signals that a domain is the official government / mission / VAC channel.
const OFFICIAL_RE = /\.gov(\.|\/|$)|\.go\.(kr|jp|th|id|tz)|\.gouv\.|\.gob\.|\.gc\.ca|embassy|emb-|botschaft|ambassade|consulate|konsulat|mofa|mfa\.|evisa|e-visa|immigration|vfsglobal|tlscontact|blsinternational|europa\.eu|state\.gov|cbp\.dhs\.gov|canada\.ca|homeaffairs\.gov\.au/i

export async function searchWeb(query) {
  const res = await fetch('https://html.duckduckgo.com/html/?q=' + encodeURIComponent(query), {
    headers: { 'user-agent': UA },
    signal: AbortSignal.timeout(15000)
  })
  if (!res.ok) throw new Error('search http ' + res.status)
  const html = await res.text()
  const out = []
  const re = /<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/g
  let m
  while ((m = re.exec(html)) && out.length < 10) {
    let href = m[1]
    const uddg = href.match(/uddg=([^&]+)/)
    if (uddg) href = decodeURIComponent(uddg[1])
    const title = m[2].replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim()
    if (/^https?:\/\//.test(href) && !/duckduckgo\.com/.test(href)) out.push({ url: href, title })
  }
  return out
}

async function fetchPageText(url) {
  const res = await fetch(url, { headers: { 'user-agent': UA }, signal: AbortSignal.timeout(20000), redirect: 'follow' })
  if (!res.ok) throw new Error('page http ' + res.status)
  const html = await res.text()
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&[a-z#0-9]+;/gi, ' ')
    .replace(/\s+/g, ' ')
    .slice(0, 5000)
}

function scoreResult(r) {
  let s = 0
  if (OFFICIAL_RE.test(r.url)) s += 10
  if (/apply|application|visa/i.test(r.url + ' ' + r.title)) s += 3
  if (/wikipedia|tripadvisor|reddit|quora|facebook|youtube/i.test(r.url)) s -= 20
  return s
}

// llmText: async (prompt) => string|null — the caller supplies the engine
// chain (Kimi K3 → Claude → Ollama). Falls back to heuristics offline.
export async function discoverPortal(trip, plan, llmText) {
  // Use the classified visa type so the search lands on the right channel
  // (e.g. "C-3 short-term visitor visa" instead of the K-ETA page a generic
  // "Korea visa" query surfaces).
  const visaName = (plan.headline || 'tourist visa').replace(/\(.*?\)/g, '').trim()
  const channelWord = plan.channel === 'agency'
    ? `accredited travel agency ${visaName} application`
    : plan.kind === 'evisa' || plan.kind === 'eta'
      ? `official ${visaName} application portal`
      : `embassy ${visaName} application requirements`
  const query = `${trip.destination} ${channelWord} for ${trip.nationality} citizens official site`
  const results = (await searchWeb(query)).map((r) => ({ ...r, score: scoreResult(r) }))
    .sort((a, b) => b.score - a.score)
  if (!results.length) throw new Error('no search results')
  const top = results.slice(0, 3)

  let pageText = ''
  let fetched = null
  for (const r of top) {
    try { pageText = await fetchPageText(r.url); fetched = r.url; break } catch { /* next */ }
  }

  let extracted = null
  let engine = 'heuristic'
  if (llmText && pageText) {
    try {
      const prompt = `A ${trip.nationality} tourist needs a ${trip.destination} visa (${plan.headline}). Below are web search results and the text of the top official page. Identify the OFFICIAL application channel.\n\nSearch results:\n${top.map((r, i) => `${i + 1}. ${r.title} — ${r.url}`).join('\n')}\n\nPage text from ${fetched}:\n${pageText.slice(0, 3500)}\n\nRespond with ONLY JSON: {"officialUrl":"the official embassy/portal homepage","applyUrl":"the most direct application/registration URL (may equal officialUrl)","steps":["step 1","step 2","step 3"],"notes":"one sentence on how this channel works"}`
      const text = await llmText(prompt)
      const m = text && text.match(/\{[\s\S]*\}/)
      if (m) {
        const parsed = JSON.parse(m[0])
        if (parsed.officialUrl) { extracted = parsed; engine = 'llm' }
      }
    } catch { /* heuristic fallback below */ }
  }
  if (!extracted) {
    extracted = {
      officialUrl: top[0].url,
      applyUrl: top.find((r) => /apply|application|evisa|e-visa/i.test(r.url))?.url || top[0].url,
      steps: ['Open the official portal', 'Register / start a tourist-visa application', 'Use the Ellis application package for every field'],
      notes: `Top official result for ${trip.destination} (${top[0].title}).`
    }
  }
  return {
    query,
    results: top.map(({ url, title }) => ({ url, title })),
    fetchedFrom: fetched,
    officialUrl: extracted.officialUrl,
    applyUrl: extracted.applyUrl || extracted.officialUrl,
    steps: Array.isArray(extracted.steps) ? extracted.steps.slice(0, 5) : [],
    notes: String(extracted.notes || ''),
    engine,
    at: Date.now()
  }
}
