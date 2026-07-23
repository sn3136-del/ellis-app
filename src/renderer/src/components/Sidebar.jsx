import { useState, useEffect } from 'react'
import { Icon } from './icons.jsx'
import { ellis } from '../lib/api.js'
import { wordmarkWhite } from '../assets/logos.js'
import { useLocale, LanguageToggle } from '../lib/locale.jsx'

const NAV = [
  { id: 'dashboard', key: 'nav.dashboard', icon: 'home' },
  { id: 'cases', key: 'nav.cases', icon: 'cases' },
  { id: 'visa', key: 'nav.visa', icon: 'globe' },
  { id: 'admin', key: 'nav.admin', icon: 'shield' },
  { id: 'assistant', key: 'nav.assistant', icon: 'chat' },
  { id: 'notifications', key: 'nav.notifications', icon: 'bell' }
]

export default function Sidebar({ role, view, onNav, onSwitchRole, onTour, notifTick }) {
  const [unread, setUnread] = useState(0)
  const { t } = useLocale()

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
        <div className="nav__label">{t('section.workspace')}</div>
        {NAV.map((n) => {
          const Ico = Icon[n.icon]
          return (
            <button key={n.id} data-tour={'nav-' + n.id} className={'nav__item' + (view === n.id ? ' is-active' : '')} onClick={() => onNav(n.id)}>
              <Ico /> <span style={{ flex: 1 }}>{t(n.key)}</span>
              {n.id === 'notifications' && unread > 0 && <span className="nav__badge">{unread}</span>}
            </button>
          )
        })}
        <div className="nav__label">{t('section.account')}</div>
        <button className={'nav__item' + (view === 'settings' ? ' is-active' : '')} onClick={() => onNav('settings')}>
          <Icon.gear /> {t('nav.settings')}
        </button>
      </nav>
      <div className="sidebar__foot" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div className="nav__label" style={{ marginBottom: 0 }}>{t('common.language')}</div>
        <LanguageToggle compact />
        <button className="sidebar__switch" onClick={onTour}>{t('action.tour')}</button>
        <button className="sidebar__switch" onClick={onSwitchRole}>{t('action.switchRole')}</button>
      </div>
    </aside>
  )
}
