// Applicant-facing handoff surfaces for the visa case flow. Each resolves one
// backend handoff (app/workflow.py _pause) and then advances the workflow by
// sending the matching Temporal/DB-runner signal.
//
// Privacy invariants enforced here:
//  - Ellis never solves CAPTCHAs and never auto-signs a government declaration.
//  - No password, OTP value, card number, CAPTCHA answer, security answer, or
//    declaration content is ever collected by Ellis — the applicant enters those
//    directly in the portal's own secure window. These modals only CONFIRM that
//    the applicant completed the step.
//  - Live View URLs are treated as sensitive: shown, never logged or emailed.
import { useEffect, useRef, useState } from 'react'
import { useToast, Loading, ErrorNote, KVList } from '../ui.jsx'
import { formatFee, formatSlot } from '../../lib/visaSession.js'

function Overlay({ children, onClose, width = 620 }) {
  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" style={{ maxWidth: width }} onClick={(e) => e.stopPropagation()}>
        {children}
      </div>
    </div>
  )
}

function Head({ title, sub, onClose }) {
  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
        <h2 style={{ marginBottom: 0 }}>{title}</h2>
        <button className="iconbtn" onClick={onClose} aria-label="Close">✕</button>
      </div>
      {sub && <div className="modal__sub">{sub}</div>}
    </>
  )
}

// ---- A safe "secure session" panel (Browserbase Live View stand-in) --------
// Sensitive-session recording is disabled; the URL is shown but never logged.
function SecureWindow({ live, label = 'secure portal window' }) {
  const url = live && live.url
  return (
    <div className="card card--soft" style={{ padding: 16, marginTop: 12 }}>
      <div className="eyebrow">Isolated {label}</div>
      <div style={{ fontSize: 13, color: 'var(--muted)', marginTop: 4 }}>
        A private browser session, one per case, opens on the official portal.
        Recording of sensitive steps is disabled. Ellis cannot see what you type here.
      </div>
      {url ? (
        <button className="btn btn--sm" style={{ marginTop: 10 }}
                onClick={() => window.ellis?.openExternal?.(url)}>
          Open secure window ↗
        </button>
      ) : (
        <div className="chip" style={{ marginTop: 10 }}>Secure window ready (mock portal)</div>
      )}
    </div>
  )
}

