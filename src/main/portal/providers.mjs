// Provider capability interfaces. Every external integration named in the brief
// (Stripe Issuing, Browserbase Live View, Docusign, transactional email) is
// represented here behind a uniform interface with a capability check. When a
// provider is not configured, ONLY that provider is disabled — the workflow
// falls back to the applicant-controlled path and keeps running.
//
// None of these ship live credentials in this repo. `isConfigured()` reads env
// vars; absent them, the safe fallback engages. This is exactly the brief's
// rule: "Missing credentials must disable only the affected integration."

const env = (k) => (process.env[k] || '').trim()

// --- Stripe Issuing (automated fee payment, when permitted) ----------------
export const StripeIssuing = {
  name: 'stripe-issuing',
  isConfigured() { return !!env('STRIPE_SECRET_KEY') && env('ELLIS_ISSUING_APPROVED') === 'true' },
  /**
   * Create a single-use virtual card with the narrowest controls. Returns a
   * reference only — full card details never leave the (unconfigured here)
   * provider and are entered only inside the isolated browser, never via LLM.
   */
  async createControlledCard() {
    if (!this.isConfigured()) return { ok: false, code: 'NOT_CONFIGURED', fallback: 'applicant_payment_window' }
    // Real implementation would call Stripe here. Not reachable without creds.
    throw new Error('StripeIssuing.createControlledCard not activated in this build')
  }
}

// --- Browserbase Live View (human handoff surface) -------------------------
export const BrowserbaseLiveView = {
  name: 'browserbase-liveview',
  isConfigured() { return !!env('BROWSERBASE_API_KEY') },
  /**
   * Mint a short-lived Live View URL for an isolated session. Fallback when
   * unconfigured: a local handoff token the UI uses to render an instruction
   * panel (the applicant completes the step on the real portal in their own
   * browser). The handoff is still first-class — just not remote-rendered.
   */
  async createLiveView({ caseId, reason, ttlMs = 10 * 60_000 }) {
    if (!this.isConfigured()) {
      return { ok: true, mode: 'local_handoff', reason, caseId, expiresAt: Date.now() + ttlMs, url: null }
    }
    throw new Error('BrowserbaseLiveView.createLiveView not activated in this build')
  }
}

// --- Docusign embedded authorization --------------------------------------
export const Docusign = {
  name: 'docusign',
  isConfigured() { return !!env('DOCUSIGN_INTEGRATION_KEY') && !!env('DOCUSIGN_ACCOUNT_ID') },
  /**
   * Create an embedded signing envelope for the Ellis authorization. Fallback
   * when unconfigured: an in-app typed/drawn e-authorization (the existing
   * SignaturePad flow) recorded with a hash — legally weaker than Docusign but
   * a real, auditable applicant authorization, and clearly labeled as such.
   */
  async createAuthorizationEnvelope(payload) {
    if (!this.isConfigured()) {
      return { ok: true, mode: 'in_app_authorization', envelopeId: null, payload }
    }
    throw new Error('Docusign.createAuthorizationEnvelope not activated in this build')
  },
  verifyWebhookSignature() { return this.isConfigured() ? false : true /* no-op accepted only in fallback */ }
}

// --- Transactional email ---------------------------------------------------
// Reuses Ellis's existing mailer (nodemailer/SMTP or local Mail) via an
// injected sender so this module stays Node-testable.
export function makeEmailProvider(sendFn) {
  return {
    name: 'email',
    isConfigured() { return typeof sendFn === 'function' },
    async send(msg) {
      if (!this.isConfigured()) return { ok: false, code: 'NO_EMAIL_PROVIDER' }
      return sendFn(msg)
    }
  }
}

/** One call to see what's live vs. running on a safe fallback. */
export function capabilityReport() {
  return {
    stripeIssuing: StripeIssuing.isConfigured(),
    browserbaseLiveView: BrowserbaseLiveView.isConfigured(),
    docusign: Docusign.isConfigured(),
    // These being false is expected in this build; the workflow still runs
    // end-to-end via applicant-controlled fallbacks.
    fallbacks: {
      payment: StripeIssuing.isConfigured() ? 'stripe_issuing' : 'applicant_payment_window',
      handoff: BrowserbaseLiveView.isConfigured() ? 'browserbase_liveview' : 'local_handoff',
      authorization: Docusign.isConfigured() ? 'docusign' : 'in_app_authorization'
    }
  }
}
