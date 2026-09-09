// Locale context: holds the current UI language, persists the choice, and
// exposes a t() bound to it. English / Simplified / Traditional Chinese ship
// as static maintained catalogs; EVERY non-English language is dynamic — the
// English catalog is translated on the backend by Kimi K3 (masked, cached,
// honest English fallback) and overlaid at runtime. RTL languages flip the
// document direction.
import { createContext, useContext, useState, useCallback, useEffect, useMemo } from 'react'
import {
  t as translate, SUPPORTED, LANGUAGE_NAMES, ALL_LANGUAGE_NAMES, RTL_LANGS,
  DEFAULT_LANG, isSupported, STRINGS, setDynamicCatalog, hasDynamicCatalog,
} from './i18n.js'
import { createCatalogLoader, createCatalogSelection } from './catalogLoader.js'

const LocaleContext = createContext({
  lang: DEFAULT_LANG, setLang: () => {}, t: (k) => k, translating: false,
})

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

const catalogLoader = createCatalogLoader({
  entries: STRINGS[DEFAULT_LANG], defaultLanguage: DEFAULT_LANG,
  install: setDynamicCatalog,
  request: async (lang, entries) => {
    // Every non-English language, including both Chinese locales, keeps its
    // dynamic overlay. The backend reuses unchanged per-string translations.
    const [{ createVisaClient }, { newSession }] = await Promise.all([
      import('./visaBackend.js'), import('./visaSession.js'),
    ])
    return createVisaClient(newSession()).i18nCatalog(lang, entries)
  },
})

export function LocaleProvider({ children }) {
  const [lang, setLangState] = useState(readStored)
  const [translating, setTranslating] = useState(false)
  const [catalogRevision, bump] = useState(0)

  // Direction follows the language ACTUALLY in effect: a dynamic locale whose
  // catalog failed to load still renders English, and English must not be
  // laid out right-to-left.
  useEffect(() => {
    const effective = (SUPPORTED.includes(lang) || hasDynamicCatalog(lang)) ? lang : DEFAULT_LANG
    applyDirection(effective)
  }, [lang, translating, catalogRevision])
  const selection = useMemo(() => createCatalogSelection({
    loader: catalogLoader, isSupported,
    hasStaticFallback: (code) => SUPPORTED.includes(code), defaultLanguage: DEFAULT_LANG,
    onLanguage: (code) => {
      setLangState(code)
      try { localStorage.setItem('ellis.locale', code) } catch { /* non-fatal */ }
    },
    onPending: setTranslating,
    onReady: () => bump((n) => n + 1),
  }), [])
  // Rehydrate the exact current catalog on boot. StrictMode's repeated effect
  // shares the same request; cleanup prevents a late completion changing state.
  useEffect(() => {
    selection.select(lang)
    return () => selection.cancel()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const setLang = useCallback((code) => selection.select(code), [selection])
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
        {LANGUAGE_NAMES[lang] || lang}
        {translating && <span className="langpick__spin" aria-label="Translating" />}
      </button>
      {open && (
        <div className="langpick__menu" role="menu">
          {Object.entries(LANGUAGE_NAMES).map(([code, name]) => (
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
