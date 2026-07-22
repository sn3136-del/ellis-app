// The visa-processing orchestrator. Portal-agnostic: it drives an adapter's
// imperative hooks through the state machine, pausing at human-handoff states
// and resuming on signals. State lives entirely on the instance and can be
// serialized (snapshot/restore) so a run survives a restart — the same
// durability Temporal would give, expressed as an explicit resumable machine
// rather than a long-lived coroutine.
//
// Safety properties enforced here:
//  - Payment / booking / submission never auto-retry without reconciliation.
//  - CAPTCHA / OTP / verification / payment / personal declaration are ALWAYS
//    applicant-completed; the orchestrator supplies only the human's result.
//  - Generated portal passwords go to the vault; the workflow keeps only refs.
//  - Material changes after review invalidate the approval and force re-review.

import { CaseMachine, isTerminal } from './stateMachine.mjs'
import { SecretsVault, generatePassword } from './vault.mjs'
import { AuditLog } from './audit.mjs'
import { BrowserbaseLiveView, StripeIssuing, capabilityReport } from './providers.mjs'
import { defaultPreferences, validatePreferences, earliestQualifying, isImprovement } from './appointments.mjs'
import { appointmentIcs } from './ics.mjs'
import { createHash } from 'node:crypto'

function hashSnapshot(obj) { return createHash('sha256').update(JSON.stringify(obj)).digest('hex') }

export class VisaWorkflow {
  constructor({ caseId, adapter, applicant, answers, documents, preferences, authorization, vault, audit, email, clock }) {
    this.caseId = caseId
    this.adapter = adapter
    this.applicant = applicant
    this.answers = answers || {}
    this.documents = documents || []
    this.prefs = defaultPreferences(preferences || {})
    this.authorization = authorization || {} // { maxFeeCents, currency, allowAutoBook, allowRepresentativeSubmit, ... }
    this.vault = vault || new SecretsVault()
    this.audit = audit || new AuditLog(clock)
    this.email = email || null
    this.clock = clock || (() => Date.now())
    this.machine = new CaseMachine('DRAFT', this.clock)

    // Human-supplied results (set by signals), consumed by the driver.
    this.inputs = {
      reviewApprovedHash: null,
      authorizationSigned: false,
      captchaSolved: false,
      emailVerifyToken: null,
      paymentApproved: false,
      paymentCompleted: false,
      selectedSlotId: null,
      rescheduleApproved: false,
      declarationCompleted: false
    }

    // Working state.
    this.credentialRef = null
    this.sessionRef = null
    this.applicationId = null
    this.receipt = null
    this.appointment = null
    this.confirmation = null
    this.reschedules = 0
    this.pending = null // { state, reason, handoff, liveView }
    this.artifacts = []
    this.reviewSnapshotHash = null
  }

  _emit(action, detail) { return this.audit.record(this.caseId, action, detail, 'ellis') }

  _pause(reason, handoff, extra = {}) {
    this.pending = { state: this.machine.state, reason, handoff, ...extra }
    this._emit('handoff_requested', { state: this.machine.state, reason, handoff })
    return this.pending
  }

  // A public snapshot of what the applicant/UI must do next.
  status() {
    return {
      caseId: this.caseId, state: this.machine.state, pending: this.pending,
      applicationId: this.applicationId, appointment: this.appointment,
      confirmation: this.confirmation, capabilities: capabilityReport()
    }
  }

  // ---- Signals (external human/webhook input) ----------------------------
  async approveReview(snapshotHash) {
    // Approval binds to the exact reviewed snapshot; a later material change
    // invalidates it and forces re-review.
    this.reviewSnapshotHash = hashSnapshot({ answers: this.answers, documents: this.documents.map((d) => d.name) })
    if (snapshotHash && snapshotHash !== this.reviewSnapshotHash) {
      this._emit('review_stale', { provided: snapshotHash, actual: this.reviewSnapshotHash })
      return await this._drive()
    }
    this.inputs.reviewApprovedHash = this.reviewSnapshotHash
    this._emit('review_approved', { hash: this.reviewSnapshotHash })
    return await this._drive()
  }

