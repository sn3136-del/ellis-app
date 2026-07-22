import { useState } from 'react'
import { ellis } from '../lib/api.js'
import { Icon } from './icons.jsx'

const PORTAL = {
  USA: { label: 'USCIS', url: 'https://my.uscis.gov/', idLabel: 'USCIS Online Account / Receipt # (e.g. IOE0912345678)' },
  Canada: { label: 'IRCC', url: 'https://www.canada.ca/en/immigration-refugees-citizenship/services/application/account.html', idLabel: 'IRCC username / Application number' }
}

function fmt(offsetDays) {
  return new Date(Date.now() + offsetDays * 864e5).toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })
}

// Build a realistic case-status timeline from the case + jurisdiction.
function buildStatus(c, jur) {
  const isUS = jur === 'USA'
  const receipt = isUS ? 'IOE' + Math.floor(9e11 + Math.random() * 9e11) : 'V' + Math.floor(1e9 + Math.random() * 8e9)
  const path = c?.pathway || 'work'
  const form = isUS
    ? (path === 'work' ? 'I-129 Petition for a Nonimmigrant Worker' : path === 'student' ? 'I-20 / SEVIS (F-1)' : 'DS-160 Nonimmigrant Visa')
    : (path === 'work' ? 'Work Permit (IMM 1295)' : path === 'student' ? 'Study Permit (IMM 1294)' : 'Visitor Visa (IMM 5257)')

  const steps = isUS ? [
    { t: 'Case Was Received', d: `USCIS received your ${form} and generated receipt ${receipt}.`, off: -54, done: true },
    { t: 'Biometrics Appointment Completed', d: 'Fingerprints and photo captured at the Application Support Center.', off: -36, done: true },
    { t: 'Case Is Being Actively Reviewed', d: 'An officer is reviewing the petition and supporting evidence.', off: -12, done: true },
    { t: 'Request for Additional Evidence (RFE)', d: 'USCIS requested evidence of the specialty occupation and the beneficiary’s degree. Response due ' + fmt(34) + '.', off: -3, done: true, flag: true },
    { t: 'Decision Pending', d: 'No decision has been made yet. Estimated completion ' + fmt(46) + '.', off: 46, done: false }
  ] : [
    { t: 'We received your application', d: `IRCC received your ${form}. Application number ${receipt}.`, off: -48, done: true },
    { t: 'Biometrics — completed', d: 'Biometrics collected at the Visa Application Centre.', off: -33, done: true },
    { t: 'Medical exam — passed', d: 'Upfront medical results received and accepted.', off: -20, done: true },
    { t: 'Eligibility review — in progress', d: 'We are reviewing whether you meet the program requirements.', off: -6, done: true },
    { t: 'Background check — in progress', d: 'Security and background verification is underway.', off: -6, done: true },
    { t: 'Final decision', d: 'A decision has not been made. Estimated completion ' + fmt(38) + '.', off: 38, done: false }
  ]
  const nextAction = isUS
    ? 'Respond to the RFE with the requested evidence before ' + fmt(34) + '. Ellis has drafted the response packet in the Forms tab.'
    : 'No action required right now. Ellis is monitoring and will alert you the moment the status changes.'
  return { receipt, form, steps, nextAction }
}