// ---- Native e-signature (handoff: authorization) ---------------------------
export function SignatureModal({ client, caseId, authorization, onDone, onClose }) {
  const toast = useToast()
  const [prep, setPrep] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [consent, setConsent] = useState(false)
  const [intent, setIntent] = useState(false)
  const [method, setMethod] = useState('typed')
  const [typed, setTyped] = useState('')
  const [signed, setSigned] = useState(null)
  const [busy, setBusy] = useState(false)
  const canvasRef = useRef(null)
  const drawnRef = useRef('')

  useEffect(() => {
    let live = true
    client.prepareAuthorization(caseId, authorization || {})
      .then((p) => { if (live) { setPrep(p); setLoading(false) } })
      .catch((e) => { if (live) { setError({ message: e.message }); setLoading(false) } })
    return () => { live = false }
  }, [caseId])

  // Minimal drawn-signature canvas (captures a data URL as the signature value).
  function startDraw(e) {
    const c = canvasRef.current; if (!c) return
    const ctx = c.getContext('2d'); const r = c.getBoundingClientRect()
    ctx.strokeStyle = '#0a0a0a'; ctx.lineWidth = 2; ctx.lineCap = 'round'
    ctx.beginPath(); ctx.moveTo(e.clientX - r.left, e.clientY - r.top)
    const move = (ev) => { ctx.lineTo(ev.clientX - r.left, ev.clientY - r.top); ctx.stroke() }
    const up = () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); drawnRef.current = c.toDataURL() }
    window.addEventListener('mousemove', move); window.addEventListener('mouseup', up)
  }

  async function sign() {
    if (!consent || !intent) { toast('Confirm consent and intent to sign'); return }
    const value = method === 'typed' ? typed.trim() : drawnRef.current
    if (!value) { toast(method === 'typed' ? 'Type your full legal name' : 'Draw your signature'); return }
    setBusy(true); setError(null)
    try {
      const res = await client.signAuthorization(caseId, {
        document_hash: prep.document_hash, consent_given: consent, intent_confirmed: intent,
        signature_method: method, signature_value: value, step_up_token: prep.step_up_token,
        auth_method: 'email_otp'
      })
      setSigned(res)
      toast('Authorization signed')
    } catch (e) {
      setError({ message: e.message }); setBusy(false)
    }
  }

  return (
    <Overlay onClose={onClose} width={680}>
      <Head title="Sign the Ellis authorization" onClose={onClose}
            sub="This authorizes Ellis to act for you on this application. It never replaces a government declaration you must sign personally." />
      {loading && <Loading label="Preparing your authorization" />}
      {error && <ErrorNote error={error} />}
      {prep && !signed && (
        <>
          <div style={{ display: 'flex', gap: 10, fontSize: 12, color: 'var(--muted)', margin: '4px 0 8px' }}>
            <span className="chip">Template {prep.template_version}</span>
            <span className="chip">Consent {prep.consent_version}</span>
          </div>
          <div className="card card--soft" style={{ padding: 14, maxHeight: 200, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 13 }}>
            {prep.document_text}
          </div>
          <div style={{ fontSize: 11, color: 'var(--muted-2)', margin: '8px 0' }}>
            Document hash: <code>{prep.document_hash?.slice(0, 32)}…</code>
          </div>
          <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', margin: '6px 0', fontSize: 13 }}>
            <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
            <span>I consent to sign this authorization electronically and to receive records electronically.</span>
          </label>
          <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', margin: '6px 0', fontSize: 13 }}>
            <input type="checkbox" checked={intent} onChange={(e) => setIntent(e.target.checked)} />
            <span>I intend this to be my signature and I authorize Ellis as described above.</span>
          </label>
          <div className="tabs" style={{ marginTop: 8 }}>
            <button className={'tab' + (method === 'typed' ? ' is-active' : '')} onClick={() => setMethod('typed')}>Type</button>
            <button className={'tab' + (method === 'drawn' ? ' is-active' : '')} onClick={() => setMethod('drawn')}>Draw</button>
          </div>
          {method === 'typed'
            ? <div className="field"><label>Full legal name</label>
                <input className="input" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="Type your full legal name" /></div>
            : <canvas ref={canvasRef} width={620} height={110} onMouseDown={startDraw}
                      style={{ border: '1px solid var(--line)', borderRadius: 10, width: '100%', marginTop: 8, cursor: 'crosshair' }} />}
          <div className="modal__foot">
            <button className="btn btn--ghost" onClick={onClose}>Cancel</button>
            <button className="btn" disabled={busy} onClick={sign}>{busy ? 'Signing…' : 'Sign authorization'}</button>
          </div>
        </>
      )}
      {signed && (
        <div className="result" style={{ marginTop: 12 }}>
          <div className="sevbadge sevbadge--ok" style={{ marginBottom: 8 }}>✓</div>
          <div style={{ fontWeight: 700 }}>Signed and recorded</div>
          <KVList fields={[
            { label: 'Signature ID', value: signed.signature_id },
            { label: 'Artifact hash', value: (signed.artifact_hash || '').slice(0, 24) + '…' },
            { label: 'Signed at', value: signed.signed_at }
          ]} />
          <div className="modal__foot">
            {signed.download && <button className="btn btn--ghost" onClick={() => window.ellis?.openExternal?.(client.base ? client.base + signed.download : signed.download)}>Download signed PDF</button>}
            <button className="btn" onClick={() => onDone && onDone(signed)}>Continue</button>
          </div>
        </div>
      )}
    </Overlay>
  )
}

