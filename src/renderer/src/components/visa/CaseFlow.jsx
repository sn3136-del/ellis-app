// The applicant case flow: prepare (documents + preferences + authorization),
// start the durable workflow, then resolve each human handoff the backend pauses
// at, and finally show the confirmation + receipt + appointment. The backend
// (DB-runner or Temporal) owns all state; this screen reads status and sends the
// matching signal for each handoff.
import { useEffect, useMemo, useRef, useState } from 'react'
import { useToast, Loading, ErrorNote, KVList, Empty } from '../ui.jsx'
import { useLocale } from '../../lib/locale.jsx'
import { HANDOFF_COPY, HANDOFF_UI } from '../../lib/visaBackend.js'
import { handoffCopy, isTerminal, formatSlot, resultDisposition } from '../../lib/visaSession.js'
import {
  preferencesTabVisible,
  validityMeta, formatDateUS, isDateKey
} from '../../lib/intake.js'
import Preferences from './Preferences.jsx'
import { ContinuePanel, DocCards } from './Checklist.jsx'
import {
  SignatureModal, LiveViewModal, PaymentApprove, PaymentModal,
  PaymentDetailsModal, AppointmentCalendar, RescheduleConfirm, DeclarationModal,
  FinalReviewModal, AdditionalInfoModal,
  PortalWatch, LiveFrame, usePortalLiveView
} from './handoffs.jsx'

