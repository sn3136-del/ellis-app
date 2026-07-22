import { useState, useEffect } from 'react'
import { ellis } from '../lib/api.js'
import { Icon } from './icons.jsx'
import { useToast } from './ui.jsx'

/* Real brand marks (inline SVG) so connecting a tool feels like the real thing. */
export const Brand = {
  slack: (p) => (
    <svg viewBox="0 0 122.8 122.8" {...p}>
      <path d="M25.8 77.6c0 7.1-5.8 12.9-12.9 12.9S0 84.7 0 77.6s5.8-12.9 12.9-12.9h12.9v12.9z" fill="#E01E5A"/>
      <path d="M32.3 77.6c0-7.1 5.8-12.9 12.9-12.9s12.9 5.8 12.9 12.9v32.3c0 7.1-5.8 12.9-12.9 12.9s-12.9-5.8-12.9-12.9V77.6z" fill="#E01E5A"/>
      <path d="M45.2 25.8c-7.1 0-12.9-5.8-12.9-12.9S38.1 0 45.2 0s12.9 5.8 12.9 12.9v12.9H45.2z" fill="#36C5F0"/>
      <path d="M45.2 32.3c7.1 0 12.9 5.8 12.9 12.9s-5.8 12.9-12.9 12.9H12.9C5.8 58.1 0 52.3 0 45.2s5.8-12.9 12.9-12.9h32.3z" fill="#36C5F0"/>
      <path d="M97 45.2c0-7.1 5.8-12.9 12.9-12.9s12.9 5.8 12.9 12.9-5.8 12.9-12.9 12.9H97V45.2z" fill="#2EB67D"/>
      <path d="M90.5 45.2c0 7.1-5.8 12.9-12.9 12.9s-12.9-5.8-12.9-12.9V12.9C64.7 5.8 70.5 0 77.6 0s12.9 5.8 12.9 12.9v32.3z" fill="#2EB67D"/>
      <path d="M77.6 97c7.1 0 12.9 5.8 12.9 12.9s-5.8 12.9-12.9 12.9-12.9-5.8-12.9-12.9V97h12.9z" fill="#ECB22E"/>
      <path d="M77.6 90.5c-7.1 0-12.9-5.8-12.9-12.9s5.8-12.9 12.9-12.9h32.3c7.1 0 12.9 5.8 12.9 12.9s-5.8 12.9-12.9 12.9H77.6z" fill="#ECB22E"/>
    </svg>
  ),
  gmail: (p) => (
    <svg viewBox="0 0 24 24" {...p}>
      <path fill="#4285F4" d="M1.636 20.91h3.819V11.73L0 7.64v11.634c0 .904.732 1.636 1.636 1.636z"/>
      <path fill="#34A853" d="M18.545 20.91h3.819c.904 0 1.636-.732 1.636-1.636V7.64l-5.455 4.09v9.18z"/>
      <path fill="#FBBC04" d="M18.545 3.82v7.91L24 7.64V4.639c0-2.023-2.309-3.178-3.927-1.964l-1.528 1.145z"/>
      <path fill="#EA4335" d="M5.455 11.73V3.82L12 8.73l6.545-4.91v7.91L12 16.64l-6.545-4.91z"/>
      <path fill="#C5221F" d="M0 4.64v3l5.455 4.09V3.82L3.927 2.675C2.309 1.46 0 2.616 0 4.639z"/>
    </svg>
  ),
  workday: (p) => (
    <svg viewBox="0 0 24 24" {...p}>
      <circle cx="12" cy="12" r="12" fill="#F38B00"/>
      <path d="M4.5 9.2l2.5 8h2.3l1.6-5.3 1.6 5.3h2.3l2.5-8h-2.2l-1.5 5.4-1.6-5.4h-2.2l-1.6 5.4-1.5-5.4z" fill="#fff"/>
      <path d="M5.6 6.6a8 8 0 0 1 12.8 0l-1.6 1.2a6 6 0 0 0-9.6 0z" fill="#005CB9"/>
    </svg>
  ),
  gcal: (p) => (
    <svg viewBox="0 0 24 24" {...p}>
      <rect x="2" y="2" width="20" height="20" rx="3" fill="#fff" stroke="#dadce0"/>
      <path d="M2 7h20v13a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2z" fill="#fff"/>
      <rect x="2" y="2" width="20" height="5" rx="2" fill="#1A73E8"/>
      <text x="12" y="17.5" fontSize="9.5" fontWeight="700" fill="#1A73E8" textAnchor="middle" fontFamily="Arial, sans-serif">31</text>
      <path d="M17 22h3a2 2 0 0 0 2-2v-3z" fill="#EA4335"/>
    </svg>
  ),
  gdrive: (p) => (
    <svg viewBox="0 0 87.3 78" {...p}>
      <path d="m6.6 66.85 3.85 6.65c.8 1.4 1.95 2.5 3.3 3.3l13.75-23.8H0c0 1.55.4 3.1 1.2 4.5z" fill="#0066da"/>
      <path d="M43.65 25 29.9 1.2c-1.35.8-2.5 1.9-3.3 3.3l-25.4 44a9.06 9.06 0 0 0-1.2 4.5h27.5z" fill="#00ac47"/>
      <path d="M73.55 76.8c1.35-.8 2.5-1.9 3.3-3.3l1.6-2.75 7.65-13.25c.8-1.4 1.2-2.95 1.2-4.5H59.8l5.85 11.5z" fill="#ea4335"/>
      <path d="m43.65 25 13.75-23.8c-1.35-.8-2.9-1.2-4.5-1.2H34.4c-1.6 0-3.15.45-4.5 1.2z" fill="#00832d"/>
      <path d="M59.85 53H27.45L13.75 76.8c1.35.8 2.9 1.2 4.5 1.2h50.8c1.6 0 3.15-.45 4.5-1.2z" fill="#2684fc"/>
      <path d="m73.4 26.5-12.7-22c-.8-1.4-1.95-2.5-3.3-3.3L43.65 25l16.15 28h27.45c0-1.55-.4-3.1-1.2-4.5z" fill="#ffba00"/>
    </svg>
  )
}

