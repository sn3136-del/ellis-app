// Schengen visa — the German appointment calendar, live.
//
// Germany's RK-Termin (service2.diplo.de, the Federal Foreign Office's own
// system serving 190+ missions) is the one Schengen booking system with NO
// ACCOUNT AT ALL. Everything else — TLScontact, VFS Global, BLS, Lithuania's
// MIGRIS — hides its calendar behind a registered login, and the trend is
// toward more mandatory login, not less. RK-Termin puts exactly one thing
// between an anonymous visitor and the real month grid: an image CAPTCHA.
//
// That shape is one Ellis can serve honestly, because the applicant solves
// that CAPTCHA themselves in their own secure window. The two rules the
// backend module holds (app/gov_calendar.py) are visible in this flow:
//
//   1. Ellis never solves the CAPTCHA. It detects the gate and hands off.
//   2. Ellis never clicks a date. On these systems a click reserves a REAL
//      slot; Ellis reads the grid and the applicant chooses.
//
// Nothing here is simulated: every mission, category and open day comes from
// the applicant's own live session on the government site.
import { useRef, useState } from 'react'
import { Loading, ErrorNote } from '../components/ui.jsx'
import { AppointmentIllustration } from '../components/visa/Illustrations.jsx'
import { LiveFrame, usePortalLiveView } from '../components/visa/handoffs.jsx'
import { createVisaClient } from '../lib/visaBackend.js'
import { newSession } from '../lib/visaSession.js'

const NAVY = 'var(--trip-navy, #0f294d)'
const GRAY = 'var(--trip-gray, #64748b)'
const BLUE = 'var(--trip-blue, #287dfa)'

function Card({ children, style = {}, ...rest }) {
  return (
    <div className="card" style={{ padding: 22, borderRadius: 20, ...style }} {...rest}>
      {children}
    </div>
  )
}

