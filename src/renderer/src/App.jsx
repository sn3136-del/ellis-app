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
import QualityConsole from './screens/QualityConsole.jsx'
import VisaConsole from './screens/VisaConsole.jsx'
import AdminConsole from './screens/AdminConsole.jsx'
import SetupWizard from './screens/SetupWizard.jsx'
import EmployerConsole from './screens/EmployerConsole.jsx'
import SchengenVisa from './screens/SchengenVisa.jsx'
import TravelDatabase from './screens/TravelDatabase.jsx'
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



/** The Ellis skyline mark: the letters as solid towers, the Empire State
 *  Building as the I with its window ribbons. Static, centered in the
 *  header between the wordmark and the controls. */
function EllisMark() {
  const INK = '#0f294d'
  const B = 150
  const LETTERS = [
    [22, [[0, 92, 13, 92], [0, 92, 40, 12], [0, 55, 32, 11], [0, 12, 40, 12]]],
    [84, [[0, 76, 13, 76], [0, 12, 36, 12]]],
    [138, [[0, 84, 13, 84], [0, 12, 36, 12]]],
    [192, [[-3, 22, 21, 22], [-1.5, 44, 18, 22], [0, 92, 15, 48],
           [2, 102, 11, 10], [3.5, 108, 8, 6], [5.5, 116, 4, 8]]],
    [228, [[0, 61, 38, 11], [0, 50, 12, 14], [0, 36, 38, 11],
           [26, 25, 12, 14], [0, 11, 38, 11]]],
  ]
  return (
    <div className="ellis-mark" aria-label="Ellis" data-testid="ellis-mark">
      <svg viewBox="14 8 258 144" height="42" style={{ display: 'block' }}>
        {LETTERS.map(([lx, rects], i) => (
          <g key={i}>
            {rects.map(([x, up, w, h], j) => (
              <rect key={j} x={lx + x} y={B - up} width={w} height={h}
                    rx="1.5" fill={INK} stroke={INK} strokeWidth="2" />
            ))}
          </g>
        ))}
        {[3.4, 6.9, 10.4].map((cx, i) => (
          <rect key={'r' + i} x={192 + cx} y={B - 88} width="1.7" height="40"
                fill="#fff" opacity="0.9" />
        ))}
        {[1.2, 4.7, 8.2, 11.7, 15.2].map((cx, i) => (
          <rect key={'s' + i} x={190.5 + cx} y={B - 40} width="1.7"
                height="13" fill="#fff" opacity="0.9" />
        ))}
        {[2, 5.2, 12.6, 15.8].map((cx, i) => (
          <rect key={'b' + i} x={189 + cx} y={B - 17} width="1.7" height="6.5"
                fill="#fff" opacity="0.9" />
        ))}
        {[4.6, 7.9].map((cx, i) => (
          <rect key={'u' + i} x={192 + cx} y={B - 100.5} width="1.6" height="7"
                fill="#fff" opacity="0.9" />
        ))}
        <rect x="197.5" y={B - 118} width="4" height="4" rx="2" fill={INK} />
        <line x1="199.5" y1={B - 118} x2="199.5" y2={B - 134}
              stroke={INK} strokeWidth="1.8" strokeLinecap="round" />
        <circle cx="199.5" cy={B - 136} r="1.5" fill={INK} />
      </svg>
    </div>
  )
}

/** Next to the language picker: one-click hops between the customer
 *  database, the quality console and the status page. The hash listener
 *  makes the in-app hops instant; the status page is its own URL. */
function SurfaceNav({ view }) {
  const { t, lang } = useLocale()
  const pill = {
    border: '1px solid #dbe3ee', background: '#fff', cursor: 'pointer',
    borderRadius: 999, fontSize: 12.5, fontWeight: 700, padding: '7px 14px',
    color: 'var(--trip-navy, #0f294d)', whiteSpace: 'nowrap',
    textDecoration: 'none',
  }
  return (
    <nav style={{ display: 'flex', gap: 8, alignItems: 'center' }}
         data-testid="surface-nav">
      {view === 'quality' ? (
        <button style={pill} data-testid="nav-database"
                onClick={() => { window.location.hash = '#database' }}>
          {t('nav.db')}
        </button>
      ) : (
        <button style={pill} data-testid="nav-ops"
                onClick={() => { window.location.hash = '#ops' }}>
          {t('nav.ops')}
        </button>
      )}
      <a style={pill} data-testid="nav-status"
         href={`/api/health/uptime?lang=${encodeURIComponent(lang)}`}>
        {t('nav.status')}
      </a>
    </nav>
  )
}

function AppInner() {
  const [persona] = useState(detectPersona)
  const adminMode = persona === 'admin'
  // Boots into the applicant intake surface; the employer persona is routed
  // to the employer console immediately after mount.
  // The Schengen lane is a standalone surface (its own case + secure window),
  // reachable by hash like the employer console.
  // Boots on the MAIN MENU (the visa console's lane cards — Travel visas
  // and Schengen visa), per owner decision; '#schengen' still deep-links
  // straight into the Germany appointment lane.
  // The Trip.com deliverable is the INFORMATION BASE: the Database is the
  // landing surface. The visa-processing console is a separate product,
  // reachable only by explicit hash — their review read any visible
  // processing surface as scope drift.
  // OWNER DECISION (2026-08-28): only the Database is available. The visa
  // platform, employer console and Schengen lane are hidden entirely — the
  // Trip.com deliverable is the information base, and any visible processing
  // surface reads as scope drift. #ops opens the quality-control backend.
  const [view, setView] = useState(() =>
    (window.location.hash || '').includes('ops') ? 'quality' : 'database')
  useEffect(() => {
    // The hash is the only router: without this listener, editing the URL to
    // #ops on an already-open page changes nothing until a manual reload.
    const onHash = () => setView(
      (window.location.hash || '').includes('ops') ? 'quality' : 'database')
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  // Employer persona routing removed with the hidden consoles.
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
      if (m === 'production' && tries < 3) setTimeout(probe, 1000)
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
          <EllisMark />
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <SurfaceNav view={view} />
            <LanguagePicker />
          </div>
        </header>
        <main className="main main--top">
          {view === 'visa' && <VisaConsole onNotify={() => {}} adminMode={adminMode} />}
          {/* Schengen: Germany's account-free RK-Termin calendar, read live. */}
          {/* The Database: traveldoc-style requirements lookup, answered by
              the Kimi-primary route decision. */}
          {view === 'database' && <TravelDatabase onBack={null} />}
          {/* Trip.com's P0: the Information Quality Control Backend (#ops). */}
          {view === 'quality' && <QualityConsole />}
          {view === 'schengen' && (
            <SchengenVisa onBack={() => {
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
