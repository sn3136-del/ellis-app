// Locale context: holds the current UI language, persists the choice, and
// exposes a t() bound to it. The LanguageToggle is the visible switch required
// by Phase 6 (English / Simplified Chinese / Traditional Chinese).
import { createContext, useContext, useState, useCallback } from 'react'
import { t as translate, SUPPORTED, LANGUAGE_NAMES, DEFAULT_LANG, isSupported } from './i18n.js'

const LocaleContext = createContext({ lang: DEFAULT_LANG, setLang: () => {}, t: (k) => k })

function readStored() {
  try {
    const v = typeof localStorage !== 'undefined' && localStorage.getItem('ellis.locale')
    return v && isSupported(v) ? v : DEFAULT_LANG
  } catch { return DEFAULT_LANG }
}

export function LocaleProvider({ children }) {
  const [lang, setLangState] = useState(readStored)
  const setLang = useCallback((l) => {
    if (!isSupported(l)) return
    setLangState(l)
    try { localStorage.setItem('ellis.locale', l) } catch { /* non-fatal */ }
  }, [])
  const t = useCallback((key, vars) => translate(lang, key, vars), [lang])
  return <LocaleContext.Provider value={{ lang, setLang, t }}>{children}</LocaleContext.Provider>
}

export function useLocale() {
  return useContext(LocaleContext)
}

// A bound t() hook for components that only need translation.
export function useT() {
  return useContext(LocaleContext).t
}

// The visible language switch.
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
