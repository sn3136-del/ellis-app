// Admin Console — country-adapter administration + approval lifecycle (Phase 2).
// Drives each adapter through discovered → … → production_active with human-only
// activation. Talks to the backend admin API (admin token grants the role); no
// provider credentials ever touch this renderer.
import { useEffect, useState } from 'react'
import { useToast, Loading, ErrorNote, Empty, KVList } from '../components/ui.jsx'
import { Icon } from '../components/icons.jsx'
import { useLocale } from '../lib/locale.jsx'
import { createVisaClient } from '../lib/visaBackend.js'
import { newAdminSession } from '../lib/visaSession.js'
import { readinessMeta, researchStatusMeta } from '../lib/intake.js'

const STATE_LABELS = {
  discovered: 'Discovered', disabled_draft: 'Disabled draft', technical_review: 'Technical review',
  policy_review: 'Policy review', mock_tested: 'Mock tested', staging_tested: 'Staging tested',
  approved: 'Approved', limited_rollout: 'Limited rollout', production_active: 'Production active',
  paused: 'Paused', rolled_back: 'Rolled back'
}
const ACTIVATION = new Set(['approved', 'limited_rollout', 'production_active'])

function StateChip({ state, enabled, kill }) {
  const ink = ACTIVATION.has(state) && enabled && !kill
  return <span className={'chip' + (ink ? ' chip--ink' : '')} style={{ background: kill ? 'var(--crit)' : undefined, color: kill ? '#fff' : undefined }}>
    {kill ? 'KILLED' : (STATE_LABELS[state] || state)}
  </span>
}

// New snapshot-pipeline tabs (Trip.com snapshot admin). Labels are i18n keys;
// the original adapter tabs keep their hardcoded labels for continuity.
const SNAPSHOT_TABS = [
  ['snapshot', 'admin.tab.snapshot'],
  ['reviews', 'admin.tab.reviews'],
  ['conflicts', 'admin.tab.conflicts'],
  ['route queue', 'admin.tab.routeQueue'],
  ['adapter tasks', 'admin.tab.adapterTasks'],
  ['research jobs', 'admin.tab.researchJobs']
]

