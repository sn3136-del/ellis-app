import { Icon } from '../components/icons.jsx'
import { ROLES } from '../lib/api.js'
import { wordmarkInk, tripcomLogo } from '../assets/logos.js'

const ICONS = { immigrant: Icon.user, employer: Icon.building, counsel: Icon.scale }

export default function RoleSelect({ onPick }) {
  return (
    <div className="roles-screen">
      <div className="roles-wrap">
        <img className="roles-logo" src={wordmarkInk} alt="Ellis" />
        <h1 className="h-serif" style={{ fontSize: 44, marginTop: 6 }}>Who is using Ellis today?</h1>
        <div className="roles-grid roles-grid--4">
          {Object.values(ROLES).map((r) => {
            const Ico = ICONS[r.id]
            return (
              <button className="rolecard" key={r.id} onClick={() => onPick(r.id)}>
                {r.id === 'tripcom'
                  ? <div className="rolecard__icon rolecard__icon--brand"><img src={tripcomLogo} alt="Trip.com" /></div>
                  : <div className="rolecard__icon"><Ico /></div>}
                <h3>{r.label}</h3>
                <p>{r.blurb}</p>
                <span className="rolecard__go">Continue <Icon.arrow style={{ width: 15, height: 15 }} /></span>
              </button>
            )
          })}
        </div>
      </div>
    </div>
  )
}
