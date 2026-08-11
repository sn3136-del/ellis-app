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
    // Dynamic UI language: the backend translates the English catalog via
    // Kimi K3 (masked + cached; honest English fallback).
    i18nCatalog: (target, entries) =>
      call('POST', '/i18n/catalog', session, { target_lang: target, entries }),

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
    // The carry-in folder for an in-person route: what is in it, what is still
    // missing. 409 (packet_not_applicable) means Ellis files this route itself.
    // Tick-box questions the consular form needs, in plain words. Answering
    // them means the downloaded form needs nothing finished by hand.
    consularFormQuestions: (id) => call('GET', `/cases/${id}/form-questions`, session),
    // The official form itself, filled from the applicant's own answers.
    // {available:false} on routes Ellis has no verified form for.
    consularForm: (id) => call('GET', `/cases/${id}/consular-form`, session),
    appointmentPacket: (id) => call('GET', `/cases/${id}/appointment-packet`, session),
    // Where this applicant books, and what they have already booked. Ellis
    // never picks a slot: the applicant chooses in the secure window.
    appointmentBooking: (id) => call('GET', `/cases/${id}/appointment-booking`, session),
    recordAppointment: (id, startUtc, location, confirmationNo = '') =>
      call('POST', `/cases/${id}/appointment-booking`, session,
           { start_utc: startUtc, location, confirmation_no: confirmationNo }),
    // Find the consular post that serves this applicant's address (official
    // sources, verified before it can become a booking link).
    findConsularPost: (id) => call('POST', `/cases/${id}/find-consular-post`, session),
    // The signature the applicant draws in Ellis's pad — placed into the
    // consular form's own Signature cell. Their strokes, their act.
    saveSignature: (id, dataUrl) =>
      call('POST', `/cases/${id}/signature`, session, { image_base64: dataUrl }),
    // Government calendars that have NO account (Germany RK-Termin, Poland
    // e-Konsulat): walk the applicant's own secure window to the month view.
    // Every mission the system serves, by NAME — an applicant knows 'Beijing',
    // not that their consulate's code is 'peki'.
    calendarMissions: (id) => call('GET', `/cases/${id}/calendar/missions`, session),
    calendarOpen: (id, locationCode, categoryId = '') =>
      call('POST', `/cases/${id}/calendar/open?location_code=${encodeURIComponent(locationCode)}` +
           (categoryId ? `&category_id=${encodeURIComponent(categoryId)}` : ''), session, {}),
    // Read the month AFTER the applicant completed the portal's image check.
    calendarMonth: (id) => call('GET', `/cases/${id}/calendar/month`, session),
    // The folder itself, as one download. Kept out of `call` because the body
    // is a zip, not JSON — same auth headers, raw blob back.
    // The whole application as ONE PDF: cover, the filled official form, then
    // every document, each behind a page naming it — one thing to scroll,
    // print and hand over. format=zip still returns the separate originals.
    downloadAppointmentPacket: async (id, format = 'pdf') => {
      const res = await fetch(`${BASE}/cases/${id}/appointment-packet?format=${format}`,
                              { headers: authHeaders(session) })
      if (!res.ok) throw new Error(`packet download failed (HTTP ${res.status})`)
      return await res.blob()
    },
    // The filled official form as a PDF — the government's own blank with the
    // applicant's answers written into it. Raw bytes, so it bypasses `call`.
    downloadConsularForm: async (id) => {
      const res = await fetch(`${BASE}/cases/${id}/consular-form?download=true`,
                              { headers: authHeaders(session) })
      if (!res.ok) throw new Error(`form download failed (HTTP ${res.status})`)
      return await res.blob()
    },
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
    // Watch-only scroll relay: the click shield eats the wheel, so the deltas
    // ride to the backend, which scrolls the case's own live session.
    scrollBrowserSession: (id, deltaY) =>
      call('POST', `/cases/${id}/browser-session/scroll`, session, { delta_y: deltaY }),

    // Attended observation: the applicant's own consented run teaches Ellis a
    // portal whose form hides behind sign-in (or a human-only check). Ellis
    // records page STRUCTURE only, and only under the versioned consent.
    portalObservation: (id) => call('GET', `/cases/${id}/portal-observation`, session),
    portalObservationConsent: (id) => call('POST', `/cases/${id}/portal-observation/consent`, session, {}),
    portalObservationDecline: (id) => call('POST', `/cases/${id}/portal-observation/decline`, session, {}),
    portalObservationStart: (id) => call('POST', `/cases/${id}/portal-observation/start`, session, {}),
    portalObservationFinish: (id) => call('POST', `/cases/${id}/portal-observation/finish`, session, {}),

    // Standing authorization (§5): granted only inside the signature ceremony
    // (signAuthorization) — read and revoke are the applicant's actions here;
    // payment always stays a separate exact-amount approval.
    getStandingAuthorization: (id, locale = 'en') =>
      call('GET', `/cases/${id}/standing-authorization?locale=${encodeURIComponent(locale)}`, session),
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
      call('GET', `/admin/global/unsupported?limit=${limit}`, session),

    // ---- H1B edition (docs/H1B_ARCHITECTURE.md) ----------------------------
    // Two-party petition pipeline. Every payload carries the attorney
    // disclaimer; per-party authorization is enforced server-side — a denied
    // action surfaces its honest 403/409 reason, never a silent no-op.
    h1bCreateCase: (body) => call('POST', '/h1b/cases', session, body),
    h1bEmployerProfiles: () => call('GET', '/h1b/employer-profiles', session),
    h1bCreateEmployerProfile: (body) => call('POST', '/h1b/employer-profiles', session, body),
    // Honest per-step status: blocked/ready/in_progress/awaiting_government/
    // verified/failed, plus the parties visible to THIS caller (party wall).
    h1bPipeline: (caseId) => call('GET', `/h1b/cases/${caseId}/pipeline`, session),
    // The guided walkthrough payload the pipeline surface renders: step cards,
    // who acts (party-scoped server-side), blockers, and next actions.
    h1bWalkthrough: (caseId, locale = 'en') =>
      call('GET', `/h1b/cases/${caseId}/walkthrough?locale=${encodeURIComponent(locale)}`, session),
    // Ask Ellis. Replies + actions-taken (denied actions reported AS denied)
    // + suggestion chips; locale rides so the answer speaks the UI language.
    h1bAssistant: (caseId, { message, locale = 'en', history = [] } = {}) =>
      call('POST', `/h1b/cases/${caseId}/assistant`, session, { message, locale, history }),
    // Release a ready step into its real child filing case (acting party or
    // admin only; blocked steps 409 naming their unverified dependencies).
    h1bReleaseStep: (caseId, stepKey) =>
      call('POST', `/h1b/cases/${caseId}/steps/${stepKey}/release`, session, {}),
    // Record a verified government outcome. body: {receipts, offline_evidence_
    // document_id} — evidence discipline is server-side (EvidenceRequired 409;
    // the offline path is an administrator act).
    h1bVerifyStep: (caseId, stepKey, body = {}) =>
      call('POST', `/h1b/cases/${caseId}/steps/${stepKey}/verify`, session, body),
    // Prepare a government form (form_key: 'i-129' | 'eta-9035') from the
    // party-partitioned case facts: filled/missing counts, the missing list,
    // and the human-only signature lines (never signed by Ellis).
    h1bPrepareForm: (caseId, formKey, locale = 'en') =>
      call('POST', `/h1b/cases/${caseId}/forms/${encodeURIComponent(formKey)}/prepare?locale=${encodeURIComponent(locale)}`, session, {}),
    // Build the mail-ready paper I-129 packet (docs/H1B_PAPER_FILING.md):
    // lockbox addresses, wet-ink warnings, exhibits, download URL.
    h1bPaperPacket: (caseId, locale = 'en') =>
      call('POST', `/h1b/cases/${caseId}/paper-packet?locale=${encodeURIComponent(locale)}`, session, {}),
    // Deterministic RFE risk signals (docs/H1B_RFE_TAXONOMY.md) with curing
    // evidence per ground. Advisory only — never legal advice.
    h1bRfeRisks: (caseId, locale = 'en') =>
      call('GET', `/h1b/cases/${caseId}/counsel/rfe-risks?locale=${encodeURIComponent(locale)}`, session),
    // Narrative DRAFT of the given kind (e.g. support_letter) — always labeled
    // a draft, petitioner-facing.
    h1bNarrative: (caseId, kind, locale = 'en') =>
      call('POST', `/h1b/cases/${caseId}/counsel/narrative?locale=${encodeURIComponent(locale)}`, session, { kind }),
    // The indexed exhibit list for the petition (missing items included
    // honestly — the index never implies a complete pack).
    h1bEvidenceIndex: (caseId, locale = 'en') =>
      call('GET', `/h1b/cases/${caseId}/counsel/evidence-index?locale=${encodeURIComponent(locale)}`, session),
    // Write ONE party's own answers (petitioner job/wage/worksite facts or
    // beneficiary facts). The party wall lives server-side: only the acting
    // party (or an admin) may write, and attestation keys are rejected.
    h1bPartyAnswers: (caseId, role, answers) =>
      call('POST', `/h1b/cases/${caseId}/party/${encodeURIComponent(role)}/answers`, session, { answers }),

    // Deterministic OEWS/OFLC prevailing-wage LEVEL analysis over the case's
    // saved petitioner facts (SOC code + worksite + offered wage). A pure
    // offline computation server-side over cached official DOL wage data — no
    // live call at fill time. The response carries its source + as_of and the
    // honest caveats that break a naive reading (a broadened-area or statewide
    // fallback when DOL has no wage for the worksite's own metro area; an OEWS
    // "High Wage"/"Annual Wage" label that invalidates the 2080-hour
    // conversion). The four level wages and the level the offer clears are
    // SUGGESTIONS a human confirms — the wage level is a legal representation on
    // the LCA/I-129, never a value Ellis files.
    h1bWageAnalysis: (caseId) =>
      call('GET', `/h1b/cases/${caseId}/wage-analysis`, session),
    // Suggest SOC (occupation) + NAICS (industry) codes from free text via the
    // CDC NIOCCS classifier. Ranked with probabilities that double as an RFE
    // confidence signal. EVERY suggestion is a legal representation the
    // petitioner must confirm and type in themselves — nothing auto-fills a
    // filed answer. Honest-degrades ({available:false}) when the classifier is
    // unreachable or unconfigured.
    h1bClassifyOccupation: (caseId, { industry_text = '', occupation_text = '' } = {}) =>
      call('POST', `/h1b/cases/${caseId}/classify-occupation`, session,
           { industry_text, occupation_text })
  }
}