export default function AdminConsole() {
  const toast = useToast()
  const { t } = useLocale()
  const [client] = useState(() => createVisaClient(newAdminSession()))
  const [tab, setTab] = useState('adapters')
  const [adapters, setAdapters] = useState(null)
  const [coverage, setCoverage] = useState([])
  const [isAdmin, setIsAdmin] = useState(false)
  const [openId, setOpenId] = useState(null)
  const [error, setError] = useState(null)

  async function load() {
    try {
      const r = await client.adminListAdapters()
      setAdapters(r.adapters); setIsAdmin(r.is_admin)
      setCoverage((await client.adminCoverage()).coverage)
    } catch (e) { setError({ message: e.message }) }
  }
  useEffect(() => { load() }, [])

  if (openId) {
    return <AdapterDetail client={client} id={openId} isAdmin={isAdmin}
      onBack={() => { setOpenId(null); load() }} onChanged={load} />
  }

  return (
    <div>
      <div className="topbar">
        <h1>Adapter Administration</h1>
        <div className="topbar__actions">
          <span className={'chip' + (isAdmin ? ' chip--ink' : '')}>{isAdmin ? 'admin' : 'read-only'}</span>
        </div>
      </div>
      <div className="page page--wide">
        {error && <ErrorNote error={error} />}
        <div className="tabs">
          {['adapters', 'review queue', 'coverage'].map((name) => (
            <button key={name} className={'tab' + (tab === name ? ' is-active' : '')} onClick={() => setTab(name)}>
              {name[0].toUpperCase() + name.slice(1)}
            </button>
          ))}
          {SNAPSHOT_TABS.map(([name, key]) => (
            <button key={name} className={'tab' + (tab === name ? ' is-active' : '')} onClick={() => setTab(name)}>
              {t(key)}
            </button>
          ))}
        </div>

        {tab === 'adapters' && (
          <div className="tabpanel">
            <CreateAdapter client={client} onCreated={load} />
            {!adapters ? <Loading label="Loading adapters" />
              : adapters.length === 0 ? <Empty title="No adapters yet" sub="Create a draft above or run portal discovery." />
                : adapters.map((a) => (
                    <div key={a.id} className="row" style={{ cursor: 'pointer' }} onClick={() => setOpenId(a.id)}>
                      <div className="row__main">
                        <div className="row__title">{a.country} · {a.visa_type}</div>
                        <div className="row__sub">{a.config?.portal_operator || 'operator TBD'} · v{a.version} · {a.service_level}</div>
                      </div>
                      <StateChip state={a.lifecycle_state} enabled={a.production_enabled} kill={a.kill_switch} />
                    </div>
                  ))}
          </div>
        )}

        {tab === 'review queue' && (
          <div className="tabpanel">
            {!adapters ? <Loading /> : (() => {
              const q = adapters.filter((a) => ['technical_review', 'policy_review', 'mock_tested', 'staging_tested'].includes(a.lifecycle_state))
              return q.length === 0 ? <Empty title="Review queue empty" sub="Nothing awaiting review." />
                : q.map((a) => (
                    <div key={a.id} className="row" style={{ cursor: 'pointer' }} onClick={() => setOpenId(a.id)}>
                      <div className="row__main"><div className="row__title">{a.country} · {a.visa_type}</div>
                        <div className="row__sub">awaiting {STATE_LABELS[a.lifecycle_state]}</div></div>
                      <Icon.arrow style={{ width: 16, height: 16 }} />
                    </div>))
            })()}
          </div>
        )}

        {tab === 'coverage' && (
          <div className="tabpanel">
            <div className="card" style={{ padding: 16 }}>
              <div className="eyebrow">Honest country-coverage matrix</div>
              {coverage.map((c, i) => (
                <div key={i} className="row">
                  <div className="row__main"><div className="row__title">{c.country} · {c.visa_type}</div>
                    <div className="row__sub">{c.service_level.replace(/_/g, ' ')}</div></div>
                  <StateChip state={c.lifecycle_state} enabled={c.production_enabled} kill={c.kill_switch} />
                </div>
              ))}
              <div style={{ fontSize: 11, color: 'var(--muted-2)', marginTop: 8 }}>
                Only <em>production approved automation</em> means individually verified, tested, approved, monitored,
                and rollback-capable. Everything else is preparation / applicant-controlled handoff.
              </div>
            </div>
          </div>
        )}

        {tab === 'snapshot' && <SnapshotTab client={client} t={t} />}
        {tab === 'reviews' && <ReviewsTab client={client} t={t} />}
        {tab === 'conflicts' && <ConflictsTab client={client} t={t} />}
        {tab === 'route queue' && <RouteQueueTab client={client} t={t} />}
        {tab === 'adapter tasks' && <AdapterTasksTab client={client} t={t} />}
        {tab === 'research jobs' && <ResearchJobsTab client={client} t={t} />}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Snapshot-pipeline admin tabs
// ---------------------------------------------------------------------------

function pct(v) {
  const n = typeof v === 'number' && Number.isFinite(v) ? v : 0
  return (n * 100).toFixed(n > 0 && n < 0.001 ? 3 : 1) + '%'
}

function SnapshotTab({ client, t }) {
  const [cov, setCov] = useState(null)
  const [batches, setBatches] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => {
    client.adminSnapshotCoverage().then(setCov).catch((e) => setError({ message: e.message }))
    client.adminSnapshotBatches().then((r) => setBatches(r.batches || [])).catch(() => setBatches([]))
  }, [])
  if (error) return <div className="tabpanel"><ErrorNote error={error} /></div>
  if (!cov) return <div className="tabpanel"><Loading label="Loading snapshot coverage" /></div>
  const metrics = cov.metrics || {}
  const dispositions = cov.matrix?.dispositions || {}
  const counts = cov.counts || {}
  return (
    <div className="tabpanel">
      <div className="eyebrow">{t('admin.snapshot.metrics')} · {cov.snapshot_date}</div>
      <div className="grid grid-4" style={{ gap: 12 }}>
        {Object.entries(metrics).map(([k, v]) => (
          <div key={k} className="card stat">
            <div className="stat__num">{pct(v)}</div>
            <div className="stat__cap" style={{ fontSize: 11.5 }}>{k.replace(/_/g, ' ').toLowerCase()}</div>
          </div>
        ))}
      </div>

      <div className="eyebrow" style={{ marginTop: 22 }}>{t('admin.snapshot.dispositions')}</div>
      <div className="card" style={{ padding: 16 }}>
        <div className="kv">
          <div className="kv__k">expected / created</div>
          <div className="kv__v">{cov.matrix?.expected_entries ?? '—'} / {cov.matrix?.created_entries ?? '—'}</div>
        </div>
        {Object.entries(dispositions).map(([k, v]) => (
          <div className="kv" key={k}>
            <div className="kv__k">{k.replace(/_/g, ' ')}</div>
            <div className="kv__v">{v}</div>
          </div>
        ))}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }}>
          {Object.entries(counts).map(([k, v]) => (
            <span key={k} className="chip" title={k}>{k.replace(/_/g, ' ')}: {v}</span>
          ))}
        </div>
        {cov.notes && <div style={{ fontSize: 11, color: 'var(--muted-2)', marginTop: 10 }}>{cov.notes}</div>}
      </div>

      <div className="eyebrow" style={{ marginTop: 22 }}>{t('admin.snapshot.batches')}</div>
      <div className="card" style={{ padding: 0, maxHeight: 380, overflowY: 'auto' }}>
        {batches === null ? <Loading /> : batches.map((b) => (
          <div key={b.key} className="row" style={{ border: 0, borderBottom: '1px solid var(--line)', borderRadius: 0, margin: 0 }}>
            <div className="row__main">
              <div className="row__title">{b.destination} <span style={{ color: 'var(--muted)', fontWeight: 400 }}>· {b.key}</span></div>
              <div className="row__sub">
                attempts {b.attempts} · records {b.records} · conflicts {b.conflicts} · reviews {b.reviews}
              </div>
            </div>
            <span className="chip">{b.stage}{b.result ? ` · ${b.result}` : ''}</span>
          </div>
        ))}
      </div>

      <ReverifyCard client={client} t={t} />
    </div>
  )
}

