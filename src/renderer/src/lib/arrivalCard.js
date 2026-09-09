// Conditional entry filing must retain its conditions at every reader surface.
export function arrivalCardLines(card, translate = value => value) {
  if (!card || typeof card !== 'object') return []
  const strings = value => (Array.isArray(value) ? value : [value])
    .filter(item => typeof item === 'string' && item.trim()).map(item => item.trim())
  const notes = [...new Set([...strings(card.notes), ...strings(card.note)])]
  if (card.required !== true && !notes.length) return []
  const heading = [...strings(card.name),
    ...(card.required === false ? [] : strings(card.submission_window))]
    .map(translate).join(', ')
  return [heading, ...notes.map(translate)].filter(Boolean)
}