  async signAuthorization() { this.inputs.authorizationSigned = true; this._emit('authorization_signed', { mode: this.authorization.mode || 'in_app_authorization' }); return await this._drive() }
  async solveCaptcha() { this.inputs.captchaSolved = true; this._emit('captcha_solved_by_applicant', {}); return await this._drive() }
  async verifyEmail(token) { this.inputs.emailVerifyToken = token; this._emit('email_verify_provided', {}); return await this._drive() }
  async approvePayment() { this.inputs.paymentApproved = true; this._emit('payment_approved', { maxFeeCents: this.authorization.maxFeeCents }); return await this._drive() }
  async completePayment() { this.inputs.paymentCompleted = true; this._emit('payment_completed_by_applicant', {}); return await this._drive() }
  async selectAppointment(slotId) { this.inputs.selectedSlotId = slotId; return await this._drive() }
  async approveReschedule() { this.inputs.rescheduleApproved = true; return await this._drive() }
  async completeDeclaration() { this.inputs.declarationCompleted = true; this._emit('declaration_completed_by_applicant', {}); return await this._drive() }
  cancel(reason = 'applicant cancelled') { this.machine.transition('CANCELLED', reason); this.pending = null; this._emit('cancelled', { reason }); return this.status() }

  async start() { return await this._drive() }

  // ---- The driver: advance until a handoff or a terminal state -----------
  async _drive() {
    // Bounded loop — each iteration either advances a state or pauses.
    for (let i = 0; i < 100; i++) {
      const st = this.machine.state
      if (isTerminal(st)) { this.pending = null; return this.status() }
      const before = st
      try {
        await this._step()
      } catch (err) {
        // Any portal/browser/network exception is a recoverable failure, never
        // a crash. Reconciliation happens on resume before retrying anything.
        const code = err?.code || err?.message || 'STEP_ERROR'
        this.pending = null
        if (!isTerminal(this.machine.state)) this.machine.transition('RECOVERABLE_FAILURE', String(code).slice(0, 80))
        this._emit('recoverable_failure', { from: before, code: String(code).slice(0, 120) })
        return this.status()
      }
      if (this.pending && this.pending.state === this.machine.state) return this.status() // waiting on human
      if (this.machine.state === before && !this.pending) {
        // No progress and not paused → nothing more to do this tick.
        return this.status()
      }
    }
    return this.status()
  }

