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
    capabilities: () => call('GET', '/capabilities', session),
    listAdapters: () => call('GET', '/adapters', session),

    createCase: (payload) => call('POST', '/cases', session, payload),
    getCase: (id) => call('GET', `/cases/${id}`, session),

    addDocument: (id, doc) => call('POST', `/cases/${id}/documents`, session, doc),
    review: (id) => call('GET', `/cases/${id}/review`, session),
    approveDocument: (id, docId, edits = []) =>
      call('POST', `/cases/${id}/documents/${docId}/approve`, session, edits),

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
    audit: (id) => call('GET', `/cases/${id}/audit`, session)
  }
}

// The handoff → UI mapping the screens use to render the right modal/panel.
export const HANDOFF_UI = {
  review: 'ReviewScreen',
  authorization: 'AuthorizationScreen',
  captcha: 'LiveViewModal',
  email_verification: 'LiveViewModal',
  otp: 'LiveViewModal',
  identity: 'LiveViewModal',
  payment: 'PaymentModal',
  appointment_selection: 'AppointmentCalendar',
  reschedule_approval: 'RescheduleConfirm',
  personal_declaration: 'LiveViewModal',
  no_availability: 'AppointmentCalendar'
}