// ---- Live View handoff (captcha / email_verification / otp / identity) -----
export function LiveViewModal({ client, caseId, pending, title, sub, onResolve, onClose }) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const [token, setToken] = useState(null)
  const [error, setError] = useState(null)
  const handoff = pending?.handoff
  const live = pending?.live_view

  useEffect(() => {
    if (handoff === 'email_verification' || handoff === 'otp') {
      client.mockVerification(caseId).then((r) => setToken(r.token)).catch(() => {})
    }
  }, [handoff, caseId])

  async function done() {
    setBusy(true); setError(null)
    try {
      if (handoff === 'email_verification' || handoff === 'otp') {
        await onResolve('verify_email', { token })
      } else if (handoff === 'captcha') {
        await onResolve('solve_captcha')
      } else {
        await onResolve('solve_captcha') // login_challenge/identity share the confirm-only path
      }
      toast('Step completed')
    } catch (e) { setError({ message: e.message }); setBusy(false) }
  }

  return (
    <Overlay onClose={onClose}>
      <Head title={title} sub={sub} onClose={onClose} />
      <SecureWindow live={live} />
      {handoff === 'captcha' && (
        <div style={{ fontSize: 13, marginTop: 12 }}>
          <strong>Ellis never solves CAPTCHAs.</strong> Complete it yourself in the secure window, then confirm below.
        </div>
      )}
      {(handoff === 'email_verification' || handoff === 'otp') && (
        <div style={{ fontSize: 13, marginTop: 12 }}>
          The portal emailed you a verification link. Open it (or, in this demo,
          Ellis detected the code from the mock inbox), then confirm below.
          {token && <div className="chip" style={{ marginTop: 8 }}>Verification detected</div>}
        </div>
      )}
      <div style={{ fontSize: 11, color: 'var(--muted-2)', marginTop: 10 }}>
        Ellis collects no passwords, one-time codes, card details, or CAPTCHA answers.
      </div>
      {error && <ErrorNote error={error} />}
      <div className="modal__foot">
        <button className="btn btn--ghost" onClick={onClose}>Not now</button>
        <button className="btn" disabled={busy} onClick={done}>{busy ? 'Confirming…' : 'I completed this step'}</button>
      </div>
    </Overlay>
  )
}

// ---- Payment approval (handoff: payment_approval) --------------------------
export function PaymentApprove({ pending, onResolve, onClose }) {
  const [busy, setBusy] = useState(false)
  const fee = pending?.fee
  return (
    <Overlay onClose={onClose} width={520}>
      <Head title="Approve the official fee" onClose={onClose}
            sub="Confirm the fee before the payment window opens. You pay it yourself." />
      <div className="stat" style={{ marginTop: 12 }}>
        <div className="stat__num">{formatFee(fee) || '—'}</div>
        <div className="stat__cap">Official portal fee</div>
      </div>
      <div className="modal__foot">
        <button className="btn btn--ghost" onClick={onClose}>Cancel</button>
        <button className="btn" disabled={busy} onClick={async () => { setBusy(true); await onResolve('approve_payment') }}>
          {busy ? 'Approving…' : 'Approve fee'}
        </button>
      </div>
    </Overlay>
  )
}

// ---- Applicant-controlled payment (handoff: payment / three_ds) ------------
export function PaymentModal({ pending, onResolve, onClose }) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const fee = pending?.fee
  return (
    <Overlay onClose={onClose}>
      <Head title="Pay the official fee" onClose={onClose}
            sub="Enter your card directly in the portal's secure window. Ellis never sees or stores your card." />
      <div className="stat" style={{ marginTop: 4 }}>
        <div className="stat__num">{formatFee(fee) || '—'}</div>
        <div className="stat__cap">Amount due at the official portal</div>
      </div>
      <SecureWindow live={pending?.live_view} label="secure payment window" />
      <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 10 }}>
        Supports payment pop-ups and 3-D Secure. If you leave, your case is kept
        safely and payment is reconciled with the portal before any retry — Ellis
        never charges twice.
      </div>
      {error && <ErrorNote error={error} />}
      <div className="modal__foot">
        <button className="btn btn--ghost" onClick={onClose}>I'll finish later</button>
        <button className="btn" disabled={busy} onClick={async () => {
          setBusy(true); setError(null)
          try { await onResolve('complete_payment'); toast('Payment confirmed') }
          catch (e) { setError({ message: e.message }); setBusy(false) }
        }}>{busy ? 'Confirming…' : 'I completed payment'}</button>
      </div>
    </Overlay>
  )
}

