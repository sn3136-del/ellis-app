// The applicant case flow: prepare (documents + preferences + authorization),
// start the durable workflow, then resolve each human handoff the backend pauses
// at, and finally show the confirmation + receipt + appointment. The backend
// (DB-runner or Temporal) owns all state; this screen reads status and sends the
// matching signal for each handoff.
import { useEffect, useRef, useState } from 'react'
import { useToast, Loading, ErrorNote, KVList, Empty } from '../ui.jsx'
import { useLocale } from '../../lib/locale.jsx'
import { HANDOFF_COPY } from '../../lib/visaBackend.js'
import { handoffCopy, isTerminal, formatSlot, resultDisposition } from '../../lib/visaSession.js'
import {
  applicableStages, showExecutionBanner, preferencesTabVisible,
  validityMeta, verificationMeta, formatDateUS, isDateKey
} from '../../lib/intake.js'
import OcrReview from './OcrReview.jsx'
import Preferences from './Preferences.jsx'
import Checklist, { ContinuePanel } from './Checklist.jsx'
import {
  SignatureModal, LiveViewModal, PaymentApprove, PaymentModal,
  AppointmentCalendar, RescheduleConfirm, DeclarationModal,
  StandingAuthModal, FinalReviewModal, AdditionalInfoModal
} from './handoffs.jsx'

// Legacy full stage list — used ONLY for cases without a saved route journey
// (no continuation kind). Routed cases render just their applicable stages.
const JOURNEY = [
  'DRAFT', 'APPLICANT_REVIEW_REQUIRED', 'AUTHORIZATION_PENDING', 'PORTAL_ACCOUNT_CREATING',
  'PORTAL_VERIFICATION_REQUIRED', 'PAYMENT_APPROVAL_REQUIRED', 'PAYMENT_ACTION_REQUIRED',
  'APPOINTMENT_BOOKING', 'PERSONAL_DECLARATION_REQUIRED', 'SUBMITTING', 'COMPLETED'
]

function Timeline({ state, journey }) {
  // Route-specific stages from the Kimi workflow plan; [] = no submission
  // timeline for this route at all (entry preparation / renewal prep).
  const stages = applicableStages(
    journey?.continuation_kind,
    (journey?.guidance || {}).workflow_plan
  ) ?? JOURNEY
  if (stages.length === 0) return null
  const idx = stages.indexOf(state)
  return (
    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', margin: '4px 0 14px' }}
      data-testid="journey-timeline">
      {stages.map((s, i) => (
        <span key={s} className={'chip' + (i <= idx && idx >= 0 ? ' chip--ink' : '')}
              style={{ fontSize: 10 }} title={s}>
          {s.replace(/_/g, ' ').toLowerCase()}
        </span>
      ))}
    </div>
  )
}