// ---- H1B wage-level + SOC/NAICS suggestion display models ------------------
// Pure, deterministic normalizers over the wage_api.py payloads, kept out of
// the React components so the honesty rules are unit-testable without a DOM.
// Doctrine carried here: every wage / level / code stays a SUGGESTION a human
// confirms; every result surfaces its source + as_of; and the DOL caveats that
// invalidate a naive reading are derived from structured flags, never swallowed.

const _ROMAN_LEVEL = { 1: 'I', 2: 'II', 3: 'III', 4: 'IV' }

// Lenient numeric coercion: strips currency/percent punctuation, returns null
// (never NaN, never 0) for anything unparseable so a missing wage never renders
// as "$0".
function _num(v) {
  if (v == null || v === '') return null
  const n = typeof v === 'number' ? v : Number(String(v).replace(/[^0-9.\-]/g, ''))
  return Number.isFinite(n) ? n : null
}

// Normalize the OEWS wage level marker to a roman numeral I–IV (accepts
// 1..4, "1".."4", or "I".."IV"); null for anything else.
function _normLevel(v) {
  if (v == null || v === '') return null
  const s = String(v).trim().toUpperCase()
  if (_ROMAN_LEVEL[s]) return _ROMAN_LEVEL[s]           // numeric -> roman
  if (['I', 'II', 'III', 'IV'].includes(s)) return s
  return null
}