  async _step() {
    const d = this.adapter.driver
    const m = this.machine
    switch (m.state) {
      case 'DRAFT': {
        const missing = (this.adapter.requiredApplicantFields || []).filter((f) => !this.answers[f])
        if (missing.length) { m.transition('DATA_INCOMPLETE', `missing ${missing.join(',')}`); return }
        m.transition('READY_FOR_REVIEW'); return
      }
      case 'DATA_INCOMPLETE': {
        const missing = (this.adapter.requiredApplicantFields || []).filter((f) => !this.answers[f])
        if (!missing.length) m.transition('READY_FOR_REVIEW')
        return
      }
      case 'READY_FOR_REVIEW': { m.transition('APPLICANT_REVIEW_REQUIRED'); return }
      case 'APPLICANT_REVIEW_REQUIRED': {
        if (!this.inputs.reviewApprovedHash) { this._pause('Review every answer and approve the application.', 'review'); return }
        this.pending = null; m.transition('AUTHORIZATION_REQUIRED'); return
      }
      case 'AUTHORIZATION_REQUIRED': { m.transition('AUTHORIZATION_PENDING'); return }
      case 'AUTHORIZATION_PENDING': {
        if (!this.inputs.authorizationSigned) { this._pause('Sign the Ellis authorization (Docusign or in-app).', 'authorization'); return }
        this.pending = null; m.transition('AUTHORIZATION_SIGNED'); return
      }
      case 'AUTHORIZATION_SIGNED': { m.transition('PORTAL_ACCOUNT_REQUIRED'); return }
      case 'PORTAL_ACCOUNT_REQUIRED': { m.transition('PORTAL_ACCOUNT_CREATING'); return }
      case 'PORTAL_ACCOUNT_CREATING': {
        // Generate a strong password, store ONLY in the vault, register.
        const password = generatePassword(this.adapter.passwordRequirements)
        const stored = this.vault.store(password, { portal: this.adapter.adapterId, kind: 'portal_password' })
        this.credentialRef = stored.ref
        this._emit('portal_password_stored', { ref: stored.ref, provider: stored.provider }) // ref only, never the value
        const res = await d.register({ email: this.applicant.email, password, fullName: this.applicant.fullName })
        if (!res.ok && res.code === 'ACCOUNT_EXISTS') { m.transition('PORTAL_LOGIN_REQUIRED', 'account already exists'); return }
        if (!res.ok) { m.transition('RECOVERABLE_FAILURE', res.code); return }
        this._pendingCaptchaToken = res.captchaToken
        if (res.captchaToken) { m.transition('CAPTCHA_ACTION_REQUIRED'); return }
        if (res.needsEmailVerification) { m.transition('PORTAL_VERIFICATION_REQUIRED'); return }
        m.transition('PORTAL_ACCOUNT_READY'); return
      }
      case 'CAPTCHA_ACTION_REQUIRED': {
        if (!this.inputs.captchaSolved) {
          const lv = this._liveView('Solve the CAPTCHA on the official portal.')
          this._pause('Complete the CAPTCHA. Ellis never solves it for you.', 'captcha', { liveView: lv }); return
        }
        // Ellis submits only the human's result marker — never a guessed answer.
        const ok = await d.submitCaptcha({ captchaToken: this._pendingCaptchaToken, humanAnswer: 'HUMAN_SOLVED' })
        this.inputs.captchaSolved = false; this.pending = null
        if (!ok.ok) { m.transition('RECOVERABLE_FAILURE', 'CAPTCHA_FAILED'); return }
        m.transition('PORTAL_VERIFICATION_REQUIRED'); return
      }
      case 'PORTAL_VERIFICATION_REQUIRED': {
        if (!this.inputs.emailVerifyToken) { this._pause('Click the verification link in your email inbox.', 'email_verification'); return }
        const ok = await d.verifyEmail({ token: this.inputs.emailVerifyToken })
        this.inputs.emailVerifyToken = null; this.pending = null
        if (!ok.ok) { this._pause('That link did not verify. Open the latest email and try again.', 'email_verification'); return }
        m.transition('PORTAL_ACCOUNT_READY'); return
      }
      case 'PORTAL_ACCOUNT_READY': { m.transition('PORTAL_LOGIN_REQUIRED'); return }
      case 'PORTAL_LOGIN_REQUIRED': {
        const password = this.vault.reveal(this.credentialRef) // in-memory only, never logged
        const res = await d.login({ email: this.applicant.email, password })
        if (!res.ok) { m.transition('RECOVERABLE_FAILURE', res.code); return }
        const stored = this.vault.store(res.sessionToken, { kind: 'portal_session' })
        this.sessionRef = stored.ref
        this._emit('portal_login', { sessionRef: stored.ref })
        m.transition('APPLICATION_FILLING'); return
      }
      case 'APPLICATION_FILLING': {
        const token = this.vault.reveal(this.sessionRef)
        if (!this.applicationId) {
          const res = await d.createApplication({ sessionToken: token, answers: this.answers })
          if (!res.ok) { m.transition('RECOVERABLE_FAILURE', res.code); return }
          this.applicationId = res.applicationId
          this._emit('application_created', { applicationId: this.applicationId })
        }
        m.transition('DOCUMENT_UPLOAD_PENDING'); return
      }
      case 'DOCUMENT_UPLOAD_PENDING': {
        const token = this.vault.reveal(this.sessionRef)
        for (const doc of this.documents) {
          const res = await d.uploadDocument({ sessionToken: token, applicationId: this.applicationId, name: doc.name, sizeBytes: doc.sizeBytes || 1024, mime: doc.mime || 'application/pdf' })
          if (!res.ok) { m.transition('RECOVERABLE_FAILURE', `upload:${res.code}`); return }
        }
        this._emit('documents_uploaded', { count: this.documents.length })
        m.transition('FEE_DISCOVERY_PENDING'); return
      }
      case 'FEE_DISCOVERY_PENDING': {
        const token = this.vault.reveal(this.sessionRef)
        const fee = await d.discoverFee({ sessionToken: token, applicationId: this.applicationId })
        if (!fee.ok) { m.transition('RECOVERABLE_FAILURE', 'fee_discovery'); return }
        this.fee = fee
        this._emit('fee_discovered', { amount: fee.amount, currency: fee.currency })
        m.transition('PAYMENT_APPROVAL_REQUIRED'); return
      }
      case 'PAYMENT_APPROVAL_REQUIRED': {
        if (!this.inputs.paymentApproved) { this._pause(`Approve the ${this.fee.display} government fee.`, 'payment_approval', { fee: this.fee }); return }
        // Enforce the authorized maximum from the signed authorization.
        if (this.authorization.maxFeeCents != null && this.fee.amount > this.authorization.maxFeeCents) {
          this.pending = null; m.transition('MANUAL_REVIEW_REQUIRED', 'fee exceeds authorized maximum'); return
        }
        this.pending = null
        m.transition('PAYMENT_ACTION_REQUIRED'); return
      }
      case 'PAYMENT_ACTION_REQUIRED': {
        // Stripe Issuing only if configured AND adapter permits automated
        // third-party payment; otherwise applicant-controlled window.
        const canAuto = StripeIssuing.isConfigured() && this.adapter.thirdPartyPaymentPolicy === 'automated'
        if (!canAuto && !this.inputs.paymentCompleted) {
          const lv = this._liveView('Complete the embassy payment. Ellis never sees your card.')
          this._pause('Complete the payment in the secure window. The fee is disclosed above.', 'payment', { liveView: lv, fee: this.fee }); return
        }
        this.pending = null
        m.transition('PAYMENT_PROCESSING'); return
      }
      case 'PAYMENT_PROCESSING': {
        const token = this.vault.reveal(this.sessionRef)
        // RECONCILE before charging: never double-pay.
        const cur = await d.getApplicationState({ sessionToken: token, applicationId: this.applicationId })
        if (cur.ok && cur.paid) { this.receipt = cur.receipt || this.receipt; m.transition('PAYMENT_COMPLETED', 'already paid (reconciled)'); return }
        const res = await d.pay({ sessionToken: token, applicationId: this.applicationId, paymentRef: this.caseId })
        if (!res.ok && res.code === 'REQUIRES_3DS') { m.transition('PAYMENT_ACTION_REQUIRED', '3DS required'); this.inputs.paymentCompleted = false; return }
        if (!res.ok) { m.transition('RECOVERABLE_FAILURE', res.code); return }
        this.receipt = res.receipt
        this.artifacts.push({ kind: 'receipt', value: res.receipt })
        this._emit('payment_captured', { receiptNo: res.receipt.receiptNo })
        m.transition('PAYMENT_COMPLETED'); return
      }
      case 'PAYMENT_COMPLETED': { m.transition('APPOINTMENT_SEARCHING'); return }
      case 'APPOINTMENT_SEARCHING': {
        const token = this.vault.reveal(this.sessionRef)
        const res = await d.searchAppointments({ sessionToken: token, locationIds: [this.prefs.preferredLocation, ...this.prefs.alternativeLocations].filter(Boolean) })
        if (!res.ok) { m.transition('RECOVERABLE_FAILURE', 'search'); return }
        this._lastSlots = res.slots
        this._emit('appointments_checked', { count: res.slots.length, at: res.checkedAt })
        m.transition('APPOINTMENT_AVAILABLE'); return
      }
      case 'APPOINTMENT_AVAILABLE': {
        const problems = validatePreferences(this.prefs)
        if (problems.length) { m.transition('MANUAL_REVIEW_REQUIRED', `pref contradiction: ${problems[0]}`); return }
        const earliest = earliestQualifying(this._lastSlots, this.prefs, this.clock())
        if (!earliest) { this._pause('No appointment currently matches your preferences. We will keep checking.', 'no_availability'); return }
        // Auto-book only when the applicant authorized it; else let them pick.
        if (this.prefs.allowAutoBook || this.inputs.selectedSlotId) {
          this._targetSlot = this.inputs.selectedSlotId
            ? this._lastSlots.find((s) => s.slotId === this.inputs.selectedSlotId) || earliest
            : earliest
          this.pending = null
          m.transition('APPOINTMENT_BOOKING'); return
        }
        this._pause('Choose an appointment from your Ellis calendar (or authorize auto-booking).', 'appointment_selection', { slots: this._lastSlots.slice(0, 20) }); return
      }
      case 'APPOINTMENT_BOOKING': {
        if (this._booking) { m.transition('APPOINTMENT_AVAILABLE', 'booking already in progress'); return }
        this._booking = true
        try {
          const token = this.vault.reveal(this.sessionRef)
          // Revalidate the exact slot immediately before booking.
          const fresh = await d.searchAppointments({ sessionToken: token })
          const stillOpen = fresh.ok && fresh.slots.some((s) => s.slotId === this._targetSlot.slotId)
          if (!stillOpen) { this.inputs.selectedSlotId = null; m.transition('APPOINTMENT_AVAILABLE', 'slot gone; re-searching'); return }
          const res = await d.bookAppointment({ sessionToken: token, applicationId: this.applicationId, slotId: this._targetSlot.slotId })
          if (!res.ok) { m.transition('APPOINTMENT_AVAILABLE', res.code); return }
          this.appointment = res.appointment
          this.artifacts.push({ kind: 'appointment', value: res.appointment })
          this._makeIcs()
          this._emit('appointment_booked', { confirmationNo: res.appointment.confirmationNo, startUtc: res.appointment.startUtc })
          await this._notify('appointment', `Your ${this.adapter.destinationCountry} visa appointment is booked.`)
          m.transition('APPOINTMENT_BOOKED'); return
        } finally { this._booking = false }
      }
      case 'APPOINTMENT_BOOKED': {
        // Optional earlier-slot reschedule, only if authorized and improving.
        if (this.prefs.allowAutoReschedule && this.reschedules < this.prefs.maxAutoReschedules) {
          const token = this.vault.reveal(this.sessionRef)
          const fresh = await d.searchAppointments({ sessionToken: token })
          const cand = earliestQualifying(fresh.slots || [], this.prefs, this.clock())
          if (cand && isImprovement(this.appointment.startUtc, cand.startUtc, this.prefs)) {
            this._targetSlot = cand
            if (this.authorization.askBeforeReschedule && !this.inputs.rescheduleApproved) {
              this._pause('An earlier appointment is available. Approve the reschedule?', 'reschedule_approval', { candidate: cand }); return
            }
            m.transition('APPOINTMENT_RESCHEDULING'); return
          }
        }
        m.transition('FINAL_REVIEW_REQUIRED'); return
      }
      case 'APPOINTMENT_RESCHEDULING': {
        this.inputs.rescheduleApproved = false; this.pending = null
        const token = this.vault.reveal(this.sessionRef)
        const res = await d.rescheduleAppointment({ sessionToken: token, applicationId: this.applicationId, newSlotId: this._targetSlot.slotId })
        if (!res.ok) { m.transition('APPOINTMENT_BOOKED', res.code); return } // keep the original on failure
        this.appointment = res.appointment; this.reschedules++
        this._makeIcs()
        this._emit('appointment_rescheduled', { confirmationNo: res.appointment.confirmationNo, count: this.reschedules })
        m.transition('APPOINTMENT_BOOKED'); return
      }
      case 'FINAL_REVIEW_REQUIRED': {
        if (this.adapter.personalDeclarationRequired) { m.transition('PERSONAL_DECLARATION_REQUIRED'); return }
        m.transition('READY_TO_SUBMIT'); return
      }
      case 'PERSONAL_DECLARATION_REQUIRED': {
        if (!this.inputs.declarationCompleted) {
          const lv = this._liveView('Personally complete the declaration on the portal.')
          this._pause('Only you can sign the declaration under penalty of perjury.', 'personal_declaration', { liveView: lv }); return
        }
        // Record the human declaration marker; Ellis never clicks it itself.
        const token = this.vault.reveal(this.sessionRef)
        const ok = await d.declarePersonally({ sessionToken: token, applicationId: this.applicationId, humanConfirmed: 'HUMAN_DECLARED' })
        this.pending = null
        if (!ok.ok) { m.transition('RECOVERABLE_FAILURE', 'declaration'); return }
        m.transition('READY_TO_SUBMIT'); return
      }
      case 'READY_TO_SUBMIT': { m.transition('SUBMITTING'); return }
      case 'SUBMITTING': {
        const token = this.vault.reveal(this.sessionRef)
        // RECONCILE before submitting: never double-submit.
        const cur = await d.getApplicationState({ sessionToken: token, applicationId: this.applicationId })
        if (cur.ok && cur.submitted) { this.confirmation = cur.confirmation; m.transition('SUBMITTED', 'already submitted (reconciled)'); return }
        const res = await d.submit({ sessionToken: token, applicationId: this.applicationId })
        if (!res.ok) { m.transition('RECOVERABLE_FAILURE', res.code); return }
        this.confirmation = res.confirmation
        this.artifacts.push({ kind: 'confirmation', value: res.confirmation })
        this._emit('submitted', { referenceNo: res.confirmation.referenceNo })
        m.transition('SUBMITTED'); return
      }
      case 'SUBMITTED': { m.transition('CONFIRMATION_PENDING'); return }
      case 'CONFIRMATION_PENDING': {
        // Store artifacts + email the applicant, then complete.
        await this._notify('submitted', `Your ${this.adapter.destinationCountry} visa application is submitted. Reference ${this.confirmation.referenceNo}.`)
        this._emit('completed', { referenceNo: this.confirmation.referenceNo, artifacts: this.artifacts.map((a) => a.kind) })
        m.transition('COMPLETED'); return
      }
      default: return
    }
  }