export default function CaseFlow({ client, caseId, onNotify, onOpenCase }) {
  const toast = useToast()
  const { t } = useLocale()
  const [tab, setTab] = useState('journey')
  const [status, setStatus] = useState(null)
  const [prefs, setPrefs] = useState(null)
  const [audit, setAudit] = useState([])
  const [error, setError] = useState(null)
  const [modal, setModal] = useState(null)   // active handoff modal id
  const [busy, setBusy] = useState(false)
  const [standing, setStanding] = useState(null)  // standing-authorization state
  const [journey, setJourney] = useState(null)    // saved guidance + checklist + audit status
  const landedOnDocs = useRef(false)

  async function refresh() {
    try {
      const c = await client.getCase(caseId)
      setStatus(c)
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
          setTab('documents')
        }
      }).catch(() => {})
    } catch (e) { setError({ message: e.message }) }
  }
  useEffect(() => { refresh() }, [caseId])

  const pending = status?.pending
  const handoff = pending?.handoff
  const state = status?.state
  const terminal = isTerminal(state)

  // Apply a workflow status response (from start/signals) to local state.
  function apply(res) {
    setStatus((prev) => ({ ...prev, ...res }))
    client.audit(caseId).then((a) => setAudit(a.events || [])).catch(() => {})
    onNotify && onNotify()
  }

  async function start() {
    setBusy(true); setError(null)
    try { apply(await client.start(caseId)); toast('Application started') }
    catch (e) {
      // Honest fail-closed reasons get applicant-facing copy — never a bare
      // HTTP status. real_only_stop = no approved live portal connection for
      // this route; Ellis never simulates a government submission.
      const reason = e.detail && typeof e.detail === 'object' ? e.detail.reason : null
      setError({
        message: reason === 'real_only_stop' ? t('case.portalUnavailable')
          : reason === 'documents_incomplete' ? e.detail.message
          : e.message
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
  const started = state && state !== 'DRAFT'
  const kind = journey?.continuation_kind
  // The Preferences tab is appointment configuration: only for routes whose
  // verified guidance requires an appointment / in-person submission.
  const tabs = ['journey', 'documents']
  if (preferencesTabVisible(journey)) tabs.push('preferences')
  tabs.push('activity')
  const tabLabel = (id) => id === 'preferences'
    ? t('case.tab.appointmentPrefs')
    : id[0].toUpperCase() + id.slice(1)

  return (
    <div>
      <div className="tabs">
        {tabs.map((tb) => (
          <button key={tb} className={'tab' + (tab === tb ? ' is-active' : '')} onClick={() => setTab(tb)}>
            {tabLabel(tb)}
          </button>
        ))}
      </div>

      {tab === 'journey' && (
        <div className="tabpanel">
          <Timeline state={state} journey={journey} />
          {/* The realness banner guards EXECUTED results. A routed case that
              has not started yet shows precise provider/capability errors at
              start time instead of a blanket warning; a route with no
              submission at all never shows it. Legacy cases keep it always. */}
          {showExecutionBanner(journey) && (started || !kind) && <ExecutionBanner status={status} />}
          <JourneyHeader t={t} journey={journey} />
          {error && <ErrorNote error={error} />}

          {!started && kind === 'entry_preparation' && (
            <EntryPrep t={t} client={client} caseId={caseId} journey={journey}
              onOpenCase={onOpenCase}
              onToDocuments={() => setTab('documents')} />
          )}

          {!started && kind === 'passport_renewal' && (
            <RenewalPrep t={t} journey={journey}
              onToDocuments={() => setTab('documents')} />
          )}

          {!started && kind !== 'entry_preparation' && kind !== 'passport_renewal' && (
            <div className="card" style={{ padding: 22 }}>
              <CaseValidity t={t} client={client} caseId={caseId} onOpenCase={onOpenCase} />
              <div style={{ fontWeight: 700, marginBottom: 6 }}>Ready to submit?</div>
              <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 12 }}>
                Add and approve your documents, then start. Ellis will pause for
                you at every step that needs you.
              </div>
              {standing?.granted && !standing?.revoked ? (
                <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                  <span className="chip chip--ink" title={standing.text_hash}>
                    Standing authorization v{standing.version} granted
                  </span>
                  <button className="btn" disabled={busy} onClick={start}>
                    {busy ? 'Starting…' : 'Start application'}
                  </button>
                </div>
              ) : (
                <div>
                  <div style={{ fontSize: 12.5, color: 'var(--muted)', marginBottom: 8 }}>
                    First, grant Ellis its one-time standing authorization: it covers
                    routine portal steps only. Payment always needs your separate,
                    exact-amount confirmation.
                  </div>
                  <button className="btn" onClick={() => setModal('standing_auth')}>
                    Review & grant authorization
                  </button>
                </div>
              )}
            </div>
          )}

          {started && !terminal && handoff && (
            <div className="card card--ink" style={{ padding: 22 }}>
              <div className="eyebrow" style={{ color: 'rgba(255,255,255,0.7)' }}>Action needed</div>
              <div style={{ fontSize: 18, fontWeight: 700, color: '#fff', margin: '4px 0' }}>{copy.title}</div>
              <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.8)', marginBottom: 14 }}>{copy.sub}</div>
              {handoff === 'review'
                ? <ReviewPanel answers={status.answers} busy={busy} onApprove={async () => {
                    setBusy(true); try { await resolve('approve_review') } finally { setBusy(false) } }} />
                : <button className="btn" style={{ background: '#fff', color: '#0a0a0a' }}
                          onClick={() => setModal(handoff)}>Continue</button>}
            </div>
          )}

          {started && !terminal && !handoff && (
            <div className="card" style={{ padding: 22 }}>
              <Loading label="Ellis is working through the portal" />
              <button className="btn btn--ghost btn--sm" style={{ marginTop: 10 }} onClick={refresh}>Refresh status</button>
            </div>
          )}

          {terminal && <ResultView status={status} />}
        </div>
      )}

      {tab === 'documents' && (
        <div className="tabpanel">
          <JourneyHeader t={t} journey={journey} />
          <HealthQuestions t={t} client={client} caseId={caseId}
            questions={journey?.health_questions} onAnswered={refresh} />
          <Checklist t={t} client={client} caseId={caseId}
            checklist={journey?.checklist}
            counts={journey?.checklist_counts && {
              required: journey.checklist_counts.required_documents ??
                (journey.checklist || []).filter((i) => i.required && i.kind === 'document').length,
              missing: journey.checklist_counts.required_missing
            }}
            translation={journey?.translation}
            onChanged={refresh} />
          <OcrReview client={client} caseId={caseId} onChanged={refresh} />
          <ContinuePanel t={t} client={client} caseId={caseId} journey={journey}
            onAdvanced={() => { setTab('journey'); refresh() }} />
        </div>
      )}

      {tab === 'preferences' && tabs.includes('preferences') && (
        <div className="tabpanel">
          <Preferences client={client} caseId={caseId} initial={prefs} onSaved={(p) => { setPrefs(p); toast('Saved') }} />
        </div>
      )}

      {tab === 'activity' && (
        <div className="tabpanel">
          <AuditTrail events={audit} />
          <PrivacyPanel client={client} caseId={caseId} onDeleted={() => onNotify && onNotify()} />
        </div>
      )}

      {/* Handoff modals */}
      {modal === 'authorization' && (
        <SignatureModal client={client} caseId={caseId} authorization={{}}
          onClose={() => setModal(null)}
          onDone={() => resolve('sign_authorization')} />
      )}
      {(modal === 'captcha' || modal === 'email_verification' || modal === 'otp' ||
        modal === 'identity' || modal === 'login_challenge') && (
        <LiveViewModal client={client} caseId={caseId} pending={pending}
          title={copy.title} sub={copy.sub}
          onResolve={resolve} onClose={() => setModal(null)} />
      )}
      {modal === 'payment_approval' && (
        <PaymentApprove pending={pending} onResolve={resolve} onClose={() => setModal(null)} />
      )}
      {(modal === 'payment' || modal === 'three_ds') && (
        <PaymentModal pending={pending} onResolve={resolve} onClose={() => setModal(null)} />
      )}
      {(modal === 'appointment_selection' || modal === 'no_availability') && (
        <AppointmentCalendar pending={pending} prefs={prefs} onResolve={resolve} onClose={() => setModal(null)} />
      )}
      {modal === 'reschedule_approval' && (
        <RescheduleConfirm pending={pending} onResolve={resolve} onClose={() => setModal(null)} />
      )}
      {modal === 'personal_declaration' && (
        <DeclarationModal onResolve={resolve} onClose={() => setModal(null)} />
      )}
      {modal === 'standing_auth' && (
        <StandingAuthModal client={client} caseId={caseId}
          onClose={() => setModal(null)}
          onDone={(res) => { setStanding(res); setModal(null); toast('Authorization granted') }} />
      )}
      {modal === 'final_review' && (
        <FinalReviewModal client={client} caseId={caseId}
          onClose={() => setModal(null)}
          onDone={async () => { setModal(null); toast('Signed'); await start() }} />
      )}
      {modal === 'additional_information' && (
        <AdditionalInfoModal pending={pending}
          onResolve={resolve}
          onGoToDocuments={() => { setModal(null); setTab('documents') }}
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

// Route summary + the Kimi route-decision chip. No official-source audit
// exists on the applicant path — deterministic validation is the check.
function JourneyHeader({ t, journey }) {
  if (!journey || !journey.continuation_kind) return null
  const g = (journey.guidance || {}).guidance || {}
  const ver = verificationMeta(journey.verification)
  return (
    <div className="card card--soft" style={{ padding: '10px 14px', marginBottom: 12 }}
      data-testid="journey-header" data-kind={journey.continuation_kind}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <span className="badge badge--ai">{t('guidance.aiBadge')}</span>
        {g.visa_category && <span className="chip">{g.visa_category}</span>}
        {g.permitted_stay && <span className="chip">{g.permitted_stay}</span>}
        {ver.verified && (
          <span className="chip" data-testid="verification-chip">{t(ver.i18nKey)}</span>
        )}
      </div>
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

// Passport-renewal case: the Kimi renewal analysis (path, form, fees, times)
// plus the linked-travel-case note. Documents flow through the normal
// checklist; approving the NEW passport resumes the travel case automatically.
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

function ReviewPanel({ answers, onApprove, busy }) {
  // Calendar dates display as U.S. MM/DD/YYYY; the answer values stay ISO.
  const fields = Object.entries(answers || {}).map(([label, value]) => ({
    label, value: isDateKey(label) ? formatDateUS(value) : String(value) }))
  return (
    <div className="card" style={{ padding: 14, background: '#fff' }}>
      <KVList fields={fields.length ? fields : [{ label: 'No answers yet', value: '' }]} />
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 10 }}>
        <button className="btn" disabled={busy} onClick={onApprove}>{busy ? 'Approving…' : 'Approve & continue'}</button>
      </div>
    </div>
  )
}

// A persistent, honest banner whenever the case is NOT running on an approved
// live-production portal. The applicant always knows the realness of what they see.
function ExecutionBanner({ status }) {
  const d = resultDisposition(status)
  if (d.isReal) return null
  return (
    <div className="card" style={{ padding: '10px 14px', marginBottom: 12,
      background: '#fff7ed', border: '1px solid #f59e0b' }}>
      <div style={{ fontWeight: 700, fontSize: 12, color: '#9a3412' }}>
        {d.executionClass} — not a real government submission
      </div>
      <div style={{ fontSize: 12, color: '#9a3412' }}>{d.disclaimer}</div>
    </div>
  )
}

function ResultView({ status }) {
  const c = status.confirmation
  const a = status.appointment
  const d = resultDisposition(status)
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
        {d.isReal ? 'Application submitted' : d.displayStatus}
      </div>
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
