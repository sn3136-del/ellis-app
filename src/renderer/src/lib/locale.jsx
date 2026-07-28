// Locale context: holds the current UI language, persists the choice, and
// exposes a t() bound to it. English / Simplified / Traditional Chinese ship
// as static maintained catalogs; EVERY other language is dynamic — the
// English catalog is translated on the backend by Kimi K3 (masked, cached,
// honest English fallback) and overlaid at runtime. RTL languages flip the
// document direction.
import { createContext, useContext, useState, useCallback, useEffect } from 'react'
import {
  t as translate, SUPPORTED, LANGUAGE_NAMES, ALL_LANGUAGE_NAMES, RTL_LANGS,
  DEFAULT_LANG, isSupported, STRINGS, setDynamicCatalog, hasDynamicCatalog,
} from './i18n.js'

const LocaleContext = createContext({
  lang: DEFAULT_LANG, setLang: () => {}, t: (k) => k, translating: false,
})

const CATALOG_CACHE_PREFIX = 'ellis.cat.v1.'

function readStored() {
  try {
    const v = typeof localStorage !== 'undefined' && localStorage.getItem('ellis.locale')
    return v && isSupported(v) ? v : DEFAULT_LANG
  } catch { return DEFAULT_LANG }
}

function applyDirection(lang) {
  try {
    document.documentElement.dir = RTL_LANGS.includes(lang) ? 'rtl' : 'ltr'
    document.documentElement.lang = lang
  } catch { /* non-fatal */ }
}

function cachedCatalog(lang) {
  try {
    const cached = localStorage.getItem(CATALOG_CACHE_PREFIX + lang)
    if (cached) return JSON.parse(cached)
  } catch { /* unreadable cache: refetch */ }
  return null
}

async function loadDynamicCatalog(lang) {
  // EVERY non-English language gets the Kimi K3 catalog — including Chinese,
  // whose shipped static strings render instantly and then upgrade key-by-key
  // when the dynamic catalog lands (the overlay in i18n.t()).
  if (lang === DEFAULT_LANG || hasDynamicCatalog(lang)) return true
  const cached = cachedCatalog(lang)
  if (cached) {
    setDynamicCatalog(lang, cached)
    return true
  }
  // A REAL session: the backend needs org/user headers, and a null session
  // sends them empty (401 — the reason the switch silently did nothing).
  const [{ createVisaClient }, { newSession }] = await Promise.all([
    import('./visaBackend.js'), import('./visaSession.js'),
  ])
  const client = createVisaClient(newSession())
  const res = await client.i18nCatalog(lang, STRINGS[DEFAULT_LANG])
  if (!res || res.status === 'unavailable' || res.status === 'unsupported_language') {
    return false      // stay honest: keep English rather than fabricating
  }
  setDynamicCatalog(lang, res.entries || {})
  try { localStorage.setItem(CATALOG_CACHE_PREFIX + lang, JSON.stringify(res.entries || {})) } catch { /* quota */ }
  return true
}

