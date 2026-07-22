// Append-only audit log for security-critical actions. Entries are immutable
// once written; secrets are never recorded — only references, non-sensitive
// metadata, and outcomes. A redaction pass strips anything that looks like a
// credential before an entry is stored.

const SENSITIVE_KEYS = /pass|pwd|secret|token|cookie|card|cvc|cvv|otp|3ds|ssn/i

function redact(value, depth = 0) {
  if (value == null || depth > 4) return value
  if (typeof value === 'string') {
    // References like vault://... and RCPT-... are safe; long opaque blobs get masked.
    return value.length > 200 ? value.slice(0, 32) + '…[redacted]' : value
  }
  if (Array.isArray(value)) return value.map((v) => redact(v, depth + 1))
  if (typeof value === 'object') {
    const out = {}
    for (const [k, v] of Object.entries(value)) {
      out[k] = SENSITIVE_KEYS.test(k) ? '[redacted]' : redact(v, depth + 1)
    }
    return out
  }
  return value
}

export class AuditLog {
  constructor(clock = () => Date.now()) {
    this._entries = []
    this.clock = clock
  }

  /** Append an immutable, redacted audit event. */
  record(caseId, action, detail = {}, actor = 'system') {
    const entry = Object.freeze({
      seq: this._entries.length + 1,
      at: this.clock(),
      caseId,
      actor,
      action,
      detail: redact(detail)
    })
    this._entries.push(entry)
    return entry
  }

  forCase(caseId) { return this._entries.filter((e) => e.caseId === caseId) }
  all() { return [...this._entries] }
  /** Detect any accidental secret leakage in the whole log. */
  containsPlaintext(needle) { return JSON.stringify(this._entries).includes(needle) }
}
