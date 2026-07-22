import { useState, useEffect } from 'react'
import { ellis, ROLES } from '../lib/api.js'
import { Icon } from './icons.jsx'

const ROLE_CARDS = [
  { id: 'immigrant', label: 'Immigrant', desc: 'The applicant: documents, questions, travel, translation.', icon: 'user' },
  { id: 'employer', label: 'Employer', desc: 'The sponsor: workforce compliance, risks, evidence.', icon: 'building' },
  { id: 'counsel', label: 'Counsel', desc: 'The attorney: the full toolkit and the final filing.', icon: 'scale' }
]

export default function TourPicker({ onPick, onClose }) {
  const [role, setRole] = useState(null)
  const [cases, setCases] = useState([])
  useEffect(() => { ellis.listCases().then(setCases) }, [])

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 580 }} onClick={(e) => e.stopPropagation()}>
        {!role ? (
          <>
            <h2>Take the guided tour</h2>
            <div className="modal__sub">First, choose the interface you want to see. Ellis looks different for each role.</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {ROLE_CARDS.map((r) => {
                const Ico = Icon[r.icon]
                return (
                  <button key={r.id} className="row" style={{ width: '100%', textAlign: 'left', cursor: 'pointer' }} onClick={() => setRole(r.id)}>
                    <div className="captile__icon" style={{ width: 38, height: 38 }}><Ico style={{ width: 18, height: 18 }} /></div>
                    <div className="row__main">
                      <div className="row__title">{r.label}</div>
                      <div className="row__sub">{r.desc}</div>
                    </div>
                    <Icon.arrow style={{ width: 16, height: 16, color: 'var(--muted)' }} />
                  </button>
                )
              })}
            </div>
            <div className="modal__foot"><button className="btn btn--ghost" onClick={onClose}>Cancel</button></div>
          </>
        ) : (
          <>
            <h2>Choose a case</h2>
            <div className="modal__sub">Touring as <strong>{ROLES?.[role]?.label || role}</strong>. Pick a template and Ellis will walk you through the whole workflow — press space to advance.</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: '50vh', overflowY: 'auto' }}>
              {cases.map((c) => (
                <button key={c.id} className="row" style={{ width: '100%', textAlign: 'left', cursor: 'pointer' }} onClick={() => onPick(role, c)}>
                  <div className="captile__icon" style={{ width: 34, height: 34 }}><Icon.folder style={{ width: 16, height: 16 }} /></div>
                  <div className="row__main">
                    <div className="row__title">{c.applicantName}</div>
                    <div className="row__sub">{c.originCountry} → {c.destinationCountry} · {c.pathway}{c.visaType ? ` · ${c.visaType}` : ''}</div>
                  </div>
                  <Icon.arrow style={{ width: 16, height: 16, color: 'var(--muted)' }} />
                </button>
              ))}
            </div>
            <div className="modal__foot"><button className="btn btn--ghost" onClick={() => setRole(null)}>Back</button></div>
          </>
        )}
      </div>
    </div>
  )
}
