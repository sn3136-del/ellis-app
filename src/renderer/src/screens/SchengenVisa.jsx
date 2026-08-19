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

const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
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
    // The backend parses the real date out of the day's href; the visible
    // cell text on RK-Termin is 'Appointments are available', never a date.
    const m = String(d.date || d.label || '')
      .match(/(\d{1,2})[.\/-](\d{1,2})[.\/-](\d{4})/)
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

// RK-Termin lists its missions in GERMAN — the Beijing post is "Peking", the
// Guangzhou one is "Kanton". An applicant types the English or local name, so
// every German exonym on the live list (read from choose_locationList.do,
// 196 missions, 2026-08-19) carries its aliases here. Matching is on the
// official name OR any alias; the name SHOWN is always the portal's own.
const MISSION_ALIASES = {
  peki: ['beijing', 'bei jing', '北京'],
  kant: ['guangzhou', 'canton', '广州'],
  shan: ['shanghai', '上海'],
  shen: ['shenyang', '沈阳'],
  cheng: ['chengdu', '成都'],
  hong: ['hong kong', 'hongkong', '香港'],
  mosk: ['moscow', 'moskva'],
  wien: ['vienna'],
  prag: ['prague', 'praha'],
  wars: ['warsaw', 'warszawa'],
  kair: ['cairo'],
  rom: ['rome', 'roma'],
  maila: ['milan', 'milano'],
  liss: ['lisbon', 'lisboa'],
  kope: ['copenhagen', 'kobenhavn'],
  brue: ['brussels', 'bruxelles'],
  athe: ['athens', 'athina'],
  buka: ['bucharest', 'bucuresti'],
  belg: ['belgrade', 'beograd'],
  tehe: ['tehran'],
  riad: ['riyadh'],
  algi: ['algiers'],
  kaps: ['cape town'],
  sing: ['singapore'],
  hoch: ['ho chi minh city', 'ho chi minh', 'saigon'],
  mexi: ['mexico city'],
  guat: ['guatemala city'],
  hava: ['havana', 'la habana'],
  niko: ['nicosia'],
  kiew: ['kyiv', 'kiev'],
  krak: ['krakow', 'cracow'],
  danz: ['gdansk'],
  bres: ['wroclaw', 'breslau'],
  pres: ['bratislava'],
  laib: ['ljubljana'],
  herm: ['sibiu'],
  oppe: ['opole'],
  jaun: ['yaounde'],
  khar: ['khartoum'],
  rang: ['yangon', 'rangoon'],
  ulan: ['ulaanbaatar', 'ulan bator'],
  asch: ['ashgabat'],
  bisc: ['bishkek'],
  dusc: ['dushanbe'],
  tasc: ['tashkent'],
  eriw: ['yerevan'],
  tifl: ['tbilisi'],
  jeka: ['yekaterinburg', 'ekaterinburg'],
  nowo: ['novosibirsk'],
  sarj: ['sarajevo'],
  wind: ['windhoek'],
  djid: ['jeddah', 'jiddah'],
  mask: ['muscat'],
  bagd: ['baghdad'],
  stra: ['strasbourg'],
  luxe: ['luxembourg'],
  addi: ['addis ababa'],
  dare: ['dar es salaam'],
  lome: ['lome'],
  osak: ['osaka', 'kobe'],
  lasp: ['las palmas'],
  wiln: ['vilnius'],
  toky: ['tokyo'],
  seou: ['seoul'],
  taip: ['taipei'],
  bangk: ['bangkok'],
  kual: ['kuala lumpur'],
  jaka: ['jakarta'],
  mani: ['manila'],
  newd: ['new delhi', 'delhi'],
  banga: ['bangalore', 'bengaluru'],
  chenn: ['chennai', 'madras'],
  isla: ['islamabad'],
  kara: ['karachi'],
  dhak: ['dhaka'],
  kath: ['kathmandu'],
  colo: ['colombo'],
  duba: ['dubai'],
  doha: ['doha'],
  ista: ['istanbul'],
  tela: ['tel aviv'],
  amma: ['amman'],
  beir: ['beirut'],
  nair: ['nairobi'],
  lago: ['lagos'],
  accr: ['accra'],
  abuj: ['abuja'],
  pret: ['pretoria'],
  saop: ['sao paulo'],
  rio: ['rio de janeiro'],
  buen: ['buenos aires'],
  santi: ['santiago'],
  lima: ['lima'],
  bogo: ['bogota'],
  cara: ['caracas'],
  newy: ['new york'],
  losa: ['los angeles'],
  sanf: ['san francisco'],
  chic: ['chicago'],
  hous: ['houston'],
  bost: ['boston'],
  miam: ['miami'],
  atla: ['atlanta'],
  wash: ['washington', 'washington dc'],
  toro: ['toronto'],
  vanc: ['vancouver'],
  otta: ['ottawa'],
  sydn: ['sydney'],
  melb: ['melbourne'],
  well: ['wellington'],
  lond: ['london'],
  edin: ['edinburgh'],
  dubl: ['dublin'],
  pari: ['paris'],
  lyon: ['lyon'],
  mars: ['marseille'],
  bord: ['bordeaux'],
  madri: ['madrid'],
  barc: ['barcelona'],
  amst: ['amsterdam'],
  bern: ['bern', 'berne'],
  stoc: ['stockholm'],
  oslo: ['oslo'],
  hels: ['helsinki'],
  reyk: ['reykjavik'],
  riga: ['riga'],
  tall: ['tallinn'],
  mins: ['minsk'],
  stpe: ['st petersburg', 'saint petersburg'],
  sofi: ['sofia'],
  skop: ['skopje'],
  tira: ['tirana'],
  zagr: ['zagreb'],
  podg: ['podgorica'],
  pris: ['pristina'],
  chis: ['chisinau'],
  baku: ['baku'],
  alma: ['almaty'],
  anka: ['ankara'],
  izmi: ['izmir'],
  antl: ['antalya'],
  thes: ['thessaloniki'],
  vall: ['valletta'],
  buda: ['budapest'],
  raba: ['rabat'],
  tuni: ['tunis'],
  abid: ['abidjan'],
  daka: ['dakar'],
  bama: ['bamako'],
  cona: ['conakry'],
  coto: ['cotonou'],
  ouag: ['ouagadougou'],
  kins: ['kinshasa'],
  luan: ['luanda'],
  lusa: ['lusaka'],
  hara: ['harare'],
  gabo: ['gaborone'],
  mapu: ['maputo'],
  anta: ['antananarivo'],
  kamp: ['kampala'],
  kiga: ['kigali'],
  asma: ['asmara'],
  abud: ['abu dhabi'],
  kuwa: ['kuwait city', 'kuwait'],
  manam: ['manama'],
  erbi: ['erbil'],
  rama: ['ramallah'],
  hano: ['hanoi'],
  phno: ['phnom penh'],
  vien: ['vientiane'],
  brisb: ['brisbane'],
  pert: ['perth'],
  adel: ['adelaide'],
  canb: ['canberra'],
  king: ['kingston'],
  ports: ['port of spain'],
  sanj: ['san jose'],
  sans: ['san salvador'],
  tegu: ['tegucigalpa'],
  manag: ['managua'],
  pana: ['panama city', 'panama'],
  quit: ['quito'],
  lapa: ['la paz'],
  monte: ['montevideo'],
  asun: ['asuncion'],
  reci: ['recife'],
  porta: ['porto alegre'],
  santo: ['santo domingo'],
  noua: ['nouakchott'],
  sana: ['sanaa'],
  kali: ['kaliningrad'],
  mala: ['malaga'],
  palm: ['palma', 'mallorca'],
  niko: ['nicosia', 'lefkosia']
}

