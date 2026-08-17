// Ellis for Trip.com — single-product shell, Trip.com themed.
//
// The app boots directly into the applicant intake surface (the Visa
// Platform, backed by the FastAPI backend over HTTP) wearing the Trip.com
// brand: logo top-left, language picker top-right, no operator chrome. The
// simulated local demo portal (TripPortal) is reachable ONLY when the backend
// reports runtime_mode === 'local_mock_demo'; in every other mode the demo
// surface is refused outright (never silently redirected into simulation),
// and mode detection fails safe to 'production'. Operator surfaces (admin
// console, setup, settings) are HIDDEN from applicants — an operator reveals
// them out-of-band via `#admin` in the URL or a persisted local flag.
import { useState, useEffect } from 'react'
import { LocaleProvider, useLocale, LanguagePicker } from './lib/locale.jsx'
import { ToastProvider } from './components/ui.jsx'
import Settings from './screens/Settings.jsx'
import TripPortal from './screens/TripPortal.jsx'
import VisaConsole from './screens/VisaConsole.jsx'
import AdminConsole from './screens/AdminConsole.jsx'
import SetupWizard from './screens/SetupWizard.jsx'
import EmployerConsole from './screens/EmployerConsole.jsx'
import AppointmentsDemo from './screens/AppointmentsDemo.jsx'
import AskEllis from './components/visa/AskEllis.jsx'
import { fetchRuntimeMode } from './lib/visaBackend.js'
import {
  detectPersona, getActiveH1bCase, subscribeActiveH1bCase
} from './lib/visaSession.js'
import { tripcomLogo } from './assets/logos.js'

export default function App() {
  // LocaleProvider wraps everything so the language picker + t() work in the
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
// ordinary applicant — not deleted. Persona detection is generalized in
// visaSession.detectPersona (H1B edition): 'applicant' (default), 'employer'
// (`#employer`, the petitioner-side console) and 'admin' (`#admin`/`#ops`),
// persisted in localStorage 'ellis_persona' with the legacy 'ellis_admin'
// flag still honored. Persona hides surfaces only — every per-party and
// admin authorization is enforced by the backend.

// Operator-only nav strip: never rendered for applicants.
function AdminNav({ view, onNav, demoMode }) {
  const items = [
    ['visa', 'Visa Platform'], ['employer', 'Employer console'],
    ['admin', 'Adapter Admin'],
    ['setup', 'Setup'], ['settings', 'Settings'],
  ]
  if (demoMode) items.push(['demo', 'Demo portal'])
  return (
    <nav style={{ display: 'flex', gap: 6 }} aria-label="Operator">
      {items.map(([id, label]) => (
        <button key={id} type="button" onClick={() => onNav(id)}
                className={'btn btn--sm' + (view === id ? '' : ' btn--ghost')}>
          {label}
        </button>
      ))}
    </nav>
  )
}

function AppInner() {
  const [persona] = useState(detectPersona)
  const adminMode = persona === 'admin'
  // Boots into the applicant intake surface; the employer persona is routed
  // to the employer console immediately after mount.
  const [view, setView] = useState(() =>
    (window.location.hash || '').includes('appointments') ? 'appointments' : 'visa')
  useEffect(() => { if (persona === 'employer') setView('employer') }, [persona])
  // The floating Ask Ellis assistant follows whichever H1B case a surface has
  // registered (H1bPipeline registers the parent case); hidden when no case.
  const [askCaseId, setAskCaseId] = useState(getActiveH1bCase)
  useEffect(() => subscribeActiveH1bCase(setAskCaseId), [])
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
        <header className="triptop" data-testid="triptop">
          <img className="triptop__logo" src={tripcomLogo} alt="Trip.com" />
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            {adminMode && <AdminNav view={view} onNav={setView} demoMode={demoMode} />}
            <LanguagePicker />
          </div>
        </header>
        <main className="main main--top">
          {view === 'visa' && <VisaConsole onNotify={() => {}} adminMode={adminMode} />}
          {/* Appointments DEMO surface: self-contained simulation for the
              Trip.com pitch — never touches the real booking pipeline. */}
          {view === 'appointments' && (
            <AppointmentsDemo onBack={() => {
              window.location.hash = '#applicant'; window.location.reload()
            }} />
          )}
          {/* Employer console: the employer persona's home; admins may visit.
              An applicant can never route here. */}
          {view === 'employer' && (persona === 'employer' || adminMode
            ? <EmployerConsole />
            : <VisaConsole onNotify={() => {}} adminMode={adminMode} />)}
          {/* Admin console is operator-only; an applicant can never route here. */}
          {view === 'admin' && (adminMode ? <AdminConsole /> : <VisaConsole onNotify={() => {}} adminMode={adminMode} />)}
          {view === 'setup' && (adminMode ? <SetupWizard /> : <VisaConsole onNotify={() => {}} adminMode={adminMode} />)}
          {view === 'settings' && (adminMode ? <Settings /> : <VisaConsole onNotify={() => {}} adminMode={adminMode} />)}
          {view === 'demo' && (demoMode
            ? <TripPortal onSwitchRole={() => setView('visa')} />
            : <DemoDisabled />)}
        </main>
        {/* Floating Ask Ellis — all personas, only while an H1B case is in
            context (H1bPipeline registers/clears the active case). */}
        {askCaseId && <AskEllis caseId={askCaseId} persona={persona} />}
      </div>
    </ToastProvider>
  )
}
