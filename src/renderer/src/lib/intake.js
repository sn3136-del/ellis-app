// Pure logic for the applicant "Start your visa" route-intake wizard. Kept free
// of React/DOM/fetch so it can be unit tested with `node --test`
// (tests/visa/intake_logic.test.mjs) and reused across screens.
//
// The honest-readiness principle applies throughout: anything unknown or
// unverified maps to a fail-safe ("pending" / "blocked") presentation — the UI
// must never invent requirements or upgrade a status.

// ---------------------------------------------------------------------------
// Conditional field visibility.
//
// The backend expresses intake-field conditions as a tiny declarative string.
// The ONLY supported pattern is `X != Y` where both sides are answer keys
// (e.g. "lawful_country_of_residence != passport_nationality"). This is a safe
// evaluator: no eval, no operators beyond `!=`, unknown patterns default to
// VISIBLE (never hide a field we do not understand).
//
// A conditional field only becomes visible once BOTH driving answers are
// present and different — while either side is still unanswered the condition
// is unresolved and the field stays hidden (it is optional context, not a
// gate).
const CONDITION_RE = /^\s*([A-Za-z0-9_]+)\s*!=\s*([A-Za-z0-9_]+)\s*$/

export function conditionField(field, answers = {}) {
  const cond = field && typeof field.condition === 'string' ? field.condition : null
  if (!cond) return true
  const m = CONDITION_RE.exec(cond)
  if (!m) return true // unknown condition shape -> default visible (fail open for display)
  const a = answers ? answers[m[1]] : undefined
  const b = answers ? answers[m[2]] : undefined
  const av = a == null ? '' : String(a).trim()
  const bv = b == null ? '' : String(b).trim()
  if (av === '' || bv === '') return false // condition unresolved -> not yet relevant
  return av !== bv
}

// ---------------------------------------------------------------------------
// Which required fields are still missing.
//
// A field is missing when it is required, has no backend default, is currently
// visible (per its condition), and the answer is empty/undefined. Fields with
// a default are never missing — the backend applies the default server-side.
export function missingRequired(intakeFields = [], answers = {}) {
  const missing = []
  for (const f of intakeFields || []) {
    if (!f || !f.key || !f.required) continue
    if (f.default !== undefined && f.default !== null && f.default !== '') continue
    if (!conditionField(f, answers)) continue
    const v = answers ? answers[f.key] : undefined
    const empty = v === undefined || v === null ||
      (typeof v === 'string' && v.trim() === '') ||
      (Array.isArray(v) && v.length === 0)
    if (empty) missing.push(f.key)
  }
  return missing
}

// ---------------------------------------------------------------------------
// Readiness presentation. One tone + one i18n label key per backend readiness
// status. Unknown/missing statuses fail safe to 'blocked' — the UI must never
// present an unrecognized status as anything actionable.
const READINESS = {
  NOT_READY: { tone: 'blocked', i18nKey: 'readiness.NOT_READY' },
  PREPARATION_ONLY: { tone: 'info', i18nKey: 'readiness.PREPARATION_ONLY' },
  APPLICANT_HANDOFF_READY: { tone: 'info', i18nKey: 'readiness.APPLICANT_HANDOFF_READY' },
  LIVE_SANDBOX_READY: { tone: 'warn', i18nKey: 'readiness.LIVE_SANDBOX_READY' },
  LIVE_PRODUCTION_READY: { tone: 'ok', i18nKey: 'readiness.LIVE_PRODUCTION_READY' }
}

export function readinessMeta(status) {
  return READINESS[status] || { tone: 'blocked', i18nKey: 'readiness.UNKNOWN' }
}

