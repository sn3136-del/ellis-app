// Public route text only: reuse exact English strings across reader/QC views.
// A translation belongs to its language and full source text, never a prefix.
export function translationSegments(text, limit = 900) {
  const pieces = []
  let rest = String(text)
  while (rest.length > limit) {
    const window = rest.slice(0, limit + 1)
    let cut = Math.max(window.lastIndexOf('. '), window.lastIndexOf('; '))
    if (cut < limit / 2) cut = window.lastIndexOf(' ')
    // An oversized URL/identifier is immutable source text. Keep it whole;
    // load/get pass it through locally instead of exposing a partial token.
    if (cut < 1) cut = rest.search(/\s/)
    if (cut < 1) cut = rest.length
    else if (window[cut] !== ' ') cut += 1
    pieces.push(rest.slice(0, cut).trim())
    rest = rest.slice(cut).trimStart()
  }
  if (rest) pieces.push(rest)
  return pieces
}

export function createValueTranslations({ storage = () => globalThis.localStorage } = {}) {
  const languages = new Map()
  const pending = new Map()
  const queued = []
  let active = 0
  function dispatch() {
    while (active < 2 && queued.length) {
      const { task, resolve, reject } = queued.shift()
      active += 1
      Promise.resolve().then(task).then(resolve, reject).finally(() => {
        active -= 1
        dispatch()
      })
    }
  }
  function schedule(task) {
    return new Promise((resolve, reject) => {
      queued.push({ task, resolve, reject })
      dispatch()
    })
  }
  function cache(lang) {
    if (!languages.has(lang)) {
      let entries = []
      try {
        const saved = JSON.parse(storage()?.getItem(`ellis.values.v3.${lang}`) || 'null')
        if (saved?.version === 3 && Array.isArray(saved.entries)) entries = saved.entries
      } catch { /* storage is optional */ }
      languages.set(lang, new Map(entries.filter((entry) => Array.isArray(entry)
        && entry.length === 2 && entry.every((v) => typeof v === 'string' && v.trim()))))
    }
    return languages.get(lang)
  }
  function persist(lang) {
    const values = cache(lang)
    while (values.size > 3000) values.delete(values.keys().next().value)
    try {
      storage()?.setItem(`ellis.values.v3.${lang}`, JSON.stringify({ version: 3,
        entries: [...values].slice(-1500) }))
    } catch { /* memory cache still works */ }
  }
  function get(lang, text) {
    if (lang === 'en') return text
    const translated = translationSegments(text).map((part) => part.length > 900 ? part : cache(lang).get(part))
    return translated.length && translated.every((part) => typeof part === 'string')
      ? translated.join(' ') : undefined
  }
  function snapshot(lang, texts) {
    return Object.fromEntries(texts.map((text) => [text, get(lang, text)])
      .filter(([, translated]) => translated !== undefined))
  }
  async function load(lang, texts, request) {
    if (lang === 'en') return snapshot(lang, texts)
    const values = cache(lang)
    if (!pending.has(lang)) pending.set(lang, new Map())
    const flights = pending.get(lang)
    const missing = [...new Set(texts.flatMap((text) => translationSegments(text)))].filter((text) => text.length <= 900 && !values.has(text))
    const owned = missing.filter((text) => !flights.has(text))
    // Register synchronously before dispatch, so simultaneous components and
    // StrictMode share the same work even when their entry keys differ.
    for (let offset = 0; offset < owned.length; offset += 120) {
      const batch = owned.slice(offset, offset + 120)
      const entries = Object.fromEntries(batch.map((text, i) => [`v${i}`, text]))
      const work = schedule(() => request(lang, entries)).then((out) => {
        if (!['ok', 'partial', 'passthrough'].includes(out?.status)) return
        batch.forEach((text, i) => {
          const value = out.entries?.[`v${i}`]
          if (typeof value === 'string' && value.trim() && (out.status === 'ok' || value !== text)) {
            values.set(text, value)
          }
        })
        persist(lang)
      }).catch(() => { /* failed strings remain eligible for a later request */ })
        .finally(() => batch.forEach((text) => {
          if (flights.get(text) === work) flights.delete(text)
        }))
      batch.forEach((text) => flights.set(text, work))
    }
    await Promise.all([...new Set(missing.map((text) => flights.get(text)).filter(Boolean))])
    return snapshot(lang, texts)
  }
  return { get, snapshot, load }
}

export const valueTranslations = createValueTranslations()
