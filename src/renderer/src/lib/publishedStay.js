// Preserve the product's qualified stay: a numeric cap alone can hide a
// cumulative period, an entry condition, or an officer's discretion.
// Presentation only; this does not infer, verify or change a permission.
export function publishedStayText(product, {
  translate = value => value,
  formatDays = days => `Up to ${days} days`,
  unknownLabel = 'Not yet confirmed',
} = {}) {
  const text = product?.permitted_stay
  if (typeof text === 'string' && text.trim()) {
    return translate(text) || text
  }
  const days = product?.max_stay_days
  if (days != null && typeof days !== 'boolean' && String(days).trim()
      && Number.isFinite(Number(days)) && Number(days) > 0) {
    return formatDays(days)
  }
  return unknownLabel
}