export default function CaseFlow({ client, caseId, onNotify, onOpenCase }) {
  const toast = useToast()
  const { t } = useLocale()
  const [status, setStatus] = useState(null)
  const [prefs, setPrefs] = useState(null)
  const [audit, setAudit] = useState([])
  const [error, setError] = useState(null)
  const [modal, setModal] = useState(null)   // active handoff modal id
  const [busy, setBusy] = useState(false)
  const [standing, setStanding] = useState(null)  // standing-authorization state
  const [journey, setJourney] = useState(null)    // saved guidance + checklist + audit status
  const [progress, setProgress] = useState(null)  // live portal progress (polled)
  const [everQueued, setEverQueued] = useState(false) // a background run exists
  const [portalBlocked, setPortalBlocked] = useState(false) // real_only_stop seen
  const [packet, setPacket] = useState(null)      // in-person route: the carry-in folder
  const landedOnDocs = useRef(false)
  const docsRef = useRef(null)          // scroll target for "go to documents"
  const progressSigRef = useRef('')
  const autoSkipRef = useRef(false)
  const autoRecheckRef = useRef(false)

  // A different case opened in-place (e.g. the renewal flow): reset every
  // per-case piece of state so the previous case never bleeds through.
  useEffect(() => {
    setStatus(null); setProgress(null); setEverQueued(false)
    setModal(null); setError(null); setJourney(null); setPortalBlocked(false)
    setPacket(null)
    landedOnDocs.current = false
    progressSigRef.current = ''
    autoSkipRef.current = false
    autoRecheckRef.current = false
  }, [caseId])

  async function refresh() {
    try {
      const c = await client.getCase(caseId)
      setStatus(c)
      // An in-person route (Schengen, US B1/B2 …) has one journey and it is
      // not this page's: answer what Ellis needs, take the folder, go and
      // present it. The packet 409s for every route Ellis files itself, so
      // this is also the flag that tells the rest of the page to stand down.
      client.appointmentPacket(caseId).then(setPacket).catch(() => setPacket(null))
      client.audit(caseId).then((a) => setAudit(a.events || [])).catch(() => {})
      client.getStandingAuthorization(caseId)
        .then((s) => setStanding(s.current)).catch(() => {})
      client.caseChecklist(caseId).then((j) => {
        setJourney(j)
        // A freshly continued case lands the applicant on document intake —
        // once, never fighting later manual tab choices.
        if (!landedOnDocs.current && c.state === 'DRAFT' &&
            j && (j.checklist_counts || {}).required_missing > 0) {
          landedOnDocs.current = true
          /* single-page: documents are already in view */
        }
      }).catch(() => {})
    } catch (e) { setError({ message: e.message }) }
  }
  useEffect(() => { refresh() }, [caseId])

  const pending = status?.pending
  // While a background run is queued/running it is RESOLVING the recorded
  // pause: the case row still carries that pause until the run persists, so
  // showing it again would invite the applicant to answer a question Ellis
  // is already acting on (and re-enter the same value twice).
  //
  // Exception — runs an OPEN dialog started and is itself waiting on: a
  // portal-view RESTORE (its embedded window shows the page being rebuilt)
  // and an on-demand FEE READ. Treating those as "Ellis is busy" closed the
  // very dialog that launched them, dropping the applicant back to the
  // "Confirm the official fee" card; pressing Continue re-opened it, which
  // auto-started another read, which closed it again — an endless loop
  // (2026-07-28).
  const selfRun = progress?.run_signal === 'restore_portal' ||
                  progress?.run_signal === 'read_fee'
  const runBusy = !!progress && (progress.active || progress.queued) && !selfRun
  const handoff = runBusy ? null : pending?.handoff
  const state = status?.state
  const terminal = isTerminal(state)
  const startedNow = state && state !== 'DRAFT'

  // Live progress polling while Ellis works in the background: the backend
  // persists every real checkpoint, so refresh/restart shows the same state.
  // When the background run pauses for the applicant (or the state advances),
  // the full case is re-fetched so the matching handoff surface appears.
  useEffect(() => {
    // Poll while the case is started OR a background run exists (a queued
    // 'start' leaves state DRAFT until the executor advances it — polling
    // must engage immediately, not only after the state moves).
    if ((!startedNow && !everQueued) || terminal) return undefined
    let live = true
    async function tick() {
      try {
        const pr = await client.caseProgress(caseId)
        if (!live) return
        setProgress(pr)
        const sig = `${pr.state}|${pr.waiting_for_applicant}|${pr.handoff}|${pr.run_status}`
        if (progressSigRef.current && progressSigRef.current !== sig) {
          refresh()
          // A reclassified pause must swap the OPEN dialog (fee_confirmation
          // -> payment_approval after a fee read, or -> additional_information
          // / captcha when the page showed the real blocker). Functional
          // update: this effect's closure over `modal` is stale (deps exclude
          // it), so the current value must come from the setter itself.
          if (pr.waiting_for_applicant && pr.handoff) {
            setModal((cur) => cur && cur !== pr.handoff ? pr.handoff : cur)
          }
        }
        progressSigRef.current = sig
      } catch { /* endpoint missing/offline: the manual Refresh still works */ }
    }
    tick()
    const iv = setInterval(tick, 2500)
    return () => { live = false; clearInterval(iv) }
  }, [caseId, startedNow, everQueued, terminal])

  // A handoff modal left open when the background run starts must close —
  // its question is already being acted on.
  useEffect(() => {
    if (runBusy && modal) setModal(null)
  }, [runBusy, modal])

  async function retryPortal() {
    setBusy(true); setError(null)
    try {
      await client.retryPortal(caseId)
      toast('Retrying from your last saved step')
      refresh()
    } catch (e) {
      setError({ message: (e.detail && e.detail.message) || e.message })
    }
    setBusy(false)
  }

  // Apply a workflow status response (from start/signals) to local state.
  // A queued response means a background run is resolving the pause the
  // applicant just answered: clear pending optimistically (the progress
  // poll restores the real pause when the run reaches one) and start polling.
  function apply(res) {
    if (res && res.queued) {
      setEverQueued(true)
      setStatus((prev) => ({ ...prev, ...res, pending: null }))
      // The signature ceremony grants the standing authorization server-side;
      // pull it straight away so no surface can still offer to authorize.
      client.getStandingAuthorization(caseId)
        .then((s) => setStanding(s.current)).catch(() => {})
    } else {
      setStatus((prev) => ({ ...prev, ...res }))
    }
    client.audit(caseId).then((a) => setAudit(a.events || [])).catch(() => {})
    onNotify && onNotify()
  }

  // Optional-only asks are never shown: fields the portal does not mark
  // required are skipped automatically and the case re-drives itself — the
  // portal's own validation asks precisely if something was needed after all.
  // One shot per case load, so a genuine backend re-ask (auto-advance failed)
  // still surfaces instead of looping.
  useEffect(() => {
    const qs = pending?.questions
    if (autoSkipRef.current || runBusy || busy) return
    if (pending?.handoff !== 'additional_information' || !Array.isArray(qs) || qs.length === 0) return
    const allSkippable = qs.every((q) =>
      q.mandatory === false && q.kind !== 'document' &&
      !String(q.key || '').startsWith('document:'))
    if (allSkippable) {
      autoSkipRef.current = true
      start(true)
    }
  }, [pending, runBusy])

  // A recorded portal_form pause re-checks itself ONCE per case load: the
  // page may have moved on (or a newer Ellis can now read the portal's own
  // complaints and ask in-app instead). A pause that survives the re-check is
  // genuine — the secure window stays the answer, with no further auto-runs.
  useEffect(() => {
    if (autoRecheckRef.current || runBusy || busy) return
    if (pending?.handoff !== 'portal_form') return
    autoRecheckRef.current = true
    start('recheck')
  }, [pending, runBusy])

  async function start(autoMode) {
    // `start` doubles as an onClick handler (which passes an event object):
    // only the literal values from the auto effects change the toast.
    const mode = autoMode === true ? 'skip' : autoMode === 'recheck' ? 'recheck' : null
    setBusy(true); setError(null)
    try {
      apply(await client.start(caseId))
      toast(mode === 'skip' ? 'Skipping the optional items — continuing your application'
        : mode === 'recheck' ? 'Re-checking the official form for you…'
        : 'Application started')
    } catch (e) {
      // Honest fail-closed reasons get applicant-facing copy — never a bare
      // HTTP status. real_only_stop = no approved live portal connection for
      // this route; Ellis never simulates a government submission.
      const d = e.detail && typeof e.detail === 'object' ? e.detail : null
      const reason = d ? d.reason : null
      // The backend already worked out the route-specific, applicant-facing
      // reason ("your passport needs no visa in advance for Indonesia") and
      // sends it. Replacing it with the generic "no approved portal
      // connection" line told a traveller who needs NO visa to wait for a
      // portal release (2026-08-04). Its sentence wins; the generic copy is
      // the fallback for a stop that carries none.
      setError({
        message: reason === 'real_only_stop' ? (d.detail || t('case.portalUnavailable'))
          : reason === 'documents_incomplete' ? d.message
          : e.message
      })
      // A portal Ellis can't drive yet may still be one the applicant's own
      // consented run can TEACH it — surface that path next to the stop. But
      // ONLY where a portal is actually involved: there is nothing to teach on
      // a route that needs no online application at all.
      const HAS_PORTAL = ['EVISA', 'ELECTRONIC_AUTHORIZATION', 'ENTRY_PREPARATION']
      if (reason === 'real_only_stop' &&
          (!d.route_outcome || HAS_PORTAL.includes(d.route_outcome))) setPortalBlocked(true)
      // The note renders at the TOP of the case; Start sits at the bottom of a
      // long checklist. Measured on the Indonesia case: the answer landed 521px
      // ABOVE the viewport, so the applicant pressed Start and watched nothing
      // happen. An explanation nobody can see is the same as no explanation.
      requestAnimationFrame(() => {
        const note = document.querySelector('[data-ellis-error]')
        if (note) note.scrollIntoView({ behavior: 'smooth', block: 'center' })
      })
    }
    setBusy(false)
  }

  // Send the signal that resolves the current handoff, then advance.
  async function resolve(signalName, body) {
    const res = await client.signal(caseId, signalName, body || {})
    apply(res)
    setModal(null)
    // pull appointment/confirmation into view when they appear
    if (res.appointment || res.confirmation) refresh()
    return res
  }

  if (!status) return <Loading label="Loading your application" />

  const copy = handoff ? handoffCopy(HANDOFF_COPY, handoff) : null
  // A QUEUED run has started the application even though the case row still
  // reads DRAFT: the executor advances the state a moment later. Without
  // `everQueued` the page fell back to its pre-authorization view for that
  // window — showing "Authorize & start" again right after a successful
  // signature, which invited a second signing ceremony (2026-07-28).
  const started = (state && state !== 'DRAFT') || everQueued
  const kind = journey?.continuation_kind
  // One flowing Trip.com-style page — no tabs, no stage strip, no route
  // summary bar: the applicant sees the CURRENT step only.
  const docsPending = !started && (journey?.checklist_counts?.required_missing ?? 0) > 0
  // A route decided in person runs ONE journey, and this page is not it. The
  // packet 409s everywhere Ellis files the application itself, so a non-null
  // packet is exactly "this is a carry-in route".
  //
  // Everything below stands down while it is showing. It had all been on
  // screen at once: a completeness score, two different missing-item lists,
  // two download buttons for overlapping content, a live government calendar,
  // and a "Ready to go? Authorize & start my application" card on a route
  // where /start is guaranteed to answer "there is no government website to
  // submit through". The applicant's own words were "a lot of fluff"
  // (2026-08-04).
  const inPerson = !started && !!packet

  return (
    <div>
      {/* The execution-class banner is not shown (product decision, 2026-08-03).
          It read as a contradiction — "LIVE_PRODUCTION — not a real government
          submission" over "This is a real result" — because the headline came
          from the verification guard and the subtitle from the class's static
          description. The GUARD itself is untouched: resultDisposition still
          gates every terminal claim, so an unverified run can never render as
          "Application submitted" or show a reference number as real. */}
      {error && <ErrorNote error={error} />}
      {portalBlocked && (
        <PortalObservation t={t} client={client} caseId={caseId} />
      )}

      {!started && kind === 'entry_preparation' && (
        <EntryPrep t={t} client={client} caseId={caseId} journey={journey}
          onOpenCase={onOpenCase}
          onToDocuments={() => docsRef.current?.scrollIntoView({ behavior: 'smooth' })} />
      )}

      {!started && kind === 'passport_renewal' && (
        <RenewalPrep t={t} journey={journey}
          onToDocuments={() => docsRef.current?.scrollIntoView({ behavior: 'smooth' })} />
      )}

      {/* Routes decided in person. ONE card, showing ONE of three steps:
          answer what Ellis still needs, take the folder, go and present it. */}
      {inPerson && (
        <ConsularJourney t={t} client={client} caseId={caseId}
          packet={packet} journey={journey} onChanged={refresh} />
      )}

      {/* Document upload — illustrated cards. EVERY continuation kind has a
          document surface (entry preparation and renewal have their own
          checklists; hiding it left their primary CTA pointing nowhere).
          In-person routes ask for their documents inside ConsularJourney's
          first step, in the same place as the questions. */}
      {!started && !inPerson && (
        <div ref={docsRef}>
          {/* Form answers the released flow is KNOWN to need — asked here,
              the moment the case opens, so the applicant never waits for a
              live portal run to rediscover them one pause at a time. */}
          <FormQuestions t={t} client={client} caseId={caseId}
            questions={journey?.form_questions} onAnswered={refresh} />
          <HealthQuestions t={t} client={client} caseId={caseId}
            questions={journey?.health_questions} onAnswered={refresh} />
          <DocCards t={t} client={client} caseId={caseId}
            checklist={journey?.checklist} translation={journey?.translation}
            onChanged={refresh} />
          {/* One primary action at a time: the Continue panel only while
              documents are still missing (it explains what remains) or on
              routes with no authorize step; otherwise the Authorize card
              below is the single button and consumes this stage itself. */}
          {docsPending && (
            <ContinuePanel t={t} client={client} caseId={caseId} journey={journey}
              onAdvanced={refresh} />
          )}
        </div>
      )}

      {/* EVERY continuation kind ends here, with no exceptions — that is the
          point. SignatureModal is the only thing that grants the standing
          authorization, this card is the only thing that opens it, and the
          readiness gate refuses any live run without it. So a kind excluded
          from this card could never start: its button enqueued a run that
          failed on a missing representative_submission_permitted, or (entry
          preparation) recorded a stage and did nothing at all. Excluding a
          kind here is how that bug is written; keep the list empty
          (2026-08-03).

          The ONE exception is a route with no filing to authorize: an
          in-person application cannot be submitted by anybody but the
          applicant, standing at the counter. This card asked them to sign a
          filing authorization and then pressed a Start that always answered
          "there is no government website to submit through" — the "I clicked
          Start and nothing happened" they reported (2026-08-04). It is not a
          continuation kind being excluded; it is a route where the button
          could never have worked. */}
      {!started && !docsPending && !inPerson && (
        <div className="card fadeup-1" style={{ padding: 24 }}>
          <CaseValidity t={t} client={client} caseId={caseId} onOpenCase={onOpenCase} />
          <div style={{ fontWeight: 700, marginBottom: 6 }}>Ready to go?</div>
          <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 12 }}>
            {kind === 'entry_preparation'
              ? `One signature authorizes Trip.com's Ellis to file your arrival card for you, on the day the destination opens it.`
              : kind === 'passport_renewal'
              ? `One signature authorizes Trip.com's Ellis to file your renewal for you — pausing only where you are needed.`
              : `One signature authorizes Trip.com's Ellis to file for you — then everything runs automatically, pausing only where you are needed.`}
          </div>
          {standing?.granted && !standing?.revoked ? (
            <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
              <span className="chip chip--ink" title={standing.text_hash}>
                Your authorization is signed
              </span>
              <button className="btn" disabled={busy} onClick={start}>
                {busy ? 'Starting…' : 'Start application'}
              </button>
            </div>
          ) : (
            <button className="btn" data-testid="authorize-and-start"
              disabled={busy || modal === 'authorization'}
              onClick={async () => {
                if (busy) return
                setBusy(true)
                try {
                  // Consume the documents stage (idempotent, server-validated)
                  // so authorize-and-start is truly ONE button, then sign.
                  try { await client.completeDocuments(caseId) } catch { /* stage may already be complete */ }
                  setModal('authorization')
                } finally { setBusy(false) }
              }}>
              {kind === 'entry_preparation'
                ? 'Authorize & file my arrival card'
                : kind === 'passport_renewal'
                ? 'Authorize & start my renewal'
                : 'Authorize & start my application'}
            </button>
          )}
        </div>
      )}

      {started && !terminal && handoff && (
        <div className="card card--ink fadeup" style={{ padding: 24 }}>
          <div className="eyebrow" style={{ color: 'rgba(255,255,255,0.7)' }}>Action needed</div>
          <div style={{ fontSize: 18, fontWeight: 700, color: '#fff', margin: '4px 0' }}>{copy.title}</div>
          <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.8)', marginBottom: 14 }}>{copy.sub}</div>
          {/* A case paused at the legacy 'review' handoff (before the review
              step was removed) must still be resolvable — it has no modal. */}
          {handoff === 'review'
            ? <ReviewPanel answers={status.answers} busy={busy} onApprove={async () => {
                setBusy(true); try { await resolve('approve_review') } finally { setBusy(false) } }} />
            : <button className="btn" style={{ background: '#fff', color: 'var(--trip-navy)' }}
                disabled={busy}
                onClick={async () => {
                  // Single ceremony: if the authorization is already signed,
                  // this pause is a stale mirror of a pre-signature run —
                  // resolve it with the signal, never a second signing pad.
                  if (handoff === 'authorization' && standing?.granted && !standing?.revoked) {
                    setBusy(true)
                    try { await resolve('sign_authorization') } finally { setBusy(false) }
                    return
                  }
                  setModal(handoff)
                }}>Continue</button>}
        </div>
      )}

      {started && !terminal && !handoff && (
        <ProgressCard client={client} caseId={caseId} progress={progress} busy={busy}
          onRefresh={refresh} onRetry={retryPortal} onResume={start} />
      )}

      {terminal && <ResultView status={status} client={client} caseId={caseId} />}

      {/* Appointment preferences whenever the verified route needs them — but
          on an in-person route only once there is an appointment to have. They
          were the last block on a page whose first question was "which city
          were you born in", asking the applicant to set a maximum travel
          distance and blackout dates before Ellis had their phone number. */}
      {preferencesTabVisible(journey) && !inPerson && (
        <div style={{ marginTop: 16 }}>
          <Preferences client={client} caseId={caseId} initial={prefs} onSaved={(p) => { setPrefs(p); toast('Saved') }} />
        </div>
      )}

      {/* Activity + privacy — discreet, always reachable. */}
      <details style={{ margin: '22px 4px 8px' }}>
        <summary style={{ fontSize: 12.5, color: 'var(--muted)', cursor: 'pointer' }}>
          Activity, privacy & your data
        </summary>
        <div style={{ marginTop: 10 }}>
          <AuditTrail events={audit} />
          <PrivacyPanel client={client} caseId={caseId} onDeleted={() => onNotify && onNotify()} />
        </div>
      </details>

      {/* Handoff modals */}
      {/* The signed instrument must state what Ellis actually does. Under the
          single-ceremony flow Ellis DOES file the application, so the
          authorization is prepared with representative submit — otherwise the
          PDF would read "NOT submit on your behalf" while Ellis files. */}
      {modal === 'authorization' && (
        <SignatureModal client={client} caseId={caseId}
          authorization={{ allow_representative_submit: true }}
          onClose={() => setModal(null)}
          onDone={() => resolve('sign_authorization')} />
      )}
      {/* Derived from HANDOFF_UI, never re-listed here. This condition used to
          spell out its own handoff names, which made it a FOURTH map that
          could silently disagree with the other three: document_upload had
          copy, a panel and a signal declared, and its Continue still did
          nothing because this line had never heard of it (2026-08-03). */}
      {HANDOFF_UI[modal] === 'LiveViewModal' && (
        <LiveViewModal client={client} caseId={caseId} pending={pending}
          title={copy.title} sub={copy.sub}
          onResolve={async (sig, body) => {
            // portal_form resolves by re-driving the case (start), not a signal.
            if (sig === 'start') {
              const res = await client.start(caseId)
              apply(res); setModal(null)
              return res
            }
            return resolve(sig, body)
          }}
          onClose={() => setModal(null)} />
      )}
      {/* No verified fee for the route and none readable on the page: the
          applicant transcribes the exact amount (the honest last resort). */}
      {modal === 'fee_confirmation' && (
        <PaymentApprove client={client} caseId={caseId} pending={pending}
          onResolve={resolve} onClose={() => setModal(null)} />
      )}
      {modal === 'payment_credentials' && (
        <PaymentDetailsModal client={client} caseId={caseId} pending={pending}
          onResolve={resolve} onClose={() => setModal(null)} />
      )}
      {/* ONE pay step: the amount on top, the portal's own payment page in the
          window below it. The applicant pays there and confirms here — their
          confirmation IS the exact-amount approval, so there is no separate
          "approve the fee" screen before it. */}
      {(modal === 'payment' || modal === 'three_ds' ||
        modal === 'payment_approval') && (
        <PaymentModal client={client} caseId={caseId} pending={pending}
          needsApproval={pending?.handoff === 'payment_approval'}
          onResolve={resolve}
          onProvideDetails={() => setModal('payment_credentials')}
          onClose={() => setModal(null)} />
      )}
      {(modal === 'appointment_selection' || modal === 'no_availability') && (
        <AppointmentCalendar pending={pending} prefs={prefs} onResolve={resolve} onClose={() => setModal(null)} />
      )}
      {modal === 'reschedule_approval' && (
        <RescheduleConfirm pending={pending} onResolve={resolve} onClose={() => setModal(null)} />
      )}
      {modal === 'personal_declaration' && (
        <DeclarationModal client={client} caseId={caseId} pending={pending}
          onResolve={resolve} onClose={() => setModal(null)} />
      )}
      {modal === 'final_review' && (
        <FinalReviewModal client={client} caseId={caseId}
          onClose={() => setModal(null)}
          onDone={async () => { setModal(null); toast('Signed'); await start() }} />
      )}
      {modal === 'additional_information' && (
        <AdditionalInfoModal pending={pending}
          client={client} caseId={caseId} checklist={journey?.checklist}
          onResolve={resolve}
          onGoToDocuments={() => {
            setModal(null)
            setTimeout(() => docsRef.current?.scrollIntoView({ behavior: 'smooth' }), 60)
          }}
          onContinueWithoutAnswers={async () => {
            // Document-only ask: nothing to type. Close the modal and re-drive
            // the paused case the same way final_review resumes (start()); the
            // portal step re-checks and either proceeds or asks again honestly.
            setModal(null); await start()
          }}
          onClose={() => setModal(null)} />
      )}
    </div>
  )
}

