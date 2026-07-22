// Portal-adapter framework — the strict, versioned contract every country/
// portal adapter must satisfy. Country-specific logic lives ONLY in adapters;
// the orchestrator and state machine never hard-code a portal.
//
// Runtime is plain Node ESM (no Electron import) so the whole framework runs
// under `node --test`. Types are expressed as JSDoc typedefs — this repo is
// plain JavaScript, so a typedef + a runtime validator is how we get "typed
// and versioned" without adding a TypeScript toolchain.

/**
 * @typedef {'automated'|'applicant'|'prohibited'} PolicyMode
 * A capability is either safe to automate, must be applicant-controlled, or
 * must never happen. Unknown rules default to 'applicant' (conservative).
 */

/**
 * @typedef {Object} FieldMapping
 * @property {string} field        Normalized applicant field key.
 * @property {string} selector     Deterministic Playwright-style selector.
 * @property {string} [fallbackHint] Non-sensitive Stagehand observe() hint.
 * @property {boolean} [sensitive] If true, value is never sent to an LLM.
 */

/**
 * @typedef {Object} PortalAdapter
 * @property {string} adapterId              Stable unique id, e.g. "mockland-tourist-v1".
 * @property {number} adapterVersion         Integer, bumped on any behavior change.
 * @property {string} destinationCountry
 * @property {string} visaType
 * @property {string} portalOperator
 * @property {string[]} approvedDomains      Navigation allowlist. Empty = nothing allowed.
 * @property {string} registrationUrl
 * @property {string} loginUrl
 * @property {string} applicationUrl
 * @property {string[]} paymentDomains
 * @property {string} appointmentUrl
 * @property {string[]} supportedLanguages
 * @property {string[]} requiredApplicantFields
 * @property {string[]} requiredDocuments
 * @property {FieldMapping[]} registrationMappings
 * @property {FieldMapping[]} loginMappings
 * @property {FieldMapping[]} applicationMappings
 * @property {string[]} allowedActions       e.g. ['navigate','fill','click','upload','read'].
 * @property {string[]} prohibitedActions
 * @property {'none'|'link'|'code'} emailVerification
 * @property {'none'|'sms'|'app'|'email'} otp
 * @property {{detect:string}} captcha        Selector/text that signals a CAPTCHA is present.
 * @property {{minLength:number,requireUpper:boolean,requireDigit:boolean,requireSymbol:boolean,maxLength:number}} passwordRequirements
 * @property {PolicyMode} paymentPolicy
 * @property {PolicyMode} thirdPartyPaymentPolicy
 * @property {'list'|'calendar'|'none'} appointmentSearch
 * @property {PolicyMode} appointmentBooking
 * @property {PolicyMode} reschedulePolicy
 * @property {string} cancellationRisks       Human-readable warning.
 * @property {PolicyMode} representativeSubmission
 * @property {boolean} personalDeclarationRequired
 * @property {'page'|'api'|'fixed'|'unknown'} feeDiscovery
 * @property {string} paymentSuccessSignal
 * @property {string} appointmentSuccessSignal
 * @property {string} submissionSuccessSignal
 * @property {string} confirmationExtraction  Selector/key for the reference number.
 * @property {string} receiptExtraction
 * @property {string} resumeBehavior
 * @property {string[]} knownFailureStates
 * @property {{searchMinIntervalMs:number,maxChecksPerDay:number}} rateLimits
 * @property {string} portalPolicyReviewDate  ISO date the policy was last reviewed.
 * @property {'mock'|'implemented'|'tested'|'production_approved'|'disabled'} productionApprovalStatus
 * @property {boolean} productionEnabled       Feature flag; must be false unless approved.
 * @property {Object} driver                   Adapter's imperative hooks (see MockPortal usage).
 */

// Conservative defaults applied to every adapter before validation. When a
// portal's rule is unknown, the safe answer is: applicant controls it, no
// automated payment, no automated final submission, explicit review required.
export const CONSERVATIVE_DEFAULTS = Object.freeze({
  supportedLanguages: ['en'],
  allowedActions: ['navigate', 'read'],
  prohibitedActions: [
    'solve_captcha', 'bypass_bot_detection', 'spoof_fingerprint',
    'rotate_proxy', 'evade_rate_limit', 'bypass_waiting_room',
    'access_other_account', 'intercept_otp', 'accept_declaration',
    'navigate_unapproved_domain'
  ],
  emailVerification: 'link',
  otp: 'none',
  passwordRequirements: { minLength: 12, requireUpper: true, requireDigit: true, requireSymbol: true, maxLength: 64 },
  paymentPolicy: 'applicant',
  thirdPartyPaymentPolicy: 'applicant',
  appointmentSearch: 'none',
  appointmentBooking: 'applicant',
  reschedulePolicy: 'applicant',
  representativeSubmission: 'applicant',
  personalDeclarationRequired: true,
  feeDiscovery: 'unknown',
  reschedulePolicyCancellationRisk: true,
  rateLimits: { searchMinIntervalMs: 60_000, maxChecksPerDay: 24 },
  productionApprovalStatus: 'mock',
  productionEnabled: false
})

const REQUIRED_STRING_FIELDS = [
  'adapterId', 'destinationCountry', 'visaType', 'portalOperator',
  'registrationUrl', 'loginUrl', 'applicationUrl', 'appointmentUrl',
  'cancellationRisks', 'paymentSuccessSignal', 'appointmentSuccessSignal',
  'submissionSuccessSignal', 'confirmationExtraction', 'receiptExtraction',
  'resumeBehavior', 'portalPolicyReviewDate'
]