// Manual reverification of a destination's official sources (admin-only).
// Records which URLs were re-checked, whether fee and portal were confirmed,
// and a mandatory note. The backend classifies each stored URL as a government
// domain or not — shown honestly per URL.
function ReverifyCard({ client, t }) {
  const toast = useToast()
  const [f, setF] = useState({ destination: '', urls: '', note: '', fee: false, portal: false })
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  async function submit() {
    const destination = f.destination.trim()
    const urls = f.urls.split('\n').map((s) => s.trim()).filter(Boolean)
    const note = f.note.trim()
    if (!destination) { toast(t('admin.reverify.destinationRequired')); return }
    if (urls.length === 0) { toast(t('admin.reverify.urlsRequired')); return }
    if (!note) { toast(t('admin.reverify.noteRequired')); return }
    setBusy(true); setError(null)
    try {
      const r = await client.adminReverify({
        destination_country: destination,
        urls,
        note,
        fee_confirmed: f.fee,
        portal_confirmed: f.portal
      })
      setResult(r)
      toast(t('admin.reverify.recorded'))
    } catch (e) { setError({ message: e.message }) }
    setBusy(false)
  }

  return (
    <>
      <div className="eyebrow" style={{ marginTop: 22 }}>{t('admin.reverify.title')}</div>
      <div className="card" style={{ padding: 16 }}>
        <div style={{ fontSize: 12.5, color: 'var(--muted)' }}>{t('admin.reverify.sub')}</div>
        <div className="grid grid-2" style={{ gap: 12, marginTop: 10 }}>
          <div className="field">
            <label>{t('admin.reverify.destination')}</label>
            <input className="input" value={f.destination}
              onChange={(e) => setF({ ...f, destination: e.target.value })} placeholder="FRA" />
          </div>
          <div className="field">
            <label>{t('admin.reverify.note')}</label>
            <input className="input" value={f.note}
              onChange={(e) => setF({ ...f, note: e.target.value })} />
          </div>
        </div>
        <div className="field" style={{ marginTop: 10 }}>
          <label>{t('admin.reverify.urls')}</label>
          <textarea className="input" rows={4} value={f.urls} spellCheck={false}
            style={{ fontFamily: 'monospace', fontSize: 12, resize: 'vertical' }}
            onChange={(e) => setF({ ...f, urls: e.target.value })}
            placeholder={'https://www.diplomatie.gouv.fr/…\nhttps://france-visas.gouv.fr/…'} />
        </div>
        <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', marginTop: 8 }}>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13 }}>
            <input type="checkbox" checked={f.fee} onChange={(e) => setF({ ...f, fee: e.target.checked })} />
            <span>{t('admin.reverify.fee')}</span>
          </label>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13 }}>
            <input type="checkbox" checked={f.portal} onChange={(e) => setF({ ...f, portal: e.target.checked })} />
            <span>{t('admin.reverify.portal')}</span>
          </label>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginTop: 12 }}>
          <div style={{ fontSize: 11, color: 'var(--muted-2)' }}>{t('admin.reverify.reminder')}</div>
          <button className="btn btn--sm" disabled={busy} onClick={submit}>
            {busy ? t('admin.reverify.submitting') : t('admin.reverify.submit')}
          </button>
        </div>
        {error && <ErrorNote error={error} />}
        {result && (
          <div style={{ marginTop: 14 }}>
            <div className="eyebrow">{t('admin.reverify.stored')} · {result.destination}</div>
            {(result.stored || []).map((s, i) => (
              <div key={i} className="row" style={{ alignItems: 'center' }}>
                <div className="row__main">
                  <div className="row__title" style={{ fontFamily: 'monospace', fontSize: 11.5, wordBreak: 'break-all' }}>
                    {s.url}
                  </div>
                </div>
                <span className={'chip' + (s.government_domain ? ' chip--ink' : '')}
                  style={s.government_domain ? undefined : { background: 'var(--crit)', color: '#fff', borderColor: 'var(--crit)' }}>
                  {s.government_domain ? t('admin.reverify.gov') : t('admin.reverify.nonGov')}
                </span>
              </div>
            ))}
            {result.note && <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 8 }}>{result.note}</div>}
          </div>
        )}
      </div>
    </>
  )
}

