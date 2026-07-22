import { createContext, useContext, useState, useCallback, useEffect } from 'react'
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

// ---------- Loading ----------
export function Loading({ label = 'Ellis is working' }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '28px 4px', color: 'var(--muted)' }}>
      <span className="spinner spinner--ink" />
      <span style={{ fontSize: 14 }}>{label}<Dots /></span>
    </div>
  )
}
function Dots() {
  const [n, setN] = useState(1)
  useEffect(() => { const t = setInterval(() => setN((x) => (x % 3) + 1), 450); return () => clearInterval(t) }, [])
  return <span>{'.'.repeat(n)}</span>
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

// ---------- AI key error banner ----------
export function ErrorNote({ error, onSettings }) {
  if (!error) return null
  return (
    <div className="card" style={{ padding: 16, borderColor: 'var(--ink)', marginTop: 16 }}>
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{error.message}</div>
      {error.code === 'NO_API_KEY' && (
        <button className="btn btn--sm" style={{ marginTop: 8 }} onClick={onSettings}>Open Settings</button>
      )}
    </div>
  )
}