// ---------------------------------------------------------------------------
// Route-check summary rows for the result panel.
//
// Turns the resolve `checks` object into honest, render-ready rows. Statuses:
//   'ok'      — verified / affirmatively fine
//   'warn'    — needs attention (conflict, unresolved jurisdiction, sandbox-only)
//   'pending' — not researched / not verified yet (the honest default)
// Never throws on missing keys: absent checks render as 'pending' with an
// honest empty detail, never as 'ok'.
export function checksSummary(checks) {
  const c = checks && typeof checks === 'object' ? checks : {}
  const reasoning = obj(c.readiness_reasoning)
  const rows = []

  // 1. Requirements verified against official sources.
  const snap = obj(c.snapshot_resolution)
  const reqVerified = reasoning.requirements_verified === true
  rows.push({
    key: 'requirements',
    labelKey: 'check.requirements',
    status: reqVerified ? 'ok' : 'pending',
    detail: joinDetail([snap.matched, snap.disposition, snap.research_status])
  })

  // 2. Source evidence backing the route.
  const src = obj(c.source_evidence)
  const verifiedCount = num(src.verified_count)
  rows.push({
    key: 'sources',
    labelKey: 'check.sources',
    status: verifiedCount > 0 ? 'ok' : 'pending',
    detail: verifiedCount > 0
      ? joinDetail([`${verifiedCount} verified`, arr(src.authorities).join(', ')])
      : '0 verified'
  })

  // 3. Open conflicts. A conflicted route is a warning, never silently ok.
  const conf = obj(c.conflicts)
  const conflicted = conf.route_conflicted === true || num(conf.open_for_destination) > 0
  rows.push({
    key: 'conflicts',
    labelKey: 'check.conflicts',
    status: conflicted ? 'warn' : 'ok',
    detail: joinDetail([
      `${num(conf.open_for_destination)} open`,
      conf.route_conflicted === true ? 'route conflicted' : ''
    ])
  })

  // 4. Fee verification.
  const fees = obj(c.fees)
  rows.push({
    key: 'fee',
    labelKey: 'check.fee',
    status: fees.verified === true ? 'ok' : 'pending',
    detail: joinDetail([`${num(fees.versions)} version(s)`, fees.verified === true ? 'verified' : 'not verified'])
  })

  // 5. Consular jurisdiction. Not required -> ok; required+resolved -> ok;
  //    required but unresolved -> warn.
  const jur = obj(c.consular_jurisdiction)
  const jurStatus = jur.required !== true ? 'ok' : (jur.resolved === true ? 'ok' : 'warn')
  rows.push({
    key: 'jurisdiction',
    labelKey: 'check.jurisdiction',
    status: jurStatus,
    detail: joinDetail([jur.status, jur.competent_post])
  })

  // 6. Official portal verification.
  const portal = obj(c.official_portal)
  const portalCount = num(portal.verified_count)
  rows.push({
    key: 'portal',
    labelKey: 'check.portal',
    status: portalCount > 0 ? 'ok' : 'pending',
    detail: portalCount > 0
      ? joinDetail([`${portalCount} verified`, arr(portal.portals).map((p) => obj(p).kind).filter(Boolean).join(', ')])
      : '0 verified'
  })

  // 7. Passport-validity rule (never guessed — 'unknown' stays pending).
  const rule = obj(c.passport_validity_rule)
  const known = typeof rule.rule_kind === 'string' && rule.rule_kind !== '' && rule.rule_kind !== 'unknown'
  rows.push({
    key: 'passportRule',
    labelKey: 'check.passportRule',
    status: known ? 'ok' : 'pending',
    detail: joinDetail([rule.rule_kind, rule.note])
  })

  // 8. Automation adapter. Production active -> ok; sandbox only -> warn;
  //    anything less -> pending.
  const ad = obj(c.adapter)
  const adapterStatus = num(ad.production_active) > 0 ? 'ok'
    : num(ad.sandbox_ready) > 0 ? 'warn' : 'pending'
  rows.push({
    key: 'adapter',
    labelKey: 'check.adapter',
    status: adapterStatus,
    detail: joinDetail([
      `${num(ad.records)} record(s)`,
      num(ad.production_active) > 0 ? 'production active' : '',
      num(ad.sandbox_ready) > 0 ? 'sandbox ready' : ''
    ])
  })

  return rows
}

// -- tiny safe accessors ----------------------------------------------------
function obj(v) { return v && typeof v === 'object' && !Array.isArray(v) ? v : {} }
function arr(v) { return Array.isArray(v) ? v : [] }
function num(v) { return typeof v === 'number' && Number.isFinite(v) ? v : 0 }
function joinDetail(parts) {
  return parts.filter((p) => p != null && String(p).trim() !== '').map(String).join(' · ')
}

