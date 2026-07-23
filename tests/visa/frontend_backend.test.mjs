// Frontend-to-backend integration test: drives the REAL visaBackend.js client
// (the exact code the Electron renderer uses) through the full applicant
// lifecycle against the running backend — create case → upload+OCR → review →
// approve → preferences → prepare+sign authorization → start → resolve every
// human handoff by signal → COMPLETED with a confirmation.
//
// Auto-skips when the backend isn't reachable, so it's safe in any environment.
// Run against the live stack:  node --test tests/visa/frontend_backend.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'

import { createVisaClient } from '../../src/renderer/src/lib/visaBackend.js'
import { newSession, isTerminal } from '../../src/renderer/src/lib/visaSession.js'

const BASE = process.env.ELLIS_BACKEND_URL || 'http://localhost:8000'

let reachable = false
try { const r = await fetch(BASE + '/healthz'); reachable = r.ok } catch { /* not up */ }

async function driveToCompletion(client, caseId) {
  let status = await client.start(caseId)
  for (let i = 0; i < 40 && !isTerminal(status.state); i++) {
    const h = status.pending && status.pending.handoff
    if (!h) break
    if (h === 'review') {
      status = await client.signal(caseId, 'approve_review', {})
    } else if (h === 'authorization') {
      const prep = await client.prepareAuthorization(caseId, {})
      const signed = await client.signAuthorization(caseId, {
        document_hash: prep.document_hash, consent_given: true, intent_confirmed: true,
        signature_method: 'typed', signature_value: 'Test Applicant',
        step_up_token: prep.step_up_token, auth_method: 'email_otp'
      })
      assert.ok(signed.artifact_hash, 'signature must produce a tamper-evident artifact hash')
      status = await client.signal(caseId, 'sign_authorization', {})
    } else if (h === 'captcha') {
      status = await client.signal(caseId, 'solve_captcha', {})
    } else if (h === 'email_verification' || h === 'otp') {
      const v = await client.mockVerification(caseId)
      status = await client.signal(caseId, 'verify_email', { token: v.token })
    } else if (h === 'payment_approval') {
      status = await client.signal(caseId, 'approve_payment', {})
    } else if (h === 'payment' || h === 'three_ds') {
      status = await client.signal(caseId, 'complete_payment', {})
    } else if (h === 'appointment_selection') {
      status = await client.signal(caseId, 'select_appointment', { slot_id: status.pending.slots[0].slotId })
    } else if (h === 'personal_declaration') {
      status = await client.signal(caseId, 'complete_declaration', {})
    } else {
      break // no_availability / reschedule_approval — not on the happy path
    }
  }
  return status
}

test('full applicant lifecycle via visaBackend.js', { skip: reachable ? false : `backend not reachable at ${BASE}` }, async () => {
  const client = createVisaClient(newSession({ orgId: 'itest-org', userId: 'itest-user' }))

  // capabilities are honest booleans (never secrets)
  const caps = await client.capabilities()
  assert.equal(typeof caps, 'object')

  // 1) create case
  const created = await client.createCase({
    full_name: 'Ingrid Test', email: 'ingrid@example.com', phone: '+100000000',
    destination_country: 'Mockland', visa_type: 'tourist', time_zone: 'UTC'
  })
  const caseId = created.id
  assert.ok(caseId, 'createCase returns an id')
  assert.equal(created.state, 'DRAFT')

  // 2) upload a document → OCR (backend-only) → review → approve
  const doc = await client.addDocument(caseId, {
    name: 'passport.pdf', mime: 'application/pdf', size_bytes: 2048,
    text: 'PASSPORT\nSurname: TEST\nGiven: INGRID\nPassport No: X1234567'
  })
  assert.ok(doc.id, 'addDocument returns a stored document id')
  const review = await client.review(caseId)
  assert.ok(Array.isArray(review.documents) && review.documents.length >= 1)
  const approved = await client.approveDocument(caseId, doc.id, [{ key: 'given_name', value: 'INGRID' }])
  assert.equal(approved.approved, true)

  // 2b) supply the destination adapter's required fields (missing-info step)
  assert.ok(review.missing_fields, 'review reports which required fields are missing')
  const filled = await client.updateAnswers(caseId, {
    passport_number: 'X1234567', nationality: 'UTO', birth_date: '900101'
  })
  assert.equal(filled.missing_fields.length, 0, `still missing: ${filled.missing_fields}`)

  // 3) appointment preferences round-trip
  const prefs = await client.setPreferences(caseId, {
    preferredLocation: 'MOCKLAND-CAP', allowAutoBook: false, applicantTimeZone: 'UTC'
  })
  assert.ok(prefs.prefs)

  // 4) drive the durable workflow through every handoff to completion
  const status = await driveToCompletion(client, caseId)
  assert.equal(status.state, 'COMPLETED', `expected COMPLETED, got ${status.state}`)
  assert.ok(status.confirmation, 'a submission confirmation is produced')

  // 5) reloading the case shows the confirmation + appointment (persisted)
  const reloaded = await client.getCase(caseId)
  assert.equal(reloaded.state, 'COMPLETED')
  assert.ok(reloaded.confirmation && reloaded.confirmation.reference_no, 'confirmation persisted on the case')
  assert.ok(reloaded.appointment && reloaded.appointment.confirmation_no, 'appointment persisted on the case')

  // 6) the audit trail records the journey (append-only, no secrets)
  const audit = await client.audit(caseId)
  const actions = audit.events.map((e) => e.action)
  assert.ok(actions.includes('case_created'))
  assert.ok(actions.includes('document_approved'))
})
