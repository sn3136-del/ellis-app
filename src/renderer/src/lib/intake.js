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

export const RESIDENCE_STATUS_OPTIONS = [
  'citizen', 'permanent_resident', 'temporary_resident', 'student', 'worker',
  'refugee_status_holder', 'asylum_seeker', 'stateless_resident', 'visitor', 'other'
]