// Fold accents so "Brussel" finds "Brüssel" and "Sao" finds "São".
function fold(s) {
  return String(s || '').toLowerCase()
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
}

/** Does this mission match what the applicant typed — by its official German
 *  name, or by any English/local alias for it? */
function missionMatches(m, query) {
  const q = fold(query).trim()
  if (!q) return true
  if (fold(m.name).includes(q)) return true
  return (MISSION_ALIASES[m.code] || []).some((a) => fold(a).includes(q)
                                                  || q.includes(fold(a)))
}

export default function SchengenVisa({ onBack }) {
  const clientRef = useRef(null)
  if (!clientRef.current) {
    clientRef.current = createVisaClient(newSession({ orgId: 'ellis-demo' }))
  }
  const client = clientRef.current

  // One flow, driven by phase — never a list of steps to click through.
  //   starting -> picking -> opening -> captcha -> submitting -> reading
  //   -> dates -> booking
  // 'submitting' deliberately keeps the live window on screen: the applicant
  // watches Ellis type their answer into the official field.
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

  // The live-view hook OPENS A SESSION of its own for whatever caseId it is
  // given. During startup that raced the mount's createBrowserSession and the
  // missions read landed mid-replacement (409 -> empty list, invisibly). It is
  // only ever rendered on the captcha step, so it is only given the case then.
  const view = usePortalLiveView(
    client, (phase === 'captcha' || phase === 'submitting') ? caseId : '')
  // A Browserbase session can idle out while the applicant reads the
  // challenge. Rather than surface a reconnect card, reopen it silently — but
  // only once per lapse, so a genuinely unavailable window does not loop.
  const reconnectRef = useRef(false)
  useEffect(() => {
    if ((phase === 'captcha' || phase === 'submitting')
        && (view.state === 'closed' || view.state === 'unavailable')) {
      if (!reconnectRef.current) { reconnectRef.current = true; view.reconnect() }
    } else if (view.state === 'embedded') {
      reconnectRef.current = false
    }
  }, [phase, view.state])

  // The secure window is plumbing the applicant never asked for, so its
  // absence is never their problem to read about: reopen it and run the call
  // again. Only a second failure is worth showing, and never in the backend's
  // own words about sessions.
  function isWindowGone(e) {
    const r = String(e?.detail?.reason || '')
    return r === 'no_secure_window' || r === 'session_ended'
  }

  // Every live call goes through the applicant's cloud browser: Ellis attaches
  // over CDP, drives a real government page, detaches. That is usually seconds
  // but can wedge, and an unbounded await leaves the surface on a spinner
  // forever ("stuck at reading the open dates"). Nothing here waits without a
  // bound; a timeout is reported as itself, not as a hang.
  function withTimeout(promise, ms, what) {
    return Promise.race([
      promise,
      new Promise((_, reject) => setTimeout(
        () => reject(Object.assign(new Error(what), { timedOut: true })), ms))
    ])
  }

  async function withWindow(fn) {
    try {
      return await fn()
    } catch (e) {
      if (!isWindowGone(e) || !caseId) throw e
      await client.createBrowserSession(caseId)
      return await fn()
    }
  }

  function fail(e) {
    // The banner is hidden on this surface by owner choice, so leave a trace
    // in the console — otherwise a startup failure is completely invisible.
    try { console.error('[schengen]', e?.detail || e?.message || e) } catch { /* noop */ }
    if (e && e.timedOut) {
      setCapNote('That took too long on the official site. Try again.')
      return
    }
    if (isWindowGone(e)) {
      setError({ message: 'That took too long and the connection dropped. '
                          + 'Pick your city again to start over.' })
      return
    }
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
        // The mission list is READ THROUGH the applicant's window, so the
        // window must exist first. (Racing these with Promise.all 409s every
        // time with no_secure_window — the list then silently arrives empty.)
        await client.createBrowserSession(made.id)
        const out = await client.calendarMissions(made.id)
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
      const out = await withTimeout(
        withWindow(() => client.calendarOpen(caseId, m.code)), 60000,
        'open the calendar')
      if (out.captcha_required) {
        setPhase('captcha')
        const img = await client.calendarCaptcha(caseId).catch(() => ({}))
        setCaptcha(img.image || '')
      } else {
        setPhase('reading')
        setMonth(await withTimeout(
          withWindow(() => client.calendarMonth(caseId)), 60000, 'read the calendar'))
        setPhase('dates')
      }
    } catch (e) { fail(e); setPhase('picking') }
  }

  // The applicant read the image and typed it here. Ellis transcribes their
  // answer into the portal's own field — it never reads the picture — then
  // goes straight on to the month.
  async function answerCaptcha() {
    if (!answer.trim()) return
    // NOT 'reading': that swaps in a full-screen loader and unmounts the live
    // window. The applicant asked to WATCH Ellis enter the answer, so the
    // window stays and only the card's own controls change.
    setPhase('submitting'); setError(null); setCapNote('')
    try {
      const out = await withTimeout(
        withWindow(() => client.calendarCaptchaSubmit(caseId, answer.trim())),
        60000, 'enter the answer')
      if (out.still_challenged) {
        setCapNote(out.note || 'That did not match — here is a fresh picture.')
        setAnswer('')
        const img = await client.calendarCaptcha(caseId).catch(() => ({}))
        setCaptcha(img.image || '')
        setPhase('captcha')
        return
      }
      const grid = await withTimeout(
        withWindow(() => client.calendarMonth(caseId)), 60000, 'read the calendar')
      setMonth(grid)
      setPhase('dates')
    } catch (e) { fail(e); setPhase('captcha') }
  }

  // The applicant's own pick, carried to the official site.
  async function pickDay(d) {
    setPhase('booking'); setError(null)
    try {
      const known = (month?.days || []).map((x) => x.href)
      const out = await withWindow(() => client.calendarPick(caseId, d.href, known))
      setPicked({ ...d, opened: out })
    } catch (e) { fail(e) }
    setPhase('dates')
  }

  const shown = useMemo(
    () => missions.filter((m) => missionMatches(m, search)), [missions, search])
  // The China posts, by CODE (their names on the site are German: Peking,
  // Kanton, Hongkong), in the order an applicant is most likely to want.
  const CHINA_CODES = ['peki', 'shan', 'kant', 'cheng', 'shen', 'hong']
  const quick = useMemo(
    () => CHINA_CODES.map((c) => missions.find((m) => m.code === c)).filter(Boolean),
    [missions])
  const grouped = useMemo(() => monthsOf(month?.days || []), [month])

  const working = ['starting', 'opening', 'reading', 'booking'].includes(phase)
  const submitting = phase === 'submitting'
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
        </div>
      </div>

      {/* Error box hidden by owner decision (theming): the flow recovers
          silently and never shows a red banner on this surface. */}
      {false && error && <ErrorNote error={error} />}

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
      {(phase === 'captcha' || submitting) && (
        <Card className="anim-rise-1" style={{ marginTop: 16, textAlign: 'center' }}
              data-testid="schengen-captcha">
          <div style={{ fontWeight: 800, fontSize: 15, color: NAVY }}>
            Type the characters you see
          </div>
          <div style={{ display: 'flex', gap: 18, flexDirection: 'column',
                        alignItems: 'center' }}>
            {captcha ? (
              // The challenge is a small bitmap; scale it up hard with
              // smoothing off so the characters stay sharp instead of blurring.
              <img src={captcha} alt="Challenge image"
                   data-testid="schengen-captcha-img"
                   style={{ height: 300, width: '100%', maxWidth: 560,
                            objectFit: 'contain', imageRendering: 'pixelated',
                            borderRadius: 14, border: '1px solid #dbe3ec',
                            background: '#fff', padding: 14,
                            display: 'block' }} />
            ) : (
              // Placeholder kept sized so the row does not jump when the
              // picture lands; the loading words are hidden by owner choice.
              <div style={{ height: 300, width: '100%', maxWidth: 560 }} aria-hidden="true" />
            )}
            <div style={{ width: '100%', maxWidth: 560 }}>
              <input className="input" value={answer} autoFocus
                     readOnly={submitting}
                     style={{ width: '100%', fontSize: 26, letterSpacing: 6,
                              fontWeight: 800, textAlign: 'center',
                              padding: '14px 12px' }}
                     data-testid="schengen-captcha-input"
                     onChange={(e) => setAnswer(e.target.value)}
                     onKeyDown={(e) => { if (e.key === 'Enter') answerCaptcha() }} />
              <button className="trip-cta trip-cta--sm"
                      style={{ marginTop: 14, display: 'block',
                               marginLeft: 'auto', marginRight: 'auto' }}
                      disabled={!answer.trim() || submitting} onClick={answerCaptcha}
                      data-testid="schengen-captcha-submit">
                {submitting ? 'Entering it below…' : 'Complete Captcha'}
              </button>
              {submitting && (
                <div style={{ fontSize: 12.5, color: GRAY, marginTop: 10 }}
                     data-testid="schengen-submitting-note">
                  Watch the window below — Ellis is typing your answer into the
                  official field and opening the calendar.
                </div>
              )}
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
                          style={{ padding: '12px 0 10px', borderRadius: 12,
                                   cursor: 'pointer', transition: 'all .15s ease',
                                   border: on ? `2px solid ${BLUE}` : '1px solid #dbe3ec',
                                   background: on ? BLUE : '#fff',
                                   color: on ? '#fff' : NAVY }}>
                          <span style={{ display: 'block', fontSize: 11,
                                         opacity: 0.7, fontWeight: 600 }}>
                            {DOW[new Date(mo.year, mo.mon, d.day).getDay()]}
                          </span>
                          <span style={{ display: 'block', fontSize: 18,
                                         fontWeight: 800, lineHeight: 1.25 }}>
                            {d.day}
                          </span>
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
      {/* The live window is a secondary aid — the challenge is already shown
          large in Ellis above. Show the frame ONLY while it is actually
          embedded; a timed-out Browserbase session must never leave a
          dead-end 'reconnect' card on this surface. When it lapses, Ellis
          quietly opens a fresh one in the background (reconnectRef). */}
      {caseId && (phase === 'captcha' || submitting) && view.state === 'embedded' && (
        <div style={{ marginTop: 18 }}>
          <LiveFrame view={view} height="40vh" watchOnly
                     client={client} caseId={caseId} />
        </div>
      )}

    </div>
  )
}