// Standalone validity fetch + renew CTA for visa/authorization flows (the
// entry-preparation panel embeds its own).
function CaseValidity({ t, client, caseId, onOpenCase }) {
  const toast = useToast()
  const [validity, setValidity] = useState(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    client.passportValidity(caseId).then(setValidity).catch(() => {})
  }, [caseId])
  if (!validity || !validity.renewal_offered) return null
  async function renewFirst() {
    setBusy(true)
    try {
      const res = await client.startRenewal(caseId)
      toast(t('renewal.started'))
      onOpenCase && onOpenCase({ id: res.renewal_case_id,
        full_name: t('renewal.title'), destination_country: '',
        visa_type: 'passport_renewal', continuation_kind: 'passport_renewal' })
    } catch (e) { toast(e.detail?.reason || e.message) }
    setBusy(false)
  }
  return (
    <div style={{ marginBottom: 14 }}>
      <ValidityRow t={t} label={formatDateUS(validity.expiry_date || '')} validity={validity}
        onRenew={renewFirst} renewBusy={busy} />
    </div>
  )
}

// Passport-validity chip + the "Renew passport first" primary action when the
// verdict calls for it. Shared by entry preparation and visa flows.
function ValidityRow({ t, label, validity, onRenew, renewBusy }) {
  const meta = validityMeta(validity && validity.status)
  return (
    <div className="kv">
      <div className="kv__k">{t('case.entryPrep.validity')}</div>
      <div className="kv__v">
        {label}
        {validity && validity.status && (
          <span className="chip" style={{ marginLeft: 8 }} data-testid="validity-status"
            data-status={validity.status}>
            {t(meta.i18nKey)}
            {validity.expiry_date ? ` · ${formatDateUS(validity.expiry_date)}` : ''}
          </span>
        )}
        {validity && validity.renewal_offered && onRenew && (
          <div style={{ marginTop: 8 }}>
            <div style={{ fontSize: 12.5, color: 'var(--muted)', marginBottom: 6 }}>
              {validity.explanation}
            </div>
            <button className="btn" disabled={renewBusy} onClick={onRenew}
              data-testid="renew-first">
              {renewBusy ? '…' : t('renewal.cta')}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

// Visa-exempt continuation: NOT a dead-end and NOT a consular visa application.
// Real entry preparation — passport validity, arrival card, onward travel,
// accommodation — driven by the same checklist. No portal-account, payment,
// appointment, or submission stage exists on this route.
function EntryPrep({ t, client, caseId, journey, onToDocuments, onOpenCase }) {
  const toast = useToast()
  const [validity, setValidity] = useState(null)
  const [renewBusy, setRenewBusy] = useState(false)
  useEffect(() => {
    client.passportValidity(caseId).then(setValidity).catch(() => {})
  }, [caseId])
  const g = (journey.guidance || {}).guidance || {}
  const counts = journey.checklist_counts || {}
  const done = (counts.required_missing || 0) === 0
  const card = g.arrival_card || {}

  async function renewFirst() {
    setRenewBusy(true)
    try {
      const res = await client.startRenewal(caseId)
      toast(t('renewal.started'))
      onOpenCase && onOpenCase({ id: res.renewal_case_id,
        full_name: t('renewal.title'),
        destination_country: '', visa_type: 'passport_renewal',
        continuation_kind: 'passport_renewal' })
    } catch (e) { toast(e.detail?.reason || e.message) }
    setRenewBusy(false)
  }

  return (
    <div className="card" style={{ padding: 22 }} data-testid="entry-prep">
      <div style={{ fontWeight: 700, marginBottom: 4 }}>{t('case.entryPrep.title')}</div>
      <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 12 }}
        data-testid="no-visa-note">{t('case.entryPrep.sub')}</div>
      {g.passport_validity && (
        <ValidityRow t={t} label={g.passport_validity} validity={validity}
          onRenew={renewFirst} renewBusy={renewBusy} />
      )}
      {card.required && (
        <div className="kv" data-testid="arrival-card-row">
          <div className="kv__k">{t('case.entryPrep.arrivalCard')}</div>
          <div className="kv__v">
            {card.name || t('case.entryPrep.arrivalCard')}
            {card.submission_window ? ` — ${card.submission_window}` : ''}
          </div>
        </div>
      )}
      {Array.isArray(g.forms) && g.forms.length > 0 && (
        <div className="kv">
          <div className="kv__k">{t('checklist.preparedLater')}</div>
          <div className="kv__v">{g.forms.join(' · ')}</div>
        </div>
      )}
      <div style={{ fontSize: 12.5, color: 'var(--muted)', margin: '10px 0' }}>
        {t('case.entryPrep.window')}
      </div>
      {done && (
        <div className="note" style={{ marginBottom: 10 }} data-testid="entry-prep-complete">
          {t('case.entryPrep.complete')}
        </div>
      )}
      <button className="btn" onClick={onToDocuments} data-testid="entry-prep-docs">
        {done ? t('case.entryPrep.review') : t('case.docsFirst')}
      </button>
    </div>
  )
}

// A route decided in person, as ONE card showing ONE step.
//
// Ellis cannot submit these applications — nobody can but the applicant, at
// the counter. So the whole journey is: tell Ellis what it still needs, take
// the finished folder, go and present it. The backend decides which step this
// case is on (packet.stage) so the screen can never disagree with the packet
// about whether it is complete, and so "already downloaded" survives a reload.
function ConsularJourney({ t, client, caseId, packet, journey, onChanged }) {
  const [reopened, setReopened] = useState(false)
  // The applicant can always go back and change an answer after downloading;
  // that is a local override of the backend's stage, never a rewrite of it.
  const stage = reopened ? 'ask' : (packet.stage || 'ask')
  return (
    <div className="card fadeup-1" style={{ padding: 22 }}
      data-testid="consular-journey" data-stage={stage}>
      {stage === 'ask' && (
        <ConsularAsk t={t} client={client} caseId={caseId} packet={packet}
          journey={journey} onChanged={onChanged} />
      )}
      {stage === 'ready' && (
        <ConsularReady t={t} client={client} caseId={caseId} packet={packet}
          onDownloaded={onChanged} onEdit={() => setReopened(true)} />
      )}
      {stage === 'next' && (
        <ConsularNext t={t} client={client} caseId={caseId} packet={packet} />
      )}
    </div>
  )
}

// STEP 1 — a wizard, not a wall. Documents first (the uploads decide which
// questions even need asking: a hotel booking answers "where will you stay").
// Then ONE question at a time, in the form's own order, each fading in:
// a required question has Next, an optional one has Skip. Nothing else on the
// screen while any of this is unanswered.
function ConsularAsk({ t, client, caseId, packet, journey, onChanged }) {
  const toast = useToast()
  const [data, setData] = useState(null)
  const [phase, setPhase] = useState(null)   // 'docs' | 'questions'
  const [idx, setIdx] = useState(0)
  const [value, setValue] = useState(null)
  const [busy, setBusy] = useState(false)

  const docsMissing = (packet.missing_documents || []).length > 0

  useEffect(() => {
    let live = true
    client.consularFormQuestions(caseId)
      .then((d) => { if (live) setData(d) })
      .catch(() => { if (live) setData({ questions: [], fields: [] }) })
    return () => { live = false }
  }, [caseId])

  // Documents come first, once, and only while any are actually owed —
  // answered questions never bounce the applicant back to the upload screen.
  useEffect(() => {
    if (phase === null && data) setPhase(docsMissing ? 'docs' : 'questions')
  }, [data, phase, docsMissing])

  // One flat list in the form's own order: written fields, then tick-boxes.
  // A question already answered (typed earlier, or read from a document) is
  // only re-asked to be CONFIRMED when it came from a document; a confirmed
  // answer never reappears.
  const list = useMemo(() => {
    if (!data) return []
    const fields = (data.fields || [])
      .filter((f) => !f.answer || f.from_document)
      .map((f) => ({ ...f, kind: 'text' }))
    const boxes = (data.questions || [])
      .filter((q) => q.answer == null || q.answer === '' ||
                     (Array.isArray(q.answer) && !q.answer.length))
      .map((q) => ({ ...q, kind: 'choice', required: true }))
    return [...fields.filter((f) => f.required), ...boxes,
            ...fields.filter((f) => !f.required)]
  }, [data])

  const q = list[idx]
  // Seed the input with what Ellis already knows the moment a question shows.
  useEffect(() => {
    if (!q) return
    setValue(q.kind === 'choice' ? (q.multi ? [] : '') : (q.answer || ''))
  }, [idx, q && q.key])

  async function advance(save) {
    if (!q) return
    setBusy(true)
    try {
      if (save) await client.updateAnswers(caseId, { [q.key]: value })
      if (idx + 1 < list.length) setIdx(idx + 1)
      else onChanged()          // the backend decides: ready, or still gaps
    } catch (e) { toast(e.detail?.detail || e.message) }
    setBusy(false)
  }

  if (!data || phase === null) return <Loading label={t('consular.ask.title')} />

  if (phase === 'docs') {
    return (
      <div className="fadeup" data-testid="consular-docs">
        <div style={{ fontWeight: 700, marginBottom: 4 }}>{t('consular.docs.title')}</div>
        <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 14 }}>
          {t('consular.docs.sub', { destination: packet.destination || '' })}
        </div>
        <DocCards t={t} client={client} caseId={caseId}
          checklist={journey?.checklist} translation={journey?.translation}
          onChanged={onChanged} />
        <button className="btn" style={{ marginTop: 12 }}
          disabled={docsMissing} onClick={() => setPhase('questions')}
          data-testid="docs-next"
          title={docsMissing ? t('consular.docs.stillMissing') : undefined}>
          {t('consular.wizard.next')}
        </button>
        {docsMissing && (
          <div style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 6 }}>
            {t('consular.docs.stillMissing')}
          </div>
        )}
      </div>
    )
  }

  if (!q) {
    // Every question asked and answered — the refresh will flip the stage.
    return <Loading label={t('consular.ready.preparing')} />
  }

  const answered = q.kind === 'choice'
    ? (q.multi ? Array.isArray(value) && value.length > 0 : value !== '')
    : String(value || '').trim() !== ''

  return (
    // key={q.key} remounts the block per question, which is what re-runs the
    // fade — the animation the applicant sees between questions.
    <div key={q.key} className="fadeup" data-testid="consular-question">
      <div style={{ fontSize: 12.5, color: 'var(--muted)', marginBottom: 8 }}>
        {t('consular.wizard.progress', { n: idx + 1, total: list.length })}
      </div>
      <div style={{ fontWeight: 700, fontSize: 17, marginBottom: 6 }}>
        {q.label}
      </div>
      {q.from_document ? (
        <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 10 }}>
          {t('formq.fromDocument', { document: q.from_document })}
        </div>
      ) : q.help ? (
        <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 10 }}>{q.help}</div>
      ) : null}

      {q.kind === 'choice' ? (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 14 }}>
          {(q.options || []).map((o) => {
            const on = q.multi ? (Array.isArray(value) && value.includes(o.value))
              : value === o.value
            return (
              <button key={o.value} type="button"
                className={'chip' + (on ? ' chip--ink' : '')}
                aria-pressed={on}
                onClick={() => setValue(q.multi
                  ? (on ? value.filter((v) => v !== o.value) : [...(value || []), o.value])
                  : o.value)}>
                {o.label}
              </button>
            )
          })}
        </div>
      ) : q.type === 'textarea' ? (
        <textarea className="input" rows={3} value={value || ''} autoFocus
          style={{ marginBottom: 14 }}
          onChange={(e) => setValue(e.target.value)} />
      ) : (
        <input className="input" autoFocus
          type={q.type === 'tel' ? 'tel' : 'text'}
          inputMode={q.type === 'tel' ? 'tel' : undefined}
          value={value || ''} style={{ marginBottom: 14 }}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && answered && !busy) advance(true) }} />
      )}

      <div style={{ display: 'flex', gap: 8 }}>
        <button className="btn" disabled={busy || !answered}
          onClick={() => advance(true)} data-testid="question-next">
          {busy ? t('formq.saving') : t('consular.wizard.next')}
        </button>
        {!q.required && (
          <button className="btn btn--ghost" disabled={busy}
            onClick={() => advance(false)} data-testid="question-skip">
            {t('consular.wizard.skip')}
          </button>
        )}
      </div>
    </div>
  )
}

