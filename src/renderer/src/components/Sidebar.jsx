import { useState, useEffect } from 'react'
import { Icon } from './icons.jsx'
import { ellis } from '../lib/api.js'
import { wordmarkWhite } from '../assets/logos.js'

const NAV = [
  { id: 'dashboard', label: 'Home', icon: 'home' },
  { id: 'cases', label: 'Cases', icon: 'cases' },
  { id: 'visa', label: 'Visa Platform', icon: 'globe' },
  { id: 'assistant', label: 'Ask Ellis', icon: 'chat' },
  { id: 'notifications', label: 'Notifications', icon: 'bell' }
]

export default function Sidebar({ role, view, onNav, onSwitchRole, onTour, notifTick }) {
  const [unread, setUnread] = useState(0)

  useEffect(() => {
    let alive = true
    const load = async () => {
      const n = await ellis.listNotifs(role)
      if (alive) setUnread(n.filter((x) => !x.read).length)
    }
    load()
    const iv = setInterval(load, 2500)
    return () => { alive = false; clearInterval(iv) }
  }, [role, view, notifTick])

  return (
    <aside className="sidebar">
      <div className="sidebar__top">
        <img className="sidebar__logo" src={wordmarkWhite} alt="Ellis" />
      </div>
      <nav className="nav">
        <div className="nav__label">Workspace</div>
        {NAV.map((n) => {
          const Ico = Icon[n.icon]
          return (
            <button key={n.id} data-tour={'nav-' + n.id} className={'nav__item' + (view === n.id ? ' is-active' : '')} onClick={() => onNav(n.id)}>
              <Ico /> <span style={{ flex: 1 }}>{n.label}</span>
              {n.id === 'notifications' && unread > 0 && <span className="nav__badge">{unread}</span>}
            </button>
          )
        })}
        <div className="nav__label">Account</div>
        <button className={'nav__item' + (view === 'settings' ? ' is-active' : '')} onClick={() => onNav('settings')}>
          <Icon.gear /> Settings
        </button>
      </nav>
      <div className="sidebar__foot" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <button className="sidebar__switch" onClick={onTour}>Take a tour</button>
        <button className="sidebar__switch" onClick={onSwitchRole}>Switch role</button>
      </div>
    </aside>
  )
}
