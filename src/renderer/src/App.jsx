// Ellis for Trip.com — single-product shell.
//
// The app boots directly into the applicant intake surface (the Visa
// Platform, backed by the FastAPI backend over HTTP). The simulated local
// demo portal (TripPortal) is reachable ONLY when the backend reports
// runtime_mode === 'local_mock_demo'; in every other mode the demo surface is
// refused outright (never silently redirected into simulation), and mode
// detection fails safe to 'production'.
import { useState, useEffect } from 'react'
import { LocaleProvider, useLocale } from './lib/locale.jsx'
import { ToastProvider } from './components/ui.jsx'
import Sidebar from './components/Sidebar.jsx'
import Settings from './screens/Settings.jsx'
import TripPortal from './screens/TripPortal.jsx'
import VisaConsole from './screens/VisaConsole.jsx'
import AdminConsole from './screens/AdminConsole.jsx'
import SetupWizard from './screens/SetupWizard.jsx'
import { fetchRuntimeMode } from './lib/visaBackend.js'

export default function App() {
  // LocaleProvider wraps everything so the language toggle + t() work in the
  // shell, every view, and the simulated-portal banner alike.
  return <LocaleProvider><AppInner /></LocaleProvider>
}

// Permanent, non-dismissible banner shown on every view while the app runs
// against the simulated local demo pipeline.
function SimulatedBanner() {
  const { t } = useLocale()
  return (
    <div className="simbanner" data-testid="simulated-banner">
      {t('banner.simulated')}
    </div>
  )
}

// Rendered when someone lands on the demo view in any non-demo runtime mode.
function DemoDisabled() {
  const { t } = useLocale()
  return (
    <div className="page">
      <div className="card" style={{ padding: 28, maxWidth: 560, margin: '80px auto 0', textAlign: 'center' }}>
        <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 8 }}>{t('demo.disabled')}</div>
        <div style={{ fontSize: 13.5, color: 'var(--muted)' }}>{t('demo.disabledHint')}</div>
      </div>
    </div>
  )
}

// Internal/operator surfaces (Adapter Admin, Cases & tools, CapabilityGrid,
// CountryMatrix, release/quarantine/rollback/kill-switch) are HIDDEN from the
// ordinary applicant — not deleted. An operator reveals them out-of-band via
// `#admin` in the URL or a persisted local flag; an applicant never sees them.
function detectAdminMode() {
  try {
    if (typeof window === 'undefined') return false
    const hash = (window.location.hash || '').toLowerCase()
    if (hash === '#admin' || hash === '#ops') {
      try { window.localStorage.setItem('ellis_admin', '1') } catch { /* ignore */ }
      return true
    }
    if (hash === '#applicant') {
      try { window.localStorage.removeItem('ellis_admin') } catch { /* ignore */ }
      return false
    }
    return window.localStorage.getItem('ellis_admin') === '1'
  } catch {
    return false
  }
}

function AppInner() {
  const [view, setView] = useState('visa')
  const [adminMode] = useState(detectAdminMode)
  // Fail-safe default: 'production' until the backend proves otherwise.
  const [runtimeMode, setRuntimeMode] = useState('production')

  useEffect(() => {
    let alive = true
    // Poll a few times: the embedded backend may still be starting when the
    // window first loads. Stop as soon as a non-production mode is confirmed.
    let tries = 0
    async function probe() {
      const m = await fetchRuntimeMode()
      if (!alive) return
      setRuntimeMode(m)
      tries += 1
      if (m === 'production' && tries < 20) setTimeout(probe, 1000)
    }
    probe()
    return () => { alive = false }
  }, [])

  const demoMode = runtimeMode === 'local_mock_demo'

  return (
    <ToastProvider>
      <div className={'app' + (demoMode ? ' app--banner' : '')}>
        {demoMode && <SimulatedBanner />}
        <div className="shell">
          <Sidebar view={view} onNav={setView} runtimeMode={runtimeMode} adminMode={adminMode} />
          <main className="main">
            {view === 'visa' && <VisaConsole onNotify={() => {}} adminMode={adminMode} />}
            {/* Admin console is operator-only; an applicant can never route here. */}
            {view === 'admin' && (adminMode ? <AdminConsole /> : <VisaConsole onNotify={() => {}} adminMode={adminMode} />)}
            {view === 'setup' && <SetupWizard />}
            {view === 'settings' && <Settings />}
            {view === 'demo' && (demoMode
              ? <TripPortal onSwitchRole={() => setView('visa')} />
              : <DemoDisabled />)}
          </main>
        </div>
      </div>
    </ToastProvider>
  )
}