// The pad the applicant signs in: their own strokes on a canvas, saved as the
// image the form's Signature cell receives. Ellis never draws a stroke — a
// signature that isn't the person's own hand is a forgery, whatever produced
// it.
function SignaturePad({ t, client, caseId, onSigned }) {
  const toast = useToast()
  const canvasRef = useRef(null)
  const drawing = useRef(false)
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)

  function pos(e) {
    const c = canvasRef.current
    const r = c.getBoundingClientRect()
    const p = e.touches ? e.touches[0] : e
    return [(p.clientX - r.left) * (c.width / r.width),
            (p.clientY - r.top) * (c.height / r.height)]
  }
  function start(e) {
    drawing.current = true
    const ctx = canvasRef.current.getContext('2d')
    ctx.lineWidth = 2.4; ctx.lineCap = 'round'; ctx.strokeStyle = '#1a2b49'
    ctx.beginPath(); ctx.moveTo(...pos(e))
    e.preventDefault()
  }
  function move(e) {
    if (!drawing.current) return
    const ctx = canvasRef.current.getContext('2d')
    ctx.lineTo(...pos(e)); ctx.stroke()
    setDirty(true)
    e.preventDefault()
  }
  function end() { drawing.current = false }
  function clear() {
    const c = canvasRef.current
    c.getContext('2d').clearRect(0, 0, c.width, c.height)
    setDirty(false)
  }
  async function save() {
    setBusy(true)
    try {
      // White ground, so the PNG pasted into the printed form reads as ink on
      // paper rather than a grey transparency block.
      const c = canvasRef.current
      const flat = document.createElement('canvas')
      flat.width = c.width; flat.height = c.height
      const fx = flat.getContext('2d')
      fx.fillStyle = '#ffffff'; fx.fillRect(0, 0, flat.width, flat.height)
      fx.drawImage(c, 0, 0)
      await client.saveSignature(caseId, flat.toDataURL('image/png'))
      onSigned && onSigned()
    } catch (e) { toast(e.detail?.detail || e.message) }
    setBusy(false)
  }

  return (
    <div style={{ margin: '4px 0 14px' }} data-testid="signature-pad">
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{t('sig.title')}</div>
      <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 8 }}>
        {t('sig.sub')}
      </div>
      <canvas ref={canvasRef} width={560} height={140}
        style={{ width: '100%', maxWidth: 560, height: 140, touchAction: 'none',
                 background: '#fff', border: '1px dashed var(--line)',
                 borderRadius: 10, cursor: 'crosshair' }}
        onMouseDown={start} onMouseMove={move} onMouseUp={end} onMouseLeave={end}
        onTouchStart={start} onTouchMove={move} onTouchEnd={end} />
      <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
        <button className="btn" onClick={save} disabled={!dirty || busy}
          data-testid="signature-save">
          {busy ? t('sig.saving') : t('sig.save')}
        </button>
        <button className="btn btn--ghost" onClick={clear} disabled={busy}>
          {t('sig.clear')}
        </button>
      </div>
    </div>
  )
}

