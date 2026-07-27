// Typed client for the Ellis visa backend (FastAPI). The Electron renderer uses
// this to drive the production multi-user backend over authenticated HTTP.
//
// In development the backend runs at http://localhost:8000 (see backend/ +
// docker-compose). Auth: Clerk session token in production; the dev token +
// org/user headers locally. Every method returns parsed JSON or throws.

const BASE = (typeof process !== 'undefined' && process.env?.ELLIS_BACKEND_URL) || 'http://localhost:8000'

function authHeaders(session) {
  // session = { token, orgId, userId }. In production `token` is the Clerk JWT
  // and org/user are derived server-side; the headers are dev-mode convenience.
  return {
    'content-type': 'application/json',
    authorization: `Bearer ${session?.token || 'dev-token'}`,
    'x-org-id': session?.orgId || '',
    'x-user-id': session?.userId || ''
  }
}

// Human-readable message from a FastAPI error detail. Structured details
// ({reason, message, detail, ...}) carry their own honest explanation — the
// applicant must never be shown a bare "HTTP 409" when one exists.
export function errorMessageFrom(detail, status) {
  if (typeof detail === 'string' && detail) return detail
  if (detail && typeof detail === 'object') {
    for (const key of ['message', 'detail', 'reason']) {
      const v = detail[key]
      if (typeof v === 'string' && v.trim()) return v
    }
  }
  return `HTTP ${status}`
}

async function call(method, path, session, body) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: authHeaders(session),
    body: body === undefined ? undefined : JSON.stringify(body)
  })
  const text = await res.text()
  const data = text ? JSON.parse(text) : {}
  if (!res.ok) {
    const detail = data.detail
    const err = new Error(errorMessageFrom(detail, res.status))
    err.status = res.status
    err.detail = detail // structured payloads (e.g. resolve 422 {missing_fields})
    throw err
  }
  return data
}

// Runtime-mode probe. GET {base}/capabilities and return its "runtime_mode"
// field (one of: test | local_mock_demo | tripcom_evaluation | staging |
// production). FAIL-SAFE: on ANY error — network failure, non-2xx, bad JSON,
// missing/empty field — return 'production' so no simulated/demo surface can
// ever appear by accident.
export async function fetchRuntimeMode(baseUrl) {
  try {
    const res = await fetch(`${baseUrl || BASE}/capabilities`, {
      headers: authHeaders(null),
      signal: typeof AbortSignal !== 'undefined' && AbortSignal.timeout ? AbortSignal.timeout(5000) : undefined
    })
    if (!res.ok) return 'production'
    const data = await res.json()
    const mode = data && typeof data.runtime_mode === 'string' ? data.runtime_mode.trim() : ''
    return mode || 'production'
  } catch {
    return 'production'
  }
}

