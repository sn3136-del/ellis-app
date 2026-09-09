// Share the request context; dates select deterministic policy overlays.
const iso3 = (s) => /^[A-Z]{3}$/.test(s || '')
function dateValue(s) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s || '')) return ''
  const d = new Date(`${s}T00:00:00Z`)
  return !Number.isNaN(d.valueOf()) && d.toISOString().slice(0, 10) === s ? s : ''
}
export function parseDatabaseRouteHash(hash, purposes = ['tourism']) {
  const m = String(hash || '').replace(/^#\/?/, '').split('/')
  if ((m[0] || '').toLowerCase() !== 'database') return null
  const nat = (m[1] || '').toUpperCase(), dest = (m[2] || '').toUpperCase()
  if (!iso3(nat) || !iso3(dest)) return null
  const tail = m.slice(5)
  const via = tail.indexOf('via'), on = tail.indexOf('on')
  return { nat, dest, purpose: purposes.includes(m[3]) ? m[3] : 'tourism',
    doc: /^[a-z_]+$/.test(m[4] || '') ? m[4] : 'ordinary_passport',
    transit: via >= 0 ? [...new Set((tail[via + 1] || '').split(',').filter(iso3))].sort() : [],
    arrival: on >= 0 ? dateValue(tail[on + 1]) : '' }
}
export function databaseRouteHash({ nat, dest, purpose, doc, transit = [], arrival = '' }) {
  if (!iso3(nat) || !iso3(dest)) return null
  const via = [...new Set(transit)].filter(iso3).sort()
  const on = dateValue(arrival)
  return `#database/${nat}/${dest}/${purpose || 'tourism'}/${doc || 'ordinary_passport'}${via.length ? `/via/${via.join(',')}` : ''}${on ? `/on/${on}` : ''}`
}
