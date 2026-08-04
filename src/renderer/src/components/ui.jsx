import { createContext, useContext, useState, useCallback, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { Icon } from './icons.jsx'

// ---------- Toast ----------
const ToastCtx = createContext(() => {})
export function useToast() { return useContext(ToastCtx) }
export function ToastProvider({ children }) {
  const [msg, setMsg] = useState(null)
  const show = useCallback((m) => {
    setMsg(m)
    setTimeout(() => setMsg(null), 2600)
  }, [])
  return (
    <ToastCtx.Provider value={show}>
      {children}
      {msg && <div className="toast">{msg}</div>}
    </ToastCtx.Provider>
  )
}

// ---------- Loading: the Trip.com airplane ----------
// Every waiting state shows a clean side-view airliner carrying the Trip.com
// name (like a livery), gliding on a dashed contrail — big enough to read,
// short and professional. Replaces the old spinner ring everywhere the
// Loading component is used.
export function TripPlane({ width = 168 }) {
  return (
    <svg width={width} viewBox="0 0 210 84" fill="none" aria-hidden="true">
      {/* fuselage */}
      <path d="M18 46c0-7 6-12.5 13.5-12.5H164c14 0 26 4.5 30 9.5 1.6 2-0.5 5-8 7-9 2.4-20 3.5-30 3.5H31.5C24 53.5 18 51 18 46z"
            fill="#ffffff" stroke="#0f294d" strokeWidth="2.4" />
      {/* cockpit window */}
      <path d="M180 42.5c4.5 0.8 8.5 2.2 11 3.7-3 1.6-7 2.7-11.6 3.4z" fill="#0f294d" opacity="0.85" />
      {/* tail fin */}
      <path d="M24 44.5 8 26c-1.4-1.7-0.1-4 2.1-4h10.4c1.9 0 3.6 0.9 4.7 2.4l12.3 17z"
            fill="#287dfa" stroke="#0f294d" strokeWidth="2.2" strokeLinejoin="round" />
      <circle cx="17.5" cy="30" r="3.4" fill="#fcaf17" />
      {/* wing */}
      <path d="M92 51.5 74 70c-1.8 1.8-0.5 4.9 2.1 4.9h13.2c1.7 0 3.3-0.7 4.4-2l18-21.4z"
            fill="#287dfa" stroke="#0f294d" strokeWidth="2.2" strokeLinejoin="round" />
      {/* livery: the Trip.com name on the fuselage */}
      <text x="66" y="47" fontFamily="Inter, system-ui, sans-serif" fontSize="14.5"
            fontWeight="800" fill="#287dfa" letterSpacing="0.2">Trip<tspan fill="#fcaf17">.</tspan><tspan fill="#0f294d">com</tspan></text>
      {/* window strip */}
      <g fill="#8592a6" opacity="0.55">
        {[58, 66, 74, 82, 90, 152, 160, 168].map((x) => (
          <rect key={x} x={x} y={37.4} width="3.4" height="3.4" rx="1.6" />
        ))}
      </g>
    </svg>
  )
}

export function Loading({ label = 'Working on it', sub = null, size = 'inline' }) {
  if (size === 'big') {
    return (
      <div className="planeload" role="status" aria-live="polite">
        <div className="planeload__sky">
          <span className="planeload__trail" />
          <span className="planeload__plane"><TripPlane width={216} /></span>
        </div>
        <div className="planeload__label">{label}<Dots /></div>
        {sub && <div className="planeload__sub">{sub}</div>}
      </div>
    )
  }
  return (
    <div className="planeload" style={{ padding: '18px 4px', gap: 10 }} role="status" aria-live="polite">
      <div className="planeload__sky" style={{ width: 250, height: 78 }}>
        <span className="planeload__trail" />
        <span className="planeload__plane" style={{ top: 2 }}><TripPlane width={152} /></span>
      </div>
      <div className="planeload__label" style={{ fontSize: 15 }}>{label}<Dots /></div>
    </div>
  )
}
function Dots() {
  const [n, setN] = useState(1)
  useEffect(() => { const t = setInterval(() => setN((x) => (x % 3) + 1), 450); return () => clearInterval(t) }, [])
  return <span>{'.'.repeat(n)}</span>
}

// ---------- Upload success tick ----------
export function UploadTick() {
  return (
    <span className="uptick" role="img" aria-label="Uploaded">
      <svg viewBox="0 0 24 24"><path d="M5 12.5l4.6 4.6L19 7.6" /></svg>
    </span>
  )
}

// ---------- Severity ----------
export function Sev({ level }) {
  const l = (level || 'info').toLowerCase()
  const good = ['low', 'info', 'pass', 'ready', 'ok', 'clear'].includes(l)
  const bad = ['high', 'critical', 'severe', 'review', 'fail', 'blocked'].includes(l)
  if (good) return <span className="sevbadge sevbadge--ok" title={l} aria-label={l}>✓</span>
  if (bad) return <span className="sevbadge sevbadge--bad" title={l} aria-label={l}>✕</span>
  return <span className="sevbadge sevbadge--mid" title={l} aria-label={l}>!</span>
}

// ---------- Findings list (risk, compliance) ----------
export function Findings({ items }) {
  if (!items?.length) return <Empty title="No issues found" sub="Ellis did not surface any flags for this case." />
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {items.map((f, i) => (
        <div key={i} className="card" style={{ padding: 18 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <Sev level={f.severity} />
            {f.owner && <span className="chip">{f.owner}</span>}
          </div>
          <div style={{ fontSize: 15, fontWeight: 600 }}>{f.title || f.issue || f.area}</div>
          {(f.explanation || f.issue) && <p className="prose" style={{ marginTop: 6 }}>{f.explanation || f.issue}</p>}
          {(f.nextAction || f.remediation) && (
            <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--line)', fontSize: 13.5 }}>
              <strong>Next: </strong>{f.nextAction || f.remediation}
              {f.dueDate && f.dueDate !== 'n/a' && <span style={{ color: 'var(--muted)' }}>  ·  due {f.dueDate}</span>}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

// ---------- Key/value field list ----------
export function KVList({ fields }) {
  if (!fields?.length) return null
  return (
    <div>
      {fields.map((f, i) => (
        <div className="kv" key={i}>
          <div className="kv__k">{f.label}</div>
          <div className="kv__v">
            {f.value}
            {f.confidence && <span className="chip" style={{ marginLeft: 8, fontSize: 11 }}>{f.confidence}</span>}
            {f.status && <span className="chip" style={{ marginLeft: 8, fontSize: 11 }}>{f.status}</span>}
          </div>
        </div>
      ))}
    </div>
  )
}

// ---------- Checklist ----------
export function Checklist({ items }) {
  if (!items?.length) return null
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {items.map((it, i) => {
        const text = typeof it === 'string' ? it : (it.item || it.action || it.label)
        const status = typeof it === 'string' ? null : it.status
        const ready = status === 'ready' || status === 'filled'
        return (
          <div key={i} className="row" style={{ padding: '11px 14px' }}>
            <span style={{ width: 20, height: 20, borderRadius: 6, display: 'grid', placeItems: 'center', border: '1px solid var(--line-strong)', background: ready ? 'var(--ink)' : 'transparent', color: '#fff', flexShrink: 0 }}>
              {ready && <Icon.check style={{ width: 13, height: 13 }} />}
            </span>
            <div className="row__main"><div className="row__title" style={{ fontWeight: 500 }}>{text}</div></div>
            {status && <span className="chip">{status}</span>}
          </div>
        )
      })}
    </div>
  )
}

// ---------- Empty ----------
export function Empty({ title, sub, children }) {
  return (
    <div className="empty">
      <div className="empty__title">{title}</div>
      {sub && <div style={{ fontSize: 14, maxWidth: 380, margin: '0 auto 16px' }}>{sub}</div>}
      {children}
    </div>
  )
}

// ---------- Overlay ----------
// Every modal backdrop MUST go through here. `position: fixed` is relative to
// the nearest ancestor with a transform/filter/backdrop-filter/containment —
// not the viewport — so a modal rendered inside an animated `.card.fadeup`
// gets trapped in that card's box and squeezed to its width (2026-08-03: the
// translation preview rendered as a ~240px vertical strip with its header
// scrolled off the top). Portalling to <body> makes that structurally
// impossible regardless of what wraps the caller. Escape closes; the backdrop
// click closes; the panel itself swallows clicks so a stray drag can't.
export function Overlay({ onClose, children, ...rest }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose && onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return createPortal(
    <div className="overlay" onClick={onClose} {...rest}>{children}</div>,
    document.body,
  )
}

// ---------- AI key error banner ----------
export function ErrorNote({ error, onSettings }) {
  if (!error) return null
  return (
    // Marked so the screen that raised it can bring it INTO VIEW. This note
    // renders at the top of a case; the button that triggers it can be a
    // thousand pixels below, and an explanation nobody scrolls to is the same
    // as no explanation at all.
    <div className="card" data-ellis-error
         style={{ padding: 16, borderColor: 'var(--ink)', marginTop: 16 }}>
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{error.message}</div>
      {error.code === 'NO_API_KEY' && (
        <button className="btn btn--sm" style={{ marginTop: 8 }} onClick={onSettings}>Open Settings</button>
      )}
    </div>
  )
}
