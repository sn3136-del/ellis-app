import { useState, useEffect } from 'react'
import { ellis, COUNTRIES, DESTINATIONS, PATHWAYS, VISA_TYPES, fmtDate, filterCasesForProfile } from '../lib/api.js'
import { Icon } from '../components/icons.jsx'
import { Empty, useToast } from '../components/ui.jsx'

export default function Cases({ role, profile, onOpen }) {
  const [cases, setCases] = useState([])
  const [creating, setCreating] = useState(false)

  async function load() { setCases(filterCasesForProfile(await ellis.listCases(), role, profile)) }
  useEffect(() => { load() }, [])

  return (
    <div>
      <div className="topbar">
        <h1>Cases</h1>
        <div className="topbar__actions">
          <button className="btn" onClick={() => setCreating(true)}><Icon.plus style={{ width: 16, height: 16 }} /> New case</button>
        </div>
      </div>
      <div className="page page--wide">
        {cases.length === 0
          ? <Empty title="No cases yet" sub="Create your first immigration case to start using Ellis."><button className="btn" onClick={() => setCreating(true)}>Create a case</button></Empty>
          : cases.map((c) => (
            <button key={c.id} className="row" style={{ width: '100%', textAlign: 'left', cursor: 'pointer' }} onClick={() => onOpen(c.id)}>
              <div className="captile__icon" style={{ width: 38, height: 38 }}><Icon.folder style={{ width: 18, height: 18 }} /></div>
              <div className="row__main">
                <div className="row__title">{c.applicantName}</div>
                <div className="row__sub">{c.originCountry || 'Origin'} → {c.destinationCountry} · {c.pathway}{c.visaType ? ` · ${c.visaType}` : ''} · {(c.documents || []).length} docs</div>
              </div>
              <span className="chip">{c.facts?.stage || 'New'}</span>
              <span style={{ fontSize: 12, color: 'var(--muted)' }}>{fmtDate(c.updatedAt)}</span>
              <Icon.arrow style={{ width: 18, height: 18, color: 'var(--muted)' }} />
            </button>
          ))}
      </div>
      {creating && <CreateModal role={role} profile={profile} onClose={() => setCreating(false)} onCreated={(id) => { setCreating(false); onOpen(id) }} />}
    </div>
  )
}

function CreateModal({ role, profile, onClose, onCreated }) {
  const toast = useToast()
  const [f, setF] = useState({
    applicantName: '',
    originCountry: profile?.originCountry || 'China',
    destinationCountry: profile?.destinationCountry || 'USA',
    pathway: profile?.pathway || 'work',
    visaType: '',
    employer: ''
  })
  const visaOpts = VISA_TYPES[f.destinationCountry]?.[f.pathway] || []

  async function create() {
    if (!f.applicantName.trim()) return
    const c = await ellis.createCase({ ...f, ownerRole: role })
    toast('Case created')
    onCreated(c.id)
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>New case</h2>
        <div className="modal__sub">Set up an immigration case. Ellis supports USA and Canada, including cross-border routes.</div>

        <div className="field"><label>Applicant name</label><input className="input" value={f.applicantName} onChange={(e) => setF({ ...f, applicantName: e.target.value })} placeholder="Full name" autoFocus /></div>

        <div className="grid grid-2">
          <div className="field"><label>Origin country (any country)</label>
            <input className="input" list="country-list" value={f.originCountry} onChange={(e) => setF({ ...f, originCountry: e.target.value })} placeholder="Type or pick a country" />
            <datalist id="country-list">{COUNTRIES.map((c) => <option key={c} value={c} />)}</datalist>
          </div>
          <div className="field"><label>Destination</label>
            <select className="select" value={f.destinationCountry} onChange={(e) => setF({ ...f, destinationCountry: e.target.value, visaType: '' })}>
              {DESTINATIONS.map((c) => <option key={c}>{c}</option>)}
            </select>
          </div>
        </div>

        <div className="grid grid-2">
          <div className="field"><label>Pathway</label>
            <select className="select" value={f.pathway} onChange={(e) => setF({ ...f, pathway: e.target.value, visaType: '' })}>
              {PATHWAYS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
            </select>
          </div>
          <div className="field"><label>Visa / permit type</label>
            <select className="select" value={f.visaType} onChange={(e) => setF({ ...f, visaType: e.target.value })}>
              <option value="">Select...</option>
              {visaOpts.map((v) => <option key={v}>{v}</option>)}
            </select>
          </div>
        </div>

        {(role === 'employer' || f.pathway === 'work') && (
          <div className="field"><label>Employer</label><input className="input" value={f.employer} onChange={(e) => setF({ ...f, employer: e.target.value })} placeholder="Sponsoring employer" /></div>
        )}

        <div className="modal__foot">
          <button className="btn btn--ghost" onClick={onClose}>Cancel</button>
          <button className="btn" onClick={create} disabled={!f.applicantName.trim()}>Create case</button>
        </div>
      </div>
    </div>
  )
}