export function createVisaClient(session) {
  return {
    base: BASE,
    capabilities: () => call('GET', '/capabilities', session),
    listAdapters: () => call('GET', '/adapters', session),

    createCase: (payload) => call('POST', '/cases', session, payload),
    getCase: (id) => call('GET', `/cases/${id}`, session),

    addDocument: (id, doc) => call('POST', `/cases/${id}/documents`, session, doc),
    review: (id) => call('GET', `/cases/${id}/review`, session),
    approveDocument: (id, docId, edits = []) =>
      call('POST', `/cases/${id}/documents/${docId}/approve`, session, edits),

    updateAnswers: (id, answers) => call('POST', `/cases/${id}/answers`, session, { answers }),

    setPreferences: (id, prefs) => call('POST', `/cases/${id}/preferences`, session, { prefs }),
    createAuthorization: (id, payload) => call('POST', `/cases/${id}/authorization`, session, payload),

    start: (id) => call('POST', `/cases/${id}/start`, session),
    // Human-handoff signals. `body` carries token (email verify) or slot_id.
    signal: (id, name, body = {}) => call('POST', `/cases/${id}/signals/${name}`, session, body),
    approveReview: (id) => call('POST', `/cases/${id}/signals/approve_review`, session, {}),
    signAuthorization: (id) => call('POST', `/cases/${id}/signals/sign_authorization`, session, {}),
    solveCaptcha: (id) => call('POST', `/cases/${id}/signals/solve_captcha`, session, {}),
    verifyEmail: (id, token) => call('POST', `/cases/${id}/signals/verify_email`, session, { token }),
    approvePayment: (id) => call('POST', `/cases/${id}/signals/approve_payment`, session, {}),
    completePayment: (id) => call('POST', `/cases/${id}/signals/complete_payment`, session, {}),
    selectAppointment: (id, slotId) =>
      call('POST', `/cases/${id}/signals/select_appointment`, session, { slot_id: slotId }),
    completeDeclaration: (id) => call('POST', `/cases/${id}/signals/complete_declaration`, session, {}),
    // Dynamic missing-information answers (handoff: additional_information).
    // `answers` = {questionKey: value}; dates may be MM/DD/YYYY (backend canonicalizes).
    provideInformation: (id, answers) =>
      call('POST', `/cases/${id}/signals/provide_information`, session, { answers }),
    // Payment details for Ellis to fill on the OFFICIAL portal (handoff:
    // payment_credentials). card = {holder, number, expiry: 'MM/YY', cvv} —
    // sent once, vault-transported server-side, never stored; the applicant
    // always clicks the portal's own payment confirmation personally.
    // {manual: true} instead: the applicant will type the card in the secure
    // window personally.
    providePaymentDetails: (id, body) =>
      call('POST', `/cases/${id}/signals/provide_payment_details`, session, body),

    appointment: (id) => call('GET', `/cases/${id}/appointment`, session),
    audit: (id) => call('GET', `/cases/${id}/audit`, session),

    // Live portal progress (applicant-safe: real checkpoints only, never
    // selectors/PII). Polled while Ellis works in the background.
    caseProgress: (id) => call('GET', `/cases/${id}/progress`, session),
    // Applicant-triggered retry from the last safe reversible checkpoint.
    retryPortal: (id) => call('POST', `/cases/${id}/portal/retry`, session, {}),
    // Rebuild the live portal page after a session loss (secure window must
    // show the real form, never a blank tab). Reversible work only.
    restorePortal: (id) => call('POST', `/cases/${id}/portal/restore`, session, {}),
    // Applicant-requested fee read from the portal's CURRENT page.
    readPortalFee: (id) => call('POST', `/cases/${id}/portal/read-fee`, session, {}),
    // Contact details the portal will use for verification codes — confirmed
    // explicitly before Ellis opens the official portal.
    getContactConfirmation: (id) => call('GET', `/cases/${id}/contact-confirmation`, session),
    confirmContact: (id, body) => call('POST', `/cases/${id}/contact-confirmation`, session, body),

    // Adapter administration (Phase 2)
    adminListAdapters: () => call('GET', '/admin/adapters', session),
    adminCoverage: () => call('GET', '/admin/coverage', session),
    adminCreateAdapter: (payload) => call('POST', '/admin/adapters', session, payload),
    adminGetAdapter: (id) => call('GET', `/admin/adapters/${id}`, session),
    adminUpdateAdapter: (id, config) => call('PUT', `/admin/adapters/${id}`, session, config),
    adminTransition: (id, toState, evidence = {}) =>
      call('POST', `/admin/adapters/${id}/transition`, session, { to_state: toState, evidence }),
    adminKill: (id, reason = '') => call('POST', `/admin/adapters/${id}/kill`, session, { reason }),
    adminClearKill: (id) => call('POST', `/admin/adapters/${id}/clear-kill`, session, {}),
    adminRollback: (id, toVersion) => call('POST', `/admin/adapters/${id}/rollback`, session, { to_version: toVersion }),

    // Internationalization (Phase 6)
    i18nLanguages: () => call('GET', '/i18n/languages', session),
    translate: (text, targetLang, sourceLang = 'auto') =>
      call('POST', '/i18n/translate', session, { text, target_lang: targetLang, source_lang: sourceLang }),
    assistantIdentity: (lang = 'en') => call('GET', `/assistant/identity?lang=${encodeURIComponent(lang)}`, session),

    // First-run administrator setup (Phase 7). Secrets are sent once and never
    // returned — status/rotate expose only redacted fingerprints.
    setupStatus: () => call('GET', '/setup/status', session),
    saveSetup: (payload) => call('POST', '/setup', session, payload),
    testSetupComponent: (component) => call('POST', `/setup/test/${component}`, session, {}),
    sendTestEmail: (to) => call('POST', '/setup/email/test', session, { to }),
    rotateSetupComponent: (component, value) => call('POST', `/setup/rotate/${component}`, session, { value }),
    revokeSetupComponent: (component) => call('POST', `/setup/revoke/${component}`, session, {}),

    // Document preview (Phase 13): short-lived signed URL for in-app rendering.
    documentPreviewUrl: (id, docId) => call('GET', `/cases/${id}/documents/${docId}/url`, session),

    // Personal-test gate + route data
    livePreflight: (id) => call('GET', `/cases/${id}/live-preflight`, session),
    passportValidity: (id) => call('GET', `/cases/${id}/passport-validity`, session),
    routeReadiness: (params) => call('GET', `/routes/readiness?${new URLSearchParams(params)}`, session),
    routeFees: (params) => call('GET', `/routes/fees?${new URLSearchParams(params)}`, session),
    routeCoverage: () => call('GET', '/routes/coverage', session),
    caseEmails: (id) => call('GET', `/cases/${id}/emails`, session),

    // Privacy (Phase 10) + ops (Phase 11)
    exportCase: (id) => call('GET', `/cases/${id}/export`, session),
    exportOrg: () => call('GET', '/export', session),
    deleteCase: (id) => call('DELETE', `/cases/${id}`, session),
    metrics: () => call('GET', '/metrics', session),

    // Native e-signature (Phase 3): prepare returns the exact document + hash +
    // a short-lived step-up token; sign submits consent/intent/typed-or-drawn.
    prepareAuthorization: (id, payload) =>
      call('POST', `/cases/${id}/authorization/prepare`, session, payload),
    signAuthorization: (id, payload) =>
      call('POST', `/cases/${id}/authorization/sign`, session, payload),

    // Human-handoff signals.
    approveReschedule: (id) => call('POST', `/cases/${id}/signals/approve_reschedule`, session, {}),
    cancel: (id) => call('POST', `/cases/${id}/signals/cancel`, session, {}),

    // MOCK-ONLY: the verification token the mock portal "emailed" (dev demos).
    mockVerification: (id) => call('GET', `/cases/${id}/mock/verification`, session),

    // Snapshot route-intake (applicant "Start your visa" wizard).
    snapshotInfo: () => call('GET', '/snapshot/info', session),
    snapshotRegistries: () => call('GET', '/snapshot/registries', session),
    createIntake: (body) => call('POST', '/intake', session, body),
    listIntakes: () => call('GET', '/intake', session),
    getIntake: (id) => call('GET', `/intake/${id}`, session),
    updateIntake: (id, body) => call('PUT', `/intake/${id}`, session, body),
    resolveIntake: (id) => call('POST', `/intake/${id}/resolve`, session, {}),
    // Kimi-primary immediate route guidance (AI-generated; cached per route).
    routeGuidance: (id) => call('POST', `/intake/${id}/guidance`, session, {}),
    // Passport upload at intake Step 1: the existing OCR/MRZ pipeline extracts
    // a deterministic profile the applicant confirms before it prefills answers.
    uploadIntakePassport: (id, doc) => call('POST', `/intake/${id}/passport`, session, doc),
    getIntakePassport: (id) => call('GET', `/intake/${id}/passport`, session),
    // The primary continuation after guidance: creates/reuses the case, saves
    // the guidance + route checklist to it, and carries documents over.
    continueIntake: (id) => call('POST', `/intake/${id}/continue`, session, {}),
    // Route journey state saved on the case (guidance + two-pass verification,
    // route workflow type, checklist, pending health questions).
    caseChecklist: (id) => call('GET', `/cases/${id}/checklist`, session),
    // Document intake: the applicant's explicit Submit fulfils a requirement
    // (idempotent server-side); withdraw returns it to Needed; set-type labels
    // an ambiguous upload from the safe whitelist; complete validates the
    // whole checklist server-side and advances the EXISTING case.
    submitChecklistDoc: (id, itemId, documentId, confirm = false) =>
      call('POST', `/cases/${id}/checklist/${encodeURIComponent(itemId)}/submit`, session,
           { document_id: documentId, confirm }),
    withdrawChecklistDoc: (id, itemId) =>
      call('POST', `/cases/${id}/checklist/${encodeURIComponent(itemId)}/withdraw`, session, {}),
    // Attach an EXISTING case document (a reused file or a translation
    // artifact) to a requirement — binding only; Submit still fulfils it.
    bindChecklistDoc: (id, itemId, documentId) =>
      call('POST', `/cases/${id}/checklist/${encodeURIComponent(itemId)}/bind`, session,
           { document_id: documentId }),
    // Applicant-requested Kimi K3 machine translation of one document's
    // OCR-extracted text (raw bytes never leave the backend).
    translateDocument: (id, docId, target = null) =>
      call('POST', `/cases/${id}/documents/${docId}/translate`, session,
           target ? { target } : {}),
    setDocumentType: (id, docId, docType) =>
      call('POST', `/cases/${id}/documents/${docId}/set-type`, session, { doc_type: docType }),
    completeDocuments: (id) => call('POST', `/cases/${id}/checklist/complete`, session, {}),
    // Passport renewal: create/reuse the linked renewal case.
    startRenewal: (id, manual = false) =>
      call('POST', `/cases/${id}/renewal`, session, { manual }),
    routeEvidence: (resolutionId) => call('GET', `/snapshot/route-evidence/${resolutionId}`, session),

    // On-demand route research (auto-started by resolve when a route is
    // missing/incomplete; the applicant UI polls until a terminal status).
    getResearchJob: (id) => call('GET', `/research-jobs/${id}`, session),
    resumeResearchJob: (id) => call('POST', `/research-jobs/${id}/resume`, session, {}),

    // Isolated per-case browser session (Browserbase Live View). The live-view
    // URL is SHORT-LIVED and sensitive: fetch a fresh one every time, keep it
    // only in component state, and NEVER cache, log, or persist it.
    createBrowserSession: (id) => call('POST', `/cases/${id}/browser-session`, session, {}),
    browserLiveView: (id) => call('GET', `/cases/${id}/browser-session/live-view`, session),
    closeBrowserSession: (id) => call('DELETE', `/cases/${id}/browser-session`, session),

    // Standing authorization (§5): one versioned grant at onboarding covers
    // routine actions; payment always stays a separate exact-amount approval.
    getStandingAuthorization: (id, locale = 'en') =>
      call('GET', `/cases/${id}/standing-authorization?locale=${encodeURIComponent(locale)}`, session),
    grantStandingAuthorization: (id, body = {}) =>
      call('POST', `/cases/${id}/standing-authorization`, session, body),
    revokeStandingAuthorization: (id, reason = '') =>
      call('DELETE', `/cases/${id}/standing-authorization`, session, { reason }),

    // Final review + exact-version signature (§7).
    getFinalReview: (id, locale = 'en') =>
      call('GET', `/cases/${id}/final-review?locale=${encodeURIComponent(locale)}`, session),
    createFinalReview: (id, locale = 'en') =>
      call('POST', `/cases/${id}/final-review?locale=${encodeURIComponent(locale)}`, session, {}),
    signFinalReview: (id, body) => call('POST', `/cases/${id}/final-review/sign`, session, body),

    // Exact-amount payment authorization state (§6).
    getPaymentAuthorization: (id) => call('GET', `/cases/${id}/payment-authorization`, session),
    // The confirmation echoes the exact amount the applicant SAW, so a stale
    // display can never approve a different figure.
    approvePaymentExact: (id, amountCents, currency) =>
      call('POST', `/cases/${id}/signals/approve_payment`, session,
           { amount_cents: amountCents, currency }),

    // Automated adapter factory (§10-§13, §33). The applicant only ever sees
    // simple progress labels — never adapter code or portal internals.
    buildConsentCopy: () => call('GET', '/adapter-build/consent-copy', session),
    requestAdapterBuild: (body) => call('POST', '/adapter-build/request', session, body),
    consentAdapterBuild: (id, locale = 'en') =>
      call('POST', `/adapter-build/${id}/consent`, session, { locale }),
    getAdapterBuild: (id) => call('GET', `/adapter-build/${id}`, session),
    resumeAdapterBuild: (id) => call('POST', `/adapter-build/${id}/resume`, session, {}),

    // Admin: adapter-factory queue + release controls (admin session required).
    adminFactoryQueue: () => call('GET', '/admin/adapter-factory/queue', session),
    adminFactoryCandidates: () => call('GET', '/admin/adapter-factory/candidates', session),
    adminFactoryEvidence: (candId, version) =>
      call('GET', `/admin/adapter-factory/candidates/${candId}/evidence${version ? `?version=${version}` : ''}`, session),
    adminFactoryRelease: (candId, version, tier) =>
      call('POST', '/admin/adapter-factory/release', session,
           { candidate_id: candId, version, tier }),
    adminFactoryQuarantine: (candId, version, reason) =>
      call('POST', '/admin/adapter-factory/quarantine', session,
           { candidate_id: candId, version, reason }),
    adminFactoryKill: (candId, reason) =>
      call('POST', '/admin/adapter-factory/kill', session, { candidate_id: candId, reason }),
    adminFactoryClearKill: (candId) =>
      call('POST', '/admin/adapter-factory/clear-kill', session, { candidate_id: candId }),
    adminFactoryRollback: (candId, tier, reason) =>
      call('POST', '/admin/adapter-factory/rollback', session,
           { candidate_id: candId, tier, reason }),
    adminFactoryReviewTasks: () => call('GET', '/admin/adapter-factory/review-tasks', session),
    adminFactoryResolveReview: (taskId, note, decision = 'resolved') =>
      call('POST', `/admin/adapter-factory/review-tasks/${taskId}/resolve`, session,
           { note, decision }),
    adminFactoryFailures: () => call('GET', '/admin/adapter-factory/failures', session),
    adminFactoryReleases: () => call('GET', '/admin/adapter-factory/releases', session),

    // Snapshot administration (admin session required).
    adminSnapshotCoverage: () => call('GET', '/admin/snapshot/coverage', session),
    adminSnapshotBatches: () => call('GET', '/admin/snapshot/batches', session),
    adminReviewQueue: () => call('GET', '/admin/snapshot/review-queue', session),
    adminResolveReview: (id, body) => call('POST', `/admin/snapshot/review-queue/${id}/resolve`, session, body),
    adminConflicts: () => call('GET', '/admin/snapshot/conflicts', session),
    adminRouteQueue: () => call('GET', '/admin/snapshot/route-queue', session),
    adminAdapterTasks: () => call('GET', '/admin/snapshot/adapter-tasks', session),
    adminResearchJobs: () => call('GET', '/admin/snapshot/research-jobs', session),
    // Manual snapshot reverification (admin session required). Body:
    // {destination_country, urls: [..], note, fee_confirmed, portal_confirmed}.
    adminReverify: (body) => call('POST', '/admin/snapshot/reverify', session, body),

    // Global route coverage (admin session required).
    adminGlobalCoverage: () => call('GET', '/admin/global/coverage', session),
    adminGlobalUnsupported: (limit = 100) =>
      call('GET', `/admin/global/unsupported?limit=${limit}`, session)
  }
}