// The four level wages as an ordered [{level, roman, wage}] list, tolerating an
// object ({'1':.., level1:.., Level1:..}) or a 4-element array.
function _levelWages(raw) {
  if (!raw || typeof raw !== 'object') return []
  const out = []
  for (const n of [1, 2, 3, 4]) {
    const w = Array.isArray(raw)
      ? raw[n - 1]
      : (raw[n] ?? raw[String(n)] ?? raw['level' + n] ?? raw['Level' + n] ?? raw['level_' + n])
    const num = _num(w)
    if (num != null) out.push({ level: n, roman: _ROMAN_LEVEL[n], wage: num })
  }
  return out
}

// Honest caveat list: structured DOL flags first (a broadened / statewide /
// national geography fallback; a High/Annual OEWS label), then any freeform
// caveat strings the backend already localized. Each entry is either
// { code } (UI localizes it) or { text } (render verbatim).
function _wageCaveats(p) {
  const out = []
  const geo = _num(p.geo_level ?? p.geoLvl ?? p.geo_lvl)
  if (geo === 2) out.push({ code: 'geo_broadened' })
  else if (geo === 3) out.push({ code: 'geo_statewide' })
  else if (geo != null && geo >= 4) out.push({ code: 'geo_national' })
  const label = String(p.label || '').toLowerCase()
  if (label.includes('high')) out.push({ code: 'label_high' })
  if (label.includes('annual')) out.push({ code: 'label_annual' })
  const extra = Array.isArray(p.caveats) ? p.caveats : []
  for (const c of extra) {
    if (typeof c === 'string' && c.trim()) out.push({ text: c.trim() })
    else if (c && typeof c === 'object' && (c.text || c.message)) {
      out.push({ text: String(c.text || c.message).trim() })
    }
  }
  return out
}