// STEP 2 — sign, then one button. Everything Ellis needs is in; the applicant
// signs their application in the pad, and the folder is one click.
function ConsularReady({ t, client, caseId, packet, onDownloaded, onEdit }) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const docs = (packet.documents || []).length

  async function download() {
    setBusy(true)
    try {
      const blob = await client.downloadAppointmentPacket(caseId)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'ellis-visa-application.pdf'
      document.body.appendChild(a); a.click(); a.remove()
      URL.revokeObjectURL(url)
      // The download is what advances the step, and the backend records it —
      // so refreshing is what moves the page on, not a local flag.
      onDownloaded && onDownloaded()
    } catch (e) { toast(e.message) }
    setBusy(false)
  }

  return (
    <div data-testid="consular-ready">
      <div style={{ fontWeight: 700, marginBottom: 4 }}>{t('consular.ready.title')}</div>
      <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 12 }}>
        {packet.official_form_included
          ? t('consular.ready.subOfficial', { count: docs })
          : t('consular.ready.subPrep', { count: docs })}
      </div>
      {/* Sign before download: the form carries a Signature cell, and the
          drawing goes into it. Re-signing any time replaces the old one. */}
      {!packet.signature_present && (
        <SignaturePad t={t} client={client} caseId={caseId}
          onSigned={onDownloaded} />
      )}
      {packet.signature_present && (
        <div style={{ marginBottom: 12 }}>
          <span className="chip chip--ink" data-testid="signature-done">
            {t('sig.done')}
          </span>
        </div>
      )}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <button className="btn" onClick={download}
          disabled={busy || !packet.signature_present}
          title={!packet.signature_present ? t('sig.needed') : undefined}
          data-testid="packet-download">
          {busy ? t('consular.ready.preparing') : t('consular.ready.download')}
        </button>
        <button className="btn btn--ghost" onClick={onEdit}>
          {t('consular.edit')}
        </button>
      </div>
    </div>
  )
}

// STEP 3 — the last page, and it holds ONLY what the applicant does now: the
// instructions and the folder. Booking tools, edit links and preference forms
// all crowded this screen and were cut ("hide everything besides the
// instruction and download again", 2026-08-04). Centered, because it is the
// end of the journey, not another form.
function ConsularNext({ t, client, caseId, packet }) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const [showDisclaimer, setShowDisclaimer] = useState(false)
  const post = packet.post || {}
  const steps = packet.next_steps || []

  async function again() {
    setBusy(true)
    try {
      const blob = await client.downloadAppointmentPacket(caseId)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'ellis-visa-application.pdf'
      document.body.appendChild(a); a.click(); a.remove()
      URL.revokeObjectURL(url)
    } catch (e) { toast(e.message) }
    setBusy(false)
  }

  return (
    <div data-testid="consular-next"
      style={{ maxWidth: 560, margin: '0 auto', textAlign: 'center',
               padding: '18px 6px 6px' }}>
      <div className="eyebrow" style={{ marginBottom: 8 }}>
        {t('consular.next.title')}
      </div>
      <div style={{ fontSize: 22, fontWeight: 700, lineHeight: 1.3,
                    marginBottom: 6 }}>
        {post.address
          ? t('consular.next.goTo', { address: post.address })
          : post.name
          ? t('consular.next.goToPost', { post: post.name })
          : t('booking.unknownPost')}
      </div>
      {post.name && post.address && (
        <div style={{ fontSize: 13.5, color: 'var(--muted)' }}>{post.name}</div>
      )}
      {post.status === 'unconfirmed' && post.name && (
        <div style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 8 }}
          data-testid="post-unconfirmed">
          {t('post.unconfirmed')}
        </div>
      )}

      {steps.length > 0 && (
        <div className="card card--soft"
          style={{ padding: '16px 20px', margin: '18px 0',
                   textAlign: 'left' }} data-testid="consular-steps">
          <div className="eyebrow" style={{ marginBottom: 8 }}>
            {t('consular.next.onTheDay')}
          </div>
          <ol style={{ margin: 0, paddingLeft: 20, fontSize: 13.5, lineHeight: 1.8 }}>
            {steps.map((s, i) => <li key={i}>{s}</li>)}
          </ol>
        </div>
      )}

      <button className="btn" onClick={again} disabled={busy}
        data-testid="packet-download-again">
        {busy ? t('consular.ready.preparing') : t('consular.next.again')}
      </button>

      <div style={{ marginTop: 22 }}>
        {showDisclaimer ? (
          <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.6,
                        maxWidth: 460, margin: '0 auto' }}
            data-testid="disclaimer-text">
            {packet.disclaimer || ''}
          </div>
        ) : (
          <button type="button" onClick={() => setShowDisclaimer(true)}
            data-testid="read-disclaimer"
            style={{ background: 'none', border: 'none', cursor: 'pointer',
                     fontSize: 12.5, color: 'var(--muted)',
                     textDecoration: 'underline' }}>
            {t('consular.next.disclaimer')}
          </button>
        )}
      </div>
    </div>
  )
}