function ReviewsTab({ client, t }) {
  const toast = useToast()
  const [tasks, setTasks] = useState(null)
  const [notes, setNotes] = useState({})
  const [busy, setBusy] = useState('')
  const [error, setError] = useState(null)
  async function load() {
    try { setTasks((await client.adminReviewQueue()).tasks || []) }
    catch (e) { setError({ message: e.message }) }
  }
  useEffect(() => { load() }, [])
  async function act(id, status) {
    const note = (notes[id] || '').trim()
    if (!note) { toast(t('admin.reviews.noteRequired')); return }
    setBusy(id)
    try {
      await client.adminResolveReview(id, { resolution_note: note, status })
      await load()
    } catch (e) { setError({ message: e.message }) }
    setBusy('')
  }
  if (error) return <div className="tabpanel"><ErrorNote error={error} /></div>
  if (tasks === null) return <div className="tabpanel"><Loading /></div>
  return (
    <div className="tabpanel">
      {tasks.length === 0 ? <Empty title={t('admin.reviews.empty')} /> : tasks.map((task) => (
        <div key={task.id} className="card" style={{ padding: 16, marginBottom: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <div style={{ fontSize: 14.5, fontWeight: 600 }}>{task.title || task.id}</div>
              <div style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 3 }}>
                {task.kind} · {task.subject} · {task.created_at}
              </div>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 12, alignItems: 'center' }}>
            <input className="input" style={{ flex: 1 }} placeholder={t('admin.reviews.notePlaceholder')}
              value={notes[task.id] || ''}
              onChange={(e) => setNotes((p) => ({ ...p, [task.id]: e.target.value }))} />
            <button className="btn btn--sm" disabled={busy === task.id}
              onClick={() => act(task.id, 'resolved')}>{t('admin.reviews.resolve')}</button>
            <button className="btn btn--sm btn--ghost" disabled={busy === task.id}
              onClick={() => act(task.id, 'rejected')}>{t('admin.reviews.reject')}</button>
          </div>
        </div>
      ))}
    </div>
  )
}

function ConflictsTab({ client, t }) {
  const [conflicts, setConflicts] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => {
    client.adminConflicts().then((r) => setConflicts(r.conflicts || [])).catch((e) => setError({ message: e.message }))
  }, [])
  if (error) return <div className="tabpanel"><ErrorNote error={error} /></div>
  if (conflicts === null) return <div className="tabpanel"><Loading /></div>
  if (conflicts.length === 0) return <div className="tabpanel"><Empty title={t('admin.conflicts.empty')} /></div>
  return (
    <div className="tabpanel">
      {conflicts.map((c, i) => (
        <div key={c.id || i} className="card" style={{ padding: 14, marginBottom: 10 }}>
          <div style={{ fontSize: 14, fontWeight: 600 }}>
            {c.kind || c.field || 'conflict'}{c.route_key ? ` · ${c.route_key}` : ''}
          </div>
          <pre style={{ fontSize: 11, whiteSpace: 'pre-wrap', margin: '8px 0 0', color: 'var(--muted)', maxHeight: 180, overflow: 'auto' }}>
            {JSON.stringify(c, null, 2)}
          </pre>
        </div>
      ))}
    </div>
  )
}

