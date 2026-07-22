// Demonstration adapter: "Mockland" tourist visa, driving the in-process
// MockPortal. This is the template every real country adapter follows. It is
// intentionally MOCK-ONLY (productionApprovalStatus: 'mock', productionEnabled:
// false) — a real portal requires its own adapter, legal review, and approval.
//
// The `driver` object holds the imperative hooks the orchestrator calls. In a
// real adapter these use Playwright against `portal` (a browser page); here
// they call the MockPortal API. The orchestrator itself is portal-agnostic.

import { registerAdapter } from '../adapterContract.mjs'
import { MOCK_BASE } from '../mockPortal.mjs'

export function buildMocklandAdapter(portal) {
  return registerAdapter({
    adapterId: 'mockland-tourist-v1',
    adapterVersion: 1,
    destinationCountry: 'Mockland',
    visaType: 'tourist',
    portalOperator: 'Mockland Bureau of Consular Affairs (MOCK)',
    approvedDomains: [new URL(MOCK_BASE).host],
    registrationUrl: `${MOCK_BASE}/register`,
    loginUrl: `${MOCK_BASE}/login`,
    applicationUrl: `${MOCK_BASE}/apply`,
    paymentDomains: [new URL(MOCK_BASE).host],
    appointmentUrl: `${MOCK_BASE}/appointments`,
    supportedLanguages: ['en'],
    requiredApplicantFields: ['fullName', 'email', 'passportNumber', 'nationality', 'birthDate'],
    requiredDocuments: ['passport', 'photo'],
    registrationMappings: [
      { field: 'email', selector: '#reg-email' },
      { field: 'password', selector: '#reg-password', sensitive: true },
      { field: 'fullName', selector: '#reg-name' }
    ],
    loginMappings: [
      { field: 'email', selector: '#login-email' },
      { field: 'password', selector: '#login-password', sensitive: true }
    ],
    applicationMappings: [
      { field: 'passportNumber', selector: '#app-passport' },
      { field: 'nationality', selector: '#app-nationality' },
      { field: 'birthDate', selector: '#app-dob' }
    ],
    allowedActions: ['navigate', 'read', 'fill', 'click', 'upload'],
    prohibitedActions: [
      'solve_captcha', 'bypass_bot_detection', 'spoof_fingerprint', 'rotate_proxy',
      'evade_rate_limit', 'bypass_waiting_room', 'access_other_account', 'intercept_otp',
      'accept_declaration', 'navigate_unapproved_domain'
    ],
    emailVerification: 'link',
    otp: 'none',
    captcha: { detect: '#captcha-challenge' },
    passwordRequirements: { minLength: 12, requireUpper: true, requireDigit: true, requireSymbol: true, maxLength: 64 },
    // The mock portal accepts a controlled card, but third-party payment policy
    // stays 'applicant' so the default path is the applicant-controlled window
    // unless Stripe Issuing is explicitly configured AND approved.
    paymentPolicy: 'applicant',
    thirdPartyPaymentPolicy: 'applicant',
    appointmentSearch: 'calendar',
    appointmentBooking: 'automated', // permitted for this mock when applicant authorizes
    reschedulePolicy: 'automated',
    cancellationRisks: 'Rescheduling frees the current slot only after the new slot is confirmed; a failed reschedule keeps the original.',
    representativeSubmission: 'applicant', // final submit requires the personal declaration
    personalDeclarationRequired: true,
    feeDiscovery: 'page',
    paymentSuccessSignal: 'receipt.receiptNo',
    appointmentSuccessSignal: 'appointment.confirmationNo',
    submissionSuccessSignal: 'confirmation.referenceNo',
    confirmationExtraction: 'confirmation.referenceNo',
    receiptExtraction: 'receipt.receiptNo',
    resumeBehavior: 'Re-establish session via login; reconcile application state before any payment/booking/submit retry.',
    knownFailureStates: ['MAINTENANCE', 'SESSION_EXPIRED', 'CAPTCHA_FAILED', 'PAYMENT_DECLINED', 'REQUIRES_3DS', 'SLOT_TAKEN'],
    rateLimits: { searchMinIntervalMs: 30_000, maxChecksPerDay: 24 },
    portalPolicyReviewDate: '2026-07-21',
    productionApprovalStatus: 'mock',
    productionEnabled: false,

    // Imperative hooks — the only place that knows how to talk to THIS portal.
    driver: {
      async register({ email, password, fullName }) { return portal.register({ email, password, fullName }) },
      async submitCaptcha(args) { return portal.submitCaptcha(args) },
      async verifyEmail(args) { return portal.verifyEmail(args) },
      async login(args) { return portal.login(args) },
      async createApplication(args) { return portal.createApplication(args) },
      async uploadDocument(args) { return portal.uploadDocument(args) },
      async discoverFee(args) { return portal.discoverFee(args) },
      async pay(args) { return portal.pay(args) },
      async complete3ds(args) { return portal.complete3ds(args) },
      async searchAppointments(args) { return portal.searchAppointments(args) },
      async bookAppointment(args) { return portal.bookAppointment(args) },
      async rescheduleAppointment(args) { return portal.rescheduleAppointment(args) },
      async declarePersonally(args) { return portal.declarePersonally(args) },
      async submit(args) { return portal.submit(args) },
      async getApplicationState(args) { return portal.getApplicationState(args) }
    }
  })
}
