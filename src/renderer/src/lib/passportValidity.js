// Preserve the policy's reference point: arrival and departure are different
// rules, and a validity-only rule does not imply an extra number of months.
export function passportValidityText(rule, translate) {
  if (!rule || typeof rule !== 'object' || Array.isArray(rule)) return null
  const keys = {
    valid_on_arrival: 'db.passportRule.arrival',
    valid_through_departure: 'db.passportRule.departure',
    months_after_arrival: 'db.passportRule.monthsAfterArrival',
    months_after_departure: 'db.passportRule.monthsAfterDeparture',
  }
  if (!Object.hasOwn(keys, rule.kind)) return null
  const key = keys[rule.kind]
  const monthRule = rule.kind.startsWith('months_after_')
  if (monthRule ? !Number.isInteger(rule.months) || rule.months <= 0
    : rule.months != null && rule.months !== 0) return null
  return translate(key, { n: rule.months })
}
