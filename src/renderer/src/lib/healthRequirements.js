// Health conditions are policy text, not just a named checklist item. Keep the
// published trigger and qualifications together; never infer whether they apply.
export function healthRequirementLines(requirements) {
  const strings = value => (Array.isArray(value) ? value : [value])
    .filter(item => typeof item === 'string' && item.trim()).map(item => item.trim())
  const unique = values => [...new Set(values)]
  const entries = Array.isArray(requirements) ? requirements : [requirements]
  return entries.flatMap(item => {
    if (typeof item === 'string') return strings(item)
    if (!item || typeof item !== 'object' || Array.isArray(item)) return []
    const applicability = typeof item.applicability === 'string' ? item.applicability.trim() : ''
    if (applicability.toLowerCase() === 'not_applicable') return []
    const name = strings(item.name).join('; ')
    const heading = [name, applicability ? `(${applicability.replace(/_/g, ' ')})` : '']
      .filter(Boolean).join(' ')
    const details = unique([
      ...strings(item.trigger), ...strings(item.question),
      ...strings(item.notes), ...strings(item.note), ...strings(item.instructions),
      ...strings(item.description),
    ])
    const countries = unique(strings(item.trigger_countries))
    // The source schema does not say these are passport nationalities: avoid
    // inventing that interpretation when they may instead refer to residence.
    const countryText = countries.length ? `Countries specified for this condition: ${countries.join(', ')}` : ''
    return unique([heading, ...details, countryText].filter(Boolean)).join(' — ') || []
  })
}
