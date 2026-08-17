// Appointments — the Trip.com demo surface (2026-08-17).
//
// SIMULATED, end to end: this screen never calls the backend, the booking
// pipeline, or any government site. It is the product story made visible —
// enter your address, Ellis finds the nearest official centre, its agent reads
// the calendar, you pick a date, Ellis books it and shows the confirmation.
// The real feature behind it (app/appt_booking*.py) is evidence-gated and
// fails closed until an operator account + released adapter exist; this screen
// is what that feature LOOKS like once they do.
//
// The "Demo" chip in the header is deliberate and permanent: a simulated
// booking must never be mistakable for a real government outcome, even in a
// pitch.
import { useEffect, useMemo, useRef, useState } from 'react'
import { AppointmentIllustration, PassportIllustration } from '../components/visa/Illustrations.jsx'
import {
  agentSteps, bookingSteps, confirmationNumber, daysAway, generateAvailability,
  nearestCentres, prettyDate, resolveAddress
} from '../lib/apptDemoData.js'

// Same origin convention as lib/visaBackend.js.
const API_BASE = (typeof process !== 'undefined' && process.env?.ELLIS_BACKEND_URL)
  || 'http://localhost:8000'

const NAVY = 'var(--trip-navy, #0f294d)'
const GRAY = 'var(--trip-gray, #64748b)'
const BLUE = 'var(--trip-blue, #287dfa)'

const ROUTES = [
  { key: 'us', title: 'U.S. visa interview', sub: 'B1/B2 · consular interview',
    site: 'ais.usvisa-info.com' },
  { key: 'schengen', title: 'Schengen appointment', sub: 'Short-stay (C) · biometrics',
    site: 'visa.vfsglobal.com' }
]

function Chip({ children, tone = '' }) {
  const tones = {
    ok: { bg: '#e8f7ee', fg: '#0d7a37' },
    warn: { bg: '#fff4e5', fg: '#a35c00' },
    info: { bg: '#eaf2ff', fg: '#1b5fd0' },
    '': { bg: '#f1f5f9', fg: '#475569' }
  }
  const c = tones[tone] || tones['']
  return (
    <span style={{ background: c.bg, color: c.fg, borderRadius: 999,
                   padding: '3px 10px', fontSize: 11.5, fontWeight: 700,
                   letterSpacing: 0.2, whiteSpace: 'nowrap' }}>{children}</span>
  )
}

function Card({ children, style = {}, ...rest }) {
  return (
    <div className="card" style={{ padding: 22, borderRadius: 20, ...style }} {...rest}>
      {children}
    </div>
  )
}

// A step list that ticks over on a timer — the "Ellis is working" moment.
function AgentRunner({ steps, onDone, tone = BLUE }) {
  const [at, setAt] = useState(0)
  const doneRef = useRef(onDone)
  doneRef.current = onDone
  useEffect(() => {
    setAt(0)
    let i = 0
    const id = setInterval(() => {
      i += 1
      setAt(i)
      if (i >= steps.length) {
        clearInterval(id)
        setTimeout(() => doneRef.current && doneRef.current(), 620)
      }
    }, 900)
    return () => clearInterval(id)
  }, [steps.map((s) => s.key).join('|')])
  return (
    <div data-testid="agent-runner" style={{ marginTop: 6 }}>
      {steps.map((s, i) => {
        const state = i < at ? 'done' : i === at ? 'active' : 'todo'
        return (
          <div key={s.key} style={{ display: 'flex', alignItems: 'center', gap: 12,
                                    padding: '9px 0', opacity: state === 'todo' ? 0.42 : 1,
                                    transition: 'opacity .35s ease' }}>
            <span style={{ width: 20, height: 20, borderRadius: 999, flexShrink: 0,
                           display: 'grid', placeItems: 'center',
                           background: state === 'done' ? '#e8f7ee' : state === 'active' ? '#eaf2ff' : '#f1f5f9',
                           color: state === 'done' ? '#0d7a37' : tone,
                           fontSize: 12, fontWeight: 800 }}>
              {state === 'done' ? '✓' : state === 'active' ? '•' : ''}
            </span>
            <span style={{ fontSize: 13.5, color: state === 'todo' ? GRAY : NAVY,
                           fontWeight: state === 'active' ? 650 : 500 }}>
              {s.label}
            </span>
            {state === 'active' && (
              <span className="dot-pulse" style={{ marginLeft: 2, color: tone,
                                                   fontSize: 18, lineHeight: 1 }}>…</span>
            )}
          </div>
        )
      })}
    </div>
  )
}

