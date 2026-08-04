// Applicant-facing handoff surfaces for the visa case flow. Each resolves one
// backend handoff (app/workflow.py _pause) and then advances the workflow by
// sending the matching Temporal/DB-runner signal.
//
// Privacy invariants enforced here:
//  - Ellis never solves CAPTCHAs and never auto-signs a government declaration.
//  - No password, OTP secret, CAPTCHA answer, security answer, or declaration
//    content is ever collected by Ellis — the applicant enters those directly
//    in the portal's own secure window. These modals only CONFIRM that the
//    applicant completed the step.
//  - Payment details are collected ONLY in PaymentDetailsModal, at the
//    applicant's explicit request, so Ellis can fill the official portal's
//    payment form: sent once over a vaulted one-time reference, never stored
//    or logged, and the portal's own payment confirmation click always stays
//    with the applicant.
//  - Live View URLs are treated as sensitive: shown, never logged or emailed.
import { useEffect, useRef, useState } from 'react'
import { useToast, Loading, ErrorNote, KVList, Overlay as Backdrop } from '../ui.jsx'
import { useT } from '../../lib/locale.jsx'
import { formatFee, formatSlot, splitQuestions, collectAnswers } from '../../lib/visaSession.js'
import { ALLOWED, MAX_BYTES, readAsBase64 } from './Checklist.jsx'

