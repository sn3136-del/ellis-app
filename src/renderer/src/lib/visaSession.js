// Pure helpers for the Visa Console. Kept free of React/DOM so they can be unit
// tested with `node --test` (see tests/visa/visa_ui_logic.test.mjs) and reused
// across the applicant screens. The typed backend client lives in visaBackend.js.

// Dev session: the local backend accepts a dev token + org/user headers. In
// production this is replaced by a Clerk JWT (org/user derived server-side).
export function newSession({ orgId = 'ellis-demo', userId = 'applicant-1', token = 'dev-token' } = {}) {
  return { token, orgId, userId }
}

// Admin session: the backend grants the admin role only for the dedicated admin
// token (dev default 'admin-token'; production = a Clerk admin claim). Adapter
// approval/activation/kill/rollback require this.
export function newAdminSession({ orgId = 'platform', userId = 'admin-1', token = 'admin-token' } = {}) {
  return { token, orgId, userId }
}

// Map an OCR confidence (0..1) to a severity bucket for the review badges.
export function confidenceLevel(confidence) {
  if (confidence == null) return 'mid'
  if (confidence >= 0.85) return 'ok'
  if (confidence >= 0.6) return 'mid'
  return 'bad'
}

// Turn a document's extracted_fields map into sorted, render-ready rows. Each
// row carries the value, confidence bucket, provenance, and whether it needs a
// second look (low confidence, applicant edit, or an MRZ/visible-zone conflict).
// Calendar-date values additionally carry `display` in the U.S. MM/DD/YYYY
// format — the canonical ISO value underneath is never altered.
import { formatDateUS, isDateKey } from './intake.js'

export function fieldRows(extractedFields = {}, conflicts = []) {
  const conflictKeys = new Set(
    (conflicts || []).flatMap((c) => (c.keys || (c.key ? [c.key] : []))))
  return Object.entries(extractedFields)
    .map(([key, v]) => {
      const value = v && typeof v === 'object' ? v.value : v
      const confidence = v && typeof v === 'object' ? v.confidence : null
      const source = (v && v.source) || 'ocr'
      const level = confidenceLevel(confidence)
      return {
        key,
        value: value ?? '',
        display: isDateKey(key) ? formatDateUS(value ?? '') : String(value ?? ''),
        confidence,
        source,
        level,
        conflict: conflictKeys.has(key),
        needsAttention: level === 'bad' || conflictKeys.has(key) || value == null || value === ''
      }
    })
    .sort((a, b) => a.key.localeCompare(b.key))
}

// A document is ready to approve once every field has a non-empty value.
export function documentReady(rows) {
  return rows.length > 0 && rows.every((r) => r.value !== '' && r.value != null)
}

const DAY_MS = 86_400_000

// Backend appointment-preference defaults (app/appointments.py default_preferences),
// mirrored so the preferences form round-trips cleanly through /preferences.
// accessibility + interpreterLanguage are extra captured requirements (stored,
// surfaced to the appointment step) and ignored by slot matching.
export function defaultPreferences(now = Date.now()) {
  return {
    preferredLocation: '',
    alternativeLocations: [],
    maxTravelKm: null,
    earliestUtc: now,
    latestUtc: now + 120 * DAY_MS,
    preferredWeekdays: [],
    excludedWeekdays: [],
    preferredTimeRange: null,        // ["09:00","12:00"] in applicant tz
    blackoutDates: [],                // ["YYYY-MM-DD"]
    minAdvanceMs: 2 * DAY_MS,
    anyQualifyingTimeOk: true,
    allowAutoBook: false,
    allowAutoReschedule: false,
    minRescheduleImprovementMs: 3 * DAY_MS,
    maxRescheduleFeeCents: 0,
    maxAutoReschedules: 2,
    applicantTimeZone: (typeof Intl !== 'undefined' && Intl.DateTimeFormat().resolvedOptions().timeZone) || 'UTC',
    askBeforeReschedule: true,
    accessibility: '',
    interpreterLanguage: ''
  }
}

export const PREF_DAY_MS = DAY_MS

// Convert a yyyy-mm-dd input to epoch ms (UTC midnight); '' → null.
export function dateToMs(s) {
  if (!s) return null
  const ms = Date.parse(s + 'T00:00:00Z')
  return Number.isNaN(ms) ? null : ms
}

// Convert epoch ms to a yyyy-mm-dd value for a date input.
export function msToDate(ms) {
  if (ms == null) return ''
  try { return new Date(ms).toISOString().slice(0, 10) } catch { return '' }
}

// Fee formatting from a portal fee descriptor {amount, currency, display}.
export function formatFee(fee) {
  if (!fee) return ''
  if (fee.display) return fee.display
  if (fee.amount != null) {
    const n = (fee.amount / 100).toFixed(2)
    return `${fee.currency || ''} ${n}`.trim()
  }
  return ''
}

// A short human label + guidance for the current handoff, with a safe fallback.
export function handoffCopy(copyMap, handoff) {
  const entry = copyMap[handoff]
  if (entry) return { title: entry[0], sub: entry[1] }
  return { title: 'Action needed', sub: 'Complete the required step to continue.' }
}

// Format a slot's start time (epoch ms) for the appointment calendar.
export function formatSlot(startUtc, timeZone) {
  try {
    return new Date(startUtc).toLocaleString(undefined, {
      weekday: 'short', month: 'short', day: 'numeric',
      hour: 'numeric', minute: '2-digit', timeZone: timeZone || undefined
    })
  } catch {
    return new Date(startUtc).toISOString()
  }
}

// Terminal states the flow treats as "done" (stop polling / show result).
export const TERMINAL_STATES = new Set(['COMPLETED', 'CANCELLED', 'REJECTED'])
export const FAILURE_STATES = new Set(['RECOVERABLE_FAILURE', 'MANUAL_REVIEW'])

export function isTerminal(state) {
  return TERMINAL_STATES.has(state)
}

// Client-side execution-classification display guard, mirroring the backend
// (app/execution.py). The production UI must REFUSE to present submitted / paid /
// booked / confirmed as real unless the backend disposition explicitly says the
// result is a real, adapter-verified government outcome. A missing disposition is
// treated as NOT real (fail safe) — a mock or sandbox run can never read as real.
export function resultDisposition(status) {
  const d = (status && status.disposition) || {}
  const ec = (status && status.execution_class) || d.execution_class || 'MOCK'
  const isReal = d.is_real_government_result === true
  const state = (status && status.state) || ''
  const displayStatus = d.display_status || (state === 'COMPLETED'
    ? (isReal ? 'COMPLETED' : `COMPLETED (${ec} — not a real government submission)`)
    : state)
  return {
    executionClass: ec,
    isReal,
    // The four guarded terminal claims may be shown as REAL only when isReal.
    canClaimReal: isReal,
    displayStatus,
    disclaimer: isReal ? '' : (d.disclaimer
      || 'This case ran on a non-production portal. Nothing was really submitted, paid, or booked with any government.')
  }
}