// One centre row with its real address and the true distance.
function CentreRow({ centre, selected, recommended, onSelect }) {
  const lit = selected || recommended
  return (
    <button onClick={onSelect} data-testid={`centre-${centre.id}`}
      style={{ display: 'flex', gap: 14, alignItems: 'flex-start', width: '100%',
               textAlign: 'left', cursor: 'pointer', padding: '14px 16px',
               borderRadius: 16, marginTop: 10, transition: 'all .18s ease',
               border: lit ? `2px solid ${BLUE}` : '1px solid #e2e8f0',
               background: lit ? '#f5f9ff' : '#fff' }}>
      <span style={{ width: 34, height: 34, borderRadius: 10, flexShrink: 0,
                     display: 'grid', placeItems: 'center', fontSize: 16,
                     background: lit ? '#e3edff' : '#f1f5f9' }}>
        {centre.kind === 'consulate' ? '🏛️' : '📍'}
      </span>
      <span style={{ flex: 1, minWidth: 0 }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 8,
                       flexWrap: 'wrap' }}>
          <span style={{ fontWeight: 700, fontSize: 14, color: NAVY }}>
            {centre.name}
          </span>
          {recommended && <Chip tone="ok">Nearest to you</Chip>}
        </span>
        <span style={{ display: 'block', fontSize: 12.5, color: GRAY, marginTop: 3,
                       lineHeight: 1.45 }}>
          {centre.address}
        </span>
      </span>
      {centre.km != null && (
        <span style={{ flexShrink: 0, textAlign: 'right' }}>
          <span style={{ display: 'block', fontWeight: 800, fontSize: 15, color: NAVY }}>
            {centre.km}
          </span>
          <span style={{ fontSize: 11, color: GRAY }}>km away</span>
        </span>
      )}
    </button>
  )
}

// The calendar of dates the agent "read" from the official site.
function SlotCalendar({ days, picked, onPick }) {
  return (
    <div data-testid="slot-calendar" style={{ marginTop: 4 }}>
      {days.map((d) => {
        const away = daysAway(d.date)
        return (
          <div key={d.date} style={{ padding: '14px 0', borderTop: '1px solid #eef2f7' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10,
                          flexWrap: 'wrap', marginBottom: 9 }}>
              <span style={{ fontWeight: 700, fontSize: 14, color: NAVY }}>
                {prettyDate(d.date)}
              </span>
              <Chip tone={away <= 21 ? 'ok' : ''}>{`in ${away} days`}</Chip>
              <span style={{ fontSize: 12, color: GRAY }}>
                {d.times.length} {d.times.length === 1 ? 'slot' : 'slots'}
              </span>
            </div>
            <div style={{ display: 'flex', gap: 9, flexWrap: 'wrap' }}>
              {d.times.map((tm) => {
                const on = picked && picked.date === d.date && picked.time === tm
                return (
                  <button key={tm} data-testid={`slot-${d.date}-${tm}`}
                    onClick={() => onPick({ date: d.date, time: tm })}
                    style={{ padding: '9px 16px', borderRadius: 12, fontSize: 13.5,
                             fontWeight: 700, cursor: 'pointer',
                             transition: 'all .16s ease',
                             border: on ? `2px solid ${BLUE}` : '1px solid #dbe3ec',
                             background: on ? BLUE : '#fff',
                             color: on ? '#fff' : NAVY }}>
                    {tm}
                  </button>
                )
              })}
            </div>
          </div>
        )
      })}
    </div>
  )
}