  _liveView(reason) {
    // Synchronous shape for the driver; provider call is fire-and-forget here.
    const configured = BrowserbaseLiveView.isConfigured()
    return { mode: configured ? 'browserbase_liveview' : 'local_handoff', reason, caseId: this.caseId }
  }

  _makeIcs() {
    const a = this.appointment
    this._ics = appointmentIcs({
      uid: `${this.caseId}@ellis`, startUtc: a.startUtc, summary: `${this.adapter.destinationCountry} visa appointment`,
      location: a.locationId, description: `Confirmation ${a.confirmationNo}`
    })
    this.artifacts = this.artifacts.filter((x) => x.kind !== 'ics')
    this.artifacts.push({ kind: 'ics', value: this._ics })
  }

  async _notify(kind, body) {
    if (!this.email) return
    // Emails carry references and appointment info — never a reusable password.
    try { await this.email.send({ to: this.applicant.email, subject: `${this.adapter.destinationCountry} visa — ${kind}`, body }) } catch { /* email failure is non-fatal */ }
  }

  snapshot() {
    // Serializable resume point (excludes the vault, which is external).
    return {
      caseId: this.caseId, adapterId: this.adapter.adapterId, state: this.machine.state,
      history: this.machine.history, inputs: this.inputs, credentialRef: this.credentialRef,
      sessionRef: this.sessionRef, applicationId: this.applicationId, receipt: this.receipt,
      appointment: this.appointment, confirmation: this.confirmation, reschedules: this.reschedules,
      pending: this.pending
    }
  }
}
