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

async function call(method, path, session, body) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: authHeaders(session),
    body: body === undefined ? undefined : JSON.stringify(body)
  })
  const text = await res.text()
  const data = text ? JSON.parse(text) : {}
  if (!res.ok) {
    const err = new Error(data.detail || `HTTP ${res.status}`)
    err.status = res.status
    throw err
  }
  return data
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

    appointment: (id) => call('GET', `/cases/${id}/appointment`, session),
    audit: (id) => call('GET', `/cases/${id}/audit`, session),

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
    mockVerification: (id) => call('GET', `/cases/${id}/mock/verification`, session)
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
  payment_approval: 'PaymentApprove', // approve_payment (shows fee)
  payment: 'PaymentModal',          // complete_payment (secure window; no card seen)
  three_ds: 'PaymentModal',
  appointment_selection: 'AppointmentCalendar', // select_appointment(slot_id)
  no_availability: 'AppointmentCalendar',
  reschedule_approval: 'RescheduleConfirm',     // approve_reschedule
  personal_declaration: 'DeclarationModal'      // complete_declaration
}

// Which signal resolves a given handoff (used by the case flow to advance).
export const HANDOFF_SIGNAL = {
  review: 'approve_review',
  captcha: 'solve_captcha',
  email_verification: 'verify_email',
  otp: 'verify_email',
  payment_approval: 'approve_payment',
  payment: 'complete_payment',
  appointment_selection: 'select_appointment',
  reschedule_approval: 'approve_reschedule',
  personal_declaration: 'complete_declaration'
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
  payment_approval: ['Approve the fee', 'Confirm the official fee before payment begins.'],
  payment: ['Pay the official fee', 'Enter your card directly in the portal — Ellis never sees it.'],
  three_ds: ['Confirm 3-D Secure', 'Approve the bank verification in the secure window.'],
  appointment_selection: ['Choose an appointment', 'Pick a qualifying slot from your calendar.'],
  no_availability: ['No slots yet', 'Nothing matches your preferences yet — Ellis keeps watching.'],
  reschedule_approval: ['Approve reschedule', 'An earlier slot is available — approve moving to it.'],
  personal_declaration: ['Sign the declaration', 'Only you can sign the government declaration, under penalty of perjury.']
}
