// Visa Console — the applicant/Trip.com-facing graphical workflow for the
// production visa backend. Talks to the FastAPI backend over authenticated HTTP
// (src/lib/visaBackend.js); the backend is the only component that reaches
// providers (Document AI, Kimi, Browserbase, portals). No provider credentials
// ever live in this renderer.
import { useEffect, useState } from 'react'
import { useToast, Loading, ErrorNote, Empty } from '../components/ui.jsx'
import { Icon } from '../components/icons.jsx'
import { useLocale } from '../lib/locale.jsx'
import { createVisaClient } from '../lib/visaBackend.js'
import { newSession } from '../lib/visaSession.js'
import CaseFlow from '../components/visa/CaseFlow.jsx'
import StartVisa from '../components/visa/StartVisa.jsx'

const LS_KEY = 'ellis.visa.cases'
function loadCases() {
  try { return JSON.parse(localStorage.getItem(LS_KEY) || '[]') } catch { return [] }
}
function saveCases(list) { try { localStorage.setItem(LS_KEY, JSON.stringify(list)) } catch { /* ignore */ } }

export default function VisaConsole({ profile, onNotify }) {
  const toast = useToast()
  const { t } = useLocale()
  const [session] = useState(() => newSession({ orgId: profile?.orgId || 'ellis-demo' }))
  const [client] = useState(() => createVisaClient(session))
  const [caps, setCaps] = useState(null)
  const [adapters, setAdapters] = useState([])
  const [cases, setCases] = useState(loadCases)
  const [openId, setOpenId] = useState(null)
  const [reachable, setReachable] = useState(null)
  const [creating, setCreating] = useState(false)
  // The route-intake wizard is the FIRST thing an applicant sees; the existing
  // case list / session tools stay reachable behind the second tab.
  const [tab, setTab] = useState('start')

  useEffect(() => {
    client.capabilities().then((c) => { setCaps(c); setReachable(true) }).catch(() => setReachable(false))
    client.listAdapters().then((r) => setAdapters(r.adapters || [])).catch(() => {})
  }, [])

  function addCase(c) {
    const next = [c, ...cases.filter((x) => x.id !== c.id)]
    setCases(next); saveCases(next)
  }

  if (openId) {
    const c = cases.find((x) => x.id === openId)
    return (
      <div>
        <div className="topbar">
          <button className="iconbtn" onClick={() => setOpenId(null)} aria-label="Back"><Icon.back style={{ width: 18, height: 18 }} /></button>
          <h1>{c?.full_name || 'Case'}</h1>
          <div style={{ fontSize: 13, color: 'var(--muted)', marginLeft: 8 }}>{c?.destination_country} · {c?.visa_type}</div>
        </div>
        <div className="page page--wide">
          <CaseFlow client={client} caseId={openId} onNotify={onNotify} />
        </div>
      </div>
    )
  }

  return (
    <div>
      <div className="topbar">
        <h1>Visa Platform</h1>
        <div className="topbar__actions">
          <span className={'chip' + (reachable ? ' chip--ink' : '')}>
            {reachable === null ? 'connecting…' : reachable ? 'backend online' : 'backend offline'}
          </span>
        </div>
      </div>

      <div className="page page--wide">
        {reachable === false && (
          <ErrorNote error={{ message: 'Cannot reach the Ellis backend at localhost:8000. Start it with `docker compose up` in backend/.' }} />
        )}

        <div className="tabs">
          <button className={'tab' + (tab === 'start' ? ' is-active' : '')} onClick={() => setTab('start')}>
            {t('visa.tab.start')}
          </button>
          <button className={'tab' + (tab === 'cases' ? ' is-active' : '')} onClick={() => setTab('cases')}>
            {t('visa.tab.cases')}
          </button>
        </div>

        {tab === 'start' && (
          <div className="tabpanel">
            <StartVisa client={client} />
          </div>
        )}

        {tab === 'cases' && (
        <div className="grid grid-2" style={{ gap: 20, alignItems: 'start' }}>
          <div>
            <div className="eyebrow">Start a case</div>
            <NewCaseForm client={client} busy={creating} setBusy={setCreating}
                         onCreated={(c) => { addCase(c); toast('Case created'); setOpenId(c.id) }} />

            <div className="eyebrow" style={{ marginTop: 24 }}>Your cases</div>
            {cases.length === 0
              ? <Empty title="No cases yet" sub="Create your first tourist-visa case above." />
              : cases.map((c) => (
                  <div key={c.id} className="row" onClick={() => setOpenId(c.id)} style={{ cursor: 'pointer' }}>
                    <div className="row__main">
                      <div className="row__title">{c.full_name}</div>
                      <div className="row__sub">{c.destination_country} · {c.visa_type} · {c.id.slice(0, 8)}</div>
                    </div>
                    <Icon.arrow style={{ width: 16, height: 16 }} />
                  </div>
                ))}
          </div>

          <div>
            <div className="eyebrow">Capabilities</div>
            {!caps ? <Loading label="Reading capabilities" /> : <CapabilityGrid caps={caps} />}

            <div className="eyebrow" style={{ marginTop: 24 }}>Supported destinations</div>
            <CountryMatrix adapters={adapters} />
          </div>
        </div>
        )}
      </div>
    </div>
  )
}