// Where the applicant physically goes. A name ("German Consulate General
// Guangzhou") is not an answer to "where do I go" — the street address is, and
// Ellis had never shown one. Renders the address whenever it has one, with the
// jurisdiction's own status beside it, so an office Ellis found but has not
// confirmed serves this address is shown AS THAT rather than withheld.
function ConsularPost({ t, post }) {
  if (!post || !post.name) return null
  return (
    <div style={{ marginBottom: 10 }} data-testid="consular-post">
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{t('post.title')}</div>
      <div className="kv"><div className="kv__k">{post.kind
        ? post.kind.replace(/_/g, ' ') : t('post.title')}</div>
        <div className="kv__v">{post.name}</div></div>
      {post.address && (
        <div className="kv"><div className="kv__k">{t('post.address')}</div>
          <div className="kv__v">{post.address}</div></div>
      )}
      {post.status === 'unconfirmed' && (
        <div className="note" style={{ marginTop: 8 }} data-testid="post-unconfirmed">
          {t('post.unconfirmed')}
          {post.source_url && (
            <div style={{ marginTop: 4, fontSize: 12, wordBreak: 'break-all' }}>
              {t('post.source')}: {post.source_url}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// Booking the in-person appointment. Every booking system keeps its slots
// behind the applicant's own account, so Ellis opens the OFFICIAL site and the
// applicant picks their own slot — then tells Ellis what they booked, so the
// date reaches their folder. Ellis never chooses a slot.
function AppointmentBooking({ t, client, caseId }) {
  const toast = useToast()
  const [state, setState] = useState(null)
  const [finding, setFinding] = useState(false)
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [form, setForm] = useState({ date: '', time: '', location: '', ref: '' })
  const load = () => client.appointmentBooking(caseId).then(setState).catch(() => setState(null))
  useEffect(() => { load() }, [caseId])
  if (!state) return null

  async function findPost() {
    setFinding(true)
    try {
      const out = await client.findConsularPost(caseId)
      // Reload whatever the search found, verified or not. Gating the refresh
      // on 'verified' meant an unverified answer — which now carries the
      // office's name and its address off an official page — was thrown away
      // and the applicant told only that nothing was confirmed.
      await load()
      if (out.status !== 'verified') toast(t('booking.notFound'))
    } catch (e) { toast(e.detail?.detail || e.message) }
    setFinding(false)
  }

  async function saveBooked() {
    const ms = Date.parse(`${form.date}T${form.time || '00:00'}`)
    if (!form.date || !Number.isFinite(ms)) { toast(t('booking.badDate')); return }
    if (ms < Date.now()) { toast(t('booking.pastDate')); return }
    setSaving(true)
    try {
      await client.recordAppointment(caseId, ms, form.location || state.post_name || '',
                                     form.ref || '')
      setEditing(false)
      await load()
    } catch (e) { toast(e.detail?.detail || e.message) }
    setSaving(false)
  }

  const appt = state.appointment
  return (
    <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--line)' }}
      data-testid="appointment-booking">
      {/* The post is named by the step that owns it (ConsularNext), and the
          form's questions are asked in the step BEFORE the folder exists —
          this card had been rendering both, so the same office appeared twice
          and answering a question here refreshed nothing (its onSaved was a
          no-op, so the completeness score above it never moved). */}
      <div style={{ fontWeight: 600, marginBottom: 6 }}>{t('booking.title')}</div>
      {/* Only where a readable government calendar actually exists for THIS
          destination — the backend says which system applies. */}
      {state.gov_calendar === 'rk_termin' && (
        <GovCalendar t={t} client={client} caseId={caseId} />
      )}
      {appt && !editing ? (
        <div data-testid="appointment-booked">
          <div className="kv">
            <div className="kv__k">{t('booking.booked')}</div>
            <div className="kv__v">
              {appt.when_utc}{appt.location ? ` — ${appt.location}` : ''}
              {appt.confirmation_no ? ` · ${appt.confirmation_no}` : ''}
            </div>
          </div>
          <button className="btn btn--ghost" style={{ marginTop: 8 }}
            onClick={() => { setForm({ date: '', time: '', location: appt.location || '', ref: '' }); setEditing(true) }}>
            {t('booking.change')}
          </button>
        </div>
      ) : editing ? (
        <div data-testid="booking-form">
          <div className="wiz-grid" style={{ gap: 10 }}>
            <div className="field"><label>{t('booking.date')}</label>
              <input type="date" className="input" value={form.date}
                onChange={(e) => setForm({ ...form, date: e.target.value })} /></div>
            <div className="field"><label>{t('booking.time')}</label>
              <input type="time" className="input" value={form.time}
                onChange={(e) => setForm({ ...form, time: e.target.value })} /></div>
            <div className="field"><label>{t('booking.where')}</label>
              <input className="input" value={form.location}
                placeholder={state.post_name || ''}
                onChange={(e) => setForm({ ...form, location: e.target.value })} /></div>
            <div className="field"><label>{t('booking.ref')}</label>
              <input className="input" value={form.ref}
                onChange={(e) => setForm({ ...form, ref: e.target.value })} /></div>
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
            <button className="btn" onClick={saveBooked} disabled={saving}
              data-testid="booking-save">
              {saving ? t('booking.saving') : t('booking.save')}
            </button>
            <button className="btn btn--ghost" onClick={() => setEditing(false)}>
              {t('pay.cancel')}
            </button>
          </div>
        </div>
      ) : state.bookable ? (
        <>
          <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 8 }}>
            {t('booking.sub', { post: state.post_name || t('packet.consulate') })}
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <a className="btn" href={state.booking_url} target="_blank" rel="noreferrer"
              data-testid="booking-open">{t('booking.open')}</a>
            <button className="btn btn--ghost" onClick={() => setEditing(true)}
              data-testid="booking-record">
              {t('booking.record')}
            </button>
          </div>
        </>
      ) : (
        <>
          <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 8 }}>
            {state.reason || t('booking.unknownPost')}
          </div>
          <button className="btn btn--ghost" onClick={findPost} disabled={finding}
            data-testid="booking-find">
            {finding ? t('booking.finding') : t('booking.find')}
          </button>
        </>
      )}
    </div>
  )
}

// The two government systems with a REAL readable calendar and no accounts
// (Germany's RK-Termin — 190+ missions — and Poland's e-Konsulat). The
// applicant completes the portal's image check in their own secure window;
// only then can Ellis read the month. Ellis never solves the check and never
// clicks a day, because on these systems a click reserves a real slot.
function GovCalendar({ t, client, caseId }) {
  const toast = useToast()
  const [open, setOpen] = useState(null)     // walk result (categories, gate)
  const [month, setMonth] = useState(null)   // month summary once readable
  const [busy, setBusy] = useState(false)
  const [loc, setLoc] = useState('')
  const [missions, setMissions] = useState(null)
  const [search, setSearch] = useState('')

  async function loadMissions() {
    setBusy(true)
    try {
      const out = await client.calendarMissions(caseId)
      setMissions(out.missions || [])
      if ((out.missions || []).length) setLoc(out.missions[0].code)
    } catch (e) { toast(e.detail?.detail || e.message) }
    setBusy(false)
  }

  async function openCalendar() {
    if (!loc.trim()) { toast(t('cal.needLocation')); return }
    setBusy(true)
    try {
      const out = await client.calendarOpen(caseId, loc.trim())
      setOpen(out); setMonth(null)
    } catch (e) { toast(e.detail?.detail || e.message) }
    setBusy(false)
  }

  async function readMonth() {
    setBusy(true)
    try { setMonth(await client.calendarMonth(caseId)) }
    catch (e) { toast(e.detail?.detail || e.message) }
    setBusy(false)
  }

  return (
    <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--line)' }}
      data-testid="gov-calendar">
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{t('cal.title')}</div>
      <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 8 }}>
        {t('cal.sub')}
      </div>
      {!open ? (
        <div>
          <div className="field">
            <label>{t('cal.location')}</label>
            {missions === null ? (
              <button className="btn btn--ghost" onClick={loadMissions} disabled={busy}
                data-testid="cal-load-missions">
                {busy ? t('cal.loadingMissions') : t('cal.loadMissions')}
              </button>
            ) : (
              <>
                <input className="input" value={search} placeholder={t('cal.searchMission')}
                  onChange={(e) => setSearch(e.target.value)} data-testid="cal-search" />
                <select className="select" value={loc} size={6} style={{ marginTop: 6 }}
                  onChange={(e) => setLoc(e.target.value)} data-testid="cal-mission">
                  {missions
                    .filter((m) => !search ||
                      m.name.toLowerCase().includes(search.toLowerCase()))
                    .map((m) => (
                      <option key={m.code} value={m.code}>{m.name}</option>
                    ))}
                </select>
              </>
            )}
          </div>
          {missions !== null && (
            <button className="btn" onClick={openCalendar} disabled={busy || !loc}
              data-testid="cal-open" style={{ marginTop: 8 }}>
              {busy ? t('cal.opening') : t('cal.open')}
            </button>
          )}
        </div>
      ) : (
        <>
          {open.captcha_required && !month && (
            <div className="note" style={{ marginBottom: 8 }} data-testid="cal-captcha">
              {t('cal.captcha')}
            </div>
          )}
          {Array.isArray(open.categories) && open.categories.length > 0 && (
            <div style={{ fontSize: 12.5, color: 'var(--muted)', marginBottom: 8 }}>
              {t('cal.categories')}: {open.categories.slice(0, 4)
                .map((c) => c.label).join(' · ')}
            </div>
          )}
          <button className="btn btn--ghost" onClick={readMonth} disabled={busy}
            data-testid="cal-read">
            {busy ? t('cal.reading') : t('cal.read')}
          </button>
          {month && (
            <div style={{ marginTop: 10 }} data-testid="cal-month">
              {!month.readable ? (
                <div className="note">{month.reason}</div>
              ) : month.none_available ? (
                <div className="note">{t('cal.none')}</div>
              ) : (
                <>
                  <div style={{ fontSize: 13, marginBottom: 6 }}>
                    {t('cal.found', { n: month.bookable_count })}
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {(month.days || []).map((d) => (
                      <a key={d.href} className="chip" href={d.href} target="_blank"
                        rel="noreferrer" title={d.title || ''}>{d.label}</a>
                    ))}
                  </div>
                </>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}

// Passport-renewal case: the Kimi renewal analysis (path, form, fees, times)
// plus the linked-travel-case note. Documents flow through the normal
// checklist; approving the NEW passport resumes the travel case automatically.
// ---- Attended observation: the applicant's own run teaches Ellis a portal --
// Shown beside a real_only_stop. Two things at once, honestly separated:
// (1) the applicant can always complete their application THEMSELVES in the
// secure window; (2) if — and only if — they sign the versioned consent,
// Ellis records the STRUCTURE of the pages they pass so future applications
// on this portal can be automated. Declining changes nothing about the case.
function PortalObservation({ t, client, caseId }) {
  const [obs, setObs] = useState(null)      // GET /portal-observation payload
  const [declined, setDeclined] = useState(false)
  const [busy, setBusy] = useState(false)
  const [session, setSession] = useState(null)  // finish() summary
  const [error, setError] = useState(null)
  const pollRef = useRef(null)

  async function refresh() {
    try { setObs(await client.portalObservation(caseId)) }
    catch (e) { setError({ message: e.message }) }
  }
  useEffect(() => {
    refresh()
    return () => pollRef.current && clearTimeout(pollRef.current)
  }, [caseId])

  // While the post-session rebuild runs, keep the card telling the truth.
  useEffect(() => {
    if (!obs?.rebuild?.active) return undefined
    pollRef.current = setTimeout(refresh, 5000)
    return () => clearTimeout(pollRef.current)
  }, [obs])

  if (!obs || !obs.available || declined) return null

  async function act(fn) {
    setBusy(true); setError(null)
    try { setObs(await fn()) }
    catch (e) { setError({ message: (e.detail && e.detail.detail) || e.message }) }
    setBusy(false)
  }

  const rb = obs.rebuild || {}
  return (
    <div className="card fadeup-1" style={{ padding: 22, marginTop: 12 }}>
      <div style={{ fontWeight: 700, marginBottom: 6 }}>
        {t('obs.title')}{obs.portal_name ? ` — ${obs.portal_name}` : ''}
      </div>
      {error && <ErrorNote error={error} />}

      {!obs.consented && (
        <div>
          <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 10 }}>
            {obs.account_required ? t('obs.blockedLogin') : t('obs.blockedGate')}
            {' '}{t('obs.offer')}
          </div>
          <div className="card card--soft" style={{ padding: 12, fontSize: 12.5,
            whiteSpace: 'pre-wrap', marginBottom: 12 }}>
            {obs.consent_text}
          </div>
          <div style={{ display: 'flex', gap: 10 }}>
            <button className="btn" disabled={busy}
              onClick={() => act(() => client.portalObservationConsent(caseId))}>
              {t('obs.agree')}
            </button>
            <button className="btn btn--ghost" disabled={busy}
              onClick={() => act(() => client.portalObservationDecline(caseId))
                .then(() => setDeclined(true))}>
              {t('obs.decline')}
            </button>
          </div>
        </div>
      )}

      {obs.consented && !session && !rb.active && !rb.released && !rb.error && (
        <ObservationWindow t={t} client={client} caseId={caseId}
          obs={obs} busy={busy}
          onStarted={refresh}
          onFinish={() => act(async () => {
            const r = await client.portalObservationFinish(caseId)
            setSession(r.session || null)
            return r
          })} />
      )}

      {session && session.rebuilt === false && (
        <div className="card card--soft" style={{ padding: 14, fontSize: 13 }}>
          {t('obs.noForm')}
        </div>
      )}
      {rb.active && (
        <div style={{ marginTop: 10 }}>
          <Loading label={t('obs.rebuilding')} />
        </div>
      )}
      {!rb.active && rb.released && (
        <div className="card card--soft" style={{ padding: 14, fontSize: 13,
          borderColor: '#16a34a' }}>
          {t('obs.released')}
        </div>
      )}
      {!rb.active && !rb.released && (rb.missing || []).length > 0 && (
        <div className="card card--soft" style={{ padding: 14, fontSize: 12.5 }}>
          <div style={{ fontWeight: 700, marginBottom: 6 }}>{t('obs.gateHold')}</div>
          {(rb.missing || []).slice(0, 4).map((m, i) => <div key={i}>· {m}</div>)}
        </div>
      )}
      {!rb.active && rb.error && (
        <div className="card card--soft" style={{ padding: 14, fontSize: 12.5 }}>
          {t('obs.rebuildError')} {rb.error}
        </div>
      )}
    </div>
  )
}

// The secure window plus the recording chrome. Recording starts only after
// the window is actually embedded — the backend attaches to THAT session.
function ObservationWindow({ t, client, caseId, obs, busy, onStarted, onFinish }) {
  const view = usePortalLiveView(client, caseId, { restore: false })
  const startedRef = useRef(false)
  useEffect(() => {
    if (view.state !== 'embedded' || startedRef.current) return
    startedRef.current = true
    client.portalObservationStart(caseId).then(onStarted).catch(() => {
      startedRef.current = false
    })
  }, [view.state])
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5,
        color: 'var(--muted)' }}>
        <span className="chip chip--ink" style={{ fontSize: 10 }}>
          {obs.active ? t('obs.recording') : t('obs.window')}
        </span>
        {obs.active && (
          <span>{obs.pages} {t('obs.pages')} · {obs.forms} {t('obs.forms')}</span>
        )}
      </div>
      <LiveFrame view={view} height="62vh" />
      <div style={{ display: 'flex', gap: 10, marginTop: 12 }}>
        <button className="btn" disabled={busy || view.state !== 'embedded'}
          onClick={onFinish}>
          {busy ? t('obs.finishing') : t('obs.finish')}
        </button>
      </div>
    </div>
  )
}