function RouteQueueTab({ client, t }) {
  const [resolutions, setResolutions] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => {
    client.adminRouteQueue().then((r) => setResolutions(r.resolutions || [])).catch((e) => setError({ message: e.message }))
  }, [])
  if (error) return <div className="tabpanel"><ErrorNote error={error} /></div>
  if (resolutions === null) return <div className="tabpanel"><Loading /></div>
  if (resolutions.length === 0) return <div className="tabpanel"><Empty title={t('admin.routeQueue.empty')} /></div>
  return (
    <div className="tabpanel">
      {resolutions.map((r) => {
        const meta = readinessMeta(r.readiness)
        return (
          <div key={r.id} className="row" style={{ alignItems: 'flex-start' }}>
            <div className="row__main">
              <div className="row__title" style={{ fontSize: 12.5, fontFamily: 'monospace', wordBreak: 'break-all' }}>{r.route_key}</div>
              <div className="row__sub">org {r.org_id} · case {r.case_id || '—'} · {r.created_at}</div>
            </div>
            <span className={'chip' + (meta.tone === 'ok' ? ' chip--ink' : '')}>{t(meta.i18nKey)}</span>
          </div>
        )
      })}
    </div>
  )
}

function AdapterTasksTab({ client, t }) {
  const [tasks, setTasks] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => {
    client.adminAdapterTasks().then((r) => setTasks(r.tasks || [])).catch((e) => setError({ message: e.message }))
  }, [])
  if (error) return <div className="tabpanel"><ErrorNote error={error} /></div>
  if (tasks === null) return <div className="tabpanel"><Loading /></div>
  if (tasks.length === 0) return <div className="tabpanel"><Empty title={t('admin.adapterTasks.empty')} /></div>
  return (
    <div className="tabpanel">
      {tasks.map((task) => (
        <div key={task.id} className="card" style={{ padding: 14, marginBottom: 10 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10 }}>
            <div style={{ fontSize: 12.5, fontFamily: 'monospace', wordBreak: 'break-all', fontWeight: 600 }}>
              {task.route_key}
            </div>
            <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
              {task.priority != null && <span className="chip">P{task.priority}</span>}
              <span className="chip chip--ink">{task.status}</span>
            </div>
          </div>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 6 }}>
            case {task.case_id || '—'}
          </div>
          {(task.portal_evidence || task.jurisdiction_evidence || task.notes) && (
            <pre style={{ fontSize: 11, whiteSpace: 'pre-wrap', margin: '8px 0 0', color: 'var(--muted)', maxHeight: 160, overflow: 'auto' }}>
              {JSON.stringify({ portal_evidence: task.portal_evidence, jurisdiction_evidence: task.jurisdiction_evidence, notes: task.notes }, null, 2)}
            </pre>
          )}
        </div>
      ))}
    </div>
  )
}

// On-demand research jobs started by applicant resolves (focused per-route
// research). Status chip tone comes from the same fail-safe mapping the
// applicant UI uses (researchStatusMeta).
function ResearchStatusChip({ status, t }) {
  const meta = researchStatusMeta(status)
  const style = meta.tone === 'blocked' ? { background: 'var(--crit)', color: '#fff', borderColor: 'var(--crit)' }
    : meta.tone === 'warn' ? { borderColor: 'var(--ink)' } : undefined
  return (
    <span className={'chip' + (meta.tone === 'ok' ? ' chip--ink' : '')} style={style} data-tone={meta.tone}>
      {t(meta.i18nKey)}
    </span>
  )
}

const RJ_TH = { textAlign: 'left', fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.04em',
  color: 'var(--muted-2)', fontWeight: 600, padding: '10px 12px', borderBottom: '1px solid var(--line)', whiteSpace: 'nowrap' }
const RJ_TD = { fontSize: 12.5, padding: '9px 12px', borderBottom: '1px solid var(--line)', verticalAlign: 'top', whiteSpace: 'nowrap' }

