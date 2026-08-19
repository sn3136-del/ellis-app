// Typed client for the Ellis visa backend (FastAPI). The Electron renderer uses
// this to drive the production multi-user backend over authenticated HTTP.
//
// In development the backend runs at http://localhost:8000 (see backend/ +
// docker-compose). Auth: Clerk session token in production; the dev token +
// org/user headers locally. Every method returns parsed JSON or throws.

export const BASE = (typeof process !== 'undefined' && process.env?.ELLIS_BACKEND_URL) || 'http://localhost:8000'

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
    // realmId is the mission's AREA (national visas vs consular matters) and
    // categoryId the specific queue inside it. Both are real applicant choices
    // — RK-Termin's own lists — so neither is guessed.
    calendarOpen: (id, locationCode, categoryId = '', realmId = '') =>
      call('POST', `/cases/${id}/calendar/open?location_code=${encodeURIComponent(locationCode)}` +
           (realmId ? `&realm_id=${encodeURIComponent(realmId)}` : '') +
           (categoryId ? `&category_id=${encodeURIComponent(categoryId)}` : ''), session, {}),
    // Read the month AFTER the applicant completed the portal's image check.
    calendarMonth: (id) => call('GET', `/cases/${id}/calendar/month`, session),
    // The applicant's OWN pick, carried to the government site. `knownHrefs`
    // is the grid Ellis displayed, so a stale link cannot open a day they
    // never saw. Ellis never chooses the day and never submits the form.
    // The CAPTCHA challenge image, enlarged for legibility. A reading aid:
    // the applicant still types the answer in their own window.
    calendarCaptcha: (id) => call('GET', `/cases/${id}/calendar/captcha`, session),
    // The answer THE APPLICANT read and typed into Ellis. Ellis transcribes it
    // into the portal's field — it never reads the image itself.
    calendarCaptchaSubmit: (id, text) =>
      call('POST', `/cases/${id}/calendar/captcha`, session, { text }),
    // The times offered on one open day. Read-only — the month says which
    // DAYS are open; the times live on the day's own page.
    calendarTimes: (id, href, knownHrefs = []) =>
      call('POST', `/cases/${id}/calendar/times`, session,
           { href, known_hrefs: knownHrefs }),
    calendarPick: (id, href, knownHrefs = []) =>
      call('POST', `/cases/${id}/calendar/pick`, session,
           { href, known_hrefs: knownHrefs }),
    // The booking form for the category the applicant chose: open it (query
    // is the site's own string from the walk), read its questions, transcribe
    // the applicant's answers, relay the confirmations they ticked verbatim
    // in Ellis, enter the picture answer they typed, and press Submit only as
    // their explicit final instruction.
    // The booking form behind the TIME the applicant picked — the href must
    // be one of the Book links Ellis read off the day page and showed.
    calendarOpenTime: (id, href, knownHrefs = []) =>
      call('POST', `/cases/${id}/calendar/time`, session,
           { href, known_hrefs: knownHrefs }),
    calendarBookForm: (id, query) =>
      call('POST', `/cases/${id}/calendar/book-form`, session, { query }),
    calendarBookFormFill: (id, answers) =>
      call('POST', `/cases/${id}/calendar/book-form/fill`, session, { answers }),
    calendarBookFormConfirm: (id, labels) =>
      call('POST', `/cases/${id}/calendar/book-form/confirm`, session, { labels }),
    calendarBookFormCaptcha: (id, text) =>
      call('POST', `/cases/${id}/calendar/book-form/captcha`, session, { text }),
    calendarBookFormSubmit: (id) =>
      call('POST', `/cases/${id}/calendar/book-form/submit`, session, {}),
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
    h1bClaimParty: (caseId, role) =>
      call('POST', `/h1b/cases/${caseId}/party/${role}/claim`, session),
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
    // The WORKER's own bundle: their facts + their uploads, one PDF, with a
    // cover stating it is not the petition. Beneficiary-authorized.
    h1bBeneficiaryPacket: (caseId, locale = 'en') =>
      call('POST', `/h1b/cases/${caseId}/beneficiary/packet?locale=${encodeURIComponent(locale)}`, session, {}),

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
           { industry_text, occupation_text }),

    // ---- Appointment cockpit (app/appt_api.py) -----------------------------
    // Ellis prepares an appointment to the edge of the human's act and stops
    // there: no slot search, no booking, no card, no CAPTCHA. Every payload
    // carries its own `human_acts` block, which the cockpit renders verbatim —
    // a caller can never mistake "Ellis prepared it" for "Ellis did it".
    // Waiver/EVUS (US) and the VIS 59-month fingerprint question (Schengen):
    // whether an in-person act is required at all.
    appointmentTriage: (caseId, locale = 'en') =>
      call('GET', `/appointments/triage/${caseId}?locale=${encodeURIComponent(locale)}`, session),
    // Everything prepared for this appointment, and everything still missing.
    // Nothing in the payload has been filed, paid, signed, or booked.
    appointmentPrestage: (caseId, locale = 'en') =>
      call('GET', `/appointments/prestage/${caseId}?locale=${encodeURIComponent(locale)}`, session),
    // The consulate's own group channel (10+ travelling together). Ellis
    // assembles and validates the roster; a human coordinator submits it.
    appointmentGroupRoster: (body = {}) =>
      call('POST', '/appointments/group-roster', session, {
        case_ids: [], group_name: '', group_kind: '', expires_on: '',
        include_passport_numbers: false, post: '', country: '',
        coordinator_name: '', coordinator_email: '', travel_start_date: '',
        options: {}, locale: 'en', ...body
      }),
    // The roster as a file the coordinator carries into the official channel.
    // `case_id` REPEATS once per member (FastAPI Query(list)), so the query is
    // built by hand — URLSearchParams would flatten the array into one value.
    // Raw bytes, so it bypasses `call`. The file identifies real travellers:
    // it goes to the coordinator and is never logged.
    downloadGroupRoster: async ({ caseIds = [], format = 'csv', ...rest } = {}) => {
      const q = caseIds.map((id) => `case_id=${encodeURIComponent(id)}`)
      q.push(`format=${encodeURIComponent(format)}`)
      for (const [k, v] of Object.entries(rest)) {
        if (v === undefined || v === null || v === '') continue
        q.push(`${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
      }
      const res = await fetch(`${BASE}/appointments/group-roster/export?${q.join('&')}`,
                              { headers: authHeaders(session) })
      if (!res.ok) throw new Error(`roster export failed (HTTP ${res.status})`)
      return await res.blob()
    },
    // Published wait times + the official places to look. There is no live slot
    // inventory here and never will be: reading a booking calendar by machine
    // gets the traveller's appointment cancelled.
    appointmentAvailability: (params = {}) =>
      call('GET', `/appointments/availability?${new URLSearchParams(params)}`, session),

    // ---- Agent-channel booking (app/appt_booking_api.py) -------------------
    // The applicant asks inside Trip.com; a NAMED HUMAN operator works the
    // official site in their own session; `booked` exists only behind evidence
    // (confirmation number + confirmation document on the case). Nothing here
    // touches the booking site.
    // Kimi K3 names the nearest official centre from the applicant's own
    // address — centre CHOICE only, no slot data, and any failure returns
    // available:false so the caller's great-circle sort stands.
    // The DS-160 question bank: the government's own questions, asked in
    // Ellis. Static reference data — no case, no network beyond our backend.
    ds160Questions: () => call('GET', '/ds160/questions', session),
    ds160Summary: () => call('GET', '/ds160/summary', session),
    bookingNearestCentre: (body = {}) =>
      call('POST', '/appointments/booking/nearest-centre', session,
           { address: '', centres: [], ...body }),
    bookingCreate: (caseId, body = {}) =>
      call('POST', `/appointments/booking/cases/${caseId}`, session,
           { route: '', posts: [], date_windows: [], note: '', ...body }),
    bookingForCase: (caseId) =>
      call('GET', `/appointments/booking/cases/${caseId}`, session),
    // Echo the slot's post+when so the server can 409 if the operator
    // re-offered between render and pick — consent binds to the seen slot.
    bookingPick: (requestId, index, slot = {}) =>
      call('POST', `/appointments/booking/${requestId}/pick`, session,
           { index, post: slot.post || '', when: slot.when || '' }),
    bookingCancel: (requestId) =>
      call('POST', `/appointments/booking/${requestId}/cancel`, session, {}),
    // Operator (admin role) side.
    bookingQueue: () => call('GET', '/appointments/booking/queue', session),
    bookingOfferSlots: (requestId, slots) =>
      call('POST', `/appointments/booking/${requestId}/offer-slots`, session, { slots }),
    // The operator's confirmation document (screenshot/PDF) — the evidence
    // half of `booked`.
    bookingEvidence: (requestId, { name = '', mime = '', content_b64 = '' } = {}) =>
      call('POST', `/appointments/booking/${requestId}/evidence`, session,
           { name, mime, content_b64 }),
    bookingBooked: (requestId, body) =>
      call('POST', `/appointments/booking/${requestId}/booked`, session,
           { confirmation_number: '', evidence_document_id: '', note: '', ...body }),
    bookingFailed: (requestId, reason) =>
      call('POST', `/appointments/booking/${requestId}/failed`, session, { reason }),
    // Ellis's browser agent, run inside the operator's authorized session:
    // reads the official calendar / drives the picked slot to confirmation.
    // Fails closed with an honest `agent` block (ran:false + reason) whenever
    // it cannot act — including when the site presents a human check, which
    // only the operator may clear.
    bookingAgentReadSlots: (requestId) =>
      call('POST', `/appointments/booking/${requestId}/agent/read-slots`, session, {}),
    bookingAgentBook: (requestId) =>
      call('POST', `/appointments/booking/${requestId}/agent/book`, session, {}),

    // ---- Travel authorizations + Schengen stay (app/travel_api.py) ---------
    // Edition-neutral: the tourist journey and an H1B beneficiary's personal
    // travel both read these. An unconfigured or unknown answer comes back as
    // an explicit unavailable with its reason — never a fabricated verdict.
    // With no destination this is the curated CATALOGUE; with one it becomes
    // this traveller's own answer (and a destination Ellis curates nothing for
    // says so, instead of an empty list that reads as "nothing required").
    travelAuthorizations: (params = {}) =>
      call('GET', `/travel/authorizations?${new URLSearchParams(params)}`, session),
    // One authorization in full, BY ITS PROGRAM KEY (esta, eta-canada, …) —
    // not by case: the record is curated reference data, the same for everyone.
    travelAuthorization: (key) =>
      call('GET', `/travel/authorizations/${encodeURIComponent(key)}`, session),
    // The 90-days-in-any-180 arithmetic. `stays` is the traveller's own
    // [{entry, exit}] history, sent as JSON in the query the endpoint declares.
    // The answer is a day COUNT, never a permission: a border officer decides
    // admission, on the day.
    schengenStay: ({ stays = [], asOf = '' } = {}) =>
      call('GET', `/travel/schengen-stay?${new URLSearchParams({
        stays: JSON.stringify(stays), as_of: asOf })}`, session),

    // ---- H1B ops (app/h1b/ops_api.py) --------------------------------------
    // The USCIS Organizational Account bulk-registration file, built from the
    // named cases and validated to the template. ORG-scoped, because one file
    // aggregates many beneficiaries for one employer. The file itself rides
    // back inside the payload (download.content_base64) — there is no separate
    // export, and there is no upload: a human signs in to myUSCIS, uploads,
    // pays, and submits.
    h1bBulkRegistration: (org, body = {}, locale = 'en') =>
      call('POST', `/h1b/orgs/${encodeURIComponent(org)}/bulk-registration?locale=${encodeURIComponent(locale)}`,
           session, { case_ids: [], fiscal_year: null, include_invalid: false, ...body }),
    // RFE response packet: each cited ground mapped to its curing evidence, the
    // exhibits on file that answer it, the gaps that do not, and a DRAFT
    // narrative. An attorney reviews it and a human uploads it.
    h1bRfeAssemble: (caseId, body = {}, locale = 'en') =>
      call('POST', `/h1b/cases/${caseId}/rfe/assemble?locale=${encodeURIComponent(locale)}`,
           session, { issues: [], notice_text: '', notice_document_id: '',
                      response_due_date: '', receipt_number: '', generate_pdf: false, ...body }),
    // The RFE ground vocabulary the assembler understands (the UI's picker).
    h1bRfeIssues: (locale = 'en') =>
      call('GET', `/h1b/rfe/issues?locale=${encodeURIComponent(locale)}`, session),
    // Advisory 8 CFR 214.2(h)(19) determination. `exempt` is true, false, or
    // the STRING 'unknown' — an entity fact nobody has stated leaves the answer
    // unknown, and the open questions are answered through the party-answers
    // endpoint (h1bPartyAnswers), never guessed here.
    h1bCapExemption: (caseId, locale = 'en') =>
      call('GET', `/h1b/cases/${caseId}/cap-exemption?locale=${encodeURIComponent(locale)}`, session),
    h1bCapExemptionQuestions: (locale = 'en') =>
      call('GET', `/h1b/cap-exemption/questions?locale=${encodeURIComponent(locale)}`, session),

    // ---- ETA-9141 prevailing wage request (both editions) ------------------
    // The same pinned forms path every prepared government blank uses
    // (forms_api.FORM_KEYS carries 'eta-9141'), named here because the PWD
    // request is its own product surface: the whole request derived from the
    // job + SOC, with each derived value naming its own source.
    prepareEta9141: (caseId, locale = 'en') =>
      call('POST', `/h1b/cases/${caseId}/forms/eta-9141/prepare?locale=${encodeURIComponent(locale)}`,
           session, {}),

    // ---- Public access file (app/h1b/paf_api.py) ---------------------------
    // 20 CFR 655.760(a) item by item, each with its citation and an honest
    // status; the 655.734 notice; and the assembled file. A fact Ellis does not
    // hold is reported missing and left blank — never completed with a guess.
    h1bPafManifest: (caseId, locale = 'en') =>
      call('GET', `/h1b/cases/${caseId}/paf/manifest?locale=${encodeURIComponent(locale)}`, session),
    h1bPafNotice: (caseId, locale = 'en') =>
      call('POST', `/h1b/cases/${caseId}/paf/notice?locale=${encodeURIComponent(locale)}`, session, {}),
    // The employer's own account of how the notice was given. Ellis records and
    // tests it; it never posts a notice or attests that one was posted.
    h1bPafPosting: (caseId, body = {}, locale = 'en') =>
      call('POST', `/h1b/cases/${caseId}/paf/posting?locale=${encodeURIComponent(locale)}`,
           session, { method: '', locations: [], individual_direct_email: false, ...body }),
    h1bPafPackage: (caseId, locale = 'en') =>
      call('GET', `/h1b/cases/${caseId}/paf/package?locale=${encodeURIComponent(locale)}`, session)
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

// ---- Filing + appointment cockpit display models ---------------------------
// docs/MAX_AUTOMATION_SPEC.md: ONE screen per filing showing exactly three
// things — everything Ellis prepared (each field with its source), what is
// still missing (each with the one input needed), and THE SINGLE ACTION that
// opens the applicant's own secure window at the point of their act.
//
// The doctrine these normalizers carry, so no component can lose it:
//   * The single action OPENS a window. It never logs in, signs, pays,
//     submits, clears a human check, or books a slot — `action.performsHumanAct`
//     is a pinned false, and the remaining human acts are always named.
//   * An unknown tap target is UNKNOWN. The count is the backend's own,
//     counted server-side from the named human acts (app/filing_acts.py); a
//     payload without one renders as unknown — this client holds no table.
//   * A verdict Ellis does not hold stays null (tri-state), never false. "No
//     appointment needed" and "Ellis cannot tell" are different answers.
//   * Only the backend may call a group roster submittable.

function _obj(v) { return v && typeof v === 'object' && !Array.isArray(v) ? v : {} }
function _arr(v) { return Array.isArray(v) ? v : [] }
function _text(v) {
  if (v == null) return ''
  if (typeof v === 'string') return v.trim()
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  return ''
}
// Tri-state: a boolean the backend actually stated, or null for "not known".
function _tri(v) {
  if (v === true || v === false) return v
  if (v === 'true' || v === 'yes') return true
  if (v === 'false' || v === 'no') return false
  return null
}
function _first(...vals) {
  for (const v of vals) { const s = _text(v); if (s) return s }
  return ''
}

// The act-key vocabulary the backend's single authority (app/filing_acts.py)
// speaks and this UI localizes as cockpit.act.{key}. A vocabulary, not data:
// which acts a filing actually has comes from the server, never from here.
export const HUMAN_ACT_KEYS = ['login', 'review', 'sign', 'pay', 'submit',
                               'captcha', 'biometrics']

// Artifacts that are never filed on a government site, so no secure window can
// ever be the "single action" for them.
export const NO_PORTAL_ROUTES = ['paf']

// The tap count as the BACKEND measured it (counted from the named human acts
// server-side — see app/filing_acts.py). A payload that carries no count, or a
// half-stated one, reads as unknown: a number is never invented here, and the
// old frontend table of targets is gone.
export function tapsToDone(payload) {
  const t = _obj(_obj(payload).taps_to_done)
  const min = _num(t.min)
  const max = _num(t.max)
  if (t.known !== true || min == null || max == null) {
    return { known: false, min: null, max: null, exact: null,
             reason: _first(t.reason, t.basis) }
  }
  return { known: true, min, max, exact: min === max ? min : null,
           reason: _text(t.basis) }
}

function _humanActs(payload) {
  // Only the backend's own typed acts (filing_acts.py / appt_eligibility —
  // who acts, why, whether it can be delegated at all). `label` is the
  // server-localized sentence and `act` is its bare key, so the sentence is
  // preferred and the key is only a last resort. No payload acts -> an EMPTY
  // list the surface renders as an honest unknown: the curated per-form
  // fallback that used to be invented here is gone.
  return _arr(payload.human_acts).map((raw) => {
    const a = _obj(raw)
    return {
      key: _text(a.key),
      act: _first(a.label, a.title, a.act),
      who: _first(a.who, a.actor),
      why: _first(a.why, a.reason),
      nonDelegable: _tri(a.non_delegable ?? a.nonDelegable),
      conditional: _tri(a.conditional) === true,
      ellisDoes: _first(a.ellis_does, a.ellisDoes)
    }
  }).filter((a) => a.act || a.key)
}

// One prepared field: what Ellis wrote, and where it came from. A value with
// no recorded source says so (sourceKnown:false) — a source is never invented.
function _preparedRows(payload) {
  const out = []
  for (const raw of _arr(payload.derived)) {
    const o = _obj(raw)
    const source = _text(o.source)
    out.push({ key: _text(o.key), label: _first(o.label, o.key), value: _text(o.value),
               source, sourceKnown: !!source, derived: true })
  }
  for (const raw of _arr(payload.prepared ?? payload.ready ?? payload.filled)) {
    const o = typeof raw === 'string' ? { label: raw } : _obj(raw)
    const source = _first(o.source, o.from)
    out.push({ key: _text(o.key), label: _first(o.label, o.question, o.key), value: _text(o.value),
               source, sourceKnown: !!source, derived: false })
  }
  return out.filter((r) => r.label || r.key)
}

// One missing item, with THE ONE INPUT needed to clear it. Never a raw key.
// `missing_required` is the consular form's name for the same list
// (main.py get_consular_form).
function _missingRows(payload) {
  const raw = payload.missing ?? payload.missing_fields ?? payload.missing_items ??
              payload.missing_required
  return _arr(raw).map((item) => {
    const o = typeof item === 'string' ? { label: item } : _obj(item)
    const label = _first(o.label, o.question, o.ask, o.key)
    return {
      key: _text(o.key),
      label,
      // The single input that clears it: the backend's own "how to resolve"
      // sentence when there is one (appt_appointments_prestage writes it), then
      // an applicant-worded question, and only then the label itself.
      input: _first(o.how_to_resolve, o.howToResolve, o.resolve_with, o.question,
                    o.ask, o.input, o.label) || label,
      // required:false is a real answer (an optional question blocks nothing);
      // an item that never said stays null rather than being called required.
      required: _tri(o.required),
      kind: _first(o.kind),
      party: _first(o.party, o.who),
      status: _text(o.status)
    }
  }).filter((m) => m.label || m.key)
}

// A PAF-manifest-shaped payload: each item carries its own regulatory status,
// so present items are prepared and everything unresolved is honestly missing
// (including 'unknown' — a conditional item nobody answered is never dropped).
function _manifestSplit(payload) {
  const documents = []
  const missing = []
  const notApplicable = []
  for (const raw of _arr(payload.items)) {
    const o = _obj(raw)
    const status = _text(o.status).toLowerCase()
    const entry = { key: _first(o.item_id, o.key, o.id),
                    label: _first(o.title, o.label, o.name, o.item_id),
                    status, citation: _first(o.citation, o.cite),
                    note: _first(o.next_action, o.reason, o.note, o.detail) }
    if (!entry.label && !entry.key) continue
    if (status === 'present') documents.push(entry)
    else if (status === 'not_applicable' || status === 'n/a') notApplicable.push(entry)
    else if (status) {
      missing.push({
        key: entry.key, label: entry.label,
        // next_action is the backend's own one-line "what closes this"; a
        // conditional item nobody answered asks its question instead.
        input: _first(o.next_action, o.condition_question, o.reason, o.description) ||
               entry.label,
        required: null, kind: 'paf_item', party: '', status
      })
    } else documents.push(entry)
  }
  return { documents, missing, notApplicable }
}

// Only https links are ever rendered, and a host Ellis does not recognize as a
// government or an authorized service provider is LABELED, not hidden.
const GOV_HOST_SUFFIXES = ['.gov', '.gouv.fr', '.europa.eu', '.gov.uk', '.gov.cn',
                           '.go.jp', '.gc.ca', '.gov.au', '.govt.nz', '.gov.in']
const AUTHORIZED_HOSTS = ['usvisascheduling.com', 'ustraveldocs.com', 'usvisa-info.com',
                          'vfsglobal.com', 'vfsevisa.com', 'tlscontact.com',
                          'blsinternational.com']

export function deepLinkView(raw) {
  const url = _text(raw)
  if (!/^https:\/\//i.test(url)) return null      // no http:, no javascript:, no data:
  let host = ''
  try { host = new URL(url).hostname.toLowerCase() } catch { return null }
  if (!host) return null
  const gov = GOV_HOST_SUFFIXES.some((s) => host.endsWith(s))
  const authorized = AUTHORIZED_HOSTS.some((h) => host === h || host.endsWith('.' + h))
  return {
    url, host,
    official: gov || authorized,
    kind: gov ? 'government' : authorized ? 'authorized_provider' : 'unrecognized'
  }
}

// The filing cockpit's whole display model. Tolerant of every prepared-filing
// payload this product produces: a prepared government form, the PWD request
// (which names its derivations), a PAF manifest, a tourist consular form.
export function filingCockpitView(payload, opts = {}) {
  const p = _obj(payload)
  const formKey = _first(opts.formKey, p.form_key, p.filing_key)
  const routeKey = _first(opts.routeKey, formKey)
  const explicitlyUnavailable = p.available === false || p.unavailable === true
  const reason = _first(p.reason, p.message)

  const prepared = _preparedRows(p)
  const manifest = _manifestSplit(p)
  const missing = _missingRows(p).concat(manifest.missing)
  const documents = _arr(p.documents ?? p.exhibits ?? p.evidence).map((raw) => {
    const o = typeof raw === 'string' ? { label: raw } : _obj(raw)
    return { key: _text(o.key ?? o.id), label: _first(o.label, o.title, o.name, o.doc_type),
             status: _text(o.status), citation: '', note: '' }
  }).filter((d) => d.label || d.key).concat(manifest.documents)

  // The consular form payload (main.py get_consular_form) counts as bare
  // `filled`/`total` numbers; a `filled` LIST is a different key (prepared
  // rows, parsed above) and must not be mistaken for a count.
  const filledCount = _num(p.filled_count ?? p.filledCount ??
                           (typeof p.filled === 'number' ? p.filled : null))
  const totalCount = _num(p.total_mapped ?? p.totalMapped ?? p.total)

  const wageRaw = p.wage ?? p.wage_analysis ?? p.wageAnalysis
  const wage = wageRaw ? wageLevelView(_obj(wageRaw).wage ?? wageRaw) : null

  const narrativeText = _first(p.draft_text, p.narrative_text,
                               typeof p.narrative === 'string' ? p.narrative : '')
  const narrative = {
    available: !!narrativeText || _obj(p.narrative).available === true,
    // A narrative is ALWAYS a draft for a licensed attorney to review.
    isDraft: true,
    text: narrativeText
  }

  const humanOnly = _arr(p.human_only).map((raw) => {
    const o = typeof raw === 'string' ? { label: raw } : _obj(raw)
    return _first(o.label, o.key)
  }).filter(Boolean)

  const notices = [p.preparation_notice, p.human_acts_notice, p.dol_use_only_notice,
                   p.nothing_submitted_notice, p.availability_notice, p.retention_notice,
                   p.posting_guidance, p.note]
    .map(_text).filter(Boolean)

  const sessionCaseId = _text(opts.sessionCaseId)
  // Some artifacts are never filed with anyone: the public access file is the
  // employer's OWN file. There is no government window to open for it, ever,
  // and saying "not started yet" would imply one is coming.
  const noPortal = NO_PORTAL_ROUTES.includes(routeKey)
  const action = {
    labelKey: 'cockpit.action.open',
    enabled: !noPortal && !!sessionCaseId && !explicitlyUnavailable,
    // Honest reason when the single action cannot be offered.
    reasonKey: noPortal ? 'cockpit.action.noPortal'
      : !sessionCaseId ? 'cockpit.action.notStarted' : '',
    reason: explicitlyUnavailable ? reason : '',
    // Pinned: this button opens a window and stops. The runtime has no path to
    // the acts below, and this flag exists so a test can say so out loud.
    performsHumanAct: false
  }

  return {
    available: !explicitlyUnavailable && (prepared.length > 0 || missing.length > 0 ||
      documents.length > 0 || filledCount != null || narrative.available),
    reason,
    formKey,
    routeKey,
    prepared,
    filledCount,
    totalCount,
    missing,
    missingCount: missing.length,
    documents,
    notApplicable: manifest.notApplicable,
    narrative,
    wage: wage && wage.available ? wage : null,
    humanOnly,
    humanActs: _humanActs(p),
    taps: tapsToDone(p),
    action,
    downloadUrl: _text(p.download_url),
    filedAt: deepLinkView(p.filed_at ?? p.official_url),
    notices,
    disclaimer: _first(p.attorney_disclaimer, p.disclaimer)
  }
}

// ---- Appointment cockpit ---------------------------------------------------
// Ellis never searches for or books a slot. That is not a policy setting: the
// penalty for automated slot search falls on the TRAVELLER (appointments
// cancelled, visas revoked), so `neverBooks` is a constant on every view here
// and the surface renders it whether or not a payload arrived.

export function appointmentTriageView(payload) {
  const p = _obj(payload)
  const t = _obj(p.triage ?? p.result ?? p)
  const verdict = _obj(t.verdict)
  const us = _obj(t.us ?? t.united_states)         // us_appointment_needed(...)
  const sch = _obj(t.schengen ?? t.vis ?? t.eu)    // schengen_biometrics_required(...)
  const evusBlock = _obj(t.evus)
  const explicitlyUnavailable = p.available === false || t.available === false
  const reason = _first(p.reason, t.reason, p.message, t.message)
  const route = _first(t.route, t.system, p.route).toLowerCase()

  // Every verdict below is tri-state, and the backend's own "unknown" string
  // lands as null. Three different answers stay three.
  const inPerson = _tri(verdict.in_person_required ?? t.in_person_required ??
                        t.appearance_required ?? t.in_person)
  // Schengen: the fingerprint gate itself (EU law), kept SEPARATE from whether
  // the member state still wants the applicant at the counter to lodge.
  const bioRequired = _tri(verdict.biometrics_required ?? sch.required)
  // Art. 13(3): only a DATED fact proves the 59-month reuse. A biometrics
  // exemption is not reuse, so this reads the reuse answer itself and never
  // infers it from "no fingerprints needed".
  const visReusable = _tri(sch.within_59_months ?? sch.reusable ?? sch.vis_reusable ??
                           t.within_59_months)
  const visMonths = _num(sch.months_since_biometrics ?? sch.vis_age_months ??
                         t.months_since_biometrics)
  // US: an interview waiver is exactly "no interview needed". EVUS is a
  // separate pre-travel enrolment on a 10-year visa.
  const explicitWaiver = _tri(us.interview_waiver_eligible ?? us.waiver_eligible ??
                              t.interview_waiver_eligible)
  const interviewNeeded = _tri(us.needed ?? verdict.interview_required)
  const waiver = explicitWaiver !== null ? explicitWaiver
    : (interviewNeeded === null ? null : !interviewNeeded)
  const evus = _tri(evusBlock.required ?? us.evus_required ?? t.evus_required)

  // What Ellis would need in order to firm up an unknown. These are the
  // questions themselves, never guesses dressed as answers.
  const openQuestions = _arr(t.open_questions ?? p.open_questions).map((raw) => {
    const o = typeof raw === 'string' ? { question: raw } : _obj(raw)
    return { question: _first(o.question, o.label),
             resolveWith: _first(o.resolve_with, o.resolveWith, o.how) }
  }).filter((q) => q.question)

  const reasons = _arr(t.reasons ?? t.why ?? p.reasons).map((r) => {
    const o = typeof r === 'string' ? { text: r } : _obj(r)
    return _first(o.text, o.label, o.reason)
  }).filter(Boolean)
  if (reason && route === 'unsupported') reasons.unshift(reason)

  return {
    // A triage that answers "Ellis cannot tell, and here is what would settle
    // it" IS an answer: the surface stays available and each line renders its
    // own unknown.
    available: !explicitlyUnavailable && (!!route || inPerson !== null ||
      bioRequired !== null || visReusable !== null || waiver !== null ||
      evus !== null || openQuestions.length > 0 || reasons.length > 0),
    reason,
    route,
    memberState: _first(t.member_state, t.state),
    post: _first(t.post, t.location),
    summary: _first(verdict.summary, t.summary),
    inPersonRequired: inPerson,
    biometricsRequired: bioRequired,
    visBiometricsReusable: visReusable,
    visMonths,
    interviewWaiverEligible: waiver,
    evusRequired: evus,
    // Whether an accredited agent could carry the whole filing (Schengen).
    agentEndToEnd: _tri(verdict.agent_deliverable_end_to_end),
    openQuestions,
    reasons,
    asOf: _first(t.as_of, p.as_of),
    humanActs: _humanActs({ human_acts: t.human_acts ?? p.human_acts }),
    disclaimer: _first(p.disclaimer, p.attorney_disclaimer),
    neverBooks: true
  }
}

export function appointmentPrestageView(payload) {
  const p = _obj(payload)
  const s = _obj(p.prestage ?? p.result ?? p)
  const explicitlyUnavailable = p.available === false || s.available === false
  const prepared = _preparedRows(s)
  const missing = _missingRows(s)
  const readiness = _obj(s.readiness)
  const documents = _arr(s.documents).map((raw) => {
    const o = typeof raw === 'string' ? { label: raw } : _obj(raw)
    return { key: _text(o.key ?? o.id), label: _first(o.label, o.title, o.name, o.doc_type),
             status: _text(o.status) }
  }).filter((d) => d.label || d.key)

  // The fee stack, each line with its own payer and channel. Ellis computes the
  // exact amount and names where to pay it; it never pays and never holds a
  // card. The server's own `ellis_never` sentence rides along verbatim.
  const fees = _arr(s.fees).map((raw) => {
    const o = _obj(raw)
    return {
      key: _text(o.key),
      label: _first(o.label, o.name),
      amount: _num(o.amount),
      currency: _first(o.currency),
      per: _first(o.per),
      payer: _first(o.payer),
      channels: _arr(o.payment_channels).map((c) => _first(_obj(c).label, _obj(c).name,
        typeof c === 'string' ? c : '')).filter(Boolean),
      note: _first(o.note),
      ellisNever: _first(o.ellis_never, o.ellisNever),
      source: _first(o.source), asOf: _first(o.as_of, o.asOf)
    }
  }).filter((f) => f.label || f.amount != null)

  return {
    available: !explicitlyUnavailable && (prepared.length > 0 || missing.length > 0 ||
      documents.length > 0 || fees.length > 0),
    reason: _first(p.reason, s.reason, p.message),
    route: _first(s.route, p.route),
    prepared,
    missing,
    missingCount: missing.length,
    // required:false items block nothing, so the blocking count is its own
    // number rather than a total that overstates what is in the way.
    missingRequiredCount: _num(readiness.missing_required) ??
      missing.filter((m) => m.required !== false).length,
    filledCount: _num(readiness.filled),
    totalCount: _num(readiness.form_fields ?? _obj(s.form).total_fields),
    documents,
    fees,
    humanActs: _humanActs({ human_acts: s.human_acts ?? p.human_acts }),
    note: _first(p.note, s.note, s.disclaimer),
    // Constant, not a payload field: nothing on a pre-stage surface has been
    // filed, paid, signed, or booked.
    nothingFiled: true,
    neverBooks: true
  }
}

// The consulate's group channel, with families and relatives explicitly
// excluded (docs/APPOINTMENTS_DESIGN.md).
//
// The MINIMUM IS PER-POST, not global. ustraveldocs China states 15 or more
// travelling together (verified live on /cn/en/step-4, 2026-08-18) while the
// design's baseline elsewhere is 10. Telling a coordinator with 12 travellers
// that they qualify, then having the post refuse the roster, wastes the work
// of twelve people — so the threshold follows the country.
const GROUP_MIN_BY_COUNTRY = { CHN: 15, CHINA: 15 }
export const GROUP_MIN_MEMBERS = 10          // baseline when the post is unknown

/** The group minimum for a post/country, falling back to the baseline. A
 *  country Ellis has not verified keeps the baseline rather than guessing a
 *  stricter number it cannot cite. */
export function groupMinMembers(country = '') {
  const key = String(country || '').trim().toUpperCase()
  return GROUP_MIN_BY_COUNTRY[key] ?? GROUP_MIN_MEMBERS
}
export const GROUP_EXCLUDED_KINDS = ['family', 'families', 'relatives',
                                     'families_and_relatives']

export function groupRosterView(payload, opts = {}) {
  const p = _obj(payload)
  const r = _obj(p.roster ?? p.result ?? p)
  const rawMembers = _arr(r.members ?? r.rows ?? r.roster ?? r.entries ?? r.applicants)
  const members = rawMembers.map((raw, i) => {
    const o = _obj(raw)
    return {
      caseId: _first(o.case_id, o.caseId),
      index: _num(o.member_index) ?? i + 1,
      name: _first(o.full_name, o.name, [_text(o.given_names), _text(o.surname)]
        .filter(Boolean).join(' ')),
      status: _text(o.status),
      // What this member is still missing before the coordinator can submit.
      missing: _arr(o.missing).map((m) => (typeof m === 'string' ? m : _first(_obj(m).label,
        _obj(m).key))).filter(Boolean),
      note: _first(o.note, o.detail)
    }
  })
  const count = members.length || _num(r.member_count ?? r.count) || 0
  const kind = _first(opts.groupKind, r.group_kind, p.group_kind).toLowerCase()

  // Ellis's OWN pre-check, before the consulate sees anything. Advisory and
  // labeled as such — the consulate's own rules are the authority.
  const preChecks = []
  // The post's own minimum decides this, not a global constant.
  const minMembers = groupMinMembers(_first(opts.country, r.country, p.country))
  if (count > 0 && count < minMembers) {
    preChecks.push({ code: 'too_few', count, min: minMembers })
  }
  if (kind && GROUP_EXCLUDED_KINDS.includes(kind)) preChecks.push({ code: 'family_excluded' })
  const incomplete = members.filter((m) => m.missing.length > 0)
  if (incomplete.length > 0) preChecks.push({ code: 'members_incomplete', count: incomplete.length })

  return {
    available: !(p.available === false || r.available === false) && count > 0,
    reason: _first(p.reason, r.reason, p.message),
    members,
    count,
    groupKind: kind,
    incompleteMembers: incomplete,
    preChecks,
    // ONLY the backend may call a roster submittable. Absent stays null, which
    // the surface renders as "Ellis cannot confirm this is ready".
    submittable: _tri(r.submittable ?? p.submittable),
    humanActs: _humanActs(p),
    // Constant: the coordinator submits and books. Ellis assembles and checks.
    coordinatorSubmits: true,
    neverBooks: true
  }
}

export function appointmentAvailabilityView(payload) {
  const p = _obj(payload)
  // `wait_time_data` is the snapshot's metadata (available / as_of / reason);
  // `wait_times` is the record list; `wait_time` is the one requested post.
  const wait = _obj(p.wait_time_data ?? p.waitTimeData)
  const one = _obj(p.wait_time)
  const _entry = (raw) => {
    const o = _obj(raw)
    return {
      post: _first(o.post, o.location, o.city),
      category: _first(o.category_label, o.category, o.visa_class),
      waitDays: _num(o.wait_days ?? o.days ?? o.wait),
      // known:false means the figure is simply not in the snapshot. It is
      // reported as unknown, never interpolated or averaged into a number.
      known: _tri(o.known) !== false,
      asOf: _first(o.as_of, o.asOf, wait.as_of, p.as_of)
    }
  }
  const entries = []
  if (one.post && _tri(one.known) === true) entries.push(_entry(one))
  for (const rec of _arr(p.wait_times ?? p.records)) {
    const e = _entry(rec)
    if ((e.post || e.waitDays != null) && !entries.some((x) => x.post === e.post &&
        x.category === e.category)) entries.push(e)
  }

  // Official destinations. An entry with no URL is a real answer ("Ellis has no
  // verified portal for this member state yet") and is kept, unlinked, rather
  // than silently dropped.
  const links = _arr(p.official_links ?? p.links).map((raw) => {
    const o = typeof raw === 'string' ? { url: raw } : _obj(raw)
    const link = deepLinkView(o.url ?? o.href)
    const label = _first(o.label, o.title)
    if (!link) {
      // Two different answers, kept apart: a destination that exists but is not
      // a safe https link ('insecure', shown unlinked with a caution), and one
      // Ellis has not verified at all ('not_determined', shown as text).
      const kind = _text(o.url ?? o.href) ? 'insecure' : 'not_determined'
      return (label || _first(o.description))
        ? { url: '', host: '', official: false, kind, label,
            description: _first(o.description) }
        : null
    }
    return { ...link, label, description: _first(o.description) }
  }).filter(Boolean)

  return {
    available: _tri(p.available ?? wait.available) === true && entries.length > 0,
    reason: _first(p.reason, wait.reason, one.reason, p.message),
    entries,
    links,
    source: _first(p.source, wait.source),
    attribution: _first(p.attribution),
    asOf: _first(wait.as_of, p.as_of),
    // The server's own sentence about why there is no live slot feed.
    whyNoLiveSlots: _first(p.why_no_live_slots),
    humanActs: _humanActs(p),
    // Constants: a published wait time is an estimate, not a slot; there is no
    // live slot inventory; and Ellis never books.
    liveSlotData: false,
    estimateOnly: true,
    neverBooks: true
  }
}

// ---- Agent-channel booking (app/appt_booking.py) ---------------------------
// The applicant asks and picks inside Trip.com; a NAMED HUMAN operator reads
// the official calendar and executes the booking in their own session. This
// view carries that honesty: every offered slot names who read it and when,
// and `isRealGovernmentResult` is true only on `booked`, which the server
// grants only behind evidence (confirmation number + document on the case).
export const BOOKING_ACTIVE_STATUSES = ['requested', 'slots_offered', 'slot_picked']

export function bookingView(payload) {
  const p = _obj(payload)
  if (p.exists === false || (!p.id && !p.status)) return { exists: false }
  const status = _text(p.status)
  const slots = _arr(p.offered_slots).map((raw) => {
    const o = _obj(raw)
    return { post: _text(o.post), when: _text(o.when), label: _text(o.label),
             recordedBy: _text(o.recorded_by), recordedAt: _text(o.recorded_at),
             // How the calendar was read: 'ellis_agent' | 'operator_manual'.
             source: _text(o.source) }
  }).filter((s) => s.post && s.when)
  const picked = _obj(p.picked_slot)
  const conf = _obj(p.confirmation)
  return {
    exists: true,
    id: _text(p.id),
    caseId: _text(p.case_id),
    route: _text(p.route),
    status,
    active: BOOKING_ACTIVE_STATUSES.includes(status),
    posts: _arr(p.posts).map(_text).filter(Boolean),
    dateWindows: _arr(p.date_windows).map((w) => ({
      from: _text(_obj(w).from), to: _text(_obj(w).to) })),
    note: _text(p.note),
    requestedAt: _text(p.requested_at),
    offeredSlots: slots,
    agentRead: p.agent_read === true,
    slotsNotice: _text(p.slots_notice),
    // The agent's own honest outcome block, when an agent endpoint answered.
    agent: p.agent ? {
      ran: _obj(p.agent).ran === true,
      kind: _text(_obj(p.agent).kind),
      reason: _text(_obj(p.agent).reason),
      liveViewUrl: _text(_obj(p.agent).live_view_url),
      confirmationHint: _text(_obj(p.agent).confirmation_hint),
      count: _num(_obj(p.agent).count)
    } : null,
    pickedSlot: picked.post ? { post: _text(picked.post), when: _text(picked.when),
                                label: _text(picked.label) } : null,
    // Confirmation renders ONLY with both halves of the evidence — the same
    // rule the server enforces, repeated here so a hand-built payload cannot
    // read as booked.
    confirmation: (conf.number && conf.evidence_document_id)
      ? { number: _text(conf.number), evidenceDocumentId: _text(conf.evidence_document_id),
          recordedBy: _text(conf.recorded_by), recordedAt: _text(conf.recorded_at),
          note: _text(conf.note) }
      : null,
    failureReason: _text(p.failure_reason),
    isRealGovernmentResult: p.is_real_government_result === true &&
      !!(conf.number && conf.evidence_document_id),
    legalBasis: { basis: _text(_obj(p.legal_basis).basis),
                  limit: _text(_obj(p.legal_basis).limit) },
    humanActs: _humanActs(p),
    neverAutomatedNotice: _text(p.never_automated_notice),
    neverBooks: true
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