// Every handoff modal goes through the portalled backdrop in ui.jsx — these
// render from inside animated cards, whose transform would otherwise become
// the containing block for `position: fixed` and trap the modal in the card.
function Overlay({ children, onClose, width = 620 }) {
  return (
    <Backdrop onClose={onClose}>
      <div className="modal" style={{ maxWidth: width }} onClick={(e) => e.stopPropagation()}>
        {children}
      </div>
    </Backdrop>
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
function SecureWindow({ live, label = 'secure portal window', onReconnect, reconnecting }) {
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
        // NEVER claim a window is "ready" when there is none — and never call
        // the real government portal a mock.
        <div style={{ marginTop: 10 }}>
          <div style={{ fontSize: 13 }}>
            The secure window is not open right now.
          </div>
          {onReconnect && (
            <button className="btn btn--sm" style={{ marginTop: 8 }}
              disabled={reconnecting} onClick={onReconnect} data-testid="reconnect-secure">
              {reconnecting ? 'Reconnecting…' : 'Reconnect the secure window'}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

// Embedded live view of the case's isolated portal session, shared by every
// handoff that needs the applicant to act ON the official page (CAPTCHA, OTP,
// portal form items, PAYMENT). The URL is short-lived and sensitive: it lives
// only in component state, is refreshed before it expires, and is never logged.
export function usePortalLiveView(client, caseId, opts = {}) {
  // restore:false — surfaces where the APPLICANT drives (attended
  // observation): a fresh window must stay wherever the flow points it, not
  // queue a portal-rebuild run for a route Ellis cannot drive yet.
  const restore = opts.restore !== false
  const [state, setState] = useState('connecting')  // connecting|embedded|unavailable|closed
  const [url, setUrl] = useState(null)
  const [nonce, setNonce] = useState(0)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [restoring, setRestoring] = useState(false)

  async function open() {
    setBusy(true)
    try {
      const s = await client.createBrowserSession(caseId)
      if (s && s.fresh && restore) {
        // A brand-new session shows a blank page. Ask Ellis to rebuild the
        // portal view at the case's current step (background, reversible),
        // and keep the notice up until the run finishes so the applicant is
        // never told to read a fee off an empty tab.
        setRestoring(true)
        client.restorePortal(caseId)
          .then(() => waitForRestore())
          .catch((e) => {
            setRestoring(false)
            setNote((e.detail && e.detail.message) || e.message ||
                    'Ellis could not rebuild the portal page.')
          })
      }
      if (s && s.mode === 'browserbase' && s.live_view_available) {
        const lv = await client.browserLiveView(caseId)
        setUrl(lv.url); setNonce((n) => n + 1); setState('embedded'); setNote('')
      } else {
        setState('unavailable')
        setNote(s && s.browserbase_configured === false
          ? 'A secure in-app window needs Browserbase configured. Complete this step on the official site instead.'
          : 'The secure window is not available right now.')
      }
    } catch (e) {
      const reason = e && typeof e.detail === 'object' ? e.detail.reason : ''
      setState('unavailable')
      setNote(reason === 'session_ended'
        ? 'The secure portal session ended. Reconnect to open a fresh one.'
        : (e.detail && e.detail.message) || e.message || 'The secure window is not available.')
    }
    setBusy(false)
  }

  // Poll until the rebuild run finishes, then refresh the embed so the
  // applicant sees the restored page (and any fee Ellis read from it).
  async function waitForRestore() {
    for (let i = 0; i < 90; i++) {
      await new Promise((r) => setTimeout(r, 3000))
      try {
        const pr = await client.caseProgress(caseId)
        if (pr.run_signal !== 'restore_portal' || (!pr.active && !pr.queued)) break
      } catch { break }
    }
    setRestoring(false)
    try {
      const lv = await client.browserLiveView(caseId)
      setUrl(lv.url); setNonce((n) => n + 1); setState('embedded')
    } catch { /* the panel keeps its current state */ }
  }

  useEffect(() => { open() }, [caseId])
  // Live-view links expire after a few minutes; refresh before they do.
  useEffect(() => {
    if (state !== 'embedded') return undefined
    const iv = setInterval(() => { open() }, 210000)
    return () => clearInterval(iv)
  }, [state, caseId])

  return { state, url, nonce, busy, note, restoring, reconnect: open,
           close: async () => {
             try { await client.closeBrowserSession(caseId) } catch { /* non-fatal */ }
             setUrl(null); setState('closed')
           } }
}

// Watch-only wrapper: the applicant sees Ellis work but cannot click or type
// into the session while it is driving — a stray click mid-fill could change
// portal state under the runner. Scrolling IS allowed: wheeling over the
// shield drops it for a beat so the wheel reaches the live view (which
// forwards it to the portal page), then it re-arms the moment the wheel goes
// quiet — clicks and typing never get a stable way through.
function WatchOnlyFrame({ children }) {
  const shieldRef = useRef(null)
  const reArm = useRef(null)
  useEffect(() => {
    const el = shieldRef.current
    if (!el) return undefined
    const onWheel = (e) => {
      e.preventDefault() // the outer page must not scroll instead
      el.style.pointerEvents = 'none'
      clearTimeout(reArm.current)
      reArm.current = setTimeout(() => { el.style.pointerEvents = 'auto' }, 400)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => { el.removeEventListener('wheel', onWheel); clearTimeout(reArm.current) }
  }, [])
  const armNow = () => {
    clearTimeout(reArm.current)
    if (shieldRef.current) shieldRef.current.style.pointerEvents = 'auto'
  }
  return (
    <div className="watchonly" data-testid="watch-only" onMouseLeave={armNow}>
      {children}
      <div ref={shieldRef} className="watchonly__shield" aria-hidden="true" />
      <span className="watchonly__note">View only — Ellis is working on this page</span>
    </div>
  )
}

export function LiveFrame({ view, height = '70vh', watchOnly = false }) {
  if (view.state === 'connecting') return <Loading label="Opening the secure portal window" />
  if (view.state === 'embedded' && view.url) {
    const frame = (
      <iframe key={view.nonce} src={view.url} title="Ellis secure window"
        sandbox="allow-same-origin allow-scripts allow-forms"
        referrerPolicy="no-referrer"
        style={{ width: '100%', height, border: '1px solid var(--line)',
          borderRadius: 10, marginTop: 8, background: '#fff' }} />
    )
    return (
      <div>
        {view.restoring && (
          <div className="card card--soft" style={{ padding: '8px 12px', marginTop: 8,
            fontSize: 12.5 }} data-testid="restoring-note">
            Ellis is rebuilding the portal page at your current step — you can
            watch it below. This takes a minute or two.
          </div>
        )}
        {watchOnly ? <WatchOnlyFrame>{frame}</WatchOnlyFrame> : frame}
      </div>
    )
  }
  return (
    <div className="card card--soft" style={{ padding: 14, marginTop: 12, fontSize: 13 }}>
      {view.note || 'The secure window is not available right now.'}
      <div style={{ marginTop: 8 }}>
        <button className="btn btn--sm" disabled={view.busy} onClick={view.reconnect}>
          {view.busy ? 'Reconnecting…' : 'Reconnect the secure window'}
        </button>
      </div>
    </div>
  )
}

// A compact view of the case's own secure portal window, shown beside ANY
// waiting surface so the applicant watches what Ellis is doing on the
// official site instead of a bare spinner. ALWAYS watch-only: while Ellis is
// the one driving, the applicant can see but not touch. Same isolated
// session as every other surface (one per case); the URL stays short-lived
// and unlogged. Surfaces where the applicant must act on the portal
// (payment, portal_form) embed LiveFrame directly, which stays interactive.
export function PortalWatch({ client, caseId, height = '40vh', label, interactive = false }) {
  const t = useT()
  const view = usePortalLiveView(client, caseId)
  if (!client || !caseId) return null
  return (
    <div style={{ marginTop: 12 }} data-testid="portal-watch">
      <div style={{ display: 'flex', justifyContent: 'space-between',
        alignItems: 'center', gap: 10 }}>
        <div className="eyebrow">{label || t('live.watch')}</div>
        <button className="btn btn--sm btn--ghost" disabled={view.busy}
          onClick={view.reconnect}>{view.busy ? '…' : t('live.refresh')}</button>
      </div>
      <LiveFrame view={view} height={height} watchOnly={!interactive} />
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
  const [continuing, setContinuing] = useState(false)
  const [continued, setContinued] = useState(false)
  const [showText, setShowText] = useState(false)
  const canvasRef = useRef(null)
  const drawnRef = useRef('')
  // Contact confirmation is automatic: the intake email IS the portal
  // contact (recorded server-side when the authorization is signed).

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
        auth_method: 'email_otp',
        // Bind the signature to the EXACT envelope this modal prepared — a
        // concurrently created envelope can never swap the terms.
        envelope_id: prep.envelope_id || ''
      })
      setSigned(res)
      toast('Authorization signed')
      // SINGLE CEREMONY: granting the authorization IS the go-ahead — the
      // application starts immediately, no second button.
      try {
        await (onDone && onDone(res))
      } catch (e2) {
        setError({ message: (e2.detail && e2.detail.message) || e2.message })
        setBusy(false)
        return
      }
      onClose && onClose()
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
          <button type="button" className="btn btn--sm btn--ghost" data-testid="read-authorization"
            onClick={() => setShowText((v) => !v)} aria-expanded={showText}
            style={{ margin: '4px 0 10px' }}>
            {showText ? 'Hide the authorization text' : 'Read the authorization'}
          </button>
          {showText && (
            <div className="card card--soft fadeup" style={{ padding: 14, maxHeight: 240,
              overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 13 }} data-testid="authorization-text">
              {prep.document_text}
              <div style={{ fontSize: 11, color: 'var(--muted-2)', marginTop: 10 }}>
                Template {prep.template_version} · Consent {prep.consent_version} ·
                Document hash <code>{prep.document_hash?.slice(0, 32)}…</code>
              </div>
            </div>
          )}
          <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', margin: '6px 0', fontSize: 13 }}>
            <input type="checkbox" checked={consent && intent}
              onChange={(e) => { setConsent(e.target.checked); setIntent(e.target.checked) }} />
            <span>I have had the chance to read this authorization; I consent to sign it
              electronically, I intend this to be my signature, and I authorize
              Trip.com's Ellis to act as described. One signature covers everything —
              CAPTCHA, verification codes and the final payment confirmation always stay with me.</span>
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
            <button className="btn" disabled={busy} onClick={sign} data-testid="grant-authorization">
              {busy ? 'Starting your application…' : 'Grant authorization'}
            </button>
          </div>
        </>
      )}
      {signed && (
        <div className="result fadeup" style={{ marginTop: 12 }}>
          <div className={'sevbadge ' + (error ? 'sevbadge--mid' : 'sevbadge--ok')}
            style={{ marginBottom: 8 }}>{error ? '!' : '✓'}</div>
          <div style={{ fontWeight: 700 }}>
            {error
              ? 'Your authorization is signed — but starting the application failed'
              : 'Authorization granted — starting your application'}
          </div>
          <div style={{ fontSize: 12.5, color: 'var(--muted)', margin: '4px 0 8px' }}>
            Ellis is opening the official portal for you now. CAPTCHA,
            verification codes and the final payment confirmation always stay
            with you. Updates from the portal go to your email.
          </div>
          <KVList fields={[
            { label: 'Signature ID', value: signed.signature_id },
            { label: 'Signed at', value: signed.signed_at }
          ]} />
          {error && <ErrorNote error={error} />}
          <div className="modal__foot">
            {signed.download && (
              <button className="btn btn--ghost" onClick={() => window.ellis?.openExternal?.(client.base ? client.base + signed.download : signed.download)}>Download signed PDF</button>
            )}
            {error && (
              <button className="btn" disabled={busy} data-testid="retry-continue"
                onClick={async () => {
                  // The signature stands; only the start leg failed. Retry it
                  // WITHOUT signing again.
                  setBusy(true); setError(null)
                  try {
                    await (onDone && onDone(signed))
                    onClose && onClose()
                  } catch (e) {
                    setError({ message: (e.detail && e.detail.message) || e.message })
                    setBusy(false)
                  }
                }}>
                {busy ? 'Starting…' : 'Start my application'}
              </button>
            )}
          </div>
        </div>
      )}
    </Overlay>
  )
}

// ---- Live View handoff (captcha / email_verification / otp / identity) -----
// Opens (or reuses) the case's isolated Browserbase session and embeds the
// provider's Live View URL in a sandboxed iframe — the "small Ellis window".
// The URL is SHORT-LIVED and sensitive: it lives only in component state and
// is NEVER logged, cached, or persisted. When Browserbase is not configured
// (local mode) or no live-view URL is available, the modal falls back to the
// original instruction-panel behavior with an honest note.
export function LiveViewModal({ client, caseId, pending, title, sub, onResolve, onClose }) {
  const toast = useToast()
  const t = useT()
  const [busy, setBusy] = useState(false)
  const [token, setToken] = useState(null)
  const [typedCode, setTypedCode] = useState('')
  const [maskedDest, setMaskedDest] = useState(null)
  const [error, setError] = useState(null)
  // In-Ellis captcha solving: the pause carries captures of the challenge
  // widget and of the full portal page (the application review as the portal
  // shows it). The HUMAN reads and solves; Ellis only transcribes the code.
  const [capImg, setCapImg] = useState(null)
  const [reviewImg, setReviewImg] = useState(null)
  // The live view is owned by the shared hook: one isolated session per case,
  // short-lived URL kept only in component state, auto-refreshed before it
  // expires, and honest about a session that ended.
  const view = usePortalLiveView(client, caseId)
  const [closing, setClosing] = useState(false)
  const handoff = pending?.handoff
  const live = pending?.live_view

  useEffect(() => {
    if (handoff === 'email_verification' || handoff === 'otp') {
      client.mockVerification(caseId).then((r) => setToken(r.token)).catch(() => {})
      // Masked destination only — the full email/phone never renders here.
      client.getContactConfirmation(caseId).then((c) => {
        setMaskedDest(handoff === 'otp' && c.phone_masked ? c.phone_masked
          : (c.email ? c.email.replace(/^(.).*(@.*)$/, '$1•••$2') : null))
      }).catch(() => {})
    }
  }, [handoff, caseId])

  useEffect(() => {
    let alive = true
    const objects = []
    async function load(docId, set) {
      if (!docId) return
      try {
        const r = await client.documentPreviewUrl(caseId, docId)
        if (!r?.url) return
        const res = await fetch(client.base + r.url)
        if (!res.ok) return
        const u = URL.createObjectURL(await res.blob())
        objects.push(u)
        if (alive) set(u)
      } catch { /* the live view remains the fallback */ }
    }
    if (handoff === 'captcha') {
      load(pending?.captcha_document_id, setCapImg)
      load(pending?.review_document_id, setReviewImg)
    }
    return () => { alive = false; objects.forEach((u) => URL.revokeObjectURL(u)) }
  }, [handoff, pending?.captcha_document_id, pending?.review_document_id, caseId])

  async function closeSecureWindow() {
    setClosing(true)
    await view.close()
    setClosing(false)
  }

  async function done() {
    setBusy(true); setError(null)
    try {
      if (handoff === 'email_verification' || handoff === 'otp') {
        await onResolve('verify_email', { token: typedCode.trim() || token })
        setTypedCode('')                        // never retained after use
      } else if (handoff === 'portal_form' || handoff === 'document_upload' ||
                 handoff === 'portal_terms_consent' || handoff === 'credentials' ||
                 handoff === 'portal_verification') {
        // Re-drive: the applicant did the thing only they may do (attached a
        // file, accepted the portal's terms, created the account) and Ellis
        // re-reads the page rather than assuming it worked.
        await onResolve('start')
      } else if (handoff === 'captcha') {
        // The applicant's typed solution rides along (vault-transported,
        // used once): Ellis enters it on the portal and continues. Without a
        // typed code, the applicant solved it in the secure window directly.
        const code = typedCode.trim()
        await onResolve('solve_captcha', code ? { token: code } : undefined)
        setTypedCode('')                        // never retained after use
      } else {
        await onResolve('solve_captcha') // login_challenge/identity share the confirm-only path
      }
      toast('Step completed')
    } catch (e) { setError({ message: e.message }); setBusy(false) }
  }

  const embedded = view.state === 'embedded' && !!view.url
  return (
    <Overlay onClose={onClose} width={embedded ? 1360 : 620}>
      <Head title={title} sub={sub} onClose={onClose} />
      {/* Safety copy stays ABOVE the embedded window for the relevant kinds. */}
      {handoff === 'captcha' && (
        <div style={{ fontSize: 13, marginTop: 12 }} data-testid="captcha-panel">
          <strong>Ellis never solves CAPTCHAs.</strong> Read the code from the
          official page below, type it here, and Ellis enters it, presses Next,
          and continues for you — or solve it directly in the secure window.
          {pending?.captcha_retry && (
            <div style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--crit)',
              marginTop: 6 }} data-testid="captcha-retry-note">
              The code wasn't accepted — type the new code shown below.
            </div>
          )}
          {reviewImg && (
            <div style={{ marginTop: 10 }} data-testid="captcha-review-shot">
              <div className="eyebrow">Your application as the portal shows it</div>
              <div style={{ maxHeight: '32vh', overflowY: 'auto', marginTop: 6,
                border: '1px solid var(--line)', borderRadius: 10 }}>
                <img src={reviewImg} alt="The portal's review of your application"
                  style={{ width: '100%', display: 'block' }} />
              </div>
            </div>
          )}
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 10,
            flexWrap: 'wrap' }}>
            {capImg && (
              <img src={capImg} alt="The portal's CAPTCHA challenge"
                data-testid="captcha-shot"
                style={{ height: 56, border: '1px solid var(--line)', borderRadius: 8 }} />
            )}
            <input className="input" style={{ maxWidth: 220 }}
              inputMode="numeric" autoComplete="off"
              placeholder="Type the code shown" value={typedCode}
              onChange={(e) => setTypedCode(e.target.value)} />
          </div>
        </div>
      )}
      {handoff === 'portal_form' && (
        <div style={{ fontSize: 13, marginTop: 12 }} data-testid="portal-form-items">
          <strong>The government form needs these from you personally:</strong>
          <ul style={{ margin: '6px 0 0 18px' }}>
            {(pending?.portal_messages || ['See the highlighted fields on the form.'])
              .map((m) => <li key={m} style={{ marginTop: 2 }}>{m}</li>)}
          </ul>
          <div style={{ color: 'var(--muted)', marginTop: 6 }}>
            This includes any declaration only you may sign. Complete them in the
            secure window below, then continue — Ellis takes it from there.
          </div>
        </div>
      )}
      {(handoff === 'email_verification' || handoff === 'otp') && (
        <div style={{ fontSize: 13, marginTop: 12 }} data-testid="otp-panel">
          <strong>Enter the verification code sent by the visa portal.</strong>
          {maskedDest && (
            <div style={{ color: 'var(--muted)', marginTop: 4 }}>
              The portal sent it to {maskedDest}.
            </div>
          )}
          <div style={{ color: 'var(--muted)', marginTop: 4 }}>
            Complete the verification in the secure portal window below — that
            is where the official portal accepts the code. If Ellis needs the
            code itself, enter it here too: it is used once for the official
            portal and deleted immediately after use, never stored or logged.
          </div>
          <input className="input" style={{ marginTop: 8, maxWidth: 220 }}
            inputMode="numeric" autoComplete="one-time-code"
            placeholder="Verification code" value={typedCode}
            onChange={(e) => setTypedCode(e.target.value)} />
          {token && !typedCode && (
            <div className="chip" style={{ marginTop: 8 }}>Verification detected</div>
          )}
        </div>
      )}
      <div style={{ fontSize: 11, color: 'var(--muted-2)', marginTop: 10 }}>
        Ellis collects no passwords or CAPTCHA answers and never solves CAPTCHAs.
        One-time codes and payment details are used only when you explicitly
        provide them for the official portal — once, then deleted, never stored.
      </div>

      <div style={{ marginTop: 12 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10 }}>
          <div className="eyebrow">{t('live.embedded')}</div>
          <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
            <button className="btn btn--sm btn--ghost" disabled={view.busy} onClick={view.reconnect}>
              {view.busy ? '…' : t('live.refresh')}
            </button>
            {embedded && (
              <button className="btn btn--sm btn--ghost" disabled={closing} onClick={closeSecureWindow}>
                {closing ? '…' : t('live.closeWindow')}
              </button>
            )}
          </div>
        </div>
        <LiveFrame view={view} />
      </div>

      {error && <ErrorNote error={error} />}
      <div className="modal__foot">
        <button className="btn btn--ghost" onClick={onClose}>Not now</button>
        <button className="btn" disabled={busy} onClick={done}>
          {busy ? 'Confirming…'
            : handoff === 'captcha' ? (typedCode.trim()
              ? 'Enter my code on the portal and continue'
              : 'CAPTCHA completed — continue')
            : handoff === 'portal_form' ? 'I finished these items — continue'
            : (handoff === 'email_verification' || handoff === 'otp') ? 'Verify code and continue'
            : 'I completed this step'}
        </button>
      </div>
    </Overlay>
  )
}

