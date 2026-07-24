// Route-specific document checklist (applicant journey, Parts 4 & 6). Items
// come from the backend (derived deterministically from the saved route
// guidance); per-item status is computed server-side from the case's
// classified documents. Applicant-facing only — no operator terminology.
import { checklistStatusMeta, checklistCounts } from '../../lib/intake.js'

export default function Checklist({ t, checklist, counts }) {
  const items = Array.isArray(checklist) ? checklist : []
  if (items.length === 0) return null
  const c = counts || checklistCounts(items)
  return (
    <div className="card" style={{ padding: 18, marginBottom: 14 }} data-testid="route-checklist">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <div style={{ fontWeight: 700 }}>{t('checklist.title')}</div>
          <div style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 2 }}>{t('checklist.sub')}</div>
        </div>
        <span className="chip">{(c.required ?? 0) - (c.missing ?? 0)}/{c.required ?? 0}</span>
      </div>
      <div style={{ marginTop: 10 }}>
        {items.map((item) => {
          const meta = checklistStatusMeta(item.status)
          const done = item.status === 'provided'
          return (
            <div key={item.id} className="row" style={{ alignItems: 'center', gap: 10 }}
              data-testid={'checklist-' + item.id} data-status={item.status}>
              <span className={'sevbadge ' + (done ? 'sevbadge--ok' : meta.tone === 'info' ? 'sevbadge--mid' : 'sevbadge--mid')}>
                {done ? '✓' : item.kind === 'check' ? '◦' : '…'}
              </span>
              <div className="row__main" style={{ minWidth: 0 }}>
                <div className="row__title" style={{ opacity: done ? 0.75 : 1 }}>{item.label}</div>
                <div className="row__sub">
                  {t(meta.i18nKey)}
                  {!item.required && item.kind === 'document' ? ` · ${t('checklist.optionalTag')}` : ''}
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