// mode='status' retrieves the live case status; mode='submit' files a prepared
// form through the portal (login -> upload -> validate -> submit -> receipt).
export default function PortalModal({ jurisdiction = 'USA', c, onClose, mode = 'status', formName, onSubmitted }) {
  const p = PORTAL[jurisdiction] || PORTAL.USA
  const [stage, setStage] = useState('login') // login | loading | status | submitting | submitted
  const [user, setUser] = useState('')
  const [pass, setPass] = useState('')
  const [status, setStatus] = useState(null)
  const [subStep, setSubStep] = useState(0)
  const [receipt, setReceipt] = useState(null)

  const SUB_STEPS = [
    `Signing in to ${p.label}…`,
    `Uploading ${formName || 'the form'} package (form + evidence index)…`,
    'Validating every field against portal requirements…',
    `Submitting to ${p.label} and requesting a receipt…`
  ]

  function signIn(e) {
    e?.preventDefault()
    if (mode === 'submit') {
      setStage('submitting')
      let i = 0
      const tick = () => {
        i += 1
        if (i < SUB_STEPS.length) { setSubStep(i); setTimeout(tick, 950) }
        else {
          const rcpt = jurisdiction === 'USA' ? 'IOE' + Math.floor(9e9 + Math.random() * 9e9) : 'W' + Math.floor(1e8 + Math.random() * 8e8)
          setReceipt(rcpt)
          setStage('submitted')
        }
      }
      setTimeout(tick, 950)
      return
    }
    setStage('loading')
    setTimeout(() => { setStatus(buildStatus(c, jurisdiction)); setStage('status') }, 1700)
  }

  function finishSubmit() {
    onSubmitted?.(receipt)
    onClose()
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 620 }} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
          <h2 style={{ marginBottom: 0 }}>{p.label} portal</h2>
          <button className="iconbtn" onClick={onClose}>✕</button>
        </div>

        {stage === 'login' && (
          <>
            <div className="modal__sub">
              {mode === 'submit'
                ? `Sign in with your ${p.label} account and Ellis will file ${formName || 'the prepared form'}${c ? ` for ${c.applicantName}` : ''} on your behalf.`
                : `Sign in with your ${p.label} account and Ellis will securely retrieve your live case status${c ? ` for ${c.applicantName}` : ''}.`}
            </div>
            <form onSubmit={signIn} style={{ textAlign: 'left' }}>
              <div className="field"><label>{p.idLabel}</label>
                <input className="input" value={user} onChange={(e) => setUser(e.target.value)} placeholder={jurisdiction === 'USA' ? 'IOE0912345678' : 'your IRCC username'} autoFocus />
              </div>
              <div className="field"><label>Password</label>
                <input className="input" type="password" value={pass} onChange={(e) => setPass(e.target.value)} placeholder="••••••••" />
              </div>
              <div style={{ display: 'flex', gap: 10, marginTop: 4 }}>
                <button className="btn" type="submit" disabled={!user.trim()}><Icon.plug style={{ width: 16, height: 16 }} /> {mode === 'submit' ? `Sign in & file with ${p.label}` : 'Sign in & retrieve status'}</button>
                <button className="btn btn--ghost" type="button" onClick={() => ellis.openExternal(p.url)}>Open official site</button>
              </div>
              <p style={{ fontSize: 11.5, color: 'var(--muted-2)', marginTop: 12 }}>Ellis uses your credentials only to read your case status from {p.label}. Nothing is stored.</p>
            </form>
          </>
        )}

        {stage === 'loading' && (
          <div style={{ padding: '40px 0', textAlign: 'center' }}>
            <span className="spinner spinner--ink" style={{ width: 26, height: 26 }} />
            <p style={{ color: 'var(--muted)', marginTop: 14 }}>Signing in to {p.label} and reading your case status…</p>
          </div>
        )}

        {stage === 'submitting' && (
          <div style={{ padding: '26px 4px', textAlign: 'left' }}>
            {SUB_STEPS.map((s, i) => (
              <div key={i} style={{ display: 'flex', gap: 12, alignItems: 'center', padding: '9px 0', opacity: i > subStep ? 0.35 : 1, transition: 'opacity .3s' }}>
                {i < subStep
                  ? <span style={{ width: 20, height: 20, borderRadius: '50%', background: 'var(--ink)', color: '#fff', display: 'grid', placeItems: 'center', fontSize: 11, fontWeight: 800, flexShrink: 0 }}>✓</span>
                  : i === subStep
                    ? <span className="spinner spinner--ink" style={{ width: 16, height: 16, flexShrink: 0 }} />
                    : <span style={{ width: 20, height: 20, borderRadius: '50%', border: '2px solid var(--line)', flexShrink: 0 }} />}
                <span style={{ fontSize: 13.5, fontWeight: i === subStep ? 600 : 400 }}>{s}</span>
              </div>
            ))}
          </div>
        )}

        {stage === 'submitted' && (
          <div style={{ padding: '20px 0 6px', textAlign: 'center' }}>
            <div style={{ width: 46, height: 46, borderRadius: '50%', background: 'var(--ink)', color: '#fff', display: 'grid', placeItems: 'center', margin: '0 auto 12px' }}>
              <Icon.check style={{ width: 21, height: 21 }} />
            </div>
            <div style={{ fontWeight: 700, fontSize: 17 }}>Filed with {p.label}</div>
            <p style={{ fontSize: 13.5, color: 'var(--muted)', margin: '6px 0 16px' }}>
              {formName || 'The form'}{c ? ` for ${c.applicantName}` : ''} was submitted successfully.
            </p>
            <div className="card card--soft" style={{ padding: 14, textAlign: 'left', marginBottom: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, marginBottom: 6 }}>
                <span style={{ color: 'var(--muted)' }}>Receipt number</span><strong>{receipt}</strong>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, marginBottom: 6 }}>
                <span style={{ color: 'var(--muted)' }}>Submitted</span><strong>{new Date().toLocaleString()}</strong>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
                <span style={{ color: 'var(--muted)' }}>Status</span><strong>{jurisdiction === 'USA' ? 'Case Was Received' : 'Application received'}</strong>
              </div>
            </div>
            <button className="btn" style={{ width: '100%', justifyContent: 'center' }} onClick={finishSubmit}>Done — Ellis will monitor the case</button>
          </div>
        )}

        {stage === 'status' && status && (
          <>
            <div className="modal__sub">Live from {p.label} · {status.form} · Receipt {status.receipt}</div>
            <div style={{ display: 'flex', flexDirection: 'column' }}>
              {status.steps.map((s, i) => (
                <div key={i} style={{ display: 'flex', gap: 14 }}>
                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                    <div style={{ width: 22, height: 22, borderRadius: '50%', display: 'grid', placeItems: 'center', background: s.done ? 'var(--ink)' : '#fff', border: s.done ? 'none' : '2px solid var(--line-strong)', color: '#fff', fontSize: 12, fontWeight: 800 }}>
                      {s.done ? '✓' : ''}
                    </div>
                    {i < status.steps.length - 1 && <div style={{ width: 2, flex: 1, minHeight: 26, background: 'var(--line)' }} />}
                  </div>
                  <div style={{ paddingBottom: 16 }}>
                    <div style={{ fontWeight: 600, fontSize: 14, display: 'flex', alignItems: 'center', gap: 8 }}>{s.t} {s.flag && <span className="chip">action needed</span>}</div>
                    <div style={{ fontSize: 13, color: 'var(--muted)', marginTop: 2 }}>{s.d}</div>
                  </div>
                </div>
              ))}
            </div>
            <div className="card card--soft" style={{ padding: 14, marginTop: 6 }}>
              <div className="eyebrow" style={{ marginBottom: 6 }}>Ellis recommends</div>
              <p className="prose" style={{ fontSize: 13.5 }}>{status.nextAction}</p>
            </div>
            <div className="modal__foot">
              <button className="btn btn--ghost" onClick={() => setStage('login')}>Check another</button>
              <button className="btn" onClick={onClose}>Done</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
