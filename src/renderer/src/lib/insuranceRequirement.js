// Only the shared server projection can confirm this Boolean's entry scope.
// Legacy responses retain a neutral unknown, even when a raw flag is false.
export function insuranceRequirement(guidance, evidence) {
  if (!guidance || typeof guidance !== 'object') return null
  const state = evidence?.insurance_required
  if (!Object.hasOwn(guidance, 'insurance_required') && !state) return null
  const value = guidance.insurance_required
  if (state?.contract === 'ellis.insurance-evidence.v1'
      && state.field === 'insurance_required' && state.status === 'verified'
      && state.scope === 'entry' && typeof value === 'boolean'
      && state.value === value) {
    return { valueKey: value ? 'db.required' : 'db.notRequired', tone: value ? 'yes' : 'no' }
  }
  return { valueKey: 'db.checkRequirementUnknown', tone: null }
}