// ---- Payment approval (handoffs: payment_approval / fee_confirmation) ------
// payment_approval: the portal displayed a machine-readable fee — the
// applicant confirms that EXACT amount. fee_confirmation: the portal shows
// the fee only on its own pages — the applicant reads it there (secure
// window) and enters the exact amount + currency; Ellis never invents one.
// Exact-amount parsing: the typed value must be unambiguous. parseFloat would
// silently truncate locale formats ('680.000' Vietnamese = 680,000 -> 680;
// '35,000' -> 35) — a wrong recorded fee. Only plain amounts are accepted:
// digits with an optional 1-2 digit decimal part.
export function parseExactAmountCents(text) {
  const s = String(text || '').trim().replace(/\s/g, '')
  if (!/^\d+([.,]\d{1,2})?$/.test(s)) return null
  const normalized = s.replace(',', '.')
  return Math.round(parseFloat(normalized) * 100)
}

const FEE_CURRENCIES = ['USD', 'VND', 'EUR', 'CNY', 'KRW', 'GBP', 'JPY', 'SGD',
  'THB', 'INR', 'IDR', 'PHP', 'AUD', 'CAD', 'MYR', 'HKD', 'TWD', 'AED']

export function PaymentApprove({ client, caseId, pending, onResolve, onClose }) {
  const t = useT()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [amount, setAmount] = useState('')
  const fee = pending?.fee || {}
  const ctx = pending?.fee_context || {}
  // Default the currency to the route's known official fee currency — the
  // applicant confirms it rather than remembering to switch from USD.
  const [currency, setCurrency] = useState((ctx.currency || 'USD').toUpperCase())
  const currencyOptions = FEE_CURRENCIES.includes((ctx.currency || '').toUpperCase()) ||
    !ctx.currency ? FEE_CURRENCIES : [ctx.currency.toUpperCase(), ...FEE_CURRENCIES]
  const confirmMode = pending?.handoff === 'fee_confirmation' || fee.amount == null
  const expected = ctx.available && ctx.total_cents != null
    ? `${(ctx.total_cents / 100).toFixed(2)} ${ctx.currency || ''}` : null
  const rows = [
    fee.government_fee_cents != null &&
      { label: t('pay.governmentFee'), value: `${(fee.government_fee_cents / 100).toFixed(2)} ${fee.currency || ''}` },
    fee.service_fee_cents != null && fee.service_fee_cents > 0 &&
      { label: t('pay.serviceFee'), value: `${(fee.service_fee_cents / 100).toFixed(2)} ${fee.currency || ''}` },
    ctx.available && ctx.service_fee_cents === 0 && fee.service_fee_cents == null &&
      { label: t('pay.serviceFee'), value: t('pay.noServiceFee') },
    { label: t('pay.payee'), value: fee.payee || pending?.payee || t('pay.officialPortal') },
    { label: t('pay.refundability'),
      value: fee.refundability || ctx.refundability || t('pay.refundUnknown') },
    Array.isArray(ctx.payment_methods) && ctx.payment_methods.length > 0 &&
      { label: t('pay.methods'), value: ctx.payment_methods.join(' · ') },
    (fee.source_url || ctx.source_url) &&
      { label: t('pay.feeSource'), value: fee.source_url || ctx.source_url }
  ].filter(Boolean)
  const parsedCents = parseExactAmountCents(amount)
  // In confirm mode the applicant must READ the fee off the official portal —
  // so show them the portal, in the case's own isolated session.
  const view = usePortalLiveView(client, caseId)
  const showPortal = confirmMode && !!client && !!caseId
  const [reading, setReading] = useState(false)
  const autoReadRef = useRef(false)

  // On-demand fee read: once the applicant has walked the portal to the step
  // that displays the amount, Ellis reads it off that page and the dialog
  // swaps to a simple approval (via the progress poll in CaseFlow).
  async function readFeeNow() {
    setReading(true)
    try {
      await client.readPortalFee(caseId)
    } catch (e) {
      setError({ message: (e.detail && e.detail.message) || e.message })
      setReading(false)
      return
    }
    for (let i = 0; i < 20; i++) {
      await new Promise((r) => setTimeout(r, 3000))
      try {
        const pr = await client.caseProgress(caseId)
        if (pr.run_signal !== 'read_fee' || (!pr.active && !pr.queued)) break
      } catch { break }
    }
    setReading(false)
  }

  // Self-healing on open: a fee-confirmation pause recorded while the live
  // page was really elsewhere (unanswered questions, the declaration, a
  // CAPTCHA) reclassifies itself the moment this dialog opens — the read-fee
  // run re-reads the page and the progress poll swaps this dialog for the
  // real ask. Once per open; the manual button stays for later re-reads.
  useEffect(() => {
    if (confirmMode && !autoReadRef.current) {
      autoReadRef.current = true
      readFeeNow()
    }
  }, [confirmMode])

  return (
    <Overlay onClose={onClose}
      width={showPortal && view.state === 'embedded' ? 1360 : 520}>
      <Head title={confirmMode ? t('pay.feeConfirmTitle') : t('pay.confirmTitle')}
        onClose={onClose}
        sub={confirmMode ? t('pay.feeConfirmSub') : t('pay.confirmSub')} />
      {!confirmMode && (
        <>
          <div className="stat" style={{ marginTop: 12 }}>
            <div className="stat__num">{formatFee(fee) || '—'}</div>
            <div className="stat__cap">{t('pay.exactAmount')}</div>
          </div>
          {fee.source === 'portal_page_read' && (
            <div className="card card--soft" style={{ padding: '10px 12px', marginTop: 8,
              fontSize: 12.5 }} data-testid="fee-read-banner">
              {t('pay.feeReadFromPortal', { fee: formatFee(fee) })}
            </div>
          )}
        </>
      )}
      {confirmMode && (
        <div style={{ marginTop: 12 }} data-testid="fee-confirm-entry">
          {/* Honesty: say whether this figure comes from an official source or
              from nowhere at all. Ellis did not read it off the portal. */}
          <div className="card card--soft" style={{ padding: '10px 12px', marginBottom: 10,
            fontSize: 12.5 }}>
            {expected
              ? t('pay.expectedFee', { fee: expected })
              : t('pay.noFeeRead')}
            {view.restoring && (
              <div style={{ marginTop: 6 }} data-testid="fee-reading">
                {t('pay.readingFee')}
              </div>
            )}
          </div>
        </div>
      )}
      {confirmMode && (
        <div style={{ marginTop: 4 }}>
          <div style={{ display: 'flex', gap: 8 }}>
            <div className="field" style={{ flex: 1 }}>
              <label>{t('pay.amountShown')}</label>
              {/* No example amount in the placeholder: a number here reads as
                  "the fee", and Ellis has not read one from the portal. */}
              <input className="input" inputMode="decimal" value={amount}
                placeholder={t('pay.amountPlaceholder')}
                onChange={(e) => setAmount(e.target.value)} />
            </div>
            <div className="field" style={{ width: 110 }}>
              <label>{t('pay.currency')}</label>
              <select className="select" value={currency}
                onChange={(e) => setCurrency(e.target.value)}>
                {currencyOptions.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
          </div>
          {amount.trim() !== '' && parsedCents == null && (
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--crit)' }}>
              {t('pay.amountUnclear')}
            </div>
          )}
          {parsedCents != null && parsedCents > 0 && (
            <div style={{ fontSize: 12.5, color: 'var(--muted)' }} data-testid="fee-echo">
              {t('pay.amountEcho', { fee: `${(parsedCents / 100).toFixed(2)} ${currency}` })}
            </div>
          )}
        </div>
      )}
      <KVList fields={rows} />
      {showPortal && (
        <div style={{ marginTop: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10 }}>
            <div className="eyebrow">{t('pay.readOnPortal')}</div>
            <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
              <button className="btn btn--sm" disabled={reading} onClick={readFeeNow}
                data-testid="read-fee-now">
                {reading ? t('pay.readingNow') : t('pay.readFeeCta')}
              </button>
              <button className="btn btn--sm btn--ghost" disabled={view.busy} onClick={view.reconnect}>
                {view.busy ? '…' : t('live.refresh')}
              </button>
            </div>
          </div>
          <div style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 6 }}>
            {t('pay.walkToFee')}
          </div>
          <LiveFrame view={view} height="66vh" />
        </div>
      )}
      <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 10 }}>
        {t('pay.exactNote')}
      </div>
      {error && <ErrorNote error={error} />}
      <div className="modal__foot">
        <button className="btn btn--ghost" onClick={onClose}>{t('pay.cancel')}</button>
        <button className="btn"
          disabled={busy || (confirmMode ? !(parsedCents != null && parsedCents > 0)
                                        : fee.amount == null)}
          onClick={async () => {
            setBusy(true); setError(null)
            try {
              // Echo the EXACT amount (shown by Ellis, or read by the
              // applicant from the official portal) so a stale display can
              // never approve a different figure — a mismatch is refused (§6).
              await onResolve('approve_payment', confirmMode
                ? { amount_cents: parsedCents, currency }
                : { amount_cents: fee.amount, currency: fee.currency })
            } catch (e) { setError({ message: e.message }); setBusy(false) }
          }}>
          {busy ? t('pay.confirming') : t('pay.confirmCta')}
        </button>
      </div>
    </Overlay>
  )
}