// ---------------------------------------------------------------------------
// On-demand route research (focused research job auto-started by resolve).
//
// The backend pipeline reports 18 fine-grained stages; applicants see 9
// friendly progress steps. Unknown stages fail safe to the FIRST step — the
// UI must never show progress it cannot vouch for.
export const RESEARCH_STEP_KEYS = [
  'research.step.stored',     // 0 Checking stored requirements
  'research.step.sources',    // 1 Researching official government sources
  'research.step.extract',    // 2 Extracting route requirements
  'research.step.consulate',  // 3 Verifying the competent consulate
  'research.step.portal',     // 4 Verifying the official portal
  'research.step.fees',       // 5 Checking fees
  'research.step.conflicts',  // 6 Checking for conflicts
  'research.step.save',       // 7 Saving verified requirements
  'research.step.adapter'     // 8 Evaluating adapter readiness
]

const RESEARCH_STAGE_STEP = {
  RECEIVE_ROUTE: 0, NORMALIZE_ROUTE: 0, CHECK_STORED_POLICY: 0,
  DISCOVER_OFFICIAL_SOURCES: 1, VERIFY_SOURCE_DOMAINS: 1, FETCH_SOURCE_CONTENT: 1,
  KIMI_EXTRACT_AND_TRANSLATE: 2, NORMALIZE_REQUIREMENTS: 2,
  RESOLVE_CONSULAR_JURISDICTION: 3,
  VERIFY_OFFICIAL_PORTAL: 4,
  VERIFY_ROUTE_FEES: 5,
  DETECT_CONFLICTS: 6,
  CREATE_IMMUTABLE_POLICY_VERSION: 7, LINK_ROUTE: 7, IMPORT_TO_POSTGRESQL: 7, EXPORT_TO_JSONL: 7,
  EVALUATE_ADAPTER_READINESS: 8, RETURN_ROUTE_STATUS: 8
}

export function researchStageMeta(stage) {
  const order = RESEARCH_STAGE_STEP[stage]
  const idx = typeof order === 'number' ? order : 0 // unknown stage -> first step (fail-safe)
  return { order: idx, i18nKey: RESEARCH_STEP_KEYS[idx] }
}

// Is a research-job status terminal (stop polling)?  queued/running keep the
// poll alive; every terminal status stops it. UNKNOWN statuses are treated as
// TERMINAL — never poll forever on a status we do not understand; the UI shows
// a generic honest "stopped" state instead.
const RESEARCH_ACTIVE = new Set(['queued', 'running'])

export function researchTerminal(status) {
  return !RESEARCH_ACTIVE.has(status)
}

// One tone + i18n label per research-job status. Unknown statuses fail safe
// to 'blocked' — never presented as progress or success.
const RESEARCH_STATUS = {
  complete: { tone: 'ok', i18nKey: 'research.status.complete' },
  running: { tone: 'info', i18nKey: 'research.status.running' },
  queued: { tone: 'info', i18nKey: 'research.status.queued' },
  research_incomplete: { tone: 'warn', i18nKey: 'research.status.research_incomplete' },
  timed_out: { tone: 'warn', i18nKey: 'research.status.timed_out' },
  conflicted: { tone: 'blocked', i18nKey: 'research.status.conflicted' },
  failed: { tone: 'blocked', i18nKey: 'research.status.failed' }
}

export function researchStatusMeta(status) {
  return RESEARCH_STATUS[status] || { tone: 'blocked', i18nKey: 'research.status.UNKNOWN' }
}

// ---------------------------------------------------------------------------
// Small shared helpers for the wizard UI (still pure).

// Basic applicant-email validation (deliberately simple; the backend is the
// authority).
export function validEmail(s) {
  return typeof s === 'string' && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(s.trim())
}

// Departure must be strictly after arrival when both are set.
export function datesOrdered(arrival, departure) {
  if (!arrival || !departure) return true
  return String(departure) > String(arrival)
}

// ---------------------------------------------------------------------------
// Kimi-primary route guidance (pure display helpers).

