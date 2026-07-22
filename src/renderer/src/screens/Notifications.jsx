import { useState, useEffect } from 'react'
import { ellis, ROLE_LABEL, fmtDate } from '../lib/api.js'
import { Icon } from '../components/icons.jsx'
import { Empty } from '../components/ui.jsx'

export default function Notifications({ role, onOpenCase, onChanged }) {
  const [items, setItems] = useState([])

  async function load() { setItems(await ellis.listNotifs(role)) }
  useEffect(() => { load() }, [role])

  async function open(n) {
    await ellis.markNotifRead(n.id)
    onChanged?.()
    if (n.caseId) onOpenCase(n.caseId)
    else load()
  }
  async function markAll() { await ellis.markAllNotifsRead(role); onChanged?.(); load() }

  return (
    <div>
      <div className="topbar">
        <h1>Notifications</h1>
        <div className="topbar__actions"><button className="btn btn--ghost" onClick={markAll}>Mark all read</button></div>
      </div>
      <div className="page">
        {items.length === 0
          ? <Empty title="No notifications" sub="Updates from other people on your cases will appear here." />
          : items.map((n) => (
            <button key={n.id} className="row" style={{ width: '100%', textAlign: 'left', cursor: 'pointer', opacity: n.read ? 0.6 : 1 }} onClick={() => open(n)}>
              <span style={{ width: 9, height: 9, borderRadius: '50%', background: n.read ? 'transparent' : 'var(--ink)', border: n.read ? '1px solid var(--line-strong)' : 'none', flexShrink: 0 }} />
              <div className="row__main">
                <div className="row__title">{n.title}</div>
                <div className="row__sub">{n.caseName ? `${n.caseName} · ` : ''}from {ROLE_LABEL[n.fromRole] || n.fromRole} · {fmtDate(n.createdAt)}</div>
              </div>
              {n.caseId && <Icon.arrow style={{ width: 16, height: 16, color: 'var(--muted)' }} />}
            </button>
          ))}
      </div>
    </div>
  )
}
