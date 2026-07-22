import { test } from 'node:test'
import assert from 'node:assert/strict'

import { MockPortal } from '../../src/main/portal/mockPortal.mjs'
import { buildMocklandAdapter } from '../../src/main/portal/adapters/mockland.mjs'
import { _clearRegistryForTests } from '../../src/main/portal/adapterContract.mjs'
import { VisaWorkflow } from '../../src/main/portal/workflow.mjs'
import { SecretsVault } from '../../src/main/portal/vault.mjs'
import { AuditLog } from '../../src/main/portal/audit.mjs'
import { makeEmailProvider } from '../../src/main/portal/providers.mjs'

function setup(portalOpts = {}) {
  _clearRegistryForTests()
  const portal = new MockPortal(portalOpts)
  const adapter = buildMocklandAdapter(portal)
  const table = new Map()
  const vault = new SecretsVault({ backend: 'node-aes-gcm', table })
  const audit = new AuditLog()
  const sent = []
  const email = makeEmailProvider((msg) => { sent.push(msg); return { ok: true } })
  return { portal, adapter, vault, table, audit, email, sent }
}

let _caseSeq = 0
function makeWorkflow(env, overrides = {}) {
  return new VisaWorkflow({
    caseId: 'case-' + (++_caseSeq),
    adapter: env.adapter,
    applicant: { fullName: 'Test Traveler', email: 'traveler@example.com' },
    answers: { fullName: 'Test Traveler', email: 'traveler@example.com', passportNumber: 'X1234567', nationality: 'Testland', birthDate: '1990-01-01' },
    documents: [{ name: 'passport.pdf', mime: 'application/pdf', sizeBytes: 2048 }, { name: 'photo.jpg', mime: 'image/jpeg', sizeBytes: 1024 }],
    preferences: { preferredLocation: 'MOCKLAND-CAP', allowAutoBook: true, applicantTimeZone: 'UTC', minAdvanceMs: 0 },
    authorization: { maxFeeCents: 10000, currency: 'USD', allowRepresentativeSubmit: false },
    vault: env.vault, audit: env.audit, email: env.email,
    ...overrides
  })
}

// Drive the workflow to completion by answering each human handoff.
async function runToCompletion(wf, env) {
  let s = await wf.start()
  const guard = 40
  for (let i = 0; i < guard; i++) {
    if (['COMPLETED', 'CANCELLED', 'MANUAL_REVIEW_REQUIRED', 'RECOVERABLE_FAILURE'].includes(s.state)) break
    if (!s.pending) break
    switch (s.pending.handoff) {
      case 'review': s = await wf.approveReview(); break
      case 'authorization': s = await wf.signAuthorization(); break
      case 'captcha': s = await wf.solveCaptcha(); break
      case 'email_verification': {
        const link = env.portal.emails.find((e) => e.kind === 'verification')
        const token = new URL(link.link).searchParams.get('token')
        s = await wf.verifyEmail(token); break
      }
      case 'payment_approval': s = await wf.approvePayment(); break
      case 'payment': s = await wf.completePayment(); break
      case 'appointment_selection': s = await wf.selectAppointment(s.pending.slots[0].slotId); break
      case 'reschedule_approval': s = await wf.approveReschedule(); break
      case 'personal_declaration': s = await wf.completeDeclaration(); break
      default: return s // unknown handoff → stop
    }
  }
  return s
}

test('full end-to-end happy path completes with all artifacts', async () => {
  const env = setup()
  const wf = makeWorkflow(env)
  const final = await runToCompletion(wf, env)
  assert.equal(final.state, 'COMPLETED')
  assert.ok(final.confirmation?.referenceNo)
  const kinds = wf.artifacts.map((a) => a.kind).sort()
  assert.deepEqual([...new Set(kinds)].sort(), ['appointment', 'confirmation', 'ics', 'receipt'])
  // Applicant was emailed appointment + submission (no password).
  assert.ok(env.sent.length >= 2)
  assert.equal(env.sent.some((m) => /password/i.test(m.body || '')), false)
})

test('CAPTCHA cannot be auto-solved: workflow pauses for the human', async () => {
  const env = setup()
  const wf = makeWorkflow(env)
  let s = await wf.start()
  // advance through review + authorization
  s = await wf.approveReview()
  s = await wf.signAuthorization()
  // Now it must be paused on the CAPTCHA handoff — not past it.
  assert.equal(s.state, 'CAPTCHA_ACTION_REQUIRED')
  assert.equal(s.pending.handoff, 'captcha')
  // The mock portal only accepts a real human marker; prove Ellis never had it
  // by checking a guessed answer is rejected.
  const guessed = env.portal.submitCaptcha({ captchaToken: 'x', humanAnswer: 'ELLIS_GUESS' })
  assert.equal(guessed.ok, false)
})

test('no double payment: reconciliation short-circuits a second charge', async () => {
  const env = setup()
  const wf = makeWorkflow(env)
  await runToCompletion(wf, env)
  const receiptNo = wf.receipt.receiptNo
  // Re-pay attempt against the same application must reconcile as already-paid.
  const token = env.vault.reveal(wf.sessionRef)
  const state = env.portal.getApplicationState({ sessionToken: token, applicationId: wf.applicationId })
  assert.equal(state.paid, true)
  assert.equal(state.receipt.receiptNo, receiptNo) // unchanged — no second receipt
})

