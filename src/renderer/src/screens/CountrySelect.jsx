import { useState } from 'react'
import { ROLES } from '../lib/api.js'
import { Icon } from '../components/icons.jsx'
import { wordmarkInk } from '../assets/logos.js'

const FLAG = { China: '🇨🇳', India: '🇮🇳', Vietnam: '🇻🇳', USA: '🇺🇸', Canada: '🇨🇦' }

// Build the ordered question flow for each role.
function flowFor(role) {
  if (role === 'immigrant') {
    return [
      { key: 'originCountry', title: 'Where are you from?', options: [{ id: 'China', label: 'China' }, { id: 'India', label: 'India' }, { id: 'Vietnam', label: 'Vietnam' }] },
      { key: 'destinationCountry', title: 'Where are you immigrating to?', options: [{ id: 'USA', label: 'United States' }, { id: 'Canada', label: 'Canada' }] },
      { key: 'pathway', title: 'What are you applying for?', kind: 'purpose', options: [{ id: 'study', label: 'Study', icon: 'doc' }, { id: 'work', label: 'Work', icon: 'building' }, { id: 'travel', label: 'Travel', icon: 'plane' }] }
    ]
  }
  if (role === 'employer') {
    return [{ key: 'destinationCountry', title: 'Where is your organization based?', options: [{ id: 'USA', label: 'United States' }, { id: 'Canada', label: 'Canada' }] }]
  }
  // counsel
  return [{ key: 'destinationCountry', title: 'Which system do you practice in?', options: [{ id: 'USA', label: 'United States' }, { id: 'Canada', label: 'Canada' }] }]
}

export default function CountrySelect({ role, onDone, onBack }) {
  const flow = flowFor(role)
  const [step, setStep] = useState(0)
  const [answers, setAnswers] = useState({})
  const q = flow[step]

  function pick(optId) {
    const next = { ...answers, [q.key]: optId }
    setAnswers(next)
    if (step < flow.length - 1) { setStep(step + 1); return }
    const jurisdiction = next.destinationCountry === 'Canada' ? 'Canada' : 'USA'
    onDone({ role, originCountry: next.originCountry || null, destinationCountry: next.destinationCountry, pathway: next.pathway || null, jurisdiction })
  }

  function back() { step > 0 ? setStep(step - 1) : onBack() }

  return (
    <div className="roles-screen">
      <div className="roles-wrap">
        <img className="roles-logo" src={wordmarkInk} alt="Ellis" />
        <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 4, textTransform: 'uppercase', letterSpacing: 1 }}>{ROLES[role].label}</div>
        <h1 className="h-serif" style={{ fontSize: 40, marginTop: 2, marginBottom: 26 }}>{q.title}</h1>

        <div className="roles-grid" style={{ maxWidth: q.options.length >= 3 ? 720 : 520, gridTemplateColumns: `repeat(${Math.min(q.options.length, 3)}, 1fr)`, margin: '0 auto', justifyContent: 'center' }}>
          {q.options.map((o) => (
            <button className="rolecard" key={o.id} onClick={() => pick(o.id)}>
              {q.kind === 'purpose'
                ? <div className="rolecard__icon">{(() => { const I = Icon[o.icon]; return <I /> })()}</div>
                : <div style={{ fontSize: 46, lineHeight: 1 }}>{FLAG[o.id]}</div>}
              <h3>{o.label}</h3>
              <span className="rolecard__go">Continue <Icon.arrow style={{ width: 15, height: 15 }} /></span>
            </button>
          ))}
        </div>

        <button className="btn btn--ghost" style={{ marginTop: 26 }} onClick={back}>Back</button>
      </div>
    </div>
  )
}
