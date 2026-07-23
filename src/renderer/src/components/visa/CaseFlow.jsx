// The applicant case flow: prepare (documents + preferences + authorization),
// start the durable workflow, then resolve each human handoff the backend pauses
// at, and finally show the confirmation + receipt + appointment. The backend
// (DB-runner or Temporal) owns all state; this screen reads status and sends the
// matching signal for each handoff.
import { useEffect, useState } from 'react'
import { useToast, Loading, ErrorNote, KVList, Empty } from '../ui.jsx'
import { HANDOFF_COPY } from '../../lib/visaBackend.js'
import { handoffCopy, isTerminal, formatSlot, resultDisposition } from '../../lib/visaSession.js'
import OcrReview from './OcrReview.jsx'
import Preferences from './Preferences.jsx'
import {
  SignatureModal, LiveViewModal, PaymentApprove, PaymentModal,
  AppointmentCalendar, RescheduleConfirm, DeclarationModal,
  StandingAuthModal, FinalReviewModal
} from './handoffs.jsx'

const JOURNEY = [
  'DRAFT', 'APPLICANT_REVIEW_REQUIRED', 'AUTHORIZATION_PENDING', 'PORTAL_ACCOUNT_CREATING',
  'PORTAL_VERIFICATION_REQUIRED', 'PAYMENT_APPROVAL_REQUIRED', 'PAYMENT_ACTION_REQUIRED',
  'APPOINTMENT_BOOKING', 'PERSONAL_DECLARATION_REQUIRED', 'SUBMITTING', 'COMPLETED'
]

function Timeline({ state }) {
  const idx = JOURNEY.indexOf(state)
  return (
    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', margin: '4px 0 14px' }}>
      {JOURNEY.map((s, i) => (
        <span key={s} className={'chip' + (i <= idx && idx >= 0 ? ' chip--ink' : '')}
              style={{ fontSize: 10 }} title={s}>
          {s.replace(/_/g, ' ').toLowerCase()}
        </span>
      ))}
    </div>
  )
}

export default function CaseFlow({ client, caseId, onNotify }) {
  const toast = useToast()
  const [tab, setTab] = useState('journey')
  const [status, setStatus] = useState(null)
  const [prefs, setPrefs] = useState(null)
  const [audit, setAudit] = useState([])
  const [error, setError] = useState(null)
  const [modal, setModal] = useState(null)   // active handoff modal id
  const [busy, setBusy] = useState(false)
  const [standing, setStanding] = useState(null)  // standing-authorization state

  async function refresh() {
    try {
      const c = await client.getCase(caseId)
      setStatus(c)
      client.audit(caseId).then((a) => setAudit(a.events || [])).catch(() => {})
      client.getStandingAuthorization(caseId)
        .then((s) => setStanding(s.current)).catch(() => {})
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
    catch (e) { setError({ message: e.message }) }
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

  return (
    <div>
      <div className="tabs">
        {['journey', 'documents', 'preferences', 'activity'].map((t) => (
          <button key={t} className={'tab' + (tab === t ? ' is-active' : '')} onClick={() => setTab(t)}>
            {t[0].toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      {tab === 'journey' && (
        <div className="tabpanel">
          <Timeline state={state} />
          <ExecutionBanner status={status} />
          {error && <ErrorNote error={error} />}

          {!started && (
            <div className="card" style={{ padding: 22 }}>
              <div style={{ fontWeight: 700, marginBottom: 6 }}>Ready to submit?</div>
              <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 12 }}>
                Add and approve your documents and set your appointment preferences,
                then start. Ellis will pause for you at every step that needs you.
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
          <OcrReview client={client} caseId={caseId} onChanged={refresh} />
        </div>
      )}

      {tab === 'preferences' && (
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
    </div>
  )
}

function ReviewPanel({ answers, onApprove, busy }) {
  const fields = Object.entries(answers || {}).map(([label, value]) => ({ label, value: String(value) }))
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