function ResearchJobsTab({ client, t }) {
  const [jobs, setJobs] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  async function load() {
    setBusy(true)
    try {
      setJobs((await client.adminResearchJobs()).jobs || [])
      setError(null)
    } catch (e) { setError({ message: e.message }) }
    setBusy(false)
  }
  useEffect(() => { load() }, [])
  return (
    <div className="tabpanel">
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 10 }}>
        <button className="btn btn--sm btn--ghost" disabled={busy} onClick={load}>
          {busy ? '…' : t('admin.researchJobs.refresh')}
        </button>
      </div>
      {error && <ErrorNote error={error} />}
      {jobs === null ? <Loading />
        : jobs.length === 0 ? <Empty title={t('admin.researchJobs.empty')} />
          : (
            <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th style={RJ_TH}>route</th>
                    <th style={RJ_TH}>status</th>
                    <th style={RJ_TH}>stage</th>
                    <th style={RJ_TH}>attempts</th>
                    <th style={RJ_TH}>researched</th>
                    <th style={RJ_TH}>model</th>
                    <th style={RJ_TH}>created</th>
                  </tr>
                </thead>
                <tbody>
                  {jobs.map((j) => (
                    <tr key={j.id}>
                      <td style={RJ_TD}>
                        <span title={j.route_key} style={{ fontFamily: 'monospace', fontSize: 11.5, display: 'inline-block',
                          maxWidth: 240, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', verticalAlign: 'bottom' }}>
                          {j.route_key || '—'}
                        </span>
                      </td>
                      <td style={RJ_TD}><ResearchStatusChip status={j.status} t={t} /></td>
                      <td style={RJ_TD}><span style={{ fontFamily: 'monospace', fontSize: 11 }}>{j.stage || '—'}</span></td>
                      <td style={RJ_TD}>{j.attempts ?? '—'}</td>
                      <td style={RJ_TD}>{j.researched_at || '—'}</td>
                      <td style={RJ_TD}>{j.kimi_model || '—'}</td>
                      <td style={RJ_TD}>{j.created_at || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
    </div>
  )
}

function CreateAdapter({ client, onCreated }) {
  const toast = useToast()
  const [f, setF] = useState({ country: '', visa_type: 'tourist', operator: '', domains: '' })
  const [busy, setBusy] = useState(false)
  async function create() {
    if (!f.country) { toast('Country required'); return }
    setBusy(true)
    try {
      await client.adminCreateAdapter({ country: f.country, visa_type: f.visa_type,
        config: { portal_operator: f.operator, official_domains: f.domains.split(',').map((s) => s.trim()).filter(Boolean) } })
      toast('Draft adapter created'); setF({ country: '', visa_type: 'tourist', operator: '', domains: '' }); onCreated()
    } catch (e) { toast(e.message) }
    setBusy(false)
  }
  return (
    <div className="card" style={{ padding: 16, marginBottom: 14 }}>
      <div className="eyebrow">New adapter draft</div>
      <div className="grid grid-2" style={{ gap: 12, marginTop: 8 }}>
        <div className="field"><label>Country</label><input className="input" value={f.country} onChange={(e) => setF({ ...f, country: e.target.value })} /></div>
        <div className="field"><label>Visa type</label><input className="input" value={f.visa_type} onChange={(e) => setF({ ...f, visa_type: e.target.value })} /></div>
        <div className="field"><label>Portal operator</label><input className="input" value={f.operator} onChange={(e) => setF({ ...f, operator: e.target.value })} /></div>
        <div className="field"><label>Official domains (comma-sep)</label><input className="input" value={f.domains} onChange={(e) => setF({ ...f, domains: e.target.value })} /></div>
      </div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 8 }}>
        <button className="btn btn--sm" disabled={busy} onClick={create}>{busy ? 'Creating…' : 'Create draft'}</button>
      </div>
    </div>
  )
}

function AdapterDetail({ client, id, isAdmin, onBack, onChanged }) {
  const toast = useToast()
  const [a, setA] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState('')

  async function load() {
    try { setA(await client.adminGetAdapter(id)) } catch (e) { setError({ message: e.message }) }
  }
  useEffect(() => { load() }, [id])

  async function act(fn, label) {
    setBusy(label); setError(null)
    try { await fn(); await load(); onChanged && onChanged(); toast(label + ' ✓') }
    catch (e) { setError({ message: e.message }); }
    setBusy('')
  }

  if (!a) return <div className="page"><Loading label="Loading adapter" /></div>
  const activation = ACTIVATION.has(a.lifecycle_state)

  return (
    <div>
      <div className="topbar">
        <button className="iconbtn" onClick={onBack}><Icon.back style={{ width: 18, height: 18 }} /></button>
        <h1>{a.country} · {a.visa_type}</h1>
        <div style={{ marginLeft: 10 }}><StateChip state={a.lifecycle_state} enabled={a.production_enabled} kill={a.kill_switch} /></div>
      </div>
      <div className="page page--wide">
        {error && <ErrorNote error={error} />}
        <div className="grid grid-2" style={{ gap: 20, alignItems: 'start' }}>
          <div>
            <div className="eyebrow">Lifecycle</div>
            <div className="card" style={{ padding: 16 }}>
              <KVList fields={[
                { label: 'State', value: STATE_LABELS[a.lifecycle_state] || a.lifecycle_state },
                { label: 'Approval', value: a.approval_status },
                { label: 'Production enabled', value: String(a.production_enabled) },
                { label: 'Kill switch', value: String(a.kill_switch) },
                { label: 'Version', value: 'v' + a.version },
                { label: 'Monitoring', value: a.monitoring_status },
                { label: 'Service level', value: a.service_level.replace(/_/g, ' ') }
              ]} />
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }}>
                {(a.allowed_transitions || []).map((s) => {
                  const needsAdmin = ACTIVATION.has(s)
                  return (
                    <button key={s} className={'btn btn--sm' + (needsAdmin ? '' : ' btn--ghost')}
                      disabled={!!busy || (needsAdmin && !isAdmin)}
                      title={needsAdmin && !isAdmin ? 'Administrator role required' : ''}
                      onClick={() => act(() => client.adminTransition(id, s, { via: 'admin_console' }), '→ ' + STATE_LABELS[s])}>
                      → {STATE_LABELS[s] || s}{needsAdmin ? ' (admin)' : ''}
                    </button>
                  )
                })}
              </div>
              <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                {!a.kill_switch
                  ? <button className="btn btn--sm" style={{ background: 'var(--crit)' }} disabled={!isAdmin || !!busy}
                      onClick={() => act(() => client.adminKill(id, 'admin console'), 'Kill')}>Emergency kill</button>
                  : <button className="btn btn--sm btn--ghost" disabled={!isAdmin || !!busy}
                      onClick={() => act(() => client.adminClearKill(id), 'Clear kill')}>Clear kill switch</button>}
              </div>
              {activation && <div style={{ fontSize: 11, color: 'var(--muted-2)', marginTop: 8 }}>
                Activation is human-administrator only. No AI/automated actor can approve or activate an adapter.
              </div>}
            </div>

            <div className="eyebrow" style={{ marginTop: 20 }}>Versions & rollback</div>
            <div className="card" style={{ padding: 12 }}>
              {(a.versions || []).map((v) => (
                <div key={v.version} className="row">
                  <div className="row__main"><div className="row__title">v{v.version} · {STATE_LABELS[v.lifecycle_state] || v.lifecycle_state}</div>
                    <div className="row__sub">by {v.created_by}</div></div>
                  <button className="btn btn--sm btn--ghost" disabled={!isAdmin || !!busy}
                    onClick={() => act(() => client.adminRollback(id, v.version), 'Rollback to v' + v.version)}>Roll back</button>
                </div>
              ))}
            </div>
          </div>

          <div>
            <div className="eyebrow">Configuration</div>
            <div className="card" style={{ padding: 14 }}>
              <pre style={{ fontSize: 11, whiteSpace: 'pre-wrap', maxHeight: 260, overflow: 'auto', margin: 0 }}>
                {JSON.stringify(a.config, null, 2)}
              </pre>
            </div>

            <div className="eyebrow" style={{ marginTop: 20 }}>Immutable audit</div>
            <div className="card" style={{ padding: 12 }}>
              {(a.audit || []).map((e) => (
                <div key={e.seq} className="row">
                  <div className="row__main"><div className="row__title" style={{ textTransform: 'capitalize' }}>{e.action.replace(/_/g, ' ')}</div>
                    <div className="row__sub">{e.actor}{e.detail?.to ? ` → ${e.detail.to}` : ''}</div></div>
                  <span className="chip" style={{ fontSize: 10 }}>#{e.seq}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
