// A translation belongs to the exact English key/value catalog it translated.
// Keep the full canonical signature so cache equality cannot hide a changed key.
export function catalogSignature(entries) {
  return JSON.stringify(Object.keys(entries).sort().map((key) => [key, entries[key]]))
}

const PREFIX = 'ellis.cat.v2.'
const LEGACY_PREFIX = 'ellis.cat.v1.'

export function createCatalogLoader({ entries, defaultLanguage = 'en', request,
  install, bundled = {}, storage = () => globalThis.localStorage }) {
  const source = Object.freeze({ ...entries })
  const signature = catalogSignature(source)
  const ready = new Set()
  const inFlight = new Map()

  function validEntries(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null
    const result = Object.fromEntries(Object.entries(value).filter(([key, text]) =>
      Object.hasOwn(source, key) && typeof text === 'string' && text.trim()))
    return Object.keys(result).length ? result : null
  }

  function localEntries(lang) {
    return validEntries(bundled[lang]) || {}
  }
  function missingEntries(lang) {
    const local = localEntries(lang)
    return Object.fromEntries(Object.entries(source).filter(([key]) => !Object.hasOwn(local, key)))
  }

  function cached(lang) {
    try {
      const store = storage()
      // v1 stored no source catalog, so even matching keys cannot prove that
      // their English meaning has stayed the same. Use the shipped fallback.
      store?.removeItem(LEGACY_PREFIX + lang)
      const value = JSON.parse(store?.getItem(PREFIX + lang) || 'null')
      if (value?.version !== 2 || value.source !== signature) return null
      return validEntries(value.entries)
    } catch { return null }
  }

  function needsLoad(lang) {
    return lang !== defaultLanguage && !ready.has(lang) && Object.keys(missingEntries(lang)).length > 0 && !cached(lang)
  }

  function load(lang) {
    if (lang === defaultLanguage || ready.has(lang)) return Promise.resolve(true)
    if (inFlight.has(lang)) return inFlight.get(lang)
    const local = localEntries(lang)
    const missing = missingEntries(lang)
    // Maintained Chinese strings ship with this exact application build.
    // Do not replace them with an older browser overlay or pay to translate
    // all labels again before the language switch can complete.
    if (!Object.keys(missing).length) {
      install(lang, local)
      ready.add(lang)
      return Promise.resolve(true)
    }
    const saved = cached(lang)
    if (saved) {
      install(lang, { ...saved, ...local })
      ready.add(lang)
      return Promise.resolve(true)
    }
    // No unversioned in-memory overlay may outlive its source catalog either.
    install(lang, local)
    const pending = Promise.resolve().then(async () => {
      const response = await request(lang, missing)
      if (!response || !['ok', 'partial', 'passthrough'].includes(response.status)) return false
      const translated = validEntries(response.entries)
      if (!translated) return false
      const complete = { ...translated, ...local }
      install(lang, complete)
      ready.add(lang)
      try {
        storage()?.setItem(PREFIX + lang, JSON.stringify({ version: 2, source: signature, entries: complete }))
      } catch { /* memory catalog and shipped fallback still work without storage */ }
      return true
    }).catch(() => false).finally(() => {
      if (inFlight.get(lang) === pending) inFlight.delete(lang)
    })
    inFlight.set(lang, pending)
    return pending
  }

  return { load, needsLoad }
}

// An old language request may fill its own cache, but cannot change a later
// choice, its spinner, or its failure fallback.
export function createCatalogSelection({ loader, isSupported, hasStaticFallback,
  defaultLanguage = 'en', onLanguage, onPending, onReady }) {
  let generation = 0
  async function select(lang) {
    if (!isSupported(lang)) return false
    const current = ++generation
    onLanguage(lang)
    onPending(loader.needsLoad(lang))
    let ok = false
    try { ok = await loader.load(lang) } catch { /* keep the honest fallback */ }
    if (current !== generation) return ok
    if (!ok && !hasStaticFallback(lang)) onLanguage(defaultLanguage)
    onReady()
    onPending(false)
    return ok
  }
  return { select, cancel: () => { generation += 1 } }
}