function NewCaseForm({ client, onCreated, busy, setBusy }) {
  const [f, setF] = useState({ full_name: '', email: '', phone: '', destination_country: 'Mockland', visa_type: 'tourist', time_zone: 'UTC' })
  const [error, setError] = useState(null)
  const set = (k, v) => setF((p) => ({ ...p, [k]: v }))
  async function create() {
    if (!f.full_name || !f.email) { setError({ message: 'Name and email are required' }); return }
    setBusy(true); setError(null)
    try {
      const res = await client.createCase(f)
      onCreated({ id: res.id, ...f, createdAt: Date.now() })
    } catch (e) { setError({ message: e.message }) }
    setBusy(false)
  }
  return (
    <div className="card" style={{ padding: 18 }}>
      <div className="grid grid-2" style={{ gap: 12 }}>
        <div className="field"><label>Full legal name</label><input className="input" value={f.full_name} onChange={(e) => set('full_name', e.target.value)} /></div>
        <div className="field"><label>Email</label><input className="input" value={f.email} onChange={(e) => set('email', e.target.value)} /></div>
        <div className="field"><label>Phone</label><input className="input" value={f.phone} onChange={(e) => set('phone', e.target.value)} /></div>
        <div className="field"><label>Time zone</label><input className="input" value={f.time_zone} onChange={(e) => set('time_zone', e.target.value)} /></div>
        <div className="field"><label>Destination</label>
          <select className="select" value={f.destination_country} onChange={(e) => set('destination_country', e.target.value)}>
            <option>Mockland</option><option>Vietnam</option>
          </select></div>
        <div className="field"><label>Visa type</label>
          <select className="select" value={f.visa_type} onChange={(e) => set('visa_type', e.target.value)}>
            <option value="tourist">Tourist</option>
          </select></div>
      </div>
      {error && <ErrorNote error={error} />}
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 10 }}>
        <button className="btn" disabled={busy} onClick={create}>{busy ? 'Creating…' : 'Create case'}</button>
      </div>
    </div>
  )
}

function CapabilityGrid({ caps }) {
  // caps is a flat/nested map of booleans + labels — render honestly as on/off.
  const entries = Object.entries(caps).filter(([, v]) => typeof v !== 'object')
  return (
    <div className="card" style={{ padding: 16 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        {entries.map(([k, v]) => (
          <span key={k} className={'chip' + (v === true ? ' chip--ink' : '')} title={String(v)}>
            {k.replace(/_/g, ' ')}{v !== true && v !== false ? `: ${v}` : ''}
          </span>
        ))}
      </div>
    </div>
  )
}

function CountryMatrix({ adapters }) {
  if (!adapters.length) return <Empty title="No adapters" sub="Adapters load from the backend." />
  return (
    <div className="card" style={{ padding: 16 }}>
      {adapters.map((a) => {
        const live = a.productionEnabled && a.approvalStatus === 'approved'
        return (
          <div key={a.adapterId || a.country} className="row">
            <div className="row__main">
              <div className="row__title">{a.country} · {a.visaType || a.visa_type || 'tourist'}</div>
              <div className="row__sub">{a.portalOperator || a.operator || 'official portal'}</div>
            </div>
            <span className={'chip' + (live ? ' chip--ink' : '')}>
              {live ? 'production' : (a.testStatus || a.approvalStatus || 'in development')}
            </span>
          </div>
        )
      })}
      <div style={{ fontSize: 11, color: 'var(--muted-2)', marginTop: 8 }}>
        A destination is marked <em>production</em> only after its official portal is verified and its
        adapter is implemented, tested, reviewed, approved, monitored, and rollback-capable.
      </div>
    </div>
  )
}