const POLICY_FIELDS = ['paymentPolicy', 'thirdPartyPaymentPolicy', 'appointmentBooking', 'reschedulePolicy', 'representativeSubmission']
const VALID_POLICY = new Set(['automated', 'applicant', 'prohibited'])
const VALID_APPROVAL = new Set(['mock', 'implemented', 'tested', 'production_approved', 'disabled'])

/** Merge conservative defaults under the adapter's explicit values. */
export function withDefaults(adapter) {
  const d = CONSERVATIVE_DEFAULTS
  return {
    adapterVersion: 1,
    approvedDomains: [],
    paymentDomains: [],
    requiredApplicantFields: [],
    requiredDocuments: [],
    registrationMappings: [],
    loginMappings: [],
    applicationMappings: [],
    knownFailureStates: [],
    captcha: { detect: '' },
    supportedLanguages: d.supportedLanguages,
    allowedActions: d.allowedActions,
    prohibitedActions: d.prohibitedActions,
    emailVerification: d.emailVerification,
    otp: d.otp,
    passwordRequirements: d.passwordRequirements,
    paymentPolicy: d.paymentPolicy,
    thirdPartyPaymentPolicy: d.thirdPartyPaymentPolicy,
    appointmentSearch: d.appointmentSearch,
    appointmentBooking: d.appointmentBooking,
    reschedulePolicy: d.reschedulePolicy,
    representativeSubmission: d.representativeSubmission,
    personalDeclarationRequired: d.personalDeclarationRequired,
    feeDiscovery: d.feeDiscovery,
    rateLimits: d.rateLimits,
    productionApprovalStatus: d.productionApprovalStatus,
    productionEnabled: d.productionEnabled,
    ...adapter
  }
}

/**
 * Validate an adapter against the contract. Returns { ok, errors, adapter }.
 * A failing adapter must never be used — the orchestrator refuses to run it.
 */
export function validateAdapter(raw) {
  const errors = []
  const adapter = withDefaults(raw || {})

  for (const f of REQUIRED_STRING_FIELDS) {
    if (!adapter[f] || typeof adapter[f] !== 'string') errors.push(`missing/invalid string field: ${f}`)
  }
  if (!Number.isInteger(adapter.adapterVersion) || adapter.adapterVersion < 1) errors.push('adapterVersion must be a positive integer')
  for (const f of POLICY_FIELDS) {
    if (!VALID_POLICY.has(adapter[f])) errors.push(`${f} must be one of automated|applicant|prohibited`)
  }
  if (!VALID_APPROVAL.has(adapter.productionApprovalStatus)) errors.push('invalid productionApprovalStatus')

  // Safety invariants — hard, non-negotiable.
  if ((adapter.prohibitedActions || []).includes('solve_captcha') === false) {
    errors.push('prohibitedActions must include solve_captcha (CAPTCHA solving is never permitted)')
  }
  if (adapter.allowedActions.some((a) => adapter.prohibitedActions.includes(a))) {
    errors.push('an action cannot be both allowed and prohibited')
  }
  if (adapter.productionEnabled && adapter.productionApprovalStatus !== 'production_approved') {
    errors.push('productionEnabled requires productionApprovalStatus === production_approved')
  }
  // Navigation allowlist must cover the URLs the adapter itself uses.
  const domainsOf = (u) => { try { return new URL(u).host } catch { return null } }
  const used = [adapter.registrationUrl, adapter.loginUrl, adapter.applicationUrl, adapter.appointmentUrl].map(domainsOf).filter(Boolean)
  const allow = new Set(adapter.approvedDomains)
  for (const host of used) {
    if (!allow.has(host)) errors.push(`approvedDomains must include ${host} (used by an adapter URL)`)
  }
  // A driver with the imperative hooks is required to actually run.
  if (!adapter.driver || typeof adapter.driver !== 'object') errors.push('adapter.driver (imperative hooks) is required')

  return { ok: errors.length === 0, errors, adapter }
}

// Registry of validated adapters, keyed by adapterId. The orchestrator only
// ever selects from here.
const REGISTRY = new Map()

export function registerAdapter(raw) {
  const { ok, errors, adapter } = validateAdapter(raw)
  if (!ok) throw new Error(`Adapter ${raw?.adapterId || '(unknown)'} failed validation: ${errors.join('; ')}`)
  REGISTRY.set(adapter.adapterId, adapter)
  return adapter
}

export function getAdapter(adapterId) {
  return REGISTRY.get(adapterId) || null
}

/** Select an adapter by destination + visa type. Prefers the highest version. */
export function selectAdapter(destinationCountry, visaType) {
  const matches = [...REGISTRY.values()]
    .filter((a) => a.destinationCountry === destinationCountry && a.visaType === visaType)
    .sort((a, b) => b.adapterVersion - a.adapterVersion)
  return matches[0] || null
}

export function listAdapters() {
  return [...REGISTRY.values()].map((a) => ({
    adapterId: a.adapterId, adapterVersion: a.adapterVersion,
    destinationCountry: a.destinationCountry, visaType: a.visaType,
    portalOperator: a.portalOperator, productionApprovalStatus: a.productionApprovalStatus,
    productionEnabled: a.productionEnabled
  }))
}

export function _clearRegistryForTests() { REGISTRY.clear() }