// ---- Appointment calendar (handoff: appointment_selection / no_availability) -
export function AppointmentCalendar({ pending, prefs, onResolve, onClose }) {
  const toast = useToast()
  const [busy, setBusy] = useState('')
  const slots = pending?.slots || []
  const tz = prefs?.timeZone
  if (pending?.handoff === 'no_availability') {
    return (
      <Overlay onClose={onClose} width={520}>
        <Head title="No matching appointment yet" onClose={onClose}
              sub="Nothing matches your preferences yet. Ellis keeps watching and will alert you." />
        <div className="modal__foot"><button className="btn" onClick={onClose}>Close</button></div>
      </Overlay>
    )
  }
  return (
    <Overlay onClose={onClose} width={640}>
      <Head title="Choose your appointment" onClose={onClose}
            sub="Pick a qualifying slot. Ellis never cancels an existing appointment before the new one is secured." />
      <div style={{ maxHeight: 320, overflow: 'auto', marginTop: 8 }}>
        {slots.length === 0 && <div className="chip">No slots offered</div>}
        {slots.map((s) => (
          <div key={s.slotId} className="row" style={{ alignItems: 'center' }}>
            <div className="row__main">
              <div className="row__title">{formatSlot(s.startUtc, tz)}</div>
              <div className="row__sub">{s.locationId}</div>
            </div>
            <button className="btn btn--sm" disabled={!!busy} onClick={async () => {
              setBusy(s.slotId)
              try { await onResolve('select_appointment', { slot_id: s.slotId }); toast('Appointment selected') }
              catch (e) { toast(e.message); setBusy('') }
            }}>{busy === s.slotId ? 'Booking…' : 'Select'}</button>
          </div>
        ))}
      </div>
    </Overlay>
  )
}

// ---- Reschedule approval (handoff: reschedule_approval) --------------------
export function RescheduleConfirm({ pending, onResolve, onClose }) {
  const [busy, setBusy] = useState(false)
  return (
    <Overlay onClose={onClose} width={520}>
      <Head title="Approve an earlier appointment" onClose={onClose}
            sub={pending?.reason || 'An earlier slot is available.'} />
      <div className="modal__foot">
        <button className="btn btn--ghost" onClick={onClose}>Keep current</button>
        <button className="btn" disabled={busy} onClick={async () => { setBusy(true); await onResolve('approve_reschedule') }}>
          {busy ? 'Rescheduling…' : 'Move to earlier slot'}
        </button>
      </div>
    </Overlay>
  )
}

// ---- Personal government declaration (handoff: personal_declaration) --------
export function DeclarationModal({ onResolve, onClose }) {
  const [agree, setAgree] = useState(false)
  const [busy, setBusy] = useState(false)
  return (
    <Overlay onClose={onClose}>
      <Head title="Sign the government declaration" onClose={onClose}
            sub="Only you can make this declaration, under penalty of perjury. Ellis and its AI never sign it for you." />
      <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', margin: '10px 0', fontSize: 13 }}>
        <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
        <span>I personally declare that the information in this application is true and complete, under penalty of perjury.</span>
      </label>
      <div className="modal__foot">
        <button className="btn btn--ghost" onClick={onClose}>Cancel</button>
        <button className="btn" disabled={!agree || busy} onClick={async () => { setBusy(true); await onResolve('complete_declaration') }}>
          {busy ? 'Submitting…' : 'I personally declare'}
        </button>
      </div>
    </Overlay>
  )
}