function Step({ n, title, done, active, children }) {
  return (
    <Card className="anim-rise" style={{ marginTop: 14,
          border: active ? `2px solid ${BLUE}` : '1px solid #e2e8f0',
          opacity: (!active && !done) ? 0.55 : 1 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <span style={{ width: 28, height: 28, borderRadius: 999, flexShrink: 0,
                       display: 'grid', placeItems: 'center', fontSize: 13,
                       fontWeight: 800,
                       background: done ? '#e8f7ee' : active ? '#eaf2ff' : '#f1f5f9',
                       color: done ? '#0d7a37' : active ? BLUE : GRAY }}>
          {done ? '✓' : n}
        </span>
        <span style={{ fontWeight: 800, fontSize: 15, color: NAVY }}>{title}</span>
      </div>
      {(active || done) && <div style={{ marginTop: 12 }}>{children}</div>}
    </Card>
  )
}

export default function SchengenVisa({ onBack }) {
  const clientRef = useRef(null)
  if (!clientRef.current) {
    clientRef.current = createVisaClient(newSession({ orgId: 'ellis-demo' }))
  }
  const client = clientRef.current

  const [caseId, setCaseId] = useState('')
  const [windowOpen, setWindowOpen] = useState(false)
  const [missions, setMissions] = useState(null)
  const [search, setSearch] = useState('')
  const [loc, setLoc] = useState('')
  const [opened, setOpened] = useState(null)   // walk result: categories + captcha gate
  const [month, setMonth] = useState(null)     // the real month grid
  const [busy, setBusy] = useState('')
  const [picked, setPicked] = useState(null)   // the day the applicant chose
  const [error, setError] = useState(null)

  const view = usePortalLiveView(client, windowOpen ? caseId : '')

  function fail(e) {
    setError({ message: e?.detail?.detail || e?.detail?.reason || e?.message
                        || 'That did not work.' })
  }

  // 1. A case to hang the session on, then the applicant's OWN secure window.
  async function startSession() {
    setBusy('session'); setError(null)
    try {
      const made = await client.createCase({
        full_name: 'Schengen test applicant', email: 'test@example.com',
        destination_country: 'Germany', visa_type: 'tourist', answers: {}
      })
      setCaseId(made.id)
      await client.createBrowserSession(made.id)
      setWindowOpen(true)
    } catch (e) { fail(e) }
    setBusy('')
  }

  // 2. Every mission RK-Termin serves, read from the live site.
  async function loadMissions() {
    setBusy('missions'); setError(null)
    try {
      const out = await client.calendarMissions(caseId)
      const list = out.missions || []
      setMissions(list)
      if (list.length && !loc) setLoc(list[0].code)
    } catch (e) { fail(e) }
    setBusy('')
  }

  // 3. Walk the applicant's window to that mission's calendar.
  async function openCalendar() {
    if (!loc) return
    setBusy('open'); setError(null)
    try {
      setMonth(null)
      setOpened(await client.calendarOpen(caseId, loc))
    } catch (e) { fail(e) }
    setBusy('')
  }

  // 4. Read the grid the applicant's own window is showing — after THEY
  //    cleared the image check. Ellis reads; it never clicks a day.
  async function readMonth() {
    setBusy('month'); setError(null)
    try { setMonth(await client.calendarMonth(caseId)) } catch (e) { fail(e) }
    setBusy('')
  }

  // 5. Carry the applicant's OWN choice to the government site: open exactly
  //    the day they clicked in Ellis, in their own window. Ellis never picks
  //    the day and never submits the form behind it.
  async function pickDay(day) {
    setBusy('pick:' + day.href); setError(null)
    try {
      const known = (month?.days || []).map((d) => d.href)
      const out = await client.calendarPick(caseId, day.href, known)
      setPicked({ ...day, opened: out })
    } catch (e) { fail(e) }
    setBusy('')
  }

  const shown = (missions || []).filter(
    (m) => !search || (m.name || '').toLowerCase().includes(search.toLowerCase()))

  return (
    <div className="page" style={{ maxWidth: 900, margin: '0 auto',
                                   padding: '26px 20px 60px' }}
         data-testid="schengen-visa">
      <button className="btn btn--sm btn--ghost" onClick={onBack}
              data-testid="schengen-back">← Menu</button>

      <div style={{ display: 'flex', alignItems: 'center', gap: 16,
                    margin: '14px 0 4px' }}>
        <AppointmentIllustration size={72} />
        <div>
          <h1 style={{ fontSize: 30, fontWeight: 800, color: NAVY, margin: 0,
                       letterSpacing: -0.6 }}>Schengen visa</h1>
          <div style={{ fontSize: 14, color: GRAY, marginTop: 5 }}>
            Germany’s official appointment calendar — the one Schengen system
            with no account. Real dates, read live.
          </div>
        </div>
      </div>

      {error && <ErrorNote error={error} />}

      <Step n={1} title="Open your secure window" active={!windowOpen} done={windowOpen}>
        <div style={{ fontSize: 13, color: GRAY, marginBottom: 12 }}>
          The calendar is read inside your own browser session, so the image
          check you clear and the slot you pick belong to you.
        </div>
        {!windowOpen && (
          <button className="trip-cta trip-cta--sm" disabled={busy === 'session'}
                  onClick={startSession} data-testid="schengen-open-window">
            {busy === 'session' ? 'Opening…' : 'Open secure window'}
          </button>
        )}
        {windowOpen && (
          <div style={{ fontSize: 12.5, color: GRAY }}>
            Session open on case <code>{caseId.slice(0, 8)}</code>
          </div>
        )}
      </Step>

      <Step n={2} title="Choose the German mission" active={windowOpen && !opened}
            done={!!opened}>
        {missions === null ? (
          <button className="btn btn--sm" disabled={busy === 'missions'}
                  onClick={loadMissions} data-testid="schengen-load-missions">
            {busy === 'missions' ? 'Reading the mission list…' : 'Load missions from the official site'}
          </button>
        ) : (
          <>
            <div style={{ fontSize: 12.5, color: GRAY, marginBottom: 8 }}>
              {missions.length} missions, read live from service2.diplo.de
            </div>
            <input className="input" style={{ width: '100%' }} value={search}
                   placeholder="Search — Beijing, Shanghai, Istanbul…"
                   onChange={(e) => setSearch(e.target.value)}
                   data-testid="schengen-search" />
            <select className="select" size={7} value={loc}
                    style={{ width: '100%', marginTop: 8 }}
                    onChange={(e) => setLoc(e.target.value)}
                    data-testid="schengen-mission">
              {shown.map((m) => (
                <option key={m.code} value={m.code}>{m.name}</option>
              ))}
            </select>
            <button className="trip-cta trip-cta--sm" style={{ marginTop: 12 }}
                    disabled={!loc || busy === 'open'} onClick={openCalendar}
                    data-testid="schengen-open-calendar">
              {busy === 'open' ? 'Opening the calendar…' : 'Open this calendar'}
            </button>
          </>
        )}
      </Step>

      <Step n={3} title="Clear the image check yourself" active={!!opened && !month}
            done={!!month}>
        {opened && (
          <>
            {Array.isArray(opened.categories) && opened.categories.length > 0 && (
              <div style={{ fontSize: 12.5, color: GRAY, marginBottom: 10 }}>
                Categories offered here: {opened.categories.slice(0, 5)
                  .map((c) => c.label).join(' · ')}
              </div>
            )}
            {opened.captcha_required && !month && (
              <div className="card card--soft" style={{ padding: 14, borderRadius: 14,
                   fontSize: 13, marginBottom: 12 }} data-testid="schengen-captcha">
                The site is showing its image check. Type the characters in the
                window below — <b>Ellis never solves it</b>. Then read the month.
              </div>
            )}
            {windowOpen && <LiveFrame view={view} height="52vh" />}
            <button className="trip-cta trip-cta--sm" style={{ marginTop: 12 }}
                    disabled={busy === 'month'} onClick={readMonth}
                    data-testid="schengen-read-month">
              {busy === 'month' ? 'Reading the month…' : 'Read the open dates'}
            </button>
          </>
        )}
      </Step>

      <Step n={4} title="The open dates" active={!!month} done={!!month}>
        {busy === 'month' && <Loading size="big" label="Reading the official calendar" />}
        {month && !month.readable && (
          <div className="card card--soft" style={{ padding: 14, borderRadius: 14,
               fontSize: 13 }} data-testid="schengen-unreadable">
            {month.reason || 'The calendar could not be read yet.'}
          </div>
        )}
        {month && month.readable && month.none_available && (
          <div className="card card--soft" style={{ padding: 14, borderRadius: 14,
               fontSize: 13 }} data-testid="schengen-none">
            This mission is showing no open dates right now. That is the real
            answer from the government site, not a failure.
          </div>
        )}
        {month && month.readable && !month.none_available && (
          <div data-testid="schengen-days">
            <div style={{ fontWeight: 800, fontSize: 15, color: NAVY,
                          marginBottom: 4 }}>
              {month.bookable_count} open {month.bookable_count === 1 ? 'day' : 'days'}
            </div>
            <div style={{ fontSize: 12.5, color: GRAY, marginBottom: 12 }}>
              Read from the official calendar just now. Ellis does not click a
              day — opening one reserves a real slot, so that choice is yours.
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}
                 data-testid="schengen-grid">
              {(month.days || []).map((d) => {
                const on = picked && picked.href === d.href
                const loading = busy === ('pick:' + d.href)
                return (
                  <button key={d.href} onClick={() => pickDay(d)} disabled={!!busy}
                    data-testid={`schengen-day-${d.label.replace(/\s+/g,'-')}`}
                    title={d.title || ''}
                    style={{ padding: '12px 16px', borderRadius: 14, minWidth: 84,
                             cursor: 'pointer', fontSize: 13.5, fontWeight: 700,
                             transition: 'all .15s ease',
                             border: on ? `2px solid ${BLUE}` : '1px solid #dbe3ec',
                             background: on ? BLUE : '#fff',
                             color: on ? '#fff' : NAVY }}>
                    {loading ? '…' : d.label}
                  </button>
                )
              })}
            </div>
            {picked && (
              <div className="card card--soft anim-rise" style={{ padding: 16,
                   borderRadius: 14, marginTop: 14 }} data-testid="schengen-picked">
                <div style={{ fontWeight: 800, fontSize: 15, color: NAVY }}>
                  {picked.label} — open in your window
                </div>
                <div style={{ fontSize: 13, color: GRAY, marginTop: 6 }}>
                  {picked.opened?.note || ('Ellis opened this exact date on the '
                    + 'official site, in your own secure window. Enter your '
                    + 'details there and confirm — the booking and its '
                    + 'confirmation email are yours to complete.')}
                </div>
              </div>
            )}
          </div>
        )}
      </Step>

      <div style={{ fontSize: 11.5, color: GRAY, marginTop: 20, lineHeight: 1.6 }}>
        Germany’s RK-Termin is the Federal Foreign Office’s own booking system
        and requires no account. Ellis reads its published calendar and never
        solves the image check, never holds and never clicks a date.
      </div>
    </div>
  )
}