function RenewalPrep({ t, journey, onToDocuments }) {
  const gr = journey.guidance || {}
  const g = gr.guidance || {}
  const authority = gr.authority || {}
  const fee = g.government_fee || {}
  return (
    <div className="card" style={{ padding: 22 }} data-testid="renewal-prep">
      <div style={{ fontWeight: 700, marginBottom: 4 }}>{t('renewal.title')}</div>
      <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 12 }}>
        {t('renewal.sub')}
      </div>
      <KVList fields={[
        g.channel && { label: t('renewal.channel'), value: String(g.channel).replace(/_/g, ' ') },
        g.renewal_form && { label: t('renewal.form'), value: g.renewal_form },
        fee.amount != null && { label: t('renewal.fee'), value: `${fee.amount} ${fee.currency || ''}` },
        g.processing_time_normal && { label: t('renewal.processing'), value: g.processing_time_normal },
        g.processing_time_expedited && { label: t('renewal.expedited'), value: g.processing_time_expedited },
        g.old_passport_surrender && { label: t('renewal.surrender'), value: g.old_passport_surrender },
        g.delivery_method && { label: t('renewal.delivery'), value: g.delivery_method },
        authority.authority && { label: t('renewal.authority'), value: authority.authority }
      ].filter(Boolean)} />
      <div style={{ fontSize: 12.5, color: 'var(--muted)', margin: '10px 0' }}>
        {t('renewal.linkedNote')}
      </div>
      <button className="btn" onClick={onToDocuments}>{t('case.docsFirst')}</button>
    </div>
  )
}

// Travel-history question asked ONLY when a conditional health rule needs it
// (e.g. yellow fever after presence in a risk country). Answering updates the
// case answers; the checklist re-derives server-side.
function FormQuestions({ t, client, caseId, questions, onAnswered }) {
  // Known-in-advance form answers (from the released flow's stored nodes) —
  // collected up front so the live run never has to pause to ask for them.
  const toast = useToast()
  const [vals, setVals] = useState({})
  const [busy, setBusy] = useState(false)
  const list = Array.isArray(questions) ? questions : []
  if (list.length === 0) return null
  const filled = list.filter((q) => String(vals[q.key] || '').trim())
  async function save() {
    const answers = {}
    for (const q of filled) answers[q.key] = String(vals[q.key]).trim()
    if (Object.keys(answers).length === 0) return
    setBusy(true)
    try {
      await client.updateAnswers(caseId, answers)
      toast(t('formq.saved'))
      setVals({})
      onAnswered && onAnswered()
    } catch (e) { toast(e.message) } finally { setBusy(false) }
  }
  return (
    <div className="card fadeup" style={{ padding: 20, marginBottom: 14 }}
      data-testid="form-questions">
      <div style={{ fontWeight: 700, marginBottom: 2 }}>{t('formq.title')}</div>
      <div style={{ fontSize: 12.5, color: 'var(--muted)', marginBottom: 12 }}>
        {t('formq.sub')}
      </div>
      <div className="grid grid-2" style={{ gap: 12 }}>
        {list.map((q) => (
          <div className="field" key={q.key}>
            <label title={q.why || ''}>{q.question}</label>
            {(q.options || []).length > 0 && !q.options_partial ? (
              /* The portal's OWN list, read in full from the official form. */
              <select className="select" value={vals[q.key] || ''}
                onChange={(e) => setVals((p) => ({ ...p, [q.key]: e.target.value }))}>
                <option value="" disabled>{t('formq.choose')}</option>
                {q.options.map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            ) : (
              <>
                <input className="input" value={vals[q.key] || ''}
                  list={(q.options || []).length > 0 ? 'opts-' + q.key : undefined}
                  placeholder={q.format && q.format !== 'free text' ? q.format : ''}
                  onChange={(e) => setVals((p) => ({ ...p, [q.key]: e.target.value }))} />
                {/* Only PART of the official list is known (long lists load a
                    screen at a time). Offering it as a closed dropdown would
                    strand anyone whose real answer sits further down it, so
                    the known values become suggestions on a field they can
                    still type into. */}
                {q.options_partial && (
                  <datalist id={'opts-' + q.key}>
                    {q.options.map((o) => <option key={o} value={o} />)}
                  </datalist>
                )}
                {(q.options_pending || q.options_partial) && (
                  <div style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 4 }}>
                    {t(q.options_partial ? 'formq.optionsPartial' : 'formq.optionsPending')}
                  </div>
                )}
              </>
            )}
          </div>
        ))}
      </div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 12 }}>
        <button className="btn" disabled={busy || filled.length === 0} onClick={save}
          data-testid="save-form-answers">
          {busy ? t('formq.saving') : t('formq.save')}
        </button>
      </div>
    </div>
  )
}

