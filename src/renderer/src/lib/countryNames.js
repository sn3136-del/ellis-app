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

// Everyday names the registry's FORMAL names don't contain as substrings:
// 韩国 must find 大韩民国, "South Korea" must find "Korea, Republic of".
// Substring search already covers prefixes (朝鲜→朝鲜民主主义人民共和国),
// so only the genuinely unreachable names are listed.
const COMMON_ALIASES = {
  KOR: '韩国 南韩 韓國 南韓 south korea 首尔 首爾',
  PRK: '北韩 北朝鲜 北韓 north korea',
  ARE: '阿联酋 阿聯酋 迪拜 杜拜 阿布扎比 阿布達比 uae dubai abu dhabi',
  USA: 'america united states of america 美利坚',
  GBR: 'britain england great britain scotland wales 大不列颠',
  RUS: '俄罗斯 russia',
  VNM: 'vietnam',
  CZE: 'czech republic',
  NLD: 'holland',
  MAC: 'macau 澳门 澳門',
  HKG: '香港',
  MMR: 'burma',
  CIV: 'ivory coast cote divoire',
  SWZ: 'swaziland 斯威士兰',
  TLS: 'east timor 东帝汶 東帝汶',
  COD: '刚果金 刚果（金） dr congo drc',
  COG: '刚果布 刚果（布）',
  AUS: '澳洲',
  NZL: '纽西兰',
  SAU: '沙地阿拉伯',
  TUR: 'turkey',
  LAO: '寮国 寮國 laos',
}

export function useLocalizedCountries(client, reg, lang) {
  const [zh, setZh] = useState(null)

  useEffect(() => {
    if (!reg || lang === 'en') { setZh(null); return }
    // The registry ships standardized names for the Chinese locales — no
    // network round-trip, no wait, no model in the loop.
    const key = lang === 'zh-Hant' ? 'name_hant' : 'name_zh'
    if ((reg.countries || []).some((c) => c[key])) {
      const m = {}
      reg.countries.forEach((c) => { if (c[key]) m[c.alpha_3] = c[key] })
      setZh(m)
      return
    }
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
      // Both scripts are searchable in EVERY locale: an ops user typing
      // 中国 with the UI in English still deserves the match.
      search: `${c.name} ${local || ''} ${c.name_zh || ''} ${c.name_hant || ''} ${COMMON_ALIASES[c.alpha_3] || ''} ${c.alpha_2 || ''} ${c.alpha_3}`.toLowerCase(),
    }
  }), [reg, zh])
}
