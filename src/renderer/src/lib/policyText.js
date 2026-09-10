// Preserve policy punctuation: a dash can encode a numeric range or date span.
// Formatting must never turn a range into two separate values.
export const formatPolicyText = (v) => {
  if (!v) return v
  let out = String(v)
  if (/https?:\/\//.test(out)) return out
  out = out
    .replace(/([A-Za-z0-9])_+([A-Za-z0-9])/g, '$1 $2')
    .replace(/\s*;\s*/g, '. ')
    .replace(/\s{2,}/g, ' ')
    .trim()
  out = out.replace(/(^|[.!?]\s+)([a-z])/g, (m, lead, ch) => lead + ch.toUpperCase())
  if (out.length > 28 && !/[.!?)]$/.test(out)) out += '.'
  return out
}

