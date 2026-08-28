// Country names in the UI language.
//
// The registry ships English names. For Chinese operators the pickers must
// read 中国, not "China" — so when the language is not English the whole
// name list is translated ONCE through the backend catalog (masked, cached
// server-side) and kept in localStorage per language. Until the catalog
// lands the English names show, then the localized ones swap in. The search
// string always carries BOTH forms, so typing 中国 or China both match.
import { useEffect, useMemo, useState } from 'react'

const LS = (lang) => `ellis.countrynames.v1.${lang}`

export function useLocalizedCountries(client, reg, lang) {
  const [zh, setZh] = useState(null)

  useEffect(() => {
    if (!reg || lang === 'en') { setZh(null); return }
    try {
      const cached = localStorage.getItem(LS(lang))
      if (cached) { setZh(JSON.parse(cached)); return }
    } catch { /* refetch */ }
    const names = (reg.countries || [])
    if (!names.length) return
    const entries = {}
    names.forEach((c) => { entries[c.alpha_3] = c.name })
    let live = true
    client.i18nCatalog(lang, entries).then((out) => {
      if (!live || !out?.entries) return
      setZh(out.entries)
      try { localStorage.setItem(LS(lang), JSON.stringify(out.entries)) } catch { /* quota */ }
    }).catch(() => { /* English stays */ })
    return () => { live = false }
  }, [client, reg, lang])

  return useMemo(() => (reg?.countries || []).map((c) => {
    const local = zh?.[c.alpha_3]
    return {
      value: c.alpha_3,
      label: `${c.flag ? c.flag + ' ' : ''}${local || c.name}`,
      search: `${c.name} ${local || ''} ${c.alpha_2 || ''} ${c.alpha_3}`.toLowerCase(),
    }
  }), [reg, zh])
}