// Disposition -> tone + i18n label. Unknown fails safe to 'blocked' (never
// presented as a confident result).
const GUIDANCE_DISPOSITION = {
  VISA_EXEMPT: { tone: 'ok', i18nKey: 'guidance.disp.exempt' },
  VISA_REQUIRED: { tone: 'info', i18nKey: 'guidance.disp.required' },
  ELECTRONIC_AUTHORIZATION_REQUIRED: { tone: 'info', i18nKey: 'guidance.disp.eta' },
  CONDITIONAL: { tone: 'warn', i18nKey: 'guidance.disp.conditional' }
}

export function guidanceDispositionMeta(disposition) {
  return GUIDANCE_DISPOSITION[disposition] ||
    { tone: 'blocked', i18nKey: 'guidance.disp.unknown' }
}

// A workflow step the AI guidance may drive. Irreversible steps ALWAYS carry
// requiresConfirmation, regardless of what the model said, so the UI can never
// present a one-click irreversible action.
const IRREVERSIBLE_STEPS = new Set([
  'account_registration', 'appointment_booking', 'payment',
  'final_review_and_signature', 'submission'
])

export function guidanceStepMeta(step) {
  const reversible = !IRREVERSIBLE_STEPS.has(step && step.step ? step.step : step)
  const id = (step && step.step) || step
  return {
    id,
    reversible,
    requiresConfirmation: !reversible,
    i18nKey: `guidance.step.${id}`
  }
}

// True only for a usable, applicant-facing AI result. KIMI_UNCERTAIN and unknown
// statuses are shown honestly (gaps surfaced), never as a confident answer.
export function guidanceIsUsable(g) {
  return !!g && g.status === 'KIMI_PRIMARY' && g.ai_generated === true
}

// ---------------------------------------------------------------------------
// Continuation after guidance (mirrors backend intake_flow.continuation_meta —
// the backend is authoritative; this drives the primary CTA only).
export const CONTINUATION_KIND = {
  VISA_REQUIRED: { kind: 'visa_application', ctaKey: 'guidance.continue.visa' },
  VISA_EXEMPT: { kind: 'entry_preparation', ctaKey: 'guidance.continue.exempt' },
  ELECTRONIC_AUTHORIZATION_REQUIRED: { kind: 'authorization_application', ctaKey: 'guidance.continue.eta' },
  CONDITIONAL: { kind: 'conditional_guidance', ctaKey: 'guidance.continue.partial' }
}

// The primary continuation for a guidance result. Blocked ONLY when the gap
// prevents safe preparation (no known disposition); a known disposition with
// residual gaps continues "with available guidance" (partial). Unknown shapes
// fail safe to blocked — the guidance page must never dead-end silently, but
// it must never invent a route either.
export function continuationMeta(g) {
  if (!g || typeof g !== 'object') {
    return { blocked: true, kind: null, ctaKey: null, partial: false, blockers: [] }
  }
  const disp = g.guidance && typeof g.guidance === 'object' ? g.guidance.disposition : undefined
  const entry = CONTINUATION_KIND[disp]
  const blockers = [
    ...(Array.isArray(g.missing_fields) ? g.missing_fields : []),
    ...(Array.isArray(g.contradictions) ? g.contradictions : [])
  ]
  if (entry && g.status === 'KIMI_PRIMARY') {
    return { blocked: false, kind: entry.kind, ctaKey: entry.ctaKey,
      partial: disp === 'CONDITIONAL', blockers: [] }
  }
  if (entry && g.status === 'KIMI_UNCERTAIN') {
    return { blocked: false, kind: entry.kind, ctaKey: 'guidance.continue.partial',
      partial: true, blockers }
  }
  return { blocked: true, kind: null, ctaKey: null, partial: false,
    blockers: blockers.length ? blockers : ['disposition'] }
}

// ---------------------------------------------------------------------------
// Passport profile display + prefill (upload-first Step 1).

