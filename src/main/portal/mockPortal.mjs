// A complete in-process mock visa portal. This is what the adapters and the
// end-to-end tests run against — never a real government portal. It models the
// full journey plus the failure/human-interaction states the real world has:
// email verification, CAPTCHA, OTP, payment success/failure/3-DS, limited and
// concurrent appointment inventory, rescheduling, personal declaration,
// submission, confirmation, receipts, session timeout, and maintenance mode.
//
// It deliberately behaves like an untrusted external system: it hands back
// opaque tokens, it can time out, it can reject, and it never lets the caller
// read another applicant's data.

import { randomUUID, createHash } from 'node:crypto'

const HOST = 'portal.mockland.gov.example'
export const MOCK_BASE = `https://${HOST}`

function hash(s) { return createHash('sha256').update(String(s)).digest('hex') }

export class MockPortal {
  constructor(opts = {}) {
    this.host = HOST
    this.maintenance = !!opts.maintenance
    this.sessionTtlMs = opts.sessionTtlMs ?? 30 * 60_000
    this.now = opts.clock || (() => Date.now())
    /** @type {Map<string, any>} accounts by email */
    this.accounts = new Map()
    /** @type {Map<string, any>} sessions by token */
    this.sessions = new Map()
    /** @type {Map<string, any>} applications by id */
    this.applications = new Map()
    /** appointment inventory: array of {slotId, locationId, startUtc, taken} */
    this.inventory = opts.inventory ? [...opts.inventory] : this._seedInventory()
    this.emails = [] // outbox the "applicant" can read verification links / OTP from
    this.paymentOutcome = opts.paymentOutcome || 'success' // 'success' | 'decline' | '3ds'
    this.requireCaptchaOnRegister = opts.requireCaptchaOnRegister ?? true
    this.log = []
  }

  _audit(event, detail) { this.log.push({ at: this.now(), event, detail }) }