// Normalize a wage-analysis payload into an honest display model. Marks itself
// unavailable (honest-degrade) unless it actually carries the four level wages.
export function wageLevelView(payload) {
  const p = payload && typeof payload === 'object' ? payload : {}
  const levels = _levelWages(p.level_wages ?? p.levels ?? p.levelWages)
  const explicitlyUnavailable = p.available === false || p.unavailable === true
  const meets = typeof p.meets_prevailing === 'boolean' ? p.meets_prevailing
    : (typeof p.meets === 'boolean' ? p.meets : null)
  return {
    available: !explicitlyUnavailable && levels.length > 0,
    reason: p.reason || p.message || '',
    source: p.source || p.provider || '',
    asOf: p.as_of || p.asOf || '',
    socCode: p.soc_code || p.socCode || '',
    socTitle: p.soc_title || p.socTitle || '',
    areaName: p.area_name || p.areaName || p.area || '',
    geoLevel: _num(p.geo_level ?? p.geoLvl ?? p.geo_lvl),
    levels,
    wageUnit: p.wage_unit || p.unit || 'year',
    offeredWage: _num(p.offered_wage ?? p.offeredWage ?? p.wage_offer),
    offeredUnit: p.offered_unit || p.offeredUnit || p.wage_offer_unit || '',
    computedLevel: _normLevel(p.computed_level ?? p.computedLevel ?? p.wage_level),
    meetsPrevailing: meets,
    label: String(p.label || '').trim(),
    caveats: _wageCaveats(p)
  }
}

// Map a NIOCCS probability (0..1) to a confidence tier. The tiers double as an
// RFE risk signal — a low-confidence top match warns the petitioner to look
// harder before committing to a code.
function _confidenceTier(prob) {
  if (typeof prob !== 'number' || Number.isNaN(prob)) return 'unknown'
  if (prob >= 0.85) return 'high'
  if (prob >= 0.5) return 'medium'
  return 'low'
}

function _suggestions(raw) {
  if (!Array.isArray(raw)) return []
  return raw.map((s) => {
    const o = s && typeof s === 'object' ? s : {}
    const probability = _num(o.probability ?? o.Probability ?? o.confidence_score ?? o.score)
    return {
      code: String(o.code ?? o.Code ?? o.soc_code ?? o.naics_code ?? '').trim(),
      title: String(o.title ?? o.Title ?? o.name ?? '').trim(),
      probability,
      confidence: _confidenceTier(probability)
    }
  }).filter((s) => s.code || s.title)
}

// Normalize a classify-occupation payload into ranked SOC + NAICS suggestions.
// confirmRequired is ALWAYS true (the code is a legal representation regardless
// of confidence); lowConfidence flags a weak top occupation match for extra
// emphasis — it never suppresses the confirm-required note.
export function socSuggestionsView(payload) {
  const p = payload && typeof payload === 'object' ? payload : {}
  const explicitlyUnavailable = p.available === false || p.unavailable === true
  const occupation = _suggestions(p.occupation ?? p.occupations ?? p.soc ?? p.Occupation)
  const industry = _suggestions(p.industry ?? p.industries ?? p.naics ?? p.Industry)
  return {
    available: !explicitlyUnavailable && (occupation.length > 0 || industry.length > 0),
    reason: p.reason || p.message || '',
    source: p.source || p.provider || '',
    asOf: p.as_of || p.asOf || '',
    occupation,
    industry,
    confirmRequired: true,
    lowConfidence: occupation.length > 0 && occupation[0].confidence === 'low'
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
  // Account creation on portals that require one. The secure window is the
  // ONLY correct surface: Ellis never creates accounts and never handles a
  // password. Emitted by released_flow.register() and, until 2026-08-03,
  // absent from all three maps — the applicant got a generic "Action needed"
  // card with no panel and no way to resolve it, and the case stalled.
  credentials: 'LiveViewModal',
  // A file input the flow could not identify (specgen emits an upload_handoff
  // node). The applicant attaches it themselves in the secure window and Ellis
  // re-drives the form — same shape as portal_form.
  document_upload: 'LiveViewModal',
  // The portal's own terms. The applicant accepts them personally in the
  // secure window; Ellis never clicks agree to a government agreement.
  portal_terms_consent: 'LiveViewModal',
  // The portal refused the automated session outright (Cloudflare bot
  // management on Thailand's TDAC). The applicant clears the check in the
  // secure window and Ellis resumes in that trusted session.
  portal_verification: 'LiveViewModal',
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
  document_upload: 'start',          // re-drive: Ellis re-reads the form page
  portal_terms_consent: 'start',
  portal_verification: 'start',
  credentials: 'start',
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
  credentials: ['Create your portal account', 'The official portal requires an account. Create it yourself in the secure window — Ellis never handles your password.'],
  document_upload: ['Attach a document', 'The official form asks for a file Ellis could not attach for you. Add it in the secure window and Ellis continues.'],
  portal_verification: ["Confirm you are a real person",
    'The official portal wants to check a person is here before it opens the form. Complete its check in the secure window and Ellis carries on filling everything in for you.'],
  portal_terms_consent: ['Accept the portal terms', "Read and accept the official portal's own terms in the secure window — Ellis never agrees to them for you."],
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
