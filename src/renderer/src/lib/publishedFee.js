// Published visa charges are in currency units, unlike payment amounts in cents.
export function publishedFeeText(fee, { zeroLabel = 'None', fromLabel = 'From' } = {}) {
  if (!fee || typeof fee !== 'object' || fee.amount == null) return null
  const amount = fee.amount
  if (typeof amount === 'boolean' || !Number.isFinite(Number(amount)) || Number(amount) < 0 || String(amount).trim() === '') return null
  const from = fee.qualifier === 'from'
  if (Number(amount) === 0 && !from) return zeroLabel
  const value = `${amount} ${fee.currency || ''}`.trim()
  return from ? `${fromLabel} ${value}` : value
}
