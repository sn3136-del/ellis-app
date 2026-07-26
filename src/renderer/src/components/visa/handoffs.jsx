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
import { useT } from '../../lib/locale.jsx'
import { formatFee, formatSlot, splitQuestions, collectAnswers } from '../../lib/visaSession.js'
import { ALLOWED, MAX_BYTES, readAsBase64 } from './Checklist.jsx'

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
  const [continuing, setContinuing] = useState(false)
  const [continued, setContinued] = useState(false)
  const [contact, setContact] = useState(null)   // {email, phone_masked, confirmed}
  const [editingContact, setEditingContact] = useState(false)
  const [contactEmail, setContactEmail] = useState('')
  const [contactPhone, setContactPhone] = useState('')
  const canvasRef = useRef(null)
  const drawnRef = useRef('')

  // The portal may send verification codes: the applicant confirms (or edits)
  // the email + masked phone BEFORE Ellis opens the official portal.
  useEffect(() => {
    let live = true
    client.getContactConfirmation(caseId)
      .then((c) => { if (live) setContact(c) })
      .catch(() => { if (live) setContact({ missing_endpoint: true, confirmed: true }) })
    return () => { live = false }
  }, [caseId])

  async function saveContact(confirm) {
    try {
      const body = { confirm: !!confirm }
      if (editingContact) {
        if (contactEmail.trim()) body.email = contactEmail.trim()
        if (contactPhone.trim()) body.phone = contactPhone.trim()
      }
      const c = await client.confirmContact(caseId, body)
      setContact(c)
      setEditingContact(false)
      if (confirm) toast('Contact details confirmed')
    } catch (e) {
      setError({ message: (e.detail && e.detail.message) || e.message })
    }
  }

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
          <div style={{ fontWeight: 700 }}>Your authorization is signed</div>
          <div style={{ fontSize: 12.5, color: 'var(--muted)', margin: '4px 0 8px' }}>
            Ellis can now help complete the official form. CAPTCHA, verification
            codes, payment approval and the final submission always stay with you.
          </div>
          <KVList fields={[
            { label: 'Signature ID', value: signed.signature_id },
            { label: 'Signed at', value: signed.signed_at }
          ]} />
          {contact && !contact.missing_endpoint && (
            <div className="card card--soft" style={{ padding: 12, marginTop: 10 }}
              data-testid="contact-confirm">
              <div className="eyebrow">Where the portal can reach you</div>
              <div style={{ fontSize: 13, marginTop: 4 }}>
                Verification codes from the official portal will go to:
              </div>
              {!editingContact ? (
                <>
                  <KVList fields={[
                    { label: 'Email', value: contact.email || '— add an email —' },
                    { label: 'Phone', value: contact.phone_masked || '— add a phone number —' }
                  ]} />
                  <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                    {!contact.confirmed && (
                      <button className="btn btn--sm" disabled={!contact.has_email}
                        onClick={() => saveContact(true)}
                        data-testid="confirm-contact">These are correct</button>
                    )}
                    {contact.confirmed && <span className="chip chip--ink">Confirmed</span>}
                    <button className="btn btn--sm btn--ghost"
                      onClick={() => { setEditingContact(true); setContactEmail(contact.email || ''); setContactPhone('') }}>
                      Edit
                    </button>
                  </div>
                </>
              ) : (
                <div style={{ marginTop: 8 }}>
                  <div className="field"><label>Email</label>
                    <input className="input" value={contactEmail}
                      onChange={(e) => setContactEmail(e.target.value)} /></div>
                  <div className="field"><label>Phone (with country code)</label>
                    <input className="input" value={contactPhone} placeholder="+86 …"
                      onChange={(e) => setContactPhone(e.target.value)} /></div>
                  <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn--sm" onClick={() => saveContact(true)}>Save and confirm</button>
                    <button className="btn btn--sm btn--ghost" onClick={() => setEditingContact(false)}>Cancel</button>
                  </div>
                </div>
              )}
            </div>
          )}
          {error && <ErrorNote error={error} />}
          <div className="modal__foot">
            {signed.download && <button className="btn btn--ghost" onClick={() => window.ellis?.openExternal?.(client.base ? client.base + signed.download : signed.download)}>Download signed PDF</button>}
            <button className="btn" data-testid="continue-to-portal"
              disabled={continuing || continued || (contact && !contact.missing_endpoint && !contact.confirmed)}
              onClick={async () => {
                // One accepted click only: disable immediately, await the
                // (fast, queued) signal, and surface failures honestly.
                setContinuing(true); setError(null)
                try {
                  await (onDone && onDone(signed))
                  setContinued(true)
                } catch (e) {
                  setError({ message: (e.detail && e.detail.message) || e.message })
                  setContinuing(false)
                }
              }}>
              {continued ? 'Continuing…' : continuing ? 'Starting…' : 'Continue to the official application'}
            </button>
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
  // Live-view state: 'connecting' | 'embedded' | 'unavailable' | 'closed'.
  // liveUrl is kept ONLY here (component state) — see privacy invariants above.
  const [liveState, setLiveState] = useState('connecting')
  const [liveUrl, setLiveUrl] = useState(null)
  const [frameNonce, setFrameNonce] = useState(0)
  const [refreshing, setRefreshing] = useState(false)
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

  // On open: create/reuse the case's isolated browser session, then fetch a
  // FRESH live-view URL (they expire quickly — never reuse a stale one).
  useEffect(() => {
    let alive = true
    async function open() {
      try {
        const s = await client.createBrowserSession(caseId)
        if (!alive) return
        if (s && s.mode === 'browserbase' && s.live_view_available) {
          const lv = await client.browserLiveView(caseId)
          if (!alive) return
          setLiveUrl(lv.url)
          setLiveState('embedded')
        } else {
          setLiveState('unavailable')
        }
      } catch {
        // No open session / local mode / provider URL unavailable → honest fallback.
        if (alive) setLiveState('unavailable')
      }
    }
    open()
    return () => { alive = false }
    // NOTE: the session is intentionally NOT deleted on unmount — it is the
    // case's own isolated automation session; only the explicit
    // "Close secure window" button tears it down.
  }, [caseId])

  async function refreshLiveView() {
    setRefreshing(true)
    try {
      const lv = await client.browserLiveView(caseId) // always a FRESH url
      setLiveUrl(lv.url)
      setFrameNonce((n) => n + 1)
      setLiveState('embedded')
    } catch {
      setLiveUrl(null)
      setLiveState('unavailable')
    }
    setRefreshing(false)
  }

  async function closeSecureWindow() {
    setClosing(true)
    try { await client.closeBrowserSession(caseId) } catch { /* non-fatal */ }
    setLiveUrl(null)
    setLiveState('closed')
    setClosing(false)
  }

  async function done() {
    setBusy(true); setError(null)
    try {
      if (handoff === 'email_verification' || handoff === 'otp') {
        await onResolve('verify_email', { token: typedCode.trim() || token })
        setTypedCode('')                        // never retained after use
      } else if (handoff === 'captcha') {
        await onResolve('solve_captcha')
      } else {
        await onResolve('solve_captcha') // login_challenge/identity share the confirm-only path
      }
      toast('Step completed')
    } catch (e) { setError({ message: e.message }); setBusy(false) }
  }

  const embedded = liveState === 'embedded' && !!liveUrl
  return (
    <Overlay onClose={onClose} width={embedded ? 960 : 620}>
      <Head title={title} sub={sub} onClose={onClose} />
      {/* Safety copy stays ABOVE the embedded window for the relevant kinds. */}
      {handoff === 'captcha' && (
        <div style={{ fontSize: 13, marginTop: 12 }}>
          <strong>Ellis never solves CAPTCHAs.</strong> Complete it yourself in the secure window, then confirm below.
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
        Ellis collects no passwords, one-time codes, card details, or CAPTCHA answers.
        Ellis never sees your card and never solves CAPTCHAs.
      </div>

      {liveState === 'connecting' && <Loading label={t('live.connecting')} />}

      {embedded && (
        <div style={{ marginTop: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10 }}>
            <div className="eyebrow">{t('live.embedded')}</div>
            <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
              <button className="btn btn--sm btn--ghost" disabled={refreshing} onClick={refreshLiveView}>
                {refreshing ? '…' : t('live.refresh')}
              </button>
              <button className="btn btn--sm btn--ghost" disabled={closing} onClick={closeSecureWindow}>
                {closing ? '…' : t('live.closeWindow')}
              </button>
            </div>
          </div>
          <iframe
            key={frameNonce}
            src={liveUrl}
            title="Ellis secure window"
            sandbox="allow-same-origin allow-scripts allow-forms"
            referrerPolicy="no-referrer"
            style={{ width: '100%', height: '70vh', border: '1px solid var(--line)',
              borderRadius: 10, marginTop: 8, background: '#fff' }}
          />
        </div>
      )}

      {liveState === 'closed' && (
        <div className="card card--soft" style={{ padding: 14, marginTop: 12, fontSize: 13 }}>
          {t('live.closed')}
        </div>
      )}

      {liveState === 'unavailable' && (
        <>
          <div className="card card--soft" style={{ padding: 14, marginTop: 12, fontSize: 13 }}>
            {t('live.unavailable')}
          </div>
          <SecureWindow live={live} />
        </>
      )}

      {error && <ErrorNote error={error} />}
      <div className="modal__foot">
        <button className="btn btn--ghost" onClick={onClose}>Not now</button>
        <button className="btn" disabled={busy} onClick={done}>
          {busy ? 'Confirming…'
            : handoff === 'captcha' ? 'CAPTCHA completed — continue'
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

export function PaymentApprove({ pending, onResolve, onClose }) {
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
  return (
    <Overlay onClose={onClose} width={520}>
      <Head title={confirmMode ? t('pay.feeConfirmTitle') : t('pay.confirmTitle')}
        onClose={onClose}
        sub={confirmMode ? t('pay.feeConfirmSub') : t('pay.confirmSub')} />
      {!confirmMode && (
        <div className="stat" style={{ marginTop: 12 }}>
          <div className="stat__num">{formatFee(fee) || '—'}</div>
          <div className="stat__cap">{t('pay.exactAmount')}</div>
        </div>
      )}
      {confirmMode && (
        <div style={{ marginTop: 12 }} data-testid="fee-confirm-entry">
          {expected && (
            <div style={{ fontSize: 12.5, color: 'var(--muted)', marginBottom: 8 }}>
              {t('pay.expectedFee', { fee: expected })}
            </div>
          )}
          <div style={{ display: 'flex', gap: 8 }}>
            <div className="field" style={{ flex: 1 }}>
              <label>{t('pay.amountShown')}</label>
              <input className="input" inputMode="decimal" value={amount}
                placeholder="25.00" onChange={(e) => setAmount(e.target.value)} />
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
  const [values, setValues] = useState({})
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
            <select className="select" value={values[q.key] || ''}
                    onChange={(e) => set(q.key, e.target.value)}>
              <option value="">{t('addinfo.selectPlaceholder')}</option>
              {q.options.map((o) => <option key={String(o)} value={String(o)}>{String(o)}</option>)}
            </select>
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

// ---- Standing authorization (brief §5) -------------------------------------
// One versioned grant at onboarding covers routine actions (portal selection,
// forms, uploads, booking within preferences, post-signature submission).
// Payment always remains a separate exact-amount confirmation.
export function StandingAuthModal({ client, caseId, locale = 'en', onDone, onClose }) {
  const t = useT()
  const [data, setData] = useState(null)
  const [agree, setAgree] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  useEffect(() => {
    client.getStandingAuthorization(caseId, locale)
      .then(setData).catch((e) => setError({ message: e.message }))
  }, [caseId, locale])
  return (
    <Overlay onClose={onClose}>
      <Head title={t('standing.title')} onClose={onClose} sub={t('standing.sub')} />
      {!data && !error && <Loading label={t('standing.loading')} />}
      {data && (
        <>
          <div className="card card--soft" style={{ padding: 14, maxHeight: 260, overflowY: 'auto',
            whiteSpace: 'pre-wrap', fontSize: 12.5, fontFamily: 'ui-monospace, monospace' }}>
            {data.text}
          </div>
          <ul style={{ fontSize: 12.5, color: 'var(--muted)', margin: '10px 0 0 18px' }}>
            <li>{t('standing.d.official')}</li>
            <li>{t('standing.d.routine')}</li>
            <li>{t('standing.d.noInvent')}</li>
            <li>{t('standing.d.notAll')}</li>
            <li>{t('standing.d.truth')}</li>
            <li>{t('standing.d.sign')}</li>
            <li>{t('standing.d.payment')}</li>
            <li>{t('standing.d.secure')}</li>
            <li>{t('standing.d.personal')}</li>
          </ul>
          <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', margin: '12px 0', fontSize: 13 }}>
            <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
            <span>{t('standing.agree', { version: data.text_version })}</span>
          </label>
        </>
      )}
      {error && <ErrorNote error={error} />}
      <div className="modal__foot">
        <button className="btn btn--ghost" onClick={onClose}>{t('standing.later')}</button>
        <button className="btn" disabled={!agree || busy || !data} onClick={async () => {
          setBusy(true); setError(null)
          try {
            const res = await client.grantStandingAuthorization(caseId, { locale })
            onDone && onDone(res)
          } catch (e) { setError({ message: e.message }); setBusy(false) }
        }}>{busy ? t('standing.granting') : t('standing.grant')}</button>
      </div>
    </Overlay>
  )
}

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