export const TOOLS = [
  { id: 'slack', name: 'Slack', logo: 'slack', desc: 'Case context from channels & DMs', fields: [{ k: 'workspace', label: 'Workspace URL', ph: 'deep24.slack.com' }, { k: 'email', label: 'Email', ph: 'you@company.com' }, { k: 'password', label: 'Password', ph: '••••••••', type: 'password' }], scopes: ['Read channel & DM history', 'Post case updates as @Ellis', 'Watch for immigration keywords'] },
  { id: 'email', name: 'Gmail', logo: 'gmail', desc: 'Ingest notices; send updates', fields: [{ k: 'email', label: 'Google account', ph: 'you@company.com' }, { k: 'password', label: 'Password', ph: '••••••••', type: 'password' }], scopes: ['Read government notices (USCIS / IRCC senders)', 'Send counsel updates with attachments'] },
  { id: 'hris', name: 'Workday', logo: 'workday', desc: 'Sync employee records', fields: [{ k: 'tenant', label: 'Workday tenant', ph: 'deep24.workday.com' }, { k: 'email', label: 'Username', ph: 'integration@deep24.com' }, { k: 'password', label: 'Password', ph: '••••••••', type: 'password' }], scopes: ['Read worker profiles & start dates', 'Sync visa expirations to worker records'] },
  { id: 'calendar', name: 'Google Calendar', logo: 'gcal', desc: 'Deadlines, biometrics, interviews', fields: [{ k: 'email', label: 'Google account', ph: 'you@company.com' }, { k: 'password', label: 'Password', ph: '••••••••', type: 'password' }], scopes: ['Create filing-deadline events', 'Add biometrics & interview appointments'] },
  { id: 'drive', name: 'Google Drive', logo: 'gdrive', desc: 'Import case documents', fields: [{ k: 'email', label: 'Google account', ph: 'you@company.com' }, { k: 'password', label: 'Password', ph: '••••••••', type: 'password' }], scopes: ['Read documents you select', 'Save filled forms & packets back to Drive'] }
]