// ---- Applicant-provided payment details (handoff: payment_credentials) -----
// The applicant gives Ellis their card details IN ELLIS; Ellis fills them on
// the official portal's own payment form. The details are sent once, travel
// as a one-time vault reference, and are never stored or logged. Ellis never
// clicks Pay — the filled page comes back to the applicant (payment handoff)
// for their own confirmation click in the secure window.
export function PaymentDetailsModal({ client, caseId, pending, onResolve, onClose }) {
  const t = useT()
  const [busy, setBusy] = useState('')      // 'fill' | 'manual'
  const [error, setError] = useState(null)
  const [errors, setErrors] = useState({})
  const [holder, setHolder] = useState('')
  const [number, setNumber] = useState('')
  const [expiry, setExpiry] = useState('')
  const [cvv, setCvv] = useState('')
  const fee = pending?.fee

  function luhnOk(digits) {
    let sum = 0
    for (let i = 0; i < digits.length; i++) {
      let d = digits.charCodeAt(digits.length - 1 - i) - 48
      if (i % 2 === 1) { d *= 2; if (d > 9) d -= 9 }
      sum += d
    }
    return sum % 10 === 0
  }

  function validate() {
    const errs = {}
    if (!holder.trim()) errs.holder = t('paydetails.errHolder')
    const digits = number.replace(/[\s-]/g, '')
    if (!/^\d{12,19}$/.test(digits) || !luhnOk(digits)) errs.number = t('paydetails.errNumber')
    if (!/^(0?[1-9]|1[0-2])\s*\/\s*(\d{2}|\d{4})$/.test(expiry.trim())) errs.expiry = t('paydetails.errExpiry')
    if (!/^\d{3,4}$/.test(cvv.trim())) errs.cvv = t('paydetails.errCvv')
    setErrors(errs)
    return Object.keys(errs).length === 0
  }

  async function submit() {
    if (!validate()) return
    setBusy('fill'); setError(null)
    try {
      await onResolve('provide_payment_details', {
        card: { holder: holder.trim(), number: number.replace(/[\s-]/g, ''),
                expiry: expiry.trim(), cvv: cvv.trim() }
      })
      // Never retained after the send — the backend vaults, uses once, destroys.
      setNumber(''); setCvv(''); setExpiry(''); setHolder('')
    } catch (e) {
      const d = e && typeof e.detail === 'object' ? e.detail : null
      if (d && d.reason === 'invalid_payment_details' && d.key) {
        // Localize known validation keys; the raw server message is the
        // fallback for anything unmapped.
        const known = { holder: 'errHolder', number: 'errNumber',
                        expiry: 'errExpiry', cvv: 'errCvv' }[d.key]
        setErrors({ [d.key]: known ? t('paydetails.' + known) : d.message })
      } else {
        setError({ message: (d && d.message) || e.message })
      }
      setBusy('')
    }
  }

  async function manual() {
    setBusy('manual'); setError(null)
    try { await onResolve('provide_payment_details', { manual: true }) }
    catch (e) { setError({ message: e.message }); setBusy('') }
  }

  const field = (label, value, set, key, props = {}) => (
    <div className="field" style={{ marginBottom: 10 }}>
      <label>{label}</label>
      <input className="input" value={value} autoComplete="off"
        onChange={(e) => { set(e.target.value); setErrors((p) => (p[key] ? { ...p, [key]: null } : p)) }}
        {...props} />
      {errors[key] && (
        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--crit)' }}>{errors[key]}</div>
      )}
    </div>
  )

  return (
    <Overlay onClose={onClose} width={client && caseId ? 1200 : 520}>
      <Head title={t('paydetails.title')} onClose={onClose} sub={t('paydetails.sub')} />
      {/* The live payment page: the applicant sees where their details are
          about to go, and watches Ellis fill the official form. */}
      <PortalWatch client={client} caseId={caseId} height="34vh" />
      <div className="stat" style={{ marginTop: 4 }}>
        <div className="stat__num">{formatFee(fee) || '—'}</div>
        <div className="stat__cap">{t('paydetails.amountCap')}</div>
      </div>
      <div style={{ marginTop: 12 }}>
        {field(t('paydetails.holder'), holder, setHolder, 'holder',
          { placeholder: t('paydetails.holderPh') })}
        {field(t('paydetails.number'), number, setNumber, 'number',
          { inputMode: 'numeric', autoComplete: 'off', placeholder: '4111 1111 1111 1111' })}
        <div style={{ display: 'flex', gap: 8 }}>
          <div style={{ flex: 1 }}>
            {field(t('paydetails.expiry'), expiry, setExpiry, 'expiry',
              { inputMode: 'numeric', placeholder: 'MM/YY' })}
          </div>
          <div style={{ width: 140 }}>
            {field(t('paydetails.cvv'), cvv, setCvv, 'cvv',
              { inputMode: 'numeric', type: 'password', maxLength: 4, placeholder: '•••' })}
          </div>
        </div>
      </div>
      <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 4 }}>
        {t('paydetails.privacy')}
      </div>
      {error && <ErrorNote error={error} />}
      <div className="modal__foot">
        <button className="btn btn--ghost" disabled={!!busy} onClick={manual}
          data-testid="payment-manual">
          {busy === 'manual' ? '…' : t('paydetails.manualCta')}
        </button>
        <button className="btn" disabled={!!busy} onClick={submit}
          data-testid="payment-fill">
          {busy === 'fill' ? t('paydetails.filling') : t('paydetails.fillCta')}
        </button>
      </div>
    </Overlay>
  )
}

