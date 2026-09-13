// Legacy Boolean flags do not distinguish visa applications from border
// checks. No validated backend contract supplies their stage, so neither a
// visa verdict nor client-side proof metadata may manufacture that scope.
// Preserve actual source-backed instructions and the original data elsewhere.
const FIELDS = [
  ['biometrics_required', 'db.biometrics'],
  ['interview_required', 'db.interview'],
  ['appointment_required', 'db.appointment'],
]

export function checkRequirements(guidance) {
  if (!guidance || typeof guidance !== 'object') return []
  return FIELDS.flatMap(([field, labelKey]) => {
    const value = guidance[field]
    if (value !== true && value !== false) return []
    return [{ field, labelKey, stageKey: 'db.checkStageUnknown',
      valueKey: 'db.checkRequirementUnknown', tone: null, scopeConfirmed: false }]
  })
}

export function shouldShowChecksCard(checks) {
  return Array.isArray(checks) && checks.length > 0 && checks.every((check) => (
    check && typeof check.valueKey === 'string' && check.valueKey.trim()
    && check.valueKey !== 'db.checkRequirementUnknown'
    && (check.valueKey === 'db.notRequired'
      || (check.scopeConfirmed === true && typeof check.stageKey === 'string'
        && check.stageKey.trim() && check.stageKey !== 'db.checkStageUnknown'))
  ))
}