function HealthQuestions({ t, client, caseId, questions, onAnswered }) {
  const toast = useToast()
  const [selecting, setSelecting] = useState(null)   // question id while picking countries
  const [picked, setPicked] = useState([])
  const list = Array.isArray(questions) ? questions : []
  if (list.length === 0) return null

  async function answer(countries) {
    try {
      await client.updateAnswers(caseId, { recent_travel_countries: countries })
      toast(t('health.saved'))
      setSelecting(null); setPicked([])
      onAnswered && onAnswered()
    } catch (e) { toast(e.message) }
  }

  return (
    <div className="card" style={{ padding: 18, marginBottom: 14 }} data-testid="health-questions">
      <div className="eyebrow">{t('health.questionTitle')}</div>
      {list.map((q) => (
        <div key={q.id} style={{ marginTop: 8 }}>
          <div style={{ fontSize: 13.5, fontWeight: 600 }}>{q.question}</div>
          {q.trigger && <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 2 }}>{q.trigger}</div>}
          {selecting !== q.id ? (
            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
              <button className="btn btn--sm" onClick={() => { setSelecting(q.id); setPicked([]) }}>
                {t('health.yes')}
              </button>
              <button className="btn btn--sm btn--ghost" onClick={() => answer([])}>
                {t('health.no')}
              </button>
            </div>
          ) : (
            <div style={{ marginTop: 8 }}>
              <div style={{ fontSize: 12.5, color: 'var(--muted)', marginBottom: 6 }}>
                {t('health.whichCountries')}
              </div>
              {(q.trigger_countries || []).length > 0 ? (
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
                  {(q.trigger_countries || []).map((c) => (
                    <button key={c} type="button"
                      className={'chip' + (picked.includes(c) ? ' chip--ink' : '')}
                      onClick={() => setPicked((p) =>
                        p.includes(c) ? p.filter((x) => x !== c) : [...p, c])}>
                      {c}
                    </button>
                  ))}
                </div>
              ) : (
                <input className="input" style={{ marginBottom: 8 }}
                  placeholder="e.g. KEN, BRA"
                  onChange={(e) => setPicked(e.target.value.split(',')
                    .map((s) => s.trim().toUpperCase()).filter(Boolean))} />
              )}
              <button className="btn btn--sm" disabled={picked.length === 0}
                onClick={() => answer(picked)}>OK</button>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

// Live portal progress: every message derives from a REAL persisted checkpoint
// (workflow state, per-field flow step, or a pending handoff) — the backend
// never fabricates progress and never mentions Kimi for portal work (the
// portal is driven by a secure browser session, not by Kimi).
function formatElapsed(seconds) {
  if (seconds == null || seconds < 0) return ''
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return m > 0 ? `${m}m ${s}s` : `${s}s`
}

function ProgressCard({ client, caseId, progress, busy, onRefresh, onRetry, onResume }) {
  const pr = progress
  if (!pr) {
    return (
      <div className="card" style={{ padding: 22 }} data-testid="portal-progress">
        <Loading label="Checking your application status" />
        <PortalWatch client={client} caseId={caseId} height="44vh" />
        <button className="btn btn--ghost btn--sm" style={{ marginTop: 10 }} onClick={onRefresh}>Refresh status</button>
      </div>
    )
  }
  const message = pr.step?.message || 'Working on your application'
  const failed = !!pr.error && !pr.active && !pr.queued
  return (
    <div className="card" style={{ padding: 22 }} data-testid="portal-progress">
      {pr.stalled ? (
        <>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Connection to the portal stalled</div>
          <div style={{ fontSize: 13, color: 'var(--muted)' }}>{pr.stall_message}</div>
        </>
      ) : failed ? (
        <>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Paused</div>
          <div style={{ fontSize: 13, color: 'var(--muted)' }}>
            {pr.error.message} Your application data is saved.
          </div>
        </>
      ) : (
        <Loading label={message} />
      )}
      {/* The portal window accompanies EVERY waiting state — working,
          stalled, or paused after a failure — so the applicant always sees
          what the official site is showing rather than a bare spinner. It is
          watch-only WHILE Ellis drives; when the run stalled or failed, Ellis
          is not driving, so the applicant regains control of the page. */}
      <PortalWatch client={client} caseId={caseId} height="44vh"
        interactive={!!pr.stalled || failed} />
      <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 10, display: 'flex',
        gap: 12, flexWrap: 'wrap' }}>
        {pr.elapsed_seconds != null && (
          <span data-testid="progress-elapsed">Working for {formatElapsed(pr.elapsed_seconds)}</span>
        )}
        {pr.last_completed && (
          <span data-testid="progress-last">Last completed: {pr.last_completed.message}</span>
        )}
        {pr.browser_session_alive && <span className="chip">Secure portal session active</span>}
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
        <button className="btn btn--ghost btn--sm" onClick={onRefresh}>Refresh status</button>
        {pr.retry_available && !pr.active && !pr.queued && (
          <button className="btn btn--sm" disabled={busy} onClick={onRetry}
            data-testid="progress-retry">
            {busy ? 'Retrying…' : 'Retry and continue my application'}
          </button>
        )}
        {!pr.retry_available && pr.resume_available && (
          <button className="btn btn--sm" disabled={busy} onClick={onResume}
            data-testid="progress-resume">
            {busy ? 'Continuing…' : 'Continue my application'}
          </button>
        )}
      </div>
    </div>
  )
}

function ReviewPanel({ answers, onApprove, busy }) {
  // Calendar dates display as U.S. MM/DD/YYYY; the answer values stay ISO.
  const fields = Object.entries(answers || {}).map(([label, value]) => ({
    label, value: isDateKey(label) ? formatDateUS(value) : String(value) }))
  return (
    <div className="card" style={{ padding: 14, background: '#fff' }}>
      <KVList fields={fields.length ? fields : [{ label: 'No answers yet', value: '' }]} />
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 10 }}>
        <button className="btn" disabled={busy} onClick={onApprove}>{busy ? 'Confirming…' : 'Confirm and continue'}</button>
      </div>
    </div>
  )
}

function ResultView({ status, client, caseId }) {
  const c = status.confirmation
  const a = status.appointment
  const d = resultDisposition(status)
  // The official confirmation page, captured at submission: shown here so the
  // applicant sees exactly what the government portal said. The signed URL is
  // short-lived; the bytes are fetched once into an object URL.
  const [shotUrl, setShotUrl] = useState(null)
  useEffect(() => {
    let alive = true
    let obj = null
    const docId = c?.screenshot_document_id
    if (!docId || !client) return undefined
    client.documentPreviewUrl(caseId, docId)
      .then(async (r) => {
        if (!alive || !r?.url) return
        const res = await fetch(client.base + r.url)
        if (!res.ok) return
        obj = URL.createObjectURL(await res.blob())
        if (alive) setShotUrl(obj)
      })
      .catch(() => {})
    return () => { alive = false; if (obj) URL.revokeObjectURL(obj) }
  }, [c?.screenshot_document_id, caseId])
  if (status.state !== 'COMPLETED') {
    return <div className="card" style={{ padding: 22 }}>
      <div style={{ fontWeight: 700 }}>{status.state.replace(/_/g, ' ')}</div>
      <div style={{ fontSize: 13, color: 'var(--muted)', marginTop: 6 }}>
        This case ended in a non-completed state. Ellis has preserved everything and, where
        the outcome was uncertain, queued it for reconciliation before any retry.
      </div>
    </div>
  }
  // The display guard: only an adapter-verified real production result may read
  // as a real "Application submitted". Otherwise we label the classification and
  // never present the reference as a real government reference.
  return (
    <div className="card" style={{ padding: 22 }}>
      <div className={'sevbadge ' + (d.isReal ? 'sevbadge--ok' : 'sevbadge--warn')} style={{ marginBottom: 8 }}>
        {d.isReal ? '✓' : '⚠'}
      </div>
      <div style={{ fontSize: 18, fontWeight: 700 }}>
        {d.isReal ? 'Application submitted — pending government decision' : d.displayStatus}
      </div>
      {d.isReal && (
        <div style={{ fontSize: 12.5, color: 'var(--muted)', margin: '4px 0 6px' }}>
          The government will review your application. Submission does not
          guarantee approval; Ellis will show the official decision when the
          portal publishes it.
        </div>
      )}
      {!d.isReal && (
        <div style={{ fontSize: 13, color: '#9a3412', background: '#fff7ed',
          border: '1px solid #f59e0b', borderRadius: 8, padding: '8px 10px', margin: '8px 0' }}>
          {d.disclaimer}
        </div>
      )}
      <KVList fields={[
        c && { label: d.isReal ? 'Reference number' : 'Mock reference (not a real visa reference)', value: c.reference_no },
        c && c.receipt_no && { label: d.isReal ? 'Receipt' : 'Mock receipt', value: c.receipt_no },
        a && { label: 'Appointment', value: a.start_utc ? formatSlot(a.start_utc) + ' · ' + a.location_id : a.confirmation_no },
        a && { label: d.isReal ? 'Appointment confirmation' : 'Mock appointment confirmation', value: a.confirmation_no }
      ].filter(Boolean)} />
      {shotUrl && (
        <div style={{ marginTop: 14 }} data-testid="confirmation-screenshot">
          <div className="eyebrow">The official portal's confirmation page</div>
          <img src={shotUrl} alt="Official confirmation page"
            style={{ width: '100%', border: '1px solid var(--line)', borderRadius: 10,
              marginTop: 6 }} />
        </div>
      )}
      {d.isReal && (
        <div style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 10 }}
          data-testid="confirmation-email-note">
          A confirmation email with these details has been sent to you.
        </div>
      )}
    </div>
  )
}

function PrivacyPanel({ client, caseId, onDeleted }) {
  const toast = useToast()
  const [busy, setBusy] = useState('')
  const [confirm, setConfirm] = useState(false)

  async function exportData() {
    setBusy('export')
    try {
      const bundle = await client.exportCase(caseId)
      const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = `ellis-case-${caseId.slice(0, 8)}.json`
      document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url)
      toast('Export downloaded')
    } catch (e) { toast(e.message) }
    setBusy('')
  }

  async function erase() {
    setBusy('delete')
    try { await client.deleteCase(caseId); toast('Case erased'); onDeleted && onDeleted() }
    catch (e) { toast(e.message); setBusy('') }
  }

  return (
    <div className="card" style={{ padding: 18, marginTop: 16 }}>
      <div className="eyebrow">Your data</div>
      <div style={{ fontSize: 13, color: 'var(--muted)', margin: '4px 0 12px' }}>
        Export everything Ellis holds for this case, or permanently erase it. Erasure
        removes your documents and personal data and keeps only a non-identifying record
        that erasure occurred.
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <button className="btn btn--ghost btn--sm" disabled={!!busy} onClick={exportData}>
          {busy === 'export' ? 'Exporting…' : 'Export my data (JSON)'}
        </button>
        {!confirm
          ? <button className="btn btn--ghost btn--sm" onClick={() => setConfirm(true)}>Delete this case…</button>
          : <>
              <button className="btn btn--sm" style={{ background: 'var(--crit)' }} disabled={!!busy} onClick={erase}>
                {busy === 'delete' ? 'Erasing…' : 'Confirm permanent erasure'}
              </button>
              <button className="btn btn--ghost btn--sm" onClick={() => setConfirm(false)}>Cancel</button>
            </>}
      </div>
    </div>
  )
}

function AuditTrail({ events }) {
  if (!events.length) return <Empty title="No activity yet" sub="Every step Ellis takes will appear here." />
  return (
    <div className="card" style={{ padding: 16 }}>
      {events.map((e) => (
        <div key={e.seq} className="row">
          <div className="row__main">
            <div className="row__title" style={{ textTransform: 'capitalize' }}>{e.action.replace(/_/g, ' ')}</div>
            <div className="row__sub">{e.actor}{e.detail && Object.keys(e.detail).length ? ' · ' + Object.keys(e.detail).join(', ') : ''}</div>
          </div>
          <div className="chip" style={{ fontSize: 10 }}>#{e.seq}</div>
        </div>
      ))}
    </div>
  )
}
