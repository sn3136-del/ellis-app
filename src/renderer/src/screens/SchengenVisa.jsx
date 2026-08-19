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
import { useEffect, useMemo, useRef, useState } from 'react'
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

// Days grouped into real calendar months. RK-Termin labels a day like
// "20.09.2026"; anything unparseable keeps its own label in a trailing group
// rather than being dropped or guessed at.
function monthsOf(days) {
  const MON = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
               'August', 'September', 'October', 'November', 'December']
  const byKey = new Map()
  const loose = []
  for (const d of days) {
    const m = (d.label || '').match(/(\d{1,2})[.\/-](\d{1,2})[.\/-](\d{4})/)
    if (!m) { loose.push(d); continue }
    const day = +m[1], mon = +m[2] - 1, year = +m[3]
    const key = `${year}-${mon}`
    if (!byKey.has(key)) {
      byKey.set(key, { key, year, mon, title: `${MON[mon]} ${year}`, days: [] })
    }
    byKey.get(key).days.push({ ...d, day })
  }
  const months = [...byKey.values()].sort((a, b) =>
    a.year - b.year || a.mon - b.mon)
  for (const m of months) m.days.sort((a, b) => a.day - b.day)
  return { months, loose }
}

export default function SchengenVisa({ onBack }) {
  const clientRef = useRef(null)
  if (!clientRef.current) {
    clientRef.current = createVisaClient(newSession({ orgId: 'ellis-demo' }))
  }
  const client = clientRef.current

  // One flow, driven by phase — never a list of steps to click through.
  //   starting -> picking -> opening -> captcha -> reading -> dates -> booking
  const [phase, setPhase] = useState('starting')
  const [caseId, setCaseId] = useState('')
  const [missions, setMissions] = useState([])
  const [search, setSearch] = useState('')
  const [listOpen, setListOpen] = useState(false)
  const [loc, setLoc] = useState('')
  const [captcha, setCaptcha] = useState('')
  const [answer, setAnswer] = useState('')
  const [capNote, setCapNote] = useState('')
  const [month, setMonth] = useState(null)
  const [picked, setPicked] = useState(null)
  const [error, setError] = useState(null)
  const startedRef = useRef(false)

  const view = usePortalLiveView(client, caseId)

  function fail(e) {
    setError({ message: e?.detail?.detail || e?.detail?.reason || e?.message
                        || 'That did not work.' })
  }

  // Everything that needs no decision happens on mount, in ONE pass: the case,
  // the applicant's secure window and the live mission list are set up while
  // they read the heading. The first thing they are asked is the only thing
  // Ellis cannot know — which city.
  useEffect(() => {
    if (startedRef.current) return
    startedRef.current = true
    ;(async () => {
      try {
        const made = await client.createCase({
          full_name: 'Schengen applicant', email: 'applicant@example.com',
          destination_country: 'Germany', visa_type: 'tourist', answers: {}
        })
        setCaseId(made.id)
        // The window and the mission list do not depend on each other.
        const [, out] = await Promise.all([
          client.createBrowserSession(made.id),
          client.calendarMissions(made.id)
        ])
        setMissions(out.missions || [])
        setPhase('picking')
      } catch (e) { fail(e); setPhase('picking') }
    })()
  }, [])

  // Choosing a city runs the whole rest of the chain without another click:
  // open the calendar, and if the site challenges, fetch the image at once.
  async function choose(m) {
    setLoc(m.code); setSearch(m.name || ''); setListOpen(false)
    setPhase('opening'); setError(null)
    setMonth(null); setPicked(null); setCaptcha(''); setAnswer(''); setCapNote('')
    try {
      const out = await client.calendarOpen(caseId, m.code)
      if (out.captcha_required) {
        setPhase('captcha')
        const img = await client.calendarCaptcha(caseId).catch(() => ({}))
        setCaptcha(img.image || '')
      } else {
        setPhase('reading')
        setMonth(await client.calendarMonth(caseId))
        setPhase('dates')
      }
    } catch (e) { fail(e); setPhase('picking') }
  }

  // The applicant read the image and typed it here. Ellis transcribes their
  // answer into the portal's own field — it never reads the picture — then
  // goes straight on to the month.
  async function answerCaptcha() {
    if (!answer.trim()) return
    setPhase('reading'); setError(null); setCapNote('')
    try {
      const out = await client.calendarCaptchaSubmit(caseId, answer.trim())
      if (out.still_challenged) {
        setCapNote(out.note || 'That did not match — here is a fresh picture.')
        setAnswer('')
        const img = await client.calendarCaptcha(caseId).catch(() => ({}))
        setCaptcha(img.image || '')
        setPhase('captcha')
        return
      }
      setMonth(await client.calendarMonth(caseId))
      setPhase('dates')
    } catch (e) { fail(e); setPhase('captcha') }
  }

  // The applicant's own pick, carried to the official site.
  async function pickDay(d) {
    setPhase('booking'); setError(null)
    try {
      const known = (month?.days || []).map((x) => x.href)
      const out = await client.calendarPick(caseId, d.href, known)
      setPicked({ ...d, opened: out })
    } catch (e) { fail(e) }
    setPhase('dates')
  }

  const shown = useMemo(() => missions.filter(
    (m) => !search || (m.name || '').toLowerCase().includes(search.toLowerCase())),
    [missions, search])
  const quick = useMemo(() => missions.filter(
    (m) => /beijing|peking|shanghai|guangzhou|chengdu|hong kong/i.test(m.name || '')),
    [missions])
  const grouped = useMemo(() => monthsOf(month?.days || []), [month])

  const working = ['starting', 'opening', 'reading', 'booking'].includes(phase)
  const workLabel = {
    starting: 'Opening your secure session and reading the mission list',
    opening: 'Opening the official calendar',
    reading: 'Reading the open dates from the official calendar',
    booking: 'Opening your date on the official site'
  }[phase]

  return (
    <div className="page" style={{ maxWidth: 860, margin: '0 auto',
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
            Germany’s official calendar — real dates, read live. Pick one and
            Ellis opens it for you.
          </div>
        </div>
      </div>

      {error && <ErrorNote error={error} />}

      {working && (
        <Card className="anim-rise" style={{ marginTop: 16 }}
              data-testid="schengen-working">
          <div style={{ display: 'flex', justifyContent: 'center',
                        padding: '10px 0 4px' }}>
            <Loading size="big" label={workLabel} />
          </div>
        </Card>
      )}

      {/* City — the one thing Ellis cannot know. Everything else runs itself. */}
      {(phase === 'picking' || phase === 'captcha' || phase === 'dates') && (
        <Card className="anim-rise" style={{ marginTop: 16 }}
              data-testid="schengen-picker">
          <div style={{ fontSize: 12, fontWeight: 800, letterSpacing: 0.6,
                        color: GRAY, textTransform: 'uppercase' }}>
            Where you are applying
          </div>
          {quick.length > 0 && (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
              {quick.map((m) => (
                <button key={m.code} onClick={() => choose(m)}
                  data-testid={`schengen-quick-${m.code}`}
                  style={{ padding: '9px 16px', borderRadius: 999, fontSize: 13.5,
                           fontWeight: 700, cursor: 'pointer',
                           border: m.code === loc ? `2px solid ${BLUE}` : '1px solid #dbe3ec',
                           background: m.code === loc ? '#f5f9ff' : '#fff', color: NAVY }}>
                  {(m.name || '').replace(/^German[y]?\s*/i, '')}
                </button>
              ))}
            </div>
          )}
          <div style={{ position: 'relative', marginTop: 12 }}>
            <input className="input" style={{ width: '100%', paddingRight: 34 }}
                   value={search}
                   placeholder="Or choose from all 190+ missions — type to filter"
                   onChange={(e) => { setSearch(e.target.value); setListOpen(true) }}
                   onFocus={() => setListOpen(true)}
                   onBlur={() => setTimeout(() => setListOpen(false), 150)}
                   onKeyDown={(e) => {
                     if (e.key === 'Enter' && shown.length) choose(shown[0])
                     if (e.key === 'Escape') setListOpen(false)
                   }}
                   data-testid="schengen-search" />
            {/* The caret says this is a dropdown, not just a search box. */}
            <button type="button" aria-label="Show all missions"
                    onMouseDown={(e) => { e.preventDefault(); setListOpen((v) => !v) }}
                    data-testid="schengen-toggle"
                    style={{ position: 'absolute', right: 8, top: 8, border: 'none',
                             background: 'transparent', cursor: 'pointer',
                             color: GRAY, padding: 4, lineHeight: 1 }}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"
                   strokeLinejoin="round" aria-hidden="true"
                   style={{ transform: listOpen ? 'rotate(180deg)' : 'none',
                            transition: 'transform .15s ease' }}>
                <path d="M6 9l6 6 6-6" />
              </svg>
            </button>
            {listOpen && shown.length > 0 && (
              <div style={{ position: 'absolute', top: '100%', left: 0, right: 0,
                            zIndex: 30, background: '#fff', borderRadius: 12,
                            border: '1px solid #e2e8f0', marginTop: 4,
                            maxHeight: 320, overflowY: 'auto',
                            boxShadow: '0 12px 32px rgba(15,41,77,.10)' }}
                   data-testid="schengen-mission-list">
                {shown.slice(0, 200).map((m) => (
                  <button key={m.code}
                    onMouseDown={(e) => { e.preventDefault(); choose(m) }}
                    data-testid={`schengen-option-${m.code}`}
                    style={{ display: 'block', width: '100%', textAlign: 'left',
                             padding: '10px 14px', border: 'none', cursor: 'pointer',
                             fontSize: 13.5, background: m.code === loc ? '#f5f9ff' : '#fff',
                             color: NAVY, fontWeight: m.code === loc ? 700 : 400 }}>
                    {m.name}
                  </button>
                ))}
              </div>
            )}
          </div>
        </Card>
      )}

      {/* The challenge: shown BIG in Ellis, answered in Ellis. */}
      {phase === 'captcha' && (
        <Card className="anim-rise-1" style={{ marginTop: 16 }}
              data-testid="schengen-captcha">
          <div style={{ fontWeight: 800, fontSize: 15, color: NAVY }}>
            Type the characters you see
          </div>
          <div style={{ fontSize: 13, color: GRAY, marginTop: 4, marginBottom: 14 }}>
            The official site asks this to prove a person is here. You read it,
            Ellis types it in for you — Ellis never reads the picture itself.
          </div>
          <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap',
                        alignItems: 'center' }}>
            {captcha ? (
              <img src={captcha} alt="Challenge image"
                   data-testid="schengen-captcha-img"
                   style={{ height: 190, imageRendering: 'pixelated',
                            borderRadius: 14, border: '1px solid #dbe3ec',
                            background: '#fff', padding: 10 }} />
            ) : (
              <div style={{ fontSize: 12.5, color: GRAY }}>Loading the picture…</div>
            )}
            <div style={{ flex: '1 1 240px', minWidth: 220 }}>
              <input className="input" value={answer} autoFocus
                     style={{ width: '100%', fontSize: 26, letterSpacing: 6,
                              fontWeight: 800, textAlign: 'center',
                              padding: '14px 12px' }}
                     data-testid="schengen-captcha-input"
                     onChange={(e) => setAnswer(e.target.value)}
                     onKeyDown={(e) => { if (e.key === 'Enter') answerCaptcha() }} />
              <button className="trip-cta trip-cta--sm"
                      style={{ marginTop: 12, width: '100%' }}
                      disabled={!answer.trim()} onClick={answerCaptcha}
                      data-testid="schengen-captcha-submit">
                Show me the dates
              </button>
              {capNote && (
                <div style={{ fontSize: 12.5, color: '#b4231f', marginTop: 10 }}
                     data-testid="schengen-captcha-note">{capNote}</div>
              )}
            </div>
          </div>
        </Card>
      )}

      {/* The real dates, as a calendar. */}
      {phase === 'dates' && month && (
        <Card className="anim-rise-2" style={{ marginTop: 16 }}
              data-testid="schengen-dates">
          {!month.readable ? (
            <div style={{ fontSize: 13.5, color: NAVY }}>
              {month.reason || 'The calendar could not be read.'}
            </div>
          ) : month.none_available ? (
            <div data-testid="schengen-none">
              <div style={{ fontWeight: 800, fontSize: 15, color: NAVY }}>
                No open dates at this mission right now
              </div>
              <div style={{ fontSize: 13, color: GRAY, marginTop: 6 }}>
                That is the real answer from the government site. German
                calendars are often empty — try another city, or check back.
              </div>
            </div>
          ) : (
            <>
              <div style={{ display: 'flex', justifyContent: 'space-between',
                            alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
                <div style={{ fontWeight: 800, fontSize: 16, color: NAVY }}>
                  {month.bookable_count} open {month.bookable_count === 1 ? 'date' : 'dates'}
                </div>
                <div style={{ fontSize: 12, color: GRAY }}>
                  read from the official calendar just now
                </div>
              </div>
              {grouped.months.map((mo) => (
                <div key={mo.key} style={{ marginTop: 18 }}>
                  <div style={{ fontSize: 13, fontWeight: 800, color: NAVY,
                                marginBottom: 10 }}>{mo.title}</div>
                  <div style={{ display: 'grid', gap: 8,
                                gridTemplateColumns: 'repeat(auto-fill, minmax(62px, 1fr))' }}>
                    {mo.days.map((d) => {
                      const on = picked && picked.href === d.href
                      return (
                        <button key={d.href} onClick={() => pickDay(d)}
                          title={d.title || d.label}
                          data-testid={`schengen-day-${d.day}`}
                          style={{ padding: '14px 0', borderRadius: 12,
                                   fontSize: 16, fontWeight: 800,
                                   cursor: 'pointer', transition: 'all .15s ease',
                                   border: on ? `2px solid ${BLUE}` : '1px solid #dbe3ec',
                                   background: on ? BLUE : '#fff',
                                   color: on ? '#fff' : NAVY }}>
                          {d.day}
                        </button>
                      )
                    })}
                  </div>
                </div>
              ))}
              {grouped.loose.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8,
                              marginTop: 16 }}>
                  {grouped.loose.map((d) => (
                    <button key={d.href} onClick={() => pickDay(d)}
                      style={{ padding: '10px 14px', borderRadius: 12,
                               fontSize: 13.5, fontWeight: 700, cursor: 'pointer',
                               border: '1px solid #dbe3ec', background: '#fff',
                               color: NAVY }}>{d.label}</button>
                  ))}
                </div>
              )}
              {picked && (
                <div className="card card--soft anim-rise" style={{ padding: 16,
                     borderRadius: 14, marginTop: 18 }}
                     data-testid="schengen-picked">
                  <div style={{ fontWeight: 800, fontSize: 15, color: NAVY }}>
                    {picked.label} — open on the official site
                  </div>
                  <div style={{ fontSize: 13, color: GRAY, marginTop: 6 }}>
                    {picked.opened?.note || ('Ellis opened this date in your own '
                      + 'session. Your name, passport and email go on the '
                      + 'booking form there — they are yours to enter.')}
                  </div>
                </div>
              )}
            </>
          )}
        </Card>
      )}

      {/* The live window is shown ONLY during the image check — that is the one
          moment the applicant benefits from seeing the official page. The rest
          of the flow (opening, reading, picking) is Ellis's to run; a window
          open the whole time is noise and an idle Browserbase view. */}
      {caseId && phase === 'captcha' && (
        <div style={{ marginTop: 18 }}>
          <div style={{ fontSize: 11.5, color: GRAY, marginBottom: 6 }}>
            Your session on service2.diplo.de — watch only
          </div>
          <LiveFrame view={view} height="40vh" watchOnly
                     client={client} caseId={caseId} />
        </div>
      )}

    </div>
  )
}
