// Admin Console — country-adapter administration + approval lifecycle (Phase 2).
// Drives each adapter through discovered → … → production_active with human-only
// activation. Talks to the backend admin API (admin token grants the role); no
// provider credentials ever touch this renderer.
import { useEffect, useState } from 'react'
import { useToast, Loading, ErrorNote, Empty, KVList } from '../components/ui.jsx'
import { Icon } from '../components/icons.jsx'
import { createVisaClient } from '../lib/visaBackend.js'
import { newAdminSession } from '../lib/visaSession.js'

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

export default function AdminConsole() {
  const toast = useToast()
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
          {['adapters', 'review queue', 'coverage'].map((t) => (
            <button key={t} className={'tab' + (tab === t ? ' is-active' : '')} onClick={() => setTab(t)}>
              {t[0].toUpperCase() + t.slice(1)}
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
      </div>
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
