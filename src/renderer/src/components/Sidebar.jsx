import { Icon } from './icons.jsx'
import { wordmarkWhite } from '../assets/logos.js'
import { useLocale, LanguageToggle } from '../lib/locale.jsx'

// The applicant primarily sees "Start your visa". Internal operator surfaces
// (Adapter Admin) are hidden unless admin mode is explicitly enabled.
const APPLICANT_NAV = [
  { id: 'visa', key: 'nav.visa', icon: 'globe' },
  { id: 'setup', key: 'nav.setup', icon: 'gear' }
]
const ADMIN_NAV = { id: 'admin', key: 'nav.admin', icon: 'shield' }

export default function Sidebar({ view, onNav, runtimeMode, adminMode }) {
  const { t } = useLocale()
  let items = adminMode
    ? [APPLICANT_NAV[0], ADMIN_NAV, APPLICANT_NAV[1]]
    : APPLICANT_NAV
  if (runtimeMode === 'local_mock_demo') {
    items = [...items, { id: 'demo', key: 'nav.demo', icon: 'cases' }]
  }

  return (
    <aside className="sidebar">
      <div className="sidebar__top">
        <img className="sidebar__logo" src={wordmarkWhite} alt="Ellis for Trip.com" />
      </div>
      <nav className="nav">
        <div className="nav__label">{t('section.workspace')}</div>
        {items.map((n) => {
          const Ico = Icon[n.icon]
          return (
            <button key={n.id} data-tour={'nav-' + n.id} className={'nav__item' + (view === n.id ? ' is-active' : '')} onClick={() => onNav(n.id)}>
              <Ico /> <span style={{ flex: 1 }}>{t(n.key)}</span>
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
      </div>
    </aside>
  )
}
