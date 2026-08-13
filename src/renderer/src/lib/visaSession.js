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

// Employer (petitioner) session: SAME org as the beneficiary's cases — org
// tenancy grants shared case reads — but a distinct userId, so the backend's
// per-party authorization (_authorize_step_action) can tell the parties apart.
// Server enforcement is the wall; this session is only the identity it checks.
export function newEmployerSession({ orgId = 'ellis-demo', userId = 'petitioner-1', token = 'dev-token' } = {}) {
  return { token, orgId, userId }
}

// ---- Persona detection ------------------------------------------------------
// Generalizes the old detectAdminMode: one bundle serves three personas.
// 'applicant' is the default; '#employer' reveals the employer console;
// '#admin' / '#ops' reveal the operator surfaces. The choice persists in
// localStorage 'ellis_persona'; the legacy 'ellis_admin' flag keeps working
// (older builds read it), and '#applicant' clears both. Pure and injectable
// ({hash, storage}) so node tests can drive it without a DOM; any failure
// (no window, storage denied) fails safe to 'applicant'.
export function detectPersona({ hash, storage } = {}) {
  try {
    const h = String(
      hash !== undefined ? hash
        : (typeof window !== 'undefined' && window.location.hash) || ''
    ).toLowerCase()
    const store = storage !== undefined ? storage
      : (typeof window !== 'undefined' ? window.localStorage : null)
    const setSafe = (k, v) => { try { store && store.setItem(k, v) } catch { /* ignore */ } }
    const delSafe = (k) => { try { store && store.removeItem(k) } catch { /* ignore */ } }
    if (h === '#admin' || h === '#ops') {
      setSafe('ellis_persona', 'admin')
      setSafe('ellis_admin', '1')          // back-compat for older builds
      return 'admin'
    }
    if (h === '#employer') {
      setSafe('ellis_persona', 'employer')
      delSafe('ellis_admin')
      return 'employer'
    }
    // '#worker' is the H-1B beneficiary's entry: same applicant persona, but
    // VisaConsole reads the hash to open on the case list (the worker's
    // petition is opened by their employer — their journey starts at "find my
    // case", never "start an application").
    if (h === '#applicant' || h === '#worker') {
      delSafe('ellis_persona')
      delSafe('ellis_admin')
      return 'applicant'
    }
    // A BARE URL ALWAYS LANDS ON THE MENU (owner decision 2026-08-13): the
    // employer persona is never resurrected from storage — it opens only when
    // its door is explicitly chosen (#employer). Persisting it meant one visit
    // to the console hijacked every later visit, and the menu never showed.
    // Admin stays persisted: an ops tool, not a product surface.
    const persisted = store ? store.getItem('ellis_persona') : null
    if (persisted === 'employer') delSafe('ellis_persona')
    if (persisted === 'admin') return 'admin'
    // Back-compat: a legacy admin flag alone still grants the admin persona.
    if (store && store.getItem('ellis_admin') === '1') return 'admin'
    return 'applicant'
  } catch {
    return 'applicant'
  }
}

// The case party a persona views the H1B pipeline as. Display-only: the
// backend enforces per-party authorization on every mutating action.
export function partyForPersona(persona) {
  if (persona === 'employer') return 'petitioner'
  if (persona === 'admin') return 'admin'
  return 'beneficiary'
}

// ---- H1B pipeline display helpers ------------------------------------------
// Mirrors of backend/app/h1b/models.py STEP_STATUSES — display-only; the
// backend stays authoritative. Unknown statuses fail safe to a muted chip.
const H1B_STEP_STATUS = {
  blocked: { tone: 'muted', i18nKey: 'h1b.status.blocked' },
  ready: { tone: 'ready', i18nKey: 'h1b.status.ready' },
  in_progress: { tone: 'active', i18nKey: 'h1b.status.in_progress' },
  awaiting_government: { tone: 'pending', i18nKey: 'h1b.status.awaiting_government' },
  verified: { tone: 'ok', i18nKey: 'h1b.status.verified' },
  failed: { tone: 'bad', i18nKey: 'h1b.status.failed' }
}

export function h1bStepMeta(status) {
  return H1B_STEP_STATUS[status] || { tone: 'muted', i18nKey: 'h1b.status.unknown' }
}

// Who acts on a pipeline step, from the viewer's seat. The other party's step
// renders as "waiting on the employer / the worker" — never as an action the
// viewer could take (server-side per-party authorization would refuse anyway).
// Admins see the acting party named, with no waiting framing.
export function h1bWhoActs(step, viewerParty) {
  const acting = step && step.acting_party === 'petitioner' ? 'petitioner' : 'beneficiary'
  if (viewerParty === 'admin') {
    return { mine: false, waiting: false, acting, i18nKey: `h1b.actingParty.${acting}` }
  }
  if (viewerParty === acting) {
    return { mine: true, waiting: false, acting, i18nKey: 'h1b.whoActs.you' }
  }
  return {
    mine: false, waiting: true, acting,
    i18nKey: acting === 'petitioner' ? 'h1b.waitingOn.petitioner' : 'h1b.waitingOn.beneficiary'
  }
}