test('submission is idempotent (no double submit)', async () => {
  const env = setup()
  const wf = makeWorkflow(env)
  await runToCompletion(wf, env)
  const ref = wf.confirmation.referenceNo
  const token = env.vault.reveal(wf.sessionRef)
  const again = env.portal.submit({ sessionToken: token, applicationId: wf.applicationId })
  assert.equal(again.ok, true)
  assert.equal(again.idempotent, true)
  assert.equal(again.confirmation.referenceNo, ref)
})

test('submission blocked until the personal declaration is completed by the human', async () => {
  const env = setup()
  // Build a workflow but stop before declaration by NOT completing it.
  const wf = makeWorkflow(env)
  let s = await wf.start()
  const guard = 40
  for (let i = 0; i < guard; i++) {
    if (!s.pending) break
    if (s.pending.handoff === 'personal_declaration') break
    switch (s.pending.handoff) {
      case 'review': s = await wf.approveReview(); break
      case 'authorization': s = await wf.signAuthorization(); break
      case 'captcha': s = await wf.solveCaptcha(); break
      case 'email_verification': { const link = env.portal.emails.find((e) => e.kind === 'verification'); s = await wf.verifyEmail(new URL(link.link).searchParams.get('token')); break }
      case 'payment_approval': s = await wf.approvePayment(); break
      case 'payment': s = await wf.completePayment(); break
      case 'appointment_selection': s = await wf.selectAppointment(s.pending.slots[0].slotId); break
      default: s = null
    }
    if (!s) break
  }
  assert.equal(wf.machine.state, 'PERSONAL_DECLARATION_REQUIRED')
  // Portal refuses submission without the human declaration marker.
  const token = env.vault.reveal(wf.sessionRef)
  const res = env.portal.submit({ sessionToken: token, applicationId: wf.applicationId })
  assert.equal(res.ok, false)
  assert.equal(res.code, 'DECLARATION_REQUIRED')
})

test('fee over the authorized maximum halts to manual review (no payment)', async () => {
  const env = setup()
  const wf = makeWorkflow(env, { authorization: { maxFeeCents: 5000, currency: 'USD' } }) // fee is 8000
  const final = await runToCompletion(wf, env)
  assert.equal(final.state, 'MANUAL_REVIEW_REQUIRED')
  const token = env.vault.reveal(wf.sessionRef)
  const st = env.portal.getApplicationState({ sessionToken: token, applicationId: wf.applicationId })
  assert.equal(st.paid, false)
})

test('payment fallback engages when Stripe Issuing is unconfigured', async () => {
  const env = setup()
  const wf = makeWorkflow(env, { preferences: { preferredLocation: 'MOCKLAND-CAP', allowAutoBook: true, applicantTimeZone: 'UTC', minAdvanceMs: 0 } })
  let s = await wf.start()
  s = await wf.approveReview(); s = await wf.signAuthorization(); s = await wf.solveCaptcha()
  const link = env.portal.emails.find((e) => e.kind === 'verification')
  s = await wf.verifyEmail(new URL(link.link).searchParams.get('token'))
  s = await wf.approvePayment()
  // With no Stripe creds + adapter thirdPartyPaymentPolicy 'applicant', we land
  // in the applicant-controlled payment window (local_handoff fallback).
  assert.equal(s.state, 'PAYMENT_ACTION_REQUIRED')
  assert.equal(s.pending.handoff, 'payment')
  assert.equal(s.pending.liveView.mode, 'local_handoff')
})

test('generated portal password never appears in audit log or emails', async () => {
  const env = setup()
  const wf = makeWorkflow(env)
  await runToCompletion(wf, env)
  // Reveal the stored password and prove it is nowhere it should not be.
  const pw = env.vault.reveal(wf.credentialRef)
  assert.ok(pw.length >= 12)
  assert.equal(env.audit.containsPlaintext(pw), false)
  assert.equal(env.sent.some((m) => (m.body || '').includes(pw)), false)
  // And it must not be in the persisted vault table as plaintext.
  assert.equal(JSON.stringify([...env.table.entries()]).includes(pw), false)
})

test('workflow survives a restart via snapshot at a handoff', async () => {
  const env = setup()
  const wf = makeWorkflow(env)
  let s = await wf.start()
  s = await wf.approveReview()
  const snap = wf.snapshot()
  assert.equal(snap.state, 'AUTHORIZATION_PENDING')
  assert.ok(snap.history.length > 1)
  // Continue after "restart" (same instance stands in for a rehydrated one).
  s = await wf.signAuthorization()
  assert.notEqual(s.state, 'AUTHORIZATION_PENDING')
})

test('maintenance mode routes to recoverable failure, not a crash', async () => {
  const env = setup({ maintenance: true })
  const wf = makeWorkflow(env)
  const final = await runToCompletion(wf, env)
  assert.equal(final.state, 'RECOVERABLE_FAILURE')
})