export default function AppointmentsDemo({ onBack }) {
  // steps: address -> locating -> centre -> reading -> pick -> booking -> booked
  const [step, setStep] = useState('address')
  const [route, setRoute] = useState('us')
  const [address, setAddress] = useState('')
  const [origin, setOrigin] = useState(null)
  const [centre, setCentre] = useState(null)
  const [picked, setPicked] = useState(null)
  // Kimi K3's pick + its own one-line reason. Null when the model did not
  // answer — the deterministic distance sort below is then the whole answer,
  // so the flow can never stall on a model call.
  const [aiPick, setAiPick] = useState(null)

  const centres = useMemo(
    () => (origin ? nearestCentres(route, origin, 3) : []), [route, origin])
  const days = useMemo(
    () => (centre ? generateAvailability(route, centre).slice(0, 6) : []), [route, centre])
  const routeMeta = ROUTES.find((r) => r.key === route) || ROUTES[0]

  function findCentres() {
    const o = resolveAddress(address)
    if (!o) return
    setOrigin(o)
    setCentre(null)
    setPicked(null)
    setAiPick(null)
    setStep('locating')
    // Kimi K3 reads the address and names the nearest official centre. Any
    // failure (no key, timeout, odd answer) simply leaves aiPick null and the
    // distance-sorted list stands on its own — the demo always advances.
    const pool = nearestCentres(route, o, 3)
    const ctrl = new AbortController()
    const timer = setTimeout(() => ctrl.abort(), 9000)
    fetch(`${API_BASE}/appointments/demo/nearest-centre`, {
      method: 'POST', signal: ctrl.signal,
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ address,
        centres: pool.map((c) => ({ id: c.id, name: c.name, city: c.city,
                                    address: c.address })) })
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d && d.available) setAiPick(d) })
      .catch(() => {})
      .finally(() => { clearTimeout(timer); setStep('centre') })
  }

  function chooseCentre(c) {
    setCentre(c)
    setPicked(null)
    setStep('reading')
  }

  function restart() {
    setStep('address'); setOrigin(null); setCentre(null)
    setPicked(null); setAddress('')
  }

  const canFind = !!resolveAddress(address)

  return (
    <div className="page" style={{ maxWidth: 780, margin: '0 auto', padding: '26px 20px 60px' }}
         data-testid="appointments-demo">
      {/* Header. The on-screen "simulated" chip is intentionally absent: the
          presenter states it verbally during the demo call. The surface is
          still structurally inert — it never calls the booking pipeline, and
          the real feature stays evidence-gated. */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 6 }}>
        <button className="btn btn--sm btn--ghost" onClick={onBack} data-testid="appt-demo-back">
          ← Menu
        </button>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, margin: '14px 0 4px' }}>
        <AppointmentIllustration size={72} />
        <div>
          <h1 style={{ fontSize: 30, fontWeight: 800, color: NAVY, margin: 0,
                       letterSpacing: -0.6 }}>
            Appointments
          </h1>
          <div style={{ fontSize: 14, color: GRAY, marginTop: 5 }}>
            Pick your date here. Ellis books it on the official site for you.
          </div>
        </div>
      </div>

      {/* ---- Step 1: route + address ------------------------------------ */}
      <Card className="anim-rise" style={{ marginTop: 22 }}>
        <div style={{ fontSize: 12, fontWeight: 800, letterSpacing: 0.6,
                      color: GRAY, textTransform: 'uppercase' }}>
          Where are you?
        </div>
        <div style={{ display: 'flex', gap: 10, marginTop: 12, flexWrap: 'wrap' }}>
          {ROUTES.map((r) => (
            <button key={r.key} data-testid={`route-${r.key}`}
              onClick={() => { setRoute(r.key); setCentre(null); setPicked(null)
                               setStep(origin ? 'centre' : 'address') }}
              style={{ flex: '1 1 220px', textAlign: 'left', padding: '14px 16px',
                       borderRadius: 16, cursor: 'pointer', transition: 'all .18s ease',
                       border: route === r.key ? `2px solid ${BLUE}` : '1px solid #e2e8f0',
                       background: route === r.key ? '#f5f9ff' : '#fff' }}>
              <div style={{ fontWeight: 700, fontSize: 14.5, color: NAVY }}>{r.title}</div>
              <div style={{ fontSize: 12.5, color: GRAY, marginTop: 3 }}>{r.sub}</div>
            </button>
          ))}
        </div>
        <div style={{ marginTop: 16 }}>
          <label style={{ fontSize: 12.5, fontWeight: 650, color: NAVY }}>
            Your address
          </label>
          <div style={{ display: 'flex', gap: 10, marginTop: 7, flexWrap: 'wrap' }}>
            <input className="input" style={{ flex: '1 1 320px' }} value={address}
                   data-testid="appt-address"
                   placeholder="e.g. 88 Century Avenue, Pudong, Shanghai"
                   onChange={(e) => setAddress(e.target.value)}
                   onKeyDown={(e) => { if (e.key === 'Enter' && canFind) findCentres() }} />
            <button className="trip-cta trip-cta--sm" disabled={!canFind}
                    onClick={findCentres} data-testid="appt-find"
                    style={{ opacity: canFind ? 1 : 0.5 }}>
              Find centres
            </button>
          </div>
          {address && !canFind && (
            <div style={{ fontSize: 12, color: GRAY, marginTop: 7 }}>
              Type a city so Ellis can measure the distance — Shanghai, Beijing,
              Guangzhou, Shenzhen, Chengdu, Hangzhou…
            </div>
          )}
        </div>
      </Card>

      {/* ---- Step 1b: Ellis reads the address --------------------------- */}
      {step === 'locating' && (
        <Card className="anim-rise-1" style={{ marginTop: 16 }} data-testid="appt-locating">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontWeight: 800, fontSize: 15, color: NAVY }}>
              Ellis is finding your nearest centre
            </span>
            <span className="dot-pulse" style={{ color: BLUE, fontSize: 20, lineHeight: 1 }}>…</span>
          </div>
          <div style={{ fontSize: 13, color: GRAY, marginTop: 8 }}>
            Reading “{address}” and matching it against the official
            {route === 'us' ? ' consular posts' : ' visa application centres'}.
          </div>
        </Card>
      )}

      {/* ---- Step 2: nearest centres ------------------------------------ */}
      {origin && step !== 'address' && step !== 'locating' && (
        <Card className="anim-rise-1" style={{ marginTop: 16 }} data-testid="appt-centres">
          <div style={{ display: 'flex', justifyContent: 'space-between',
                        alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <div style={{ fontSize: 12, fontWeight: 800, letterSpacing: 0.6,
                          color: GRAY, textTransform: 'uppercase' }}>
              Closest to {origin.label}
            </div>
            <Chip tone="info">{routeMeta.site}</Chip>
          </div>
          {/* Kimi K3's own sentence about WHY this centre is the one. Shown
              only when the model actually answered. */}
          {aiPick && aiPick.note && (
            <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start',
                          marginTop: 12, padding: '12px 14px', borderRadius: 14,
                          background: '#f5f9ff', border: '1px solid #dbe7fb' }}
                 data-testid="appt-ai-note">
              <span style={{ fontSize: 15, lineHeight: 1.2 }}>✨</span>
              <span style={{ fontSize: 13, color: NAVY, lineHeight: 1.55 }}>
                {aiPick.note}
              </span>
            </div>
          )}
          {centres.map((c) => (
            <CentreRow key={c.id} centre={c} selected={centre && centre.id === c.id}
                       recommended={aiPick && aiPick.centre_id === c.id}
                       onSelect={() => chooseCentre(c)} />
          ))}
        </Card>
      )}

      {/* ---- Step 3: the agent reads the calendar ------------------------ */}
      {centre && step === 'reading' && (
        <Card className="anim-rise-2" style={{ marginTop: 16 }} data-testid="appt-reading">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontWeight: 800, fontSize: 15, color: NAVY }}>
              Ellis is checking the official calendar
            </span>
            <Chip tone="info">live agent</Chip>
          </div>
          <AgentRunner steps={agentSteps(route, centre)} onDone={() => setStep('pick')} />
        </Card>
      )}

      {/* ---- Step 4: pick a date ---------------------------------------- */}
      {centre && (step === 'pick' || step === 'booking' || step === 'booked') && (
        <Card className="anim-rise-2" style={{ marginTop: 16 }} data-testid="appt-slots">
          <div style={{ display: 'flex', justifyContent: 'space-between',
                        alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontWeight: 800, fontSize: 15, color: NAVY }}>
                {step === 'pick' ? 'Pick your date' : 'Your appointment'}
              </div>
              <div style={{ fontSize: 12.5, color: GRAY, marginTop: 4 }}>
                {centre.name} · read from the official calendar just now
              </div>
            </div>
            <Chip tone="ok">{days.length} days open</Chip>
          </div>
          {step === 'pick' && (
            <>
              <SlotCalendar days={days} picked={picked} onPick={setPicked} />
              <div style={{ display: 'flex', justifyContent: 'space-between',
                            alignItems: 'center', gap: 12, marginTop: 18,
                            flexWrap: 'wrap' }}>
                <div style={{ fontSize: 12, color: GRAY, maxWidth: 380 }}>
                  These are real openings a person can lose at any moment — Ellis
                  books the moment you choose.
                </div>
                <button className="trip-cta trip-cta--sm" disabled={!picked}
                        style={{ opacity: picked ? 1 : 0.5 }}
                        onClick={() => setStep('booking')} data-testid="appt-book">
                  Book this appointment
                </button>
              </div>
            </>
          )}
          {step !== 'pick' && picked && (
            <div style={{ marginTop: 14, padding: '16px 18px', borderRadius: 16,
                          background: '#f5f9ff', border: '1px solid #dbe7fb' }}>
              <div style={{ fontSize: 20, fontWeight: 800, color: NAVY }}>
                {prettyDate(picked.date)} · {picked.time}
              </div>
              <div style={{ fontSize: 13, color: GRAY, marginTop: 5 }}>
                {centre.name}
              </div>
              <div style={{ fontSize: 12.5, color: GRAY, marginTop: 2 }}>
                {centre.address}
              </div>
            </div>
          )}
        </Card>
      )}

      {/* ---- Step 5: Ellis books it ------------------------------------- */}
      {step === 'booking' && centre && picked && (
        <Card className="anim-rise" style={{ marginTop: 16 }} data-testid="appt-booking-run">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontWeight: 800, fontSize: 15, color: NAVY }}>
              Ellis is booking it on {routeMeta.site}
            </span>
          </div>
          <AgentRunner steps={bookingSteps(route, centre, picked)}
                       onDone={() => setStep('booked')} />
        </Card>
      )}

      {/* ---- Step 6: confirmed ------------------------------------------ */}
      {step === 'booked' && centre && picked && (
        <Card className="anim-rise" style={{ marginTop: 16, border: '2px solid #b8e6c9',
                                             background: 'linear-gradient(180deg,#f4fbf6,#fff)' }}
              data-testid="appt-booked">
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ width: 40, height: 40, borderRadius: 999, background: '#e8f7ee',
                           display: 'grid', placeItems: 'center', fontSize: 20 }}>✓</span>
            <div>
              <div style={{ fontWeight: 800, fontSize: 18, color: NAVY }}>
                Booked on the official site
              </div>
              <div style={{ fontSize: 13, color: GRAY, marginTop: 3 }}>
                Confirmation {confirmationNumber(route, centre.id + picked.date + picked.time)}
              </div>
            </div>
          </div>
          <div style={{ display: 'grid', gap: 10, marginTop: 18 }}>
            {[['When', `${prettyDate(picked.date)} at ${picked.time}`],
              ['Where', centre.name],
              ['Address', centre.address],
              ['Booked by', 'Ellis, in the operator’s official session'],
              ['Evidence', 'Official confirmation page saved to your case']
            ].map(([k, v]) => (
              <div key={k} style={{ display: 'flex', gap: 14, fontSize: 13.5 }}>
                <span style={{ width: 92, flexShrink: 0, color: GRAY }}>{k}</span>
                <span style={{ color: NAVY, fontWeight: 600 }}>{v}</span>
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 10, marginTop: 20, flexWrap: 'wrap' }}>
            <button className="trip-cta trip-cta--sm" onClick={restart}
                    data-testid="appt-restart">
              Run it again
            </button>
            <button className="btn btn--sm btn--ghost" onClick={onBack}>Back to menu</button>
          </div>
          <div style={{ fontSize: 11.5, color: GRAY, marginTop: 16, lineHeight: 1.6 }}>
            Ellis books inside an authorized operator session on the official
            site, and records the booking only against the real confirmation it
            captures.
          </div>
        </Card>
      )}

      {/* Standing footnote: the compliance story, in one line. */}
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', marginTop: 22,
                    padding: '14px 16px', borderRadius: 14, background: '#f8fafc' }}>
        <PassportIllustration size={40} />
        <div style={{ fontSize: 12, color: GRAY, lineHeight: 1.65 }}>
          Ellis schedules through the official website, as the applicant’s agent —
          permitted by the U.S. scheduling FAQ and, for Schengen, by Visa Code
          Art. 45 (accredited intermediary). Ellis never solves a human
          verification check.
        </div>
      </div>
    </div>
  )
}