// ---- Ask Ellis action honesty ----------------------------------------------
// The assistant reports the actions it attempted. The backend contract
// (h1b/assistant.py execute_tool) is {tool, summary, ok} where `summary` is
// the localized honest sentence (done / denied / failed) and `ok` is true only
// for a genuinely performed act. Execution-class honesty: an action renders as
// DONE only when the backend explicitly said so (ok === true or an explicit
// done status); a denied action renders as denied (never hidden, never
// softened); anything unknown fails safe to "not performed" — an unconfirmed
// act can never display as one that happened.
const _ASSISTANT_DONE = new Set(['done', 'ok', 'success', 'completed', 'performed'])
const _ASSISTANT_DENIED = new Set(['denied', 'refused', 'forbidden', 'unauthorized', 'rejected'])

export function assistantActionMeta(action) {
  const status = String((action && action.status) || '').toLowerCase()
  const denied = _ASSISTANT_DENIED.has(status) || (action && action.denied === true)
  const done = !denied &&
    ((action && action.ok === true) || _ASSISTANT_DONE.has(status))
  return {
    // The backend summary is the honest localized sentence; fall back to the
    // tool/action name for older shapes.
    label: (action && (action.summary || action.label || action.tool || action.action)) || '',
    detail: (action && (action.detail || action.reason)) || '',
    done,
    denied,
    i18nKey: denied ? 'askellis.actionDenied'
      : done ? 'askellis.actionDone'
      : 'askellis.actionNotDone'
  }
}

// ---- Active H1B case (Ask Ellis mount point) -------------------------------
// App.jsx mounts the floating Ask Ellis button once; whichever surface is
// showing an H1B case (H1bPipeline in the case flow or the employer console)
// registers it here so the assistant always has honest case context — and
// disappears when no case is open.
let _activeH1bCase = ''
const _activeH1bSubs = new Set()

export function setActiveH1bCase(caseId) {
  _activeH1bCase = caseId || ''
  for (const fn of _activeH1bSubs) {
    try { fn(_activeH1bCase) } catch { /* subscriber errors never propagate */ }
  }
}

export function getActiveH1bCase() {
  return _activeH1bCase
}

export function subscribeActiveH1bCase(fn) {
  _activeH1bSubs.add(fn)
  return () => _activeH1bSubs.delete(fn)
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

// ---- Dynamic "additional information" questions (Part 4) -------------------
// Mid-flight the live portal may need a detail Ellis never collected. The
// backend pauses with handoff "additional_information" and a list of
// applicant-friendly questions: {key, question, why, format, mandatory,
// kind: 'text'|'date'|'select'|'document', options?}. Document-kind questions
// (key prefixed "document:") are resolved on the Documents tab, never typed
// into the modal.

export function isDocumentQuestion(q) {
  return !!q && (q.kind === 'document' || String(q.key || '').startsWith('document:'))
}

// Split the pending questions into typed-answer questions and document asks.
export function splitQuestions(questions) {
  const list = Array.isArray(questions) ? questions : []
  return {
    inputQuestions: list.filter((q) => q && q.key && !isDocumentQuestion(q)),
    documentQuestions: list.filter((q) => q && q.key && isDocumentQuestion(q))
  }
}

// Basic MM/DD/YYYY shape check for typed date answers. The backend remains the
// date authority (it canonicalizes to ISO) — this only catches obvious slips
// before a round trip.
export function isValidDateShape(value) {
  const m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(String(value == null ? '' : value).trim())
  if (!m) return false
  const mm = Number(m[1])
  const dd = Number(m[2])
  return mm >= 1 && mm <= 12 && dd >= 1 && dd <= 31
}

// Validate the modal's entered values against the asked (non-document)
// questions. Returns { answers, errors }: answers holds ONLY answered keys
// (trimmed, non-empty); errors maps question key -> i18n error key. Mandatory
// questions must be answered; date answers must pass the shape check.
export function collectAnswers(questions, values = {}) {
  const answers = {}
  const errors = {}
  for (const q of Array.isArray(questions) ? questions : []) {
    if (!q || !q.key || isDocumentQuestion(q)) continue
    const v = String(values[q.key] == null ? '' : values[q.key]).trim()
    if (!v) {
      if (q.mandatory) errors[q.key] = 'addinfo.errRequired'
      continue
    }
    if (q.kind === 'date' && !isValidDateShape(v)) {
      errors[q.key] = 'addinfo.errDate'
      continue
    }
    answers[q.key] = v
  }
  return { answers, errors }
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