// ---- Applicant-confirmed payment (handoff: payment / three_ds) --------------
export function PaymentModal({ client, caseId, pending, onResolve, onProvideDetails,
                               onClose, needsApproval = false }) {
  const toast = useToast()
  const t = useT()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  // The payment is confirmed ON the official portal, inside the case's own
  // isolated session, embedded here — no detour to a separate browser. When
  // Ellis pre-filled the applicant's details (payment_prefilled), the
  // applicant reviews them on the page and clicks the portal's own confirm;
  // otherwise they enter the card in the window personally. payment_fillable
  // routes offer the card ask (again) — a rebuilt session shows an empty
  // form, and a declined attempt may need a different card.
  const view = usePortalLiveView(client, caseId)
  const fee = pending?.fee
  const feeLabel = formatFee(fee)
  const prefilled = !!pending?.payment_prefilled
  const fillable = !!pending?.payment_fillable
  const embedded = view.state === 'embedded' && !!view.url
  return (
    <Overlay onClose={onClose} width={embedded ? 1360 : 620}>
      <Head title={feeLabel ? t('paywin.payTitle', { fee: feeLabel })
                            : (prefilled ? t('paywin.titlePrefilled') : t('paywin.title'))}
            onClose={onClose}
            sub={prefilled ? t('paywin.subPrefilled') : t('paywin.sub')} />
      <div className="stat" style={{ marginTop: 4 }}>
        <div className="stat__num">{feeLabel || '—'}</div>
        <div className="stat__cap">{t('paywin.amountCap')}</div>
      </div>
      {/* Where the amount came from — the portal's own page, or the route's
          published official schedule. Never presented as more than it is. */}
      {fee?.source === 'verified_official_fee_record' && (
        <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: -2 }}
          data-testid="fee-source-note">
          {t('paywin.feeFromSchedule')}
        </div>
      )}
      {prefilled && (
        <div className="card card--soft" style={{ padding: '10px 12px', marginTop: 10,
          fontSize: 12.5 }} data-testid="payment-prefilled-note">
          {t('paywin.prefilledNote')}
        </div>
      )}
      <div style={{ marginTop: 12 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10 }}>
          <div className="eyebrow">{t('live.embedded')}</div>
          <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
            {fillable && onProvideDetails && (
              <button className="btn btn--sm btn--ghost" onClick={onProvideDetails}
                data-testid="provide-details-again">
                {t('paywin.provideDetails')}
              </button>
            )}
            <button className="btn btn--sm btn--ghost" disabled={view.busy} onClick={view.reconnect}>
              {view.busy ? '…' : t('live.refresh')}
            </button>
          </div>
        </div>
        <LiveFrame view={view} />
      </div>
      <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 10 }}>
        {t('paywin.reconcileNote')}
      </div>
      {error && <ErrorNote error={error} />}
      <div className="modal__foot">
        <button className="btn btn--ghost" onClick={onClose}>{t('paywin.later')}</button>
        <button className="btn" disabled={busy} onClick={async () => {
          setBusy(true); setError(null)
          try {
            // The applicant paid on the portal's own page in the window above.
            // Pressing this IS their confirmation of the exact amount shown —
            // it records the exact-amount authorization (approve_payment) and
            // then reports the payment (complete_payment), so the two are one
            // action rather than two screens. Ellis still never clicks Pay:
            // the money moved by the applicant's own hand on the portal.
            if (needsApproval) {
              if (!fee?.amount || !fee?.currency) {
                throw new Error('The fee amount is not available yet — reopen this step.')
              }
              await onResolve('approve_payment',
                { amount_cents: fee.amount, currency: fee.currency })
            }
            await onResolve('complete_payment')
            toast(t('paywin.checking'))
          } catch (e) { setError({ message: e.message }); setBusy(false) }
        }}>{busy ? t('paywin.checking') : t('paywin.paidCta')}</button>
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
// The declaration is always MADE by the applicant, here in Ellis, against the
// portal's own verbatim statement when the run read one off the form
// (pending.portal_messages). Ellis then transcribes the recorded declaration
// onto the official form and clicks the portal's Next — it never declares on
// its own initiative.
export function DeclarationModal({ client, caseId, pending, onResolve, onClose }) {
  const [agree, setAgree] = useState(false)
  const [busy, setBusy] = useState(false)
  const portalStatements = pending?.portal_messages || []
  return (
    <Overlay onClose={onClose} width={client && caseId ? 1360 : 620}>
      <Head title="Make the official declaration" onClose={onClose}
            sub="Only you can make this declaration. Ellis records it, marks it on the official form for you, and continues to the next step — it never declares on its own." />
      {portalStatements.length > 0 && (
        <div className="card card--soft" style={{ padding: 14, fontSize: 13, marginTop: 8 }}
          data-testid="portal-declaration-text">
          <div className="eyebrow">
            {portalStatements.length > 1 ? "The official form's declarations"
              : "The official form's declaration"}
          </div>
          {portalStatements.map((s) => (
            <div key={s} style={{ marginTop: 6, whiteSpace: 'pre-wrap' }}>{s}</div>
          ))}
        </div>
      )}
      <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', margin: '10px 0', fontSize: 13 }}>
        <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
        <span>
          {portalStatements.length > 0
            ? 'I personally make the declaration shown above, exactly as the official form states it.'
            : 'I personally declare that the information in this application is true and complete, under penalty of perjury.'}
        </span>
      </label>
      {/* The live page: the applicant sees the declaration on the official
          form, and watches Ellis mark it after they declare. */}
      <PortalWatch client={client} caseId={caseId} height="34vh" />
      <div className="modal__foot">
        <button className="btn btn--ghost" onClick={onClose}>Cancel</button>
        <button className="btn" disabled={!agree || busy} onClick={async () => { setBusy(true); await onResolve('complete_declaration') }}>
          {busy ? 'Recording…' : 'I personally declare'}
        </button>
      </div>
    </Overlay>
  )
}

