// Display names only. Stored reviewer IDs, official quotations and links stay
// unchanged so an audit can still identify the original author and evidence.
const provider = /\b(?:codex|chatgpt|openai|claude)(?:ai)?(?:[-_][a-z0-9]+)*\b/i
const providers = /\b(?:codex|chatgpt|openai|claude)(?:ai)?(?:[-_][a-z0-9]+)*\b/gi

export function reviewAttributionLabel(value, aiLabel = 'AI review') {
  return typeof value === 'string' && !/^https?:\/\//i.test(value) && provider.test(value)
    ? aiLabel : value
}

// Used for Ellis-authored messages, never verbatim source quotes. Preserve
// embedded URLs exactly, including paths that happen to contain a name.
export function reviewDisplayText(value) {
  if (typeof value !== 'string') return value
  return value.split(/(https?:\/\/[^\s<>]+)/gi).map(part => /^https?:\/\//i.test(part)
    ? part : part.replace(providers, 'AI')).join('')
}