// The backend pauses at these handoff strings (app/workflow.py _pause). Each maps
// to the applicant UI surface that resolves it. Keep in sync with the backend.
export const HANDOFF_UI = {
  review: 'ReviewPanel',            // approve_review
  authorization: 'SignatureModal',  // prepare+sign, then sign_authorization
  captcha: 'LiveViewModal',         // solve_captcha (Ellis never solves it)
  email_verification: 'LiveViewModal', // verify_email(token)
  otp: 'LiveViewModal',
  identity: 'LiveViewModal',
  login_challenge: 'LiveViewModal',
  portal_form: 'LiveViewModal',     // finish form items only you may complete
  payment_approval: 'PaymentApprove', // approve_payment (shows fee)
  fee_confirmation: 'PaymentApprove', // applicant confirms the exact portal fee
  payment_credentials: 'PaymentDetailsModal', // provide_payment_details (Ellis fills the portal)
  payment: 'PaymentModal',          // complete_payment (applicant confirms on the portal)
  three_ds: 'PaymentModal',
  appointment_selection: 'AppointmentCalendar', // select_appointment(slot_id)
  no_availability: 'AppointmentCalendar',
  reschedule_approval: 'RescheduleConfirm',     // approve_reschedule
  personal_declaration: 'DeclarationModal',     // complete_declaration
  final_review: 'FinalReviewModal',             // review + sign the exact version
  additional_information: 'AdditionalInfoModal' // provide_information(answers)
}