// ---- Dynamic missing information (handoff: additional_information) ---------
// Mid-flight the official form needed a detail Ellis never collected. The
// modal shows the portal's questions in applicant-friendly wording only —
// never selectors, node ids, or developer terminology. Typed answers go back
// via the provide_information signal and the SAME application resumes where
// it paused. Document-kind questions are resolved on the Documents tab first;
// when there is nothing to type, Continue re-drives the case the same way the
// final-review handoff resumes (the Journey tab's start).
export function AdditionalInfoModal({ client, caseId, checklist, pending, onResolve,
                                      onGoToDocuments, onContinueWithoutAnswers, onClose }) {
  const t = useT()
  const { inputQuestions, documentQuestions } = splitQuestions(pending?.questions)
  // A re-asked question (the portal rejected the stored value, or its list
  // changed) starts from the applicant's PREVIOUS answer — never a blank
  // field that reads as "Ellis forgot what I already told it".
  const [values, setValues] = useState(() => Object.fromEntries(
    (pending?.questions || [])
      .filter((q) => q.key && q.previous_answer != null && q.previous_answer !== '')
      .map((q) => [q.key, String(q.previous_answer)])))
  const [errors, setErrors] = useState({})     // question key -> display message
  const [error, setError] = useState(null)     // non-field backend rejection
  const [busy, setBusy] = useState(false)
  const [uploads, setUploads] = useState({})   // doc question key -> 'busy'|'done'|error

  function set(key, v) {
    setValues((p) => ({ ...p, [key]: v }))
    setErrors((p) => (p[key] ? { ...p, [key]: null } : p))
  }

  // The checklist requirement a document question maps to ('document:photo'
  // -> the item whose id or satisfied_by covers 'photo').
  function checklistItemFor(docType) {
    const list = Array.isArray(checklist) ? checklist : []
    return list.find((i) => i.id === docType) ||
      list.find((i) => Array.isArray(i.satisfied_by) && i.satisfied_by.includes(docType)) ||
      null
  }

  // Inline fulfilment: upload + bind + explicit submit in one step, right in
  // the question dialog — no detour through the Documents tab.
  async function uploadFor(q, file) {
    if (!file || !client) return
    if (!ALLOWED[file.type]) {
      setUploads((p) => ({ ...p, [q.key]: t('checklist.unsupportedType') })); return
    }
    if (file.size > MAX_BYTES) {
      setUploads((p) => ({ ...p, [q.key]: t('checklist.tooLarge') })); return
    }
    setUploads((p) => ({ ...p, [q.key]: 'busy' }))
    try {
      const docType = (q.key || '').split(':')[1] || ''
      const item = checklistItemFor(docType)
      const b64 = await readAsBase64(file)
      const res = await client.addDocument(caseId, {
        name: file.name, mime: file.type, size_bytes: file.size,
        content_b64: b64, ...(item ? { checklist_item_id: item.id } : {})
      })
      if (res && res.rejected) throw new Error(res.message || t('checklist.unreadableToast'))
      if (item && res && res.id) {
        await client.submitChecklistDoc(caseId, item.id, res.id, true)
      }
      setUploads((p) => ({ ...p, [q.key]: 'done' }))
    } catch (e) {
      setUploads((p) => ({
        ...p, [q.key]: (e.detail && e.detail.message) || e.message || 'upload failed'
      }))
    }
  }

  // The dialog can hold a dozen questions: a problem field may be scrolled
  // far out of view, so a silent early-return would read as a dead button.
  // Always scroll to the first problem AND summarize next to the button.
  function showFieldErrors(errs) {
    setErrors(errs)
    const keys = Object.keys(errs)
    setError({ message: t('addinfo.errSummary', { count: keys.length }) })
    const el = document.getElementById('aiq-' + keys[0])
    if (el && el.scrollIntoView) el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

  async function submit() {
    setError(null)
    const { answers, errors: errs } = collectAnswers(inputQuestions, values)
    if (Object.keys(errs).length > 0) {
      showFieldErrors(Object.fromEntries(
        Object.entries(errs).map(([k, key]) => [k, t(key)])))
      return
    }
    if (Object.keys(answers).length === 0) {
      // Document-only ask (or nothing typed): nothing to send. Close and let
      // the case re-drive from the Journey tab — never an empty signal.
      if (onContinueWithoutAnswers) await onContinueWithoutAnswers()
      else onClose()
      return
    }
    setBusy(true)
    try { await onResolve('provide_information', { answers }) }
    catch (e) {
      // 422 invalid_answer carries {key, message} — show it inline at the
      // exact question (scrolled into view); anything else is a general note.
      const d = e && typeof e.detail === 'object' ? e.detail : null
      if (d && d.reason === 'invalid_answer' && d.key && d.message) {
        showFieldErrors({ [d.key]: d.message })
      } else {
        setError({ message: (d && d.message) || e.message })
      }
      setBusy(false)
    }
  }

  return (
    // No portal preview here on purpose: this dialog is a form to fill in,
    // not a wait — an embedded live window is noise while typing answers.
    <Overlay onClose={onClose}>
      <Head title={t('addinfo.title')} onClose={onClose} sub={t('addinfo.sub')} />
      {inputQuestions.map((q) => (
        <div key={q.key} id={'aiq-' + q.key} className="field" style={{ marginBottom: 12 }}>
          <label>
            {q.question}{q.mandatory === false ? ` ${t('addinfo.optional')}` : ''}
          </label>
          {q.why && (
            <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: -2 }}>{q.why}</div>
          )}
          {q.kind === 'select' && Array.isArray(q.options) && q.options.length > 0 ? (
            q.options_partial ? (
              // A PARTIAL read of the portal's list is suggestions, never the
              // menu: a hard dropdown here trapped a traveller whose answer
              // sat past the rows Ellis managed to read (2026-08-04,
              // Singapore's place of residence). Type anything; what is known
              // autocompletes; the fill step verifies against the live list.
              <>
                <input className="input" list={`aiq-list-${q.key}`}
                  value={values[q.key] || ''}
                  placeholder={t('addinfo.partialPlaceholder')}
                  onChange={(e) => set(q.key, e.target.value)} />
                <datalist id={`aiq-list-${q.key}`}>
                  {q.options.map((o) => <option key={String(o)} value={String(o)} />)}
                </datalist>
              </>
            ) : (
            <select className="select" value={values[q.key] || ''}
                    onChange={(e) => set(q.key, e.target.value)}>
              <option value="">{t('addinfo.selectPlaceholder')}</option>
              {q.options.map((o) => <option key={String(o)} value={String(o)}>{String(o)}</option>)}
            </select>
            )
          ) : (
            <input className="input" value={values[q.key] || ''}
                   placeholder={q.kind === 'date' ? 'MM/DD/YYYY' : (q.format || '')}
                   onChange={(e) => set(q.key, e.target.value)} />
          )}
          {errors[q.key] && (
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--crit)' }}>{errors[q.key]}</div>
          )}
        </div>
      ))}
      {inputQuestions.some((q) => q.deferred_followups) && (
        <div style={{ fontSize: 12.5, color: 'var(--muted)', margin: '2px 0 10px' }}
          data-testid="deferred-note">
          {t('addinfo.deferredNote')}
        </div>
      )}
      {documentQuestions.length > 0 && (
        <div className="card card--soft" style={{ padding: 14, marginTop: 4 }}>
          <div className="eyebrow">{t('addinfo.docsTitle')}</div>
          {documentQuestions.map((q) => (
            <div key={q.key} style={{ marginTop: 8 }} data-testid={`doc-question-${q.key}`}>
              <div style={{ fontSize: 13.5, fontWeight: 600 }}>{q.question}</div>
              {q.why && (
                <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 2 }}>{q.why}</div>
              )}
              {uploads[q.key] === 'done' ? (
                <span className="chip chip--ink" style={{ marginTop: 8 }}>
                  {t('addinfo.uploadedHere')}
                </span>
              ) : (
                <div style={{ marginTop: 8 }}>
                  <label className="btn btn--sm" style={{ cursor: 'pointer' }}>
                    {uploads[q.key] === 'busy' ? t('addinfo.uploading') : t('addinfo.uploadHere')}
                    <input type="file" accept=".pdf,.jpg,.jpeg,.png,.tiff"
                      style={{ display: 'none' }}
                      disabled={uploads[q.key] === 'busy'}
                      onChange={(e) => uploadFor(q, e.target.files && e.target.files[0])} />
                  </label>
                  {uploads[q.key] && uploads[q.key] !== 'busy' && uploads[q.key] !== 'done' && (
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--crit)', marginTop: 4 }}>
                      {uploads[q.key]}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
          <div style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 10 }}>
            {t('addinfo.docsNote')}
          </div>
          {onGoToDocuments && (
            <button className="btn btn--sm btn--ghost" style={{ marginTop: 10 }} onClick={onGoToDocuments}>
              {t('addinfo.docsCta')}
            </button>
          )}
        </div>
      )}
      {error && <ErrorNote error={error} />}
      <div className="modal__foot">
        <button className="btn btn--ghost" onClick={onClose}>{t('addinfo.later')}</button>
        <button className="btn" disabled={busy} onClick={submit}>
          {busy ? t('addinfo.submitting') : t('addinfo.submit')}
        </button>
      </div>
    </Overlay>
  )
}