// Age is ALWAYS derived from the date of birth and today — never typed by OCR
// or a model. Returns null for anything unparseable (the UI then leaves the
// age field editable instead of showing a wrong number).
export function deriveAge(birthIso, todayIso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(birthIso || ''))
  const t = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(todayIso || ''))
  if (!m || !t) return null
  const [by, bm, bd] = [Number(m[1]), Number(m[2]), Number(m[3])]
  const [ty, tm, td] = [Number(t[1]), Number(t[2]), Number(t[3])]
  const age = ty - by - ((tm < bm || (tm === bm && td < bd)) ? 1 : 0)
  return age >= 0 && age <= 130 ? age : null
}

// Order in which extracted profile fields are shown to the applicant.
export const PROFILE_FIELD_ORDER = [
  'full_name', 'surname', 'given_names', 'passport_number', 'nationality',
  'issuing_country', 'birth_date', '_age', 'sex', 'expiry_date', 'issue_date',
  'place_of_birth', 'issuing_authority'
]

// Render-ready rows for the extracted-passport preview. Every row carries its
// provenance and whether the applicant must confirm it; unknown/missing fields
// simply do not appear (never invented).
export function profileRows(profile) {
  const fields = profile && typeof profile === 'object' && profile.fields &&
    typeof profile.fields === 'object' ? profile.fields : {}
  const rows = []
  for (const key of PROFILE_FIELD_ORDER) {
    const f = fields[key]
    if (!f || f.value == null || f.value === '') continue
    const conf = typeof f.confidence === 'number' ? f.confidence : 0
    rows.push({
      key,
      labelKey: 'ppf.' + (key === '_age' ? 'age' : key),
      value: String(f.value),
      confidence: conf,
      source: f.source === 'mrz' ? 'mrz' : f.source === 'derived' ? 'derived' : 'ocr',
      needsConfirm: f.needs_confirmation === true,
      note: typeof f.note === 'string' ? f.note : '',
      level: f.needs_confirmation === true ? 'bad' : conf >= 0.9 ? 'ok' : 'mid'
    })
  }
  return rows
}

// The wizard-answer prefill from a profile + applicant edits made in the
// preview. Edits win over extracted values; empty edits fall back.
export function prefillWithEdits(profile, edits = {}) {
  const base = profile && typeof profile === 'object' && profile.prefill &&
    typeof profile.prefill === 'object' ? { ...profile.prefill } : {}
  const keyMap = {
    full_name: 'full_name', surname: 'surname', given_names: 'given_names',
    passport_number: 'passport_number', nationality: 'passport_nationality',
    issuing_country: 'passport_issuing_country', birth_date: 'birth_date',
    sex: 'sex', expiry_date: 'passport_expiry_date'
  }
  for (const [profKey, val] of Object.entries(edits || {})) {
    const ansKey = keyMap[profKey]
    if (ansKey && val != null && String(val).trim() !== '') base[ansKey] = String(val).trim()
  }
  // Age always re-derives from the (possibly edited) date of birth.
  if (base.birth_date) {
    const age = deriveAge(base.birth_date, new Date().toISOString().slice(0, 10))
    if (age != null) base.age = age
    else delete base.age
  }
  return base
}

// ---------------------------------------------------------------------------
// Route checklist display helpers (items come from the backend checklist).
const CHECKLIST_STATUS = {
  provided: { tone: 'ok', i18nKey: 'checklist.provided' },
  pending: { tone: 'pending', i18nKey: 'checklist.pending' },
  auto: { tone: 'info', i18nKey: 'checklist.auto' },
  prepared_later: { tone: 'info', i18nKey: 'checklist.preparedLater' }
}

export function checklistStatusMeta(status) {
  return CHECKLIST_STATUS[status] || { tone: 'pending', i18nKey: 'checklist.pending' }
}

export function checklistCounts(items) {
  const list = Array.isArray(items) ? items : []
  const required = list.filter((i) => i && i.required && i.kind === 'document')
  const missing = required.filter((i) => i.status === 'pending')
  return { total: list.length, required: required.length, missing: missing.length,
    complete: required.length > 0 && missing.length === 0 }
}

export const RESIDENCE_STATUS_OPTIONS = [
  'citizen', 'permanent_resident', 'temporary_resident', 'student', 'worker',
  'refugee_status_holder', 'asylum_seeker', 'stateless_resident', 'visitor', 'other'
]