// Which signal resolves a given handoff (used by the case flow to advance).
export const HANDOFF_SIGNAL = {
  review: 'approve_review',
  captcha: 'solve_captcha',
  email_verification: 'verify_email',
  otp: 'verify_email',
  payment_approval: 'approve_payment',
  fee_confirmation: 'approve_payment',
  payment_credentials: 'provide_payment_details',
  payment: 'complete_payment',
  appointment_selection: 'select_appointment',
  reschedule_approval: 'approve_reschedule',
  personal_declaration: 'complete_declaration',
  final_review: 'start',
  portal_form: 'start',
  additional_information: 'provide_information'
}

// Human-readable label + one-line guidance per handoff, for the flow header.
export const HANDOFF_COPY = {
  review: ['Review your answers', 'Confirm every extracted value before Ellis proceeds.'],
  authorization: ['Sign the Ellis authorization', 'Authorize Ellis to act for you on this application.'],
  captcha: ['Solve the CAPTCHA', 'Complete it yourself in the secure window — Ellis never solves CAPTCHAs.'],
  email_verification: ['Verify your email', 'Open the verification link the portal emailed you.'],
  otp: ['Enter the one-time code', 'Type the code from your authenticator or SMS in the secure window.'],
  identity: ['Identity check', 'Complete the identity step in the secure window.'],
  login_challenge: ['Portal login challenge', 'Complete the portal sign-in challenge in the secure window.'],
  portal_form: ['Finish the highlighted items on the official form',
    'A few items on the government form need your personal input — including any declaration only you may sign. Complete them in the secure window, then continue.'],
  payment_approval: ['Review and confirm payment', 'Confirm the official fee before payment begins.'],
  fee_confirmation: ['Confirm the official fee', 'Enter the exact fee the official portal shows, then confirm to continue to payment.'],
  payment_credentials: ['Enter your payment details',
    'Ellis fills them on the official portal for you — used once, never stored. You review the filled page and confirm the payment yourself.'],
  payment: ['Confirm the payment', 'Review the payment page in the secure window and confirm it on the official portal yourself.'],
  three_ds: ['Confirm 3-D Secure', 'Approve the bank verification in the secure window.'],
  appointment_selection: ['Choose an appointment', 'Pick a qualifying slot from your calendar.'],
  no_availability: ['No slots yet', 'Nothing matches your preferences yet — Ellis keeps watching.'],
  reschedule_approval: ['Approve reschedule', 'An earlier slot is available — approve moving to it.'],
  personal_declaration: ['Sign the declaration', 'Only you can sign the government declaration, under penalty of perjury.'],
  final_review: ['Final review & signature', 'Review the exact final application and sign it before Ellis submits.'],
  additional_information: ['Additional information required', 'The official application form needs a few more details. Ellis saved everything else and will continue exactly where it paused.']
}