  _seedInventory() {
    // Deterministic inventory: two locations, slots over the next 45 days.
    const out = []
    const base = this.now()
    const locs = ['MOCKLAND-CAP', 'MOCKLAND-NORTH']
    for (let d = 3; d < 45; d++) {
      const day = new Date(base + d * 86400000)
      // weekdays only
      if (day.getUTCDay() === 0 || day.getUTCDay() === 6) continue
      for (const h of [9, 11, 14]) {
        for (const loc of locs) {
          const start = Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate(), h, 0, 0)
          out.push({ slotId: `S-${loc}-${start}`, locationId: loc, startUtc: start, taken: false })
        }
      }
    }
    return out
  }

  _guardUp() { if (this.maintenance) { const e = new Error('PORTAL_MAINTENANCE'); e.code = 'MAINTENANCE'; throw e } }

  _session(token) {
    const s = this.sessions.get(token)
    if (!s) { const e = new Error('NO_SESSION'); e.code = 'NO_SESSION'; throw e }
    if (this.now() - s.createdAt > this.sessionTtlMs) { this.sessions.delete(token); const e = new Error('SESSION_EXPIRED'); e.code = 'SESSION_EXPIRED'; throw e }
    return s
  }

  // --- Registration ------------------------------------------------------
  register({ email, password, fullName }) {
    this._guardUp()
    if (!email || !password) { const e = new Error('MISSING_FIELDS'); e.code = 'MISSING_FIELDS'; throw e }
    if (this.accounts.has(email)) return { ok: false, code: 'ACCOUNT_EXISTS' }
    // Portal password policy (mirrors the demo adapter's declared policy).
    if (String(password).length < 12) return { ok: false, code: 'WEAK_PASSWORD' }
    const account = { email, passwordHash: hash(password), fullName, verified: false, createdAt: this.now() }
    this.accounts.set(email, account)
    const captchaToken = this.requireCaptchaOnRegister ? randomUUID() : null
    const verifyToken = randomUUID()
    account._verifyToken = verifyToken
    this.emails.push({ to: email, kind: 'verification', link: `${MOCK_BASE}/verify?token=${verifyToken}`, at: this.now() })
    this._audit('register', { email, captcha: !!captchaToken })
    return { ok: true, captchaToken, needsEmailVerification: true }
  }

  // CAPTCHA is solved BY A HUMAN. The portal only ever tells you whether a
  // presented answer is correct — it never exposes the answer, and Ellis must
  // never call this on the applicant's behalf with a guessed value.
  submitCaptcha({ captchaToken, humanAnswer }) {
    this._guardUp()
    // The only "correct" answer is the literal marker a real person types after
    // reading the challenge. Automated callers don't have it.
    if (humanAnswer === 'HUMAN_SOLVED') { this._audit('captcha_ok', { captchaToken }); return { ok: true } }
    return { ok: false, code: 'CAPTCHA_FAILED' }
  }

  // The applicant clicks the link in their own inbox — modeled as reading the
  // token from the outbox and confirming it. Ellis never auto-intercepts this.
  verifyEmail({ token }) {
    this._guardUp()
    for (const acc of this.accounts.values()) {
      if (acc._verifyToken === token) { acc.verified = true; this._audit('email_verified', { email: acc.email }); return { ok: true } }
    }
    return { ok: false, code: 'BAD_TOKEN' }
  }

  // --- Login -------------------------------------------------------------
  login({ email, password }) {
    this._guardUp()
    const acc = this.accounts.get(email)
    if (!acc || acc.passwordHash !== hash(password)) return { ok: false, code: 'BAD_CREDENTIALS' }
    if (!acc.verified) return { ok: false, code: 'NOT_VERIFIED' }
    const token = randomUUID()
    this.sessions.set(token, { email, createdAt: this.now() })
    this._audit('login', { email })
    return { ok: true, sessionToken: token }
  }

  // --- Application -------------------------------------------------------
  createApplication({ sessionToken, answers }) {
    const s = this._session(sessionToken)
    const id = 'APP-' + randomUUID().slice(0, 8).toUpperCase()
    this.applications.set(id, { id, owner: s.email, answers: { ...answers }, documents: [], paid: false, declared: false, submitted: false, appointment: null })
    this._audit('application_created', { id, owner: s.email })
    return { ok: true, applicationId: id }
  }

  uploadDocument({ sessionToken, applicationId, name, sizeBytes, mime }) {
    const s = this._session(sessionToken)
    const app = this.applications.get(applicationId)
    if (!app || app.owner !== s.email) return { ok: false, code: 'NOT_FOUND' }
    if (sizeBytes > 10 * 1024 * 1024) return { ok: false, code: 'FILE_TOO_LARGE' }
    if (!['application/pdf', 'image/jpeg', 'image/png'].includes(mime)) return { ok: false, code: 'BAD_MIME' }
    app.documents.push({ name, sizeBytes, mime })
    return { ok: true, documentCount: app.documents.length }
  }

  // --- Fee + payment -----------------------------------------------------
  discoverFee({ sessionToken, applicationId }) {
    const s = this._session(sessionToken)
    const app = this.applications.get(applicationId)
    if (!app || app.owner !== s.email) return { ok: false, code: 'NOT_FOUND' }
    return { ok: true, amount: 8000, currency: 'USD', display: 'US$80.00' } // cents
  }

  // Payment is performed inside the browser session. The portal never returns
  // card data; it returns only a non-sensitive outcome. A '3ds' outcome means
  // the human must complete bank authentication in the live window.
  pay({ sessionToken, applicationId, paymentRef }) {
    const s = this._session(sessionToken)
    const app = this.applications.get(applicationId)
    if (!app || app.owner !== s.email) return { ok: false, code: 'NOT_FOUND' }
    if (this.paymentOutcome === 'decline') return { ok: false, code: 'PAYMENT_DECLINED' }
    if (this.paymentOutcome === '3ds') return { ok: false, code: 'REQUIRES_3DS', threeDsToken: randomUUID() }
    app.paid = true
    app.receipt = { receiptNo: 'RCPT-' + randomUUID().slice(0, 8).toUpperCase(), amount: 8000, currency: 'USD', paymentRef: paymentRef || null, at: this.now() }
    this._audit('paid', { applicationId })
    return { ok: true, receipt: app.receipt }
  }

  complete3ds({ sessionToken, applicationId, threeDsToken }) {
    const s = this._session(sessionToken)
    const app = this.applications.get(applicationId)
    if (!app || app.owner !== s.email) return { ok: false, code: 'NOT_FOUND' }
    app.paid = true
    app.receipt = { receiptNo: 'RCPT-' + randomUUID().slice(0, 8).toUpperCase(), amount: 8000, currency: 'USD', at: this.now() }
    return { ok: true, receipt: app.receipt }
  }

  // --- Appointments ------------------------------------------------------
  searchAppointments({ sessionToken, locationIds }) {
    this._session(sessionToken)
    const wanted = new Set(locationIds && locationIds.length ? locationIds : this.inventory.map((i) => i.locationId))
    const open = this.inventory.filter((i) => !i.taken && wanted.has(i.locationId))
      .map((i) => ({ slotId: i.slotId, locationId: i.locationId, startUtc: i.startUtc }))
      .sort((a, b) => a.startUtc - b.startUtc)
    return { ok: true, slots: open, checkedAt: this.now() }
  }

  // Booking revalidates the slot atomically. Concurrent attempts on the same
  // slot: exactly one wins.
  bookAppointment({ sessionToken, applicationId, slotId }) {
    const s = this._session(sessionToken)
    const app = this.applications.get(applicationId)
    if (!app || app.owner !== s.email) return { ok: false, code: 'NOT_FOUND' }
    const slot = this.inventory.find((i) => i.slotId === slotId)
    if (!slot) return { ok: false, code: 'SLOT_GONE' }
    if (slot.taken) return { ok: false, code: 'SLOT_TAKEN' }
    slot.taken = true
    app.appointment = { slotId, locationId: slot.locationId, startUtc: slot.startUtc, confirmationNo: 'APT-' + randomUUID().slice(0, 8).toUpperCase(), bookedAt: this.now() }
    this._audit('appointment_booked', { applicationId, slotId })
    return { ok: true, appointment: app.appointment }
  }

  // Reschedule frees the old slot only after the new one is secured.
  rescheduleAppointment({ sessionToken, applicationId, newSlotId }) {
    const s = this._session(sessionToken)
    const app = this.applications.get(applicationId)
    if (!app || app.owner !== s.email || !app.appointment) return { ok: false, code: 'NOT_FOUND' }
    const next = this.inventory.find((i) => i.slotId === newSlotId)
    if (!next || next.taken) return { ok: false, code: 'SLOT_TAKEN' }
    const prev = this.inventory.find((i) => i.slotId === app.appointment.slotId)
    next.taken = true
    if (prev) prev.taken = false
    app.appointment = { slotId: newSlotId, locationId: next.locationId, startUtc: next.startUtc, confirmationNo: 'APT-' + randomUUID().slice(0, 8).toUpperCase(), bookedAt: this.now() }
    this._audit('appointment_rescheduled', { applicationId, newSlotId })
    return { ok: true, appointment: app.appointment }
  }

  // --- Personal declaration + submission ---------------------------------
  // The declaration is under penalty of perjury — it must be completed by the
  // applicant personally. The portal will not accept a submission until the
  // declaration flag was set through this human path.
  declarePersonally({ sessionToken, applicationId, humanConfirmed }) {
    const s = this._session(sessionToken)
    const app = this.applications.get(applicationId)
    if (!app || app.owner !== s.email) return { ok: false, code: 'NOT_FOUND' }
    if (humanConfirmed !== 'HUMAN_DECLARED') return { ok: false, code: 'DECLARATION_REQUIRED' }
    app.declared = true
    return { ok: true }
  }

  submit({ sessionToken, applicationId }) {
    const s = this._session(sessionToken)
    const app = this.applications.get(applicationId)
    if (!app || app.owner !== s.email) return { ok: false, code: 'NOT_FOUND' }
    if (!app.paid) return { ok: false, code: 'NOT_PAID' }
    if (!app.declared) return { ok: false, code: 'DECLARATION_REQUIRED' }
    if (app.submitted) return { ok: true, idempotent: true, confirmation: app.confirmation } // idempotent
    app.submitted = true
    app.confirmation = { referenceNo: 'REF-' + randomUUID().slice(0, 10).toUpperCase(), submittedAt: this.now() }
    this._audit('submitted', { applicationId, ref: app.confirmation.referenceNo })
    return { ok: true, confirmation: app.confirmation, receipt: app.receipt, appointment: app.appointment }
  }

  // Reconciliation probe — lets the orchestrator ask "did my last attempt
  // actually succeed?" before ever retrying a payment/booking/submission.
  getApplicationState({ sessionToken, applicationId }) {
    const s = this._session(sessionToken)
    const app = this.applications.get(applicationId)
    if (!app || app.owner !== s.email) return { ok: false, code: 'NOT_FOUND' }
    return { ok: true, paid: app.paid, declared: app.declared, submitted: app.submitted, appointment: app.appointment, confirmation: app.confirmation || null, receipt: app.receipt || null }
  }
}
