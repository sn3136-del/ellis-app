// Presentation only: retain the full structured values in the audit/export.
const humanize = value => String(value).replace(/_/g, ' ').replace(/\b([a-z])/g, c => c.toUpperCase())
const decode = value => {
  if (typeof value !== 'string' || !/^[\[{]/.test(value.trim())) return value
  try { return JSON.parse(value) } catch { return value }
}
const ordered = value => Array.isArray(value) ? value.map(ordered)
  : value && typeof value === 'object'
    ? Object.fromEntries(Object.keys(value).sort().map(key => [key, ordered(value[key])])) : value
export const sameChangeValue = (a, b) => JSON.stringify(ordered(decode(a))) === JSON.stringify(ordered(decode(b)))

export function changeOriginKind(change) {
  if (change.origin === 'grounded_recheck') return 'recheck'
  if (change.origin === 'engine') return 'engine'
  const authors = new Set()
  const visit = value => {
    if (!value || typeof value !== 'object') return
    if (typeof value.verifier === 'string') authors.add(value.verifier.toLowerCase())
    Object.values(value).forEach(visit)
  }
  // Attribute this change from its own new evidence, never a current override.
  visit(decode(change.changes?.field_provenance?.to))
  return authors.size === 1 && authors.has('ai') ? 'aiReview' : 'qc'
}

export function formatChangeValue(field, raw, { t, valueLabel = (_, value) => value, fieldLabel = humanize }, depth = 0) {
  const value = decode(raw)
  if (value == null || value === '') return null
  if (typeof value === 'boolean') return t(value ? 'ops.chg.yes' : 'ops.chg.no')
  if (typeof value !== 'object') {
    const labeled = valueLabel(field, value)
    const text = String(labeled === value && /^[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+$/.test(value)
      ? humanize(value) : labeled)
    return text.length > 220 ? text.slice(0, 220) + '…' : text
  }
  const keys = Object.keys(value)
  if (!keys.length) return null
  if (field === 'field_provenance') {
    const sources = new Set()
    const visit = entry => {
      if (!entry || typeof entry !== 'object') return
      if (typeof entry.source_url === 'string' && entry.source_url.trim()) sources.add(entry.source_url.trim())
      Object.values(entry).forEach(visit)
    }
    visit(value)
    return t('ops.chg.sourceDetails', { fields: keys.length, sources: sources.size })
  }
  if ('amount' in value) {
    if (value.amount == null) {
      const details = Object.fromEntries(Object.entries(value).filter(([key]) => key !== 'amount'))
      return formatChangeValue(field, details, { t, valueLabel, fieldLabel }, depth + 1)
    }
    const amount = `${value.amount} ${value.currency || ''}`.trim()
    const qualified = value.qualifier === 'from' ? t('ops.chg.feeFrom', { amount }) : amount
    return value.note ? `${qualified} · ${value.note}` : qualified
  }
  if (depth >= 2) return t('ops.chg.detailCount', { n: keys.length })
  if (Array.isArray(value)) {
    const parts = value.slice(0, 3).map(item => formatChangeValue(field, item, { t, valueLabel, fieldLabel }, depth + 1)).filter(Boolean)
    if (value.length > 3) parts.push(t('ops.chg.moreDetails', { n: value.length - 3 }))
    return parts.join(' · ') || null
  }
  const parts = keys.slice(0, 3).map(key => {
    const text = formatChangeValue(key, value[key], { t, valueLabel, fieldLabel }, depth + 1)
    return text == null ? null : `${fieldLabel(key)}: ${text}`
  }).filter(Boolean)
  if (keys.length > 3) parts.push(t('ops.chg.moreDetails', { n: keys.length - 3 }))
  return parts.join(' · ') || null
}

export function changeDisplayEntries(changes, action, options) {
  return Object.entries(changes || {}).flatMap(([field, delta]) => {
    if (!delta || typeof delta !== 'object') return []
    if (action === 'modify' && sameChangeValue(delta.from, delta.to)) return []
    const before = formatChangeValue(field, delta.from, options)
    const after = formatChangeValue(field, delta.to, options)
    if (before == null && after == null) return []
    // Equal counts can conceal different quotations or source pages. Keep the
    // audit event, but show a readable update instead of an identical arrow.
    const summaryOnly = action === 'modify' && before === after
    return [[field, before, summaryOnly ? options.t('ops.chg.detailsUpdated', { details: after }) : after, summaryOnly]]
  })
}