export function LocaleProvider({ children }) {
  const [lang, setLangState] = useState(readStored)
  const [translating, setTranslating] = useState(false)
  const [, bump] = useState(0)

  // Direction follows the language ACTUALLY in effect: a dynamic locale whose
  // catalog failed to load still renders English, and English must not be
  // laid out right-to-left.
  useEffect(() => {
    const effective = (SUPPORTED.includes(lang) || hasDynamicCatalog(lang)) ? lang : DEFAULT_LANG
    applyDirection(effective)
  }, [lang, translating])
  // Rehydrate a persisted non-English locale on boot (Chinese included: its
  // static strings show at once, the Kimi catalog overlays when loaded).
  useEffect(() => {
    if (lang !== DEFAULT_LANG && !hasDynamicCatalog(lang)) {
      loadDynamicCatalog(lang)
        .then((ok) => { if (ok) bump((n) => n + 1) })
        .catch(() => { /* static/en fallback stays */ })
    }
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  const setLang = useCallback(async (l) => {
    if (!isSupported(l)) return
    const needsFetch = l !== DEFAULT_LANG && !hasDynamicCatalog(l) && !cachedCatalog(l)
    const hasStaticFallback = SUPPORTED.includes(l)
    // OPTIMISTIC SWITCH: apply the language immediately and translate in the
    // background. A first-time language is ~600 strings of live translation —
    // blocking on it made the picker look broken (nothing happened for
    // minutes). Untranslated keys fall back to English and swap in as they
    // land; the result is cached, so every later switch is instant.
    setLangState(l)
    try { localStorage.setItem('ellis.locale', l) } catch { /* non-fatal */ }
    if (!needsFetch) {
      if (l !== DEFAULT_LANG) await loadDynamicCatalog(l).catch(() => false)
      bump((n) => n + 1)
      return
    }
    setTranslating(true)
    try {
      const ok = await loadDynamicCatalog(l)
      bump((n) => n + 1)
      if (!ok && !hasStaticFallback) {
        // Honestly unavailable: fall back to English rather than leave the
        // applicant on a language the app cannot actually render.
        setLangState(DEFAULT_LANG)
        try { localStorage.setItem('ellis.locale', DEFAULT_LANG) } catch { /* non-fatal */ }
      }
    } catch {
      // Chinese keeps its static catalog; a dynamic-only language falls back
      // to English rather than stranding the applicant.
      if (!hasStaticFallback) {
        setLangState(DEFAULT_LANG)
        try { localStorage.setItem('ellis.locale', DEFAULT_LANG) } catch { /* non-fatal */ }
      }
    } finally {
      setTranslating(false)
    }
  }, [])
  const t = useCallback((key, vars) => translate(lang, key, vars), [lang])
  return (
    <LocaleContext.Provider value={{ lang, setLang, t, translating }}>
      {children}
    </LocaleContext.Provider>
  )
}

export function useLocale() {
  return useContext(LocaleContext)
}

// A bound t() hook for components that only need translation.
export function useT() {
  return useContext(LocaleContext).t
}

// Compact pill toggle (legacy consumer support: the three static locales).
export function LanguageToggle({ compact = false }) {
  const { lang, setLang } = useLocale()
  return (
    <div className="lang-toggle" role="group" aria-label="Language"
         style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
      {SUPPORTED.map((code) => (
        <button key={code} type="button" onClick={() => setLang(code)}
                aria-pressed={lang === code}
                className={'btn btn--sm' + (lang === code ? '' : ' btn--ghost')}
                style={{ fontSize: 11, padding: compact ? '2px 6px' : '4px 8px' }}>
          {compact ? code.replace('zh-', '') : LANGUAGE_NAMES[code]}
        </button>
      ))}
    </div>
  )
}

// The full language picker (top bar): every supported language; dynamic ones
// are translated live by the backend the first time they are chosen.
export function LanguagePicker() {
  const { lang, setLang, translating } = useLocale()
  const [open, setOpen] = useState(false)
  useEffect(() => {
    if (!open) return undefined
    const close = () => setOpen(false)
    window.addEventListener('click', close)
    return () => window.removeEventListener('click', close)
  }, [open])
  return (
    <div className="langpick" onClick={(e) => e.stopPropagation()}>
      <button type="button" className="langpick__btn" data-testid="language-picker"
              aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             strokeWidth="2" aria-hidden="true">
          <circle cx="12" cy="12" r="9" />
          <path d="M3 12h18M12 3c2.5 2.6 3.8 5.7 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.7-3.8-9S9.5 5.6 12 3z" />
        </svg>
        {ALL_LANGUAGE_NAMES[lang] || lang}
        {translating && <span className="langpick__spin" aria-label="Translating" />}
      </button>
      {open && (
        <div className="langpick__menu" role="menu">
          {Object.entries(ALL_LANGUAGE_NAMES).map(([code, name]) => (
            <button key={code} type="button" role="menuitem" className="langpick__item"
                    aria-pressed={lang === code}
                    onClick={() => { setOpen(false); setLang(code) }}>
              <span>{name}</span>
              {lang === code && <span aria-hidden="true">✓</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
