import { useState, useEffect } from 'react'
import { ellis, ROLE_TABS } from './lib/api.js'
import { buildTourSteps } from './lib/tour.js'
import { ToastProvider } from './components/ui.jsx'
import Sidebar from './components/Sidebar.jsx'
import OnboardingDemo from './components/OnboardingDemo.jsx'
import Tour from './components/Tour.jsx'
import TourPicker from './components/TourPicker.jsx'
import RoleSelect from './screens/RoleSelect.jsx'
import CountrySelect from './screens/CountrySelect.jsx'
import Login from './screens/Login.jsx'
import Dashboard from './screens/Dashboard.jsx'
import Cases from './screens/Cases.jsx'
import CaseWorkspace from './screens/CaseWorkspace.jsx'
import Assistant from './screens/Assistant.jsx'
import Settings from './screens/Settings.jsx'
import Notifications from './screens/Notifications.jsx'
import TripPortal from './screens/TripPortal.jsx'

export default function App() {
  const [booted, setBooted] = useState(false)
  const [showDemo, setShowDemo] = useState(false)
  const [role, setRole] = useState(null)
  const [profile, setProfile] = useState(null)
  const [authed, setAuthed] = useState(false)
  const [view, setView] = useState('dashboard')
  const [caseId, setCaseId] = useState(null)
  const [caseTab, setCaseTab] = useState('overview')

  const [pickingTour, setPickingTour] = useState(false)
  const [tourSteps, setTourSteps] = useState(null)
  const [notifTick, setNotifTick] = useState(0)
  const bumpNotif = () => setNotifTick((n) => n + 1)

  useEffect(() => {
    ellis.getSettings().then((s) => {
      setShowDemo(!s.onboarded)
      setBooted(true)
    })
  }, [])

  function finishDemo() {
    ellis.saveSettings({ onboarded: true })
    setShowDemo(false)
  }

  function nav(v) {
    setView(v)
    if (v !== 'case') setCaseId(null)
  }
  function openCase(id) {
    setCaseId(id)
    setCaseTab('overview')
    setView('case')
  }
  function switchRole() {
    setRole(null); setProfile(null); setAuthed(false); setCaseId(null); setView('dashboard'); setTourSteps(null)
  }

  const controller = { nav, openCase, setCaseTab }
  function startTourWith(tourRole, c) {
    setPickingTour(false)
    if (tourRole && tourRole !== role) setRole(tourRole)
    setTourSteps(buildTourSteps(tourRole || role, c, controller))
  }

  if (!booted) return null
  if (showDemo) return <OnboardingDemo onFinish={finishDemo} />
  if (!role) return <ToastProvider><RoleSelect onPick={(r) => { setRole(r); if (r === 'tripcom') setProfile({ role: 'tripcom', global: true }); setView('dashboard') }} /></ToastProvider>
  if (!profile) return <ToastProvider><CountrySelect role={role} onDone={setProfile} onBack={() => setRole(null)} /></ToastProvider>
  if (!authed && role !== 'tripcom') return <ToastProvider><Login role={role} profile={profile} onAuth={() => setAuthed(true)} onBack={() => setProfile(null)} /></ToastProvider>

  // Trip.com is its own branded portal — no sidebar, one purpose.
  if (role === 'tripcom') return <ToastProvider><TripPortal onSwitchRole={switchRole} /></ToastProvider>

  return (
    <ToastProvider>
      <div className="shell">
        <Sidebar role={role} view={view} onNav={nav} onSwitchRole={switchRole} onTour={() => setPickingTour(true)} notifTick={notifTick} />
        <main className="main">
          {view === 'dashboard' && <Dashboard role={role} profile={profile} onNav={nav} onOpenCase={openCase} onNotify={bumpNotif} />}
          {view === 'cases' && <Cases role={role} profile={profile} onOpen={openCase} />}
          {view === 'case' && caseId && <CaseWorkspace caseId={caseId} role={role} profile={profile} tab={caseTab} onTab={setCaseTab} onBack={() => nav('cases')} onSettings={() => nav('settings')} onNotify={bumpNotif} />}
          {view === 'assistant' && <Assistant role={role} onSettings={() => nav('settings')} />}
          {view === 'notifications' && <Notifications role={role} onOpenCase={openCase} onChanged={bumpNotif} />}
          {view === 'settings' && <Settings onReplayTour={() => setPickingTour(true)} onReplayOnboarding={() => setShowDemo(true)} />}
        </main>
      </div>

      {pickingTour && <TourPicker onPick={startTourWith} onClose={() => setPickingTour(false)} />}
      {tourSteps && <Tour steps={tourSteps} onClose={() => setTourSteps(null)} />}
    </ToastProvider>
  )
}