// (StandingAuthModal removed 2026-07-28: the standing authorization is granted
// only inside the signature ceremony — SignatureModal — never by a checkbox.)

// ---- Final review + exact-version signature (brief §7) ---------------------
// The applicant reviews the complete final package, then signs that EXACT
// version (content hash echoed). Any later material change invalidates it.
export function FinalReviewModal({ client, caseId, locale = 'en', onDone, onClose }) {
  const t = useT()
  const [review, setReview] = useState(null)   // created review version + step-up token
  const [consent, setConsent] = useState(false)
  const [intent, setIntent] = useState(false)
  const [signature, setSignature] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  useEffect(() => {
    client.createFinalReview(caseId, locale)
      .then(setReview).catch((e) => setError({ message: e.message }))
  }, [caseId, locale])
  const pkg = review?.package || {}
  const answerRows = Object.entries(pkg.answers || {})
    .map(([label, value]) => ({ label, value: String(value) }))
  const docRows = (pkg.documents || [])
    .map((d) => ({ label: d.name, value: d.approved ? t('final.docApproved') : t('final.docPending') }))
  return (
    <Overlay onClose={onClose}>
      <Head title={t('final.title')} onClose={onClose} sub={t('final.sub')} />
      {!review && !error && <Loading label={t('final.loading')} />}
      {review && (
        <div style={{ maxHeight: '46vh', overflowY: 'auto' }}>
          <div className="eyebrow">{t('final.travel')}</div>
          <KVList fields={[
            { label: t('final.applicant'), value: pkg.applicant?.full_name || '' },
            { label: t('final.destination'), value: pkg.travel?.destination || '' },
            { label: t('final.visaType'), value: pkg.travel?.visa_type || '' },
            { label: t('final.portal'), value: pkg.portal || '' },
            pkg.fees?.available !== false && pkg.fees?.amount != null &&
              { label: t('final.fee'), value: `${(pkg.fees.amount / 100).toFixed(2)} ${pkg.fees.currency || ''}` },
            pkg.appointment &&
              { label: t('final.appointment'), value: pkg.appointment.confirmation_no || pkg.appointment.slot_id }
          ].filter(Boolean)} />
          <div className="eyebrow" style={{ marginTop: 10 }}>{t('final.answers')}</div>
          <KVList fields={answerRows} />
          {docRows.length > 0 && (
            <>
              <div className="eyebrow" style={{ marginTop: 10 }}>{t('final.documents')}</div>
              <KVList fields={docRows} />
            </>
          )}
          <div style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 8 }}>
            {t('final.versionLine', { version: review.version, hash: (review.content_hash || '').slice(0, 12) })}
          </div>
        </div>
      )}
      {review && (
        <>
          <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', margin: '10px 0 4px', fontSize: 13 }}>
            <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
            <span>{t('final.consent')}</span>
          </label>
          <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', margin: '4px 0', fontSize: 13 }}>
            <input type="checkbox" checked={intent} onChange={(e) => setIntent(e.target.checked)} />
            <span>{t('final.intent')}</span>
          </label>
          <input className="input" style={{ marginTop: 6 }} placeholder={t('final.typedSignature')}
                 value={signature} onChange={(e) => setSignature(e.target.value)} />
        </>
      )}
      {error && <ErrorNote error={error} />}
      <div className="modal__foot">
        <button className="btn btn--ghost" onClick={onClose}>{t('final.later')}</button>
        <button className="btn" disabled={!review || !consent || !intent || !signature.trim() || busy}
                onClick={async () => {
          setBusy(true); setError(null)
          try {
            await client.signFinalReview(caseId, {
              review_version_id: review.id,
              content_hash: review.content_hash,
              consent_given: consent, intent_confirmed: intent,
              signature_method: 'typed', signature_value: signature.trim(),
              step_up_token: review.step_up_token
            })
            onDone && onDone()
          } catch (e) { setError({ message: e.message }); setBusy(false) }
        }}>{busy ? t('final.signing') : t('final.signCta')}</button>
      </div>
    </Overlay>
  )
}