/* Sign-in modal shown when a tool logo is clicked. */
export function ConnectModal({ tool, connected, onDone, onClose }) {
  const [vals, setVals] = useState({})
  const [stage, setStage] = useState(connected ? 'connected' : 'login') // login | authorizing | success | connected
  const Logo = Brand[tool.logo]
  const primary = tool.fields.find((f) => f.k !== 'password')

  function signIn(e) {
    e?.preventDefault()
    setStage('authorizing')
    setTimeout(() => setStage('success'), 1600)
  }
  function finish() { onDone(true, vals[primary?.k] || ''); onClose() }
  function disconnect() { onDone(false, ''); onClose() }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 440 }} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 4 }}>
          <div style={{ width: 40, height: 40, borderRadius: 10, border: '1px solid var(--line)', display: 'grid', placeItems: 'center', background: '#fff' }}>
            <Logo style={{ width: 22, height: 22 }} />
          </div>
          <div style={{ flex: 1 }}>
            <h2 style={{ marginBottom: 0, fontSize: 19 }}>{stage === 'connected' ? tool.name : `Sign in to ${tool.name}`}</h2>
            <div style={{ fontSize: 12.5, color: 'var(--muted)' }}>{tool.desc}</div>
          </div>
          <button className="iconbtn" onClick={onClose}>✕</button>
        </div>

        {stage === 'login' && (
          <form onSubmit={signIn} style={{ textAlign: 'left', marginTop: 14 }}>
            {tool.fields.map((f) => (
              <div className="field" key={f.k}>
                <label>{f.label}</label>
                <input className="input" type={f.type || 'text'} placeholder={f.ph} value={vals[f.k] || ''}
                  onChange={(e) => setVals({ ...vals, [f.k]: e.target.value })} autoFocus={f === tool.fields[0]} />
              </div>
            ))}
            <div className="card card--soft" style={{ padding: 12, margin: '4px 0 14px' }}>
              <div className="eyebrow" style={{ marginBottom: 6 }}>Ellis will be able to</div>
              {tool.scopes.map((s, i) => (
                <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12.5, color: 'var(--muted)', marginBottom: 4 }}>
                  <Icon.check style={{ width: 12, height: 12, flexShrink: 0 }} /> {s}
                </div>
              ))}
            </div>
            <button className="btn" type="submit" style={{ width: '100%', justifyContent: 'center' }} disabled={!vals[primary?.k]?.trim()}>
              Sign in & authorize Ellis
            </button>
            <p style={{ fontSize: 11, color: 'var(--muted-2)', marginTop: 10, textAlign: 'center' }}>Credentials are used once to authorize; Ellis stores only the access token on this device.</p>
          </form>
        )}

        {stage === 'authorizing' && (
          <div style={{ padding: '38px 0', textAlign: 'center' }}>
            <span className="spinner spinner--ink" style={{ width: 24, height: 24 }} />
            <p style={{ color: 'var(--muted)', marginTop: 14, fontSize: 13.5 }}>Signing in to {tool.name} and requesting access…</p>
          </div>
        )}

        {stage === 'success' && (
          <div style={{ padding: '26px 0 8px', textAlign: 'center' }}>
            <div style={{ width: 44, height: 44, borderRadius: '50%', background: 'var(--ink)', color: '#fff', display: 'grid', placeItems: 'center', margin: '0 auto 12px' }}>
              <Icon.check style={{ width: 20, height: 20 }} />
            </div>
            <div style={{ fontWeight: 700, fontSize: 16 }}>{tool.name} connected</div>
            <p style={{ fontSize: 13, color: 'var(--muted)', margin: '6px 0 18px' }}>Ellis is now syncing. New case context will appear automatically.</p>
            <button className="btn" style={{ width: '100%', justifyContent: 'center' }} onClick={finish}>Done</button>
          </div>
        )}

        {stage === 'connected' && (
          <div style={{ marginTop: 14 }}>
            <div className="card card--soft" style={{ padding: 12, marginBottom: 14, display: 'flex', alignItems: 'center', gap: 10 }}>
              <Icon.check style={{ width: 15, height: 15 }} />
              <span style={{ fontSize: 13.5 }}>Connected and syncing</span>
            </div>
            <div style={{ display: 'flex', gap: 10 }}>
              <button className="btn btn--ghost" style={{ flex: 1, justifyContent: 'center' }} onClick={disconnect}>Disconnect</button>
              <button className="btn" style={{ flex: 1, justifyContent: 'center' }} onClick={onClose}>Keep connected</button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

/* Logo tile grid — click a logo to sign in. */
export function ToolsGrid({ ids }) {
  const toast = useToast()
  const [integ, setInteg] = useState({})
  const [active, setActive] = useState(null)
  const tools = ids ? TOOLS.filter((t) => ids.includes(t.id)) : TOOLS

  useEffect(() => { ellis.getSettings().then((s) => setInteg(s.integrations || {})) }, [])

  async function setConnected(id, on) {
    const next = { ...integ, [id]: on }
    setInteg(next)
    const s = await ellis.getSettings()
    await ellis.saveSettings({ ...s, integrations: next })
    const t = TOOLS.find((x) => x.id === id)
    toast(on ? `${t.name} connected` : `${t.name} disconnected`)
  }

  return (
    <>
      <div style={{ display: 'grid', gridTemplateColumns: `repeat(${tools.length}, 1fr)`, gap: 12 }}>
        {tools.map((t) => {
          const Logo = Brand[t.logo]
          const on = !!integ[t.id]
          return (
            <button key={t.id} onClick={() => setActive(t)}
              style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 9, padding: '18px 8px 14px', borderRadius: 14, cursor: 'pointer', font: 'inherit', background: '#fff', border: on ? '1.5px solid var(--ink)' : '1px solid var(--line)', transition: 'transform .15s, box-shadow .15s', position: 'relative' }}
              onMouseEnter={(e) => { e.currentTarget.style.transform = 'translateY(-2px)'; e.currentTarget.style.boxShadow = 'var(--shadow)' }}
              onMouseLeave={(e) => { e.currentTarget.style.transform = ''; e.currentTarget.style.boxShadow = '' }}>
              {on && (
                <span style={{ position: 'absolute', top: 8, right: 8, width: 17, height: 17, borderRadius: '50%', background: 'var(--ink)', color: '#fff', display: 'grid', placeItems: 'center' }}>
                  <Icon.check style={{ width: 10, height: 10 }} />
                </span>
              )}
              <Logo style={{ width: 30, height: 30 }} />
              <div style={{ fontSize: 12.5, fontWeight: 600 }}>{t.name}</div>
              <div style={{ fontSize: 10.5, color: on ? 'var(--ink)' : 'var(--muted-2)', fontWeight: on ? 600 : 400 }}>{on ? 'Connected' : 'Click to connect'}</div>
            </button>
          )
        })}
      </div>
      {active && (
        <ConnectModal tool={active} connected={!!integ[active.id]}
          onDone={(on) => setConnected(active.id, on)} onClose={() => setActive(null)} />
      )}
    </>
  )
}
