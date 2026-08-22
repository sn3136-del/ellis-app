// Book your appointment — the REAL agent-channel booking desk, wearing the
// look built for the Trip.com demo (2026-08-18).
//
// Every state on this surface is the backend's own (app/appt_booking*.py):
//   * the request is a real AppointmentBookingRequest on the case;
//   * every offered slot was READ from the official calendar in an authorized
//     operator session (by Ellis's browser agent or a named person) and is
//     stamped with who read it and when — never generated, never guessed;
//   * "Booked" renders only when the server granted it, which structurally
//     requires the evidence pair (confirmation number + confirmation
//     document on the case).
// What IS computed locally: which official centre is nearest the applicant's
// own city (real great-circle math over verified centres, with Kimi K3
// annotating the pick). Centre choice is the applicant's; dates are not.
import { useEffect, useMemo, useRef, useState } from 'react'
import { useLocale } from '../../lib/locale.jsx'
import { Loading, ErrorNote } from '../ui.jsx'
import { bookingView } from '../../lib/visaBackend.js'
import {
  nearestCentres, resolveAddress, routeCatalogue, centreForPost,
  prettyDate, daysAway, splitWhen
} from '../../lib/apptCentres.js'
import { searchCities } from '../../lib/worldCities.js'

const NAVY = 'var(--trip-navy, #0f294d)'
const GRAY = 'var(--trip-gray, #64748b)'
const BLUE = 'var(--trip-blue, #287dfa)'

// Destination -> booking route. The 29 Schengen states share one route; the
// member state named is the one whose appointment is being booked.
const DEST_BY_ISO3 = {
  USA: { route: 'us_b1b2', name: 'United States', flag: '🇺🇸' },
  AUT: { route: 'schengen', name: 'Austria', flag: '🇦🇹' },
  BEL: { route: 'schengen', name: 'Belgium', flag: '🇧🇪' },
  BGR: { route: 'schengen', name: 'Bulgaria', flag: '🇧🇬' },
  HRV: { route: 'schengen', name: 'Croatia', flag: '🇭🇷' },
  CZE: { route: 'schengen', name: 'Czechia', flag: '🇨🇿' },
  DNK: { route: 'schengen', name: 'Denmark', flag: '🇩🇰' },
  EST: { route: 'schengen', name: 'Estonia', flag: '🇪🇪' },
  FIN: { route: 'schengen', name: 'Finland', flag: '🇫🇮' },
  FRA: { route: 'schengen', name: 'France', flag: '🇫🇷' },
  DEU: { route: 'schengen', name: 'Germany', flag: '🇩🇪' },
  GRC: { route: 'schengen', name: 'Greece', flag: '🇬🇷' },
  HUN: { route: 'schengen', name: 'Hungary', flag: '🇭🇺' },
  ISL: { route: 'schengen', name: 'Iceland', flag: '🇮🇸' },
  ITA: { route: 'schengen', name: 'Italy', flag: '🇮🇹' },
  LVA: { route: 'schengen', name: 'Latvia', flag: '🇱🇻' },
  LIE: { route: 'schengen', name: 'Liechtenstein', flag: '🇱🇮' },
  LTU: { route: 'schengen', name: 'Lithuania', flag: '🇱🇹' },
  LUX: { route: 'schengen', name: 'Luxembourg', flag: '🇱🇺' },
  MLT: { route: 'schengen', name: 'Malta', flag: '🇲🇹' },
  NLD: { route: 'schengen', name: 'Netherlands', flag: '🇳🇱' },
  NOR: { route: 'schengen', name: 'Norway', flag: '🇳🇴' },
  POL: { route: 'schengen', name: 'Poland', flag: '🇵🇱' },
  PRT: { route: 'schengen', name: 'Portugal', flag: '🇵🇹' },
  ROU: { route: 'schengen', name: 'Romania', flag: '🇷🇴' },
  SVK: { route: 'schengen', name: 'Slovakia', flag: '🇸🇰' },
  SVN: { route: 'schengen', name: 'Slovenia', flag: '🇸🇮' },
  ESP: { route: 'schengen', name: 'Spain', flag: '🇪🇸' },
  SWE: { route: 'schengen', name: 'Sweden', flag: '🇸🇪' },
  CHE: { route: 'schengen', name: 'Switzerland', flag: '🇨🇭' },
}

/** Booking route metadata for a destination ISO3 — null when no agent-channel
 *  booking route exists for it (the surface then simply does not render). */
export function bookingRouteForDestination(iso3) {
  const d = DEST_BY_ISO3[String(iso3 || '').toUpperCase()]
  return d ? { ...d } : null
}

// Present a Schengen centre under the SELECTED member state's mission (the
// same real VAC buildings host many member states' missions; the state named
// is the one whose appointment is being booked).
function labelForState(centre, destName) {
  if (!destName || !centre.routes.includes('schengen')) return centre
  if (centre.state === destName) return centre
  return { ...centre, name: `${destName} Visa Application Centre — ${centre.city}` }
}

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

function Card({ children, style = {}, className = '', ...rest }) {
  return (
    <div className={`card ${className}`}
         style={{ padding: 22, borderRadius: 20, ...style }} {...rest}>
      {children}
    </div>
  )
}

// A known centre on a real map (keyless Google embed pinned to the centre's
// coordinates), with the address as caption. Unknown location -> no map,
// never a guessed pin.
function CentreMap({ centre }) {
  if (!centre || centre.lat == null) return null
  return (
    <div style={{ marginTop: 14 }} data-testid="booka-map">
      <iframe
        title={`Map — ${centre.name}`}
        src={`https://maps.google.com/maps?q=${centre.lat},${centre.lon}&z=15&output=embed&hl=en`}
        style={{ width: '100%', height: 240, border: 'none', borderRadius: 14 }}
        loading="lazy" referrerPolicy="no-referrer-when-downgrade" />
      <div style={{ fontSize: 12, color: GRAY, marginTop: 6 }}>
        📍 {centre.name} · {centre.address}
      </div>
    </div>
  )
}

function CentreRow({ t, centre, selected, recommended, onSelect }) {
  const lit = selected || recommended
  return (
    <button onClick={onSelect} data-testid={`booka-centre-${centre.id}`}
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
        <span style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ fontWeight: 700, fontSize: 14, color: NAVY }}>
            {centre.name}
          </span>
          {recommended && <Chip tone="ok">{t('booka.nearest')}</Chip>}
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
          <span style={{ fontSize: 11, color: GRAY }}>{t('booka.kmAway')}</span>
        </span>
      )}
    </button>
  )
}

// The REAL offered slots, grouped by day when the recorded `when` starts with
// an ISO date. Whatever a person recorded renders verbatim — no reformatting
// of their words into claims they did not make.
// The applicant builds a shortlist: click to add, click again to remove.
// The number on a chosen slot is its rank — first choice, second choice, and
// so on. Ellis books the highest one still available and nothing else, so
// every slot Ellis can book is one the applicant put there themselves.
function OfferedSlots({ t, slots, shortlist, onToggle, busy, max }) {
  const rankOf = (i) => {
    const at = shortlist.findIndex((s) => s.index === i)
    return at < 0 ? 0 : at + 1
  }
  const groups = useMemo(() => {
    const byDate = new Map()
    slots.forEach((s, index) => {
      const { date, time } = splitWhen(s.when)
      const key = date || `__raw${index}`
      if (!byDate.has(key)) byDate.set(key, { date, items: [] })
      byDate.get(key).items.push({ ...s, index, time })
    })
    return [...byDate.values()]
  }, [slots])
  return (
    <div data-testid="booka-slots" style={{ marginTop: 4 }}>
      {groups.map((g, gi) => {
        const away = g.date ? daysAway(g.date) : null
        return (
          <div key={g.date || gi} style={{ padding: '14px 0', borderTop: '1px solid #eef2f7' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10,
                          flexWrap: 'wrap', marginBottom: 9 }}>
              <span style={{ fontWeight: 700, fontSize: 14, color: NAVY }}>
                {g.date ? prettyDate(g.date) : g.items[0].when}
              </span>
              {away != null && away >= 0 && (
                <Chip tone={away <= 21 ? 'ok' : ''}>{t('booka.inDays', { days: away })}</Chip>
              )}
            </div>
            <div style={{ display: 'flex', gap: 9, flexWrap: 'wrap' }}>
              {g.items.map((s) => {
                const rank = rankOf(s.index)
                const on = rank > 0
                const full = !on && shortlist.length >= max
                const label = g.date ? (s.time || s.label || g.date) : (s.label || s.when)
                return (
                  <button key={s.index} data-testid={`booka-slot-${s.index}`}
                    disabled={busy || full} onClick={() => onToggle(s)}
                    title={s.source === 'ellis_agent'
                      ? t('booka.readByAgent', { at: s.recordedAt })
                      : t('booka.seenBy', { who: s.recordedBy, at: s.recordedAt })}
                    style={{ padding: '9px 16px', borderRadius: 12, fontSize: 13.5,
                             fontWeight: 700,
                             cursor: full ? 'not-allowed' : 'pointer',
                             transition: 'all .16s ease',
                             display: 'inline-flex', alignItems: 'center', gap: 7,
                             opacity: full ? 0.45 : 1,
                             border: on ? `2px solid ${BLUE}` : '1px solid #dbe3ec',
                             background: on ? BLUE : '#fff',
                             color: on ? '#fff' : NAVY }}>
                    {on && (
                      <span data-testid={`booka-rank-${s.index}`}
                            style={{ width: 18, height: 18, borderRadius: 999,
                                     background: '#fff', color: BLUE,
                                     fontSize: 11, fontWeight: 800,
                                     display: 'inline-flex', alignItems: 'center',
                                     justifyContent: 'center', flex: 'none' }}>
                        {rank}
                      </span>
                    )}
                    {label}
                  </button>
                )
              })}
            </div>
            {/* Which post these openings belong to, when it differs per slot. */}
            <div style={{ fontSize: 11.5, color: GRAY, marginTop: 7 }}>
              {[...new Set(g.items.map((s) => s.post))].join(' · ')}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// Small honest footer: the legal ground the desk stands on, the server's own
// words. Rendered wherever a request exists.
function LegalFooter({ view }) {
  if (!view || !view.exists) return null
  return (
    <div style={{ marginTop: 14 }}>
      {view.legalBasis.basis && (
        <div style={{ fontSize: 11, color: GRAY }} data-testid="booka-basis">
          {view.legalBasis.basis}{view.legalBasis.limit ? ` ${view.legalBasis.limit}` : ''}
        </div>
      )}
    </div>
  )
}

/**
 * The applicant's whole booking journey on one surface, for a case whose
 * destination has an agent-channel route:
 *   find your centre (city autocomplete + real distances + Kimi's note)
 *   -> ask Ellis to book there (creates the real request)
 *   -> the official calendar is read in an authorized session (poll)
 *   -> pick your date (real recorded slots)
 *   -> booked, behind evidence (confirmation number + document).
 */
export default function BookAppointment({ client, caseId, destination = 'USA',
                                          applicantName = '', title = true }) {
  const { t } = useLocale()
  const dest = bookingRouteForDestination(destination) || DEST_BY_ISO3.USA
  const route = dest.route                       // us_b1b2 | schengen
  const centreRoute = route === 'schengen' ? 'schengen' : 'us'
  const destName = route === 'schengen' ? dest.name : ''

  // ---- the real request (poll while active) -------------------------------
  const [view, setView] = useState(null)   // null = first fetch unresolved
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  // The shortlist being built, in the applicant's own click order, and the
  // server's own cap on how many preferences it will accept.
  const [shortlist, setShortlist] = useState([])
  const maxRanked = view?.maxRanked || 5
  const timer = useRef(null)
  const caseRef = useRef(caseId)

  async function refresh() {
    const forCase = caseId
    try {
      const payload = await client.bookingForCase(forCase)
      if (caseRef.current !== forCase) return
      setView(bookingView(payload))
      setError(null)
    } catch (e) {
      if (caseRef.current !== forCase) return
      setError({ message: e.message })
    }
  }
  useEffect(() => { caseRef.current = caseId; setView(null); refresh() }, [caseId])
  // A fresh offer wipes the shortlist. The server does the same to any
  // ranking already stored, so a choice made against dates that no longer
  // exist can never ride along into the new offer.
  const offerSignature = (view?.offeredSlots || [])
    .map((s) => `${s.post}@${s.when}`).join('|')
  useEffect(() => { setShortlist([]) }, [offerSignature])

  function toggleSlot(s) {
    setShortlist((cur) => {
      const at = cur.findIndex((x) => x.index === s.index)
      if (at >= 0) return cur.filter((x) => x.index !== s.index)
      if (cur.length >= maxRanked) return cur
      return [...cur, s]
    })
  }

  useEffect(() => {
    clearInterval(timer.current)
    if (view?.active) timer.current = setInterval(refresh, 4000)
    return () => clearInterval(timer.current)
  }, [view?.active, view?.status])

  async function act(fn) {
    setBusy(true); setError(null)
    try { setView(bookingView(await fn())) } catch (e) { setError({ message: e.message }) }
    setBusy(false)
  }

  // ---- centre finding (local math + Kimi's note) --------------------------
  const [address, setAddress] = useState('')
  const [origin, setOrigin] = useState(null)
  const [locating, setLocating] = useState(false)
  const [aiPick, setAiPick] = useState(null)
  const [cityOpen, setCityOpen] = useState(false)
  const [centre, setCentre] = useState(null)     // the applicant's chosen centre
  const [ds160, setDs160] = useState('')
  const citySuggestions = useMemo(() => searchCities(address, 8), [address])
  const lastSearchRef = useRef('')

  const centres = useMemo(() => {
    if (!origin) return []
    const relabel = (list) => {
      const seen = new Set()
      return list.map((c) => labelForState(c, destName)).filter((c) => {
        if (seen.has(c.name)) return false
        seen.add(c.name)
        return true
      })
    }
    const base = nearestCentres(centreRoute, origin, 3)
    if (!origin.approx || !aiPick || !aiPick.centre_id) return relabel(base)
    if (base.some((c) => c.id === aiPick.centre_id)) {
      return relabel([...base].sort((a, b) =>
        (a.id === aiPick.centre_id ? -1 : 0) - (b.id === aiPick.centre_id ? -1 : 0)))
    }
    const picked = routeCatalogue(centreRoute).find((c) => c.id === aiPick.centre_id)
    return relabel(picked ? [{ ...picked, km: null }, ...base.slice(0, 2)] : base)
  }, [centreRoute, origin, aiPick, destName])
  // "Nearest to you" is a claim: real coordinates (math) or Kimi's explicit
  // placement. An unplaced city gets neither badge nor "closest" wording.
  const placed = !!origin && (!origin.approx || !!(aiPick && aiPick.centre_id))

  function findCentres(originOverride) {
    const ov = originOverride && typeof originOverride.lat === 'number'
      ? originOverride : null
    const o = ov || resolveAddress(address)
    if (!o) return
    setOrigin(o)
    setCentre(null)
    setAiPick(null)
    setLocating(true)
    // Kimi K3 reads the address and names the nearest official centre. For a
    // city the local table knows, it gets the 3 candidates (fast confirm);
    // for anything else it gets the WHOLE route catalogue and does the real
    // locating. Any failure leaves aiPick null and the local list stands.
    const pool = o.approx ? routeCatalogue(centreRoute)
      : nearestCentres(centreRoute, o, 3)
    let done = false
    const finish = () => { if (!done) { done = true; setLocating(false) } }
    const cap = setTimeout(finish, 10000)
    client.bookingNearestCentre({
      address,
      centres: pool.map((c) => ({ id: c.id, name: c.name, city: c.city,
                                  address: c.address }))
    })
      .then((d) => { if (d && d.available) setAiPick(d) })
      .catch(() => {})
      .finally(() => { clearTimeout(cap); finish() })
  }

  function pickCity(c) {
    setAddress(`${c.name}, ${c.country}`)
    setCityOpen(false)
    lastSearchRef.current = `${c.name}, ${c.country}`
    findCentres({ lat: c.lat, lon: c.lon, label: c.name })
  }

  // The search starts ITSELF: a moment after typing stops, a recognized city
  // (or a long-enough free-text address) runs — no button.
  useEffect(() => {
    const text = address.trim()
    if (!text || text === lastSearchRef.current) return
    const id = setTimeout(() => {
      const o = resolveAddress(text)
      if (!o) return
      if (!o.approx || text.length >= 8) {
        lastSearchRef.current = text
        findCentres()
      }
    }, 900)
    return () => clearTimeout(id)
  }, [address])

  async function requestBooking() {
    if (!centre) return
    // The DS-160 confirmation number travels in the request note — the
    // scheduling profile asks for it, so the desk should have it on file.
    const note = route === 'us_b1b2' && ds160.trim()
      ? `DS-160 confirmation: ${ds160.trim()}` : ''
    await act(() => client.bookingCreate(caseId, {
      route, posts: [centre.name.slice(0, 80)], date_windows: [], note }))
  }

  // ---- render -------------------------------------------------------------
  const status = view?.status
  const showFind = !!view && (!view.exists ||
    (!view.active && status !== 'booked'))
  // A finished (failed / cancelled) request stays visible above the fresh
  // start: the history is honest, not swept away.
  const ended = view && view.exists &&
    (status === 'failed' || status === 'cancelled')

  const bookedCentre = view?.pickedSlot
    ? centreForPost(centreRoute, view.pickedSlot.post) : null

  return (
    <div data-testid="book-appointment" style={{ marginTop: 8 }}>
      {title && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, margin: '10px 0 4px' }}>
          <div>
            <div style={{ fontSize: 20, fontWeight: 800, color: NAVY,
                          letterSpacing: -0.3, textAlign: 'left' }}>
              {t('booka.title')}
            </div>
            <div style={{ fontSize: 13, color: GRAY, marginTop: 3, textAlign: 'left' }}>
              {t('booka.sub')}
            </div>
          </div>
        </div>
      )}
      {error && <ErrorNote error={error} />}
      {view === null && !error && (
        <div style={{ fontSize: 12.5, color: GRAY, marginTop: 8 }}
             data-testid="booka-loading">{t('cockpit.loading')}</div>
      )}
      {view === null && error && (
        <button className="btn btn--sm btn--ghost" style={{ marginTop: 8 }}
                disabled={busy} onClick={refresh} data-testid="booka-retry">
          {t('common.retry')}
        </button>
      )}

      {ended && (
        <Card className="anim-rise" style={{ marginTop: 12, padding: 16 }}
              data-testid="booka-ended">
          <div style={{ fontSize: 13, color: NAVY, fontWeight: 700 }}>
            {t(`appt.booking.status.${status}`)}
          </div>
          {view.failureReason && (
            <div style={{ fontSize: 12.5, color: GRAY, marginTop: 5 }}>
              {view.failureReason}
            </div>
          )}
        </Card>
      )}

      {/* ---- Find your centre + ask Ellis to book --------------------------- */}
      {showFind && (
        <>
          <Card className="anim-rise" style={{ marginTop: 12 }} data-testid="booka-find">
            <div style={{ display: 'flex', justifyContent: 'space-between',
                          alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <div style={{ fontSize: 12, fontWeight: 800, letterSpacing: 0.6,
                            color: GRAY, textTransform: 'uppercase' }}>
                {t('booka.destination')}
              </div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8 }}>
              <span style={{ fontSize: 18 }}>{dest.flag}</span>
              <span style={{ fontWeight: 700, fontSize: 14.5, color: NAVY }}>
                {dest.name}
              </span>
            </div>
            <div style={{ marginTop: 16 }}>
              <label style={{ fontSize: 12.5, fontWeight: 650, color: NAVY }}>
                {t('booka.yourCity')}
              </label>
              <div style={{ display: 'flex', gap: 10, marginTop: 7, flexWrap: 'wrap',
                            position: 'relative' }}>
                <div style={{ flex: '1 1 320px', position: 'relative' }}>
                  <input className="input" style={{ width: '100%' }} value={address}
                         data-testid="booka-city"
                         placeholder={t('booka.cityPlaceholder')}
                         onChange={(e) => { setAddress(e.target.value); setCityOpen(true) }}
                         onFocus={() => setCityOpen(true)}
                         onBlur={() => setTimeout(() => setCityOpen(false), 150)}
                         onKeyDown={(e) => {
                           if (e.key === 'Enter') {
                             const top = citySuggestions[0]
                             if (top) pickCity(top)
                             else if (address.trim().length >= 3) findCentres()
                           }
                         }} />
                  {cityOpen && citySuggestions.length > 0 && (
                    <div style={{ position: 'absolute', top: '100%', left: 0, right: 0,
                                  zIndex: 30, background: '#fff', borderRadius: 12,
                                  border: '1px solid #e2e8f0', marginTop: 4,
                                  boxShadow: '0 12px 32px rgba(15,41,77,.10)',
                                  overflow: 'hidden' }}
                         data-testid="booka-city-list">
                      {citySuggestions.map((c) => (
                        <button key={`${c.name}-${c.country}`}
                          onMouseDown={(e) => { e.preventDefault(); pickCity(c) }}
                          style={{ display: 'flex', width: '100%', gap: 8,
                                   justifyContent: 'space-between', padding: '10px 14px',
                                   cursor: 'pointer', border: 'none', background: '#fff',
                                   textAlign: 'left', fontSize: 13.5 }}>
                          <span style={{ color: NAVY, fontWeight: 650 }}>{c.name}</span>
                          <span style={{ color: GRAY, fontSize: 12.5 }}>{c.country}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>
          </Card>

          {locating && (
            <Card className="anim-rise-1" style={{ marginTop: 12 }} data-testid="booka-locating">
              <div style={{ display: 'flex', justifyContent: 'center', padding: '14px 0 6px' }}>
                <div style={{ transform: 'scale(1.25)', transformOrigin: 'center top' }}>
                  <Loading size="big" label={t('booka.locating')} />
                </div>
              </div>
            </Card>
          )}

          {origin && !locating && (
            <Card className="anim-rise-1" style={{ marginTop: 12 }} data-testid="booka-centres">
              <div style={{ fontSize: 12, fontWeight: 800, letterSpacing: 0.6,
                            color: GRAY, textTransform: 'uppercase' }}>
                {placed ? t('booka.closestTo', { place: origin.label || address })
                        : t('booka.suggested')}
              </div>
              {aiPick && aiPick.note && (
                <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start',
                              marginTop: 12, padding: '12px 14px', borderRadius: 14,
                              background: '#f5f9ff', border: '1px solid #dbe7fb' }}
                     data-testid="booka-ai-note">
                  <span style={{ fontSize: 15, lineHeight: 1.2 }}>✨</span>
                  <span style={{ fontSize: 13, color: NAVY, lineHeight: 1.55 }}>
                    {aiPick.note}
                  </span>
                </div>
              )}
              {centres.map((c, i) => (
                <CentreRow key={c.id} t={t} centre={c}
                           selected={centre && centre.id === c.id}
                           recommended={placed && (origin.approx
                             ? (aiPick && aiPick.centre_id === c.id)
                             : i === 0)}
                           onSelect={() => setCentre(c)} />
              ))}
              {centre && (
                <div className="anim-rise" style={{ marginTop: 6 }}>
                  <CentreMap centre={centre} />
                  {route === 'us_b1b2' && (
                    <div style={{ marginTop: 14 }}>
                      <label style={{ fontSize: 12.5, fontWeight: 650, color: NAVY }}>
                        {t('booka.ds160Label')}
                      </label>
                      <input className="input" style={{ width: '100%', marginTop: 6 }}
                             value={ds160} data-testid="booka-ds160"
                             placeholder="AA00XXXXXX"
                             onChange={(e) => setDs160(e.target.value)} />
                      <div style={{ fontSize: 11.5, color: GRAY, marginTop: 5 }}>
                        {t('booka.ds160Hint')}
                      </div>
                    </div>
                  )}
                  <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
                    <button className="trip-cta trip-cta--sm" disabled={busy}
                            onClick={requestBooking} data-testid="booka-request">
                      {busy ? t('booka.requesting') : t('booka.requestCta')}
                    </button>
                  </div>
                </div>
              )}
            </Card>
          )}
        </>
      )}

      {/* ---- Requested: the calendar is being read -------------------------- */}
      {view && view.exists && status === 'requested' && (
        <Card className="anim-rise" style={{ marginTop: 12 }} data-testid="booka-requested">
          <div style={{ display: 'flex', justifyContent: 'center', padding: '10px 0 0' }}>
            <Loading size="big" label={t('booka.requestedTitle')} />
          </div>
          <div style={{ fontSize: 12.5, color: GRAY, textAlign: 'center',
                        maxWidth: 420, margin: '10px auto 0' }}>
            {t('booka.requestedSub')}
          </div>
          <div style={{ fontSize: 12.5, color: GRAY, textAlign: 'center', marginTop: 8 }}>
            {view.posts.join(' · ')}
          </div>
          <div style={{ display: 'flex', justifyContent: 'center', marginTop: 14 }}>
            <button className="btn btn--sm btn--ghost" disabled={busy}
                    onClick={() => act(() => client.bookingCancel(view.id))}
                    data-testid="booka-cancel">
              {t('booka.cancel')}
            </button>
          </div>
          <LegalFooter view={view} />
        </Card>
      )}

      {/* ---- Pick your date (REAL recorded slots) --------------------------- */}
      {view && view.exists && status === 'slots_offered' && (
        <Card className="anim-rise" style={{ marginTop: 12 }} data-testid="booka-pick">
          <div style={{ display: 'flex', justifyContent: 'space-between',
                        alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontWeight: 800, fontSize: 15, color: NAVY }}>
                {t('booka.slotsTitle')}
              </div>
              <div style={{ fontSize: 12.5, color: GRAY, marginTop: 4 }}>
                {view.slotsNotice}
              </div>
            </div>
            {view.agentRead && <Chip tone="info">{t('booka.agentRead')}</Chip>}
          </div>
          <OfferedSlots t={t} slots={view.offeredSlots} shortlist={shortlist}
                        busy={busy} max={maxRanked} onToggle={toggleSlot} />

          {/* The shortlist, in the applicant's own order, and what Ellis will
              do with it — stated before they commit, not after. */}
          <div style={{ marginTop: 14, padding: '14px 16px', borderRadius: 14,
                        background: '#f6f9ff' }}
               data-testid="booka-shortlist">
            <div style={{ display: 'flex', alignItems: 'center', gap: 10,
                          flexWrap: 'wrap' }}>
              <span style={{ fontSize: 13.5, fontWeight: 800, color: NAVY }}>
                {t('booka.rankTitle', { n: shortlist.length, max: maxRanked })}
              </span>
              {shortlist.length > 0 && (
                <button className="btn btn--sm btn--ghost" disabled={busy}
                        data-testid="booka-rank-clear"
                        onClick={() => setShortlist([])}>
                  {t('booka.rankClear')}
                </button>
              )}
            </div>
            {shortlist.length === 0 ? (
              <div style={{ fontSize: 12.5, color: GRAY, marginTop: 6 }}>
                {t('booka.rankEmpty', { max: maxRanked })}
              </div>
            ) : (
              <ol style={{ margin: '10px 0 0', paddingLeft: 20, fontSize: 13,
                           color: NAVY }}>
                {shortlist.map((s) => {
                  const { date, time } = splitWhen(s.when)
                  return (
                    <li key={s.index} style={{ marginBottom: 3 }}>
                      {date ? `${prettyDate(date)}${time ? ' · ' + time : ''}`
                            : (s.label || s.when)}
                      <span style={{ color: GRAY }}>{' · ' + s.post}</span>
                    </li>
                  )
                })}
              </ol>
            )}
            <div style={{ fontSize: 12, color: GRAY, marginTop: 10 }}>
              {view.rankingNotice}
            </div>
            <div style={{ marginTop: 12, textAlign: 'right' }}>
              <button className="btn btn--primary" disabled={busy || !shortlist.length}
                      data-testid="booka-rank-confirm"
                      onClick={() => act(() =>
                        client.bookingRank(view.id, shortlist))}>
                {t('booka.rankConfirm', { n: shortlist.length })}
              </button>
            </div>
          </div>

          <div style={{ display: 'flex', justifyContent: 'space-between',
                        alignItems: 'center', gap: 12, marginTop: 14, flexWrap: 'wrap' }}>
            <div style={{ fontSize: 12, color: GRAY, maxWidth: 380 }}>
              {t('booka.loseWarning')}
            </div>
            <button className="btn btn--sm btn--ghost" disabled={busy}
                    onClick={() => act(() => client.bookingCancel(view.id))}
                    data-testid="booka-cancel">
              {t('booka.cancel')}
            </button>
          </div>
          <LegalFooter view={view} />
        </Card>
      )}

      {/* ---- Picked: Ellis is booking it ------------------------------------ */}
      {view && view.exists && status === 'slot_picked' && (
        <Card className="anim-rise" style={{ marginTop: 12 }} data-testid="booka-booking">
          <div style={{ display: 'flex', justifyContent: 'center', padding: '10px 0 0' }}>
            <Loading size="big" label={t('booka.bookingTitle')} />
          </div>
          {view.pickedSlot && (
            <div style={{ marginTop: 12, padding: '16px 18px', borderRadius: 16,
                          background: '#f5f9ff', border: '1px solid #dbe7fb' }}>
              <div style={{ fontSize: 18, fontWeight: 800, color: NAVY }}>
                {view.pickedSlot.when}
              </div>
              <div style={{ fontSize: 13, color: GRAY, marginTop: 4 }}>
                {view.pickedSlot.post}
              </div>
              {/* The fallbacks they authorised, so it is visible what else
                  Ellis is allowed to book if this one is gone. */}
              {view.rankedSlots.length > 1 && (
                <div style={{ marginTop: 12, paddingTop: 12,
                              borderTop: '1px solid #dbe7fb' }}
                     data-testid="booka-ranked-confirmed">
                  <div style={{ fontSize: 12, fontWeight: 800, color: GRAY,
                                letterSpacing: 0.5, textTransform: 'uppercase' }}>
                    {t('booka.rankFallbacks')}
                  </div>
                  <ol style={{ margin: '6px 0 0', paddingLeft: 20, fontSize: 12.5,
                               color: NAVY }} start={2}>
                    {view.rankedSlots.slice(1).map((r, i) => (
                      <li key={i} style={{ marginBottom: 2 }}>
                        {r.label || r.when}
                        <span style={{ color: GRAY }}>{' · ' + r.post}</span>
                      </li>
                    ))}
                  </ol>
                </div>
              )}
            </div>
          )}
          <div style={{ fontSize: 12.5, color: GRAY, textAlign: 'center',
                        maxWidth: 420, margin: '12px auto 0' }}>
            {t('booka.bookingSub')}
          </div>
          <div style={{ display: 'flex', justifyContent: 'center', marginTop: 14 }}>
            <button className="btn btn--sm btn--ghost" disabled={busy}
                    onClick={() => act(() => client.bookingCancel(view.id))}
                    data-testid="booka-cancel">
              {t('booka.cancel')}
            </button>
          </div>
          <LegalFooter view={view} />
        </Card>
      )}

      {/* ---- Booked, behind evidence ---------------------------------------- */}
      {view && view.exists && status === 'booked' && view.confirmation && (
        <Card className="anim-rise"
              style={{ marginTop: 12, border: '2px solid #b8e6c9',
                       background: 'linear-gradient(180deg,#f4fbf6,#fff)' }}
              data-testid="booka-booked">
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ width: 40, height: 40, borderRadius: 999, background: '#e8f7ee',
                           display: 'grid', placeItems: 'center', fontSize: 20 }}>✓</span>
            <div>
              <div style={{ fontWeight: 800, fontSize: 18, color: NAVY }}>
                {t('booka.bookedTitle')}
              </div>
              <div style={{ fontSize: 13, color: GRAY, marginTop: 3 }}>
                {t('booka.confirmation', { number: view.confirmation.number })}
              </div>
            </div>
          </div>
          <div style={{ display: 'grid', gap: 10, marginTop: 18 }}>
            {[...(applicantName ? [[t('booka.applicant'), applicantName]] : []),
              [t('booka.when'), view.pickedSlot?.when || ''],
              [t('booka.where'), view.pickedSlot?.post || ''],
              ...(bookedCentre ? [[t('booka.address'), bookedCentre.address]] : [])
            ].filter(([, v]) => v).map(([k, v]) => (
              <div key={k} style={{ display: 'flex', gap: 14, fontSize: 13.5 }}>
                <span style={{ width: 92, flexShrink: 0, color: GRAY }}>{k}</span>
                <span style={{ color: NAVY, fontWeight: 600 }}>{v}</span>
              </div>
            ))}
          </div>
          <CentreMap centre={bookedCentre} />
          <div style={{ fontSize: 11.5, color: GRAY, marginTop: 12 }}
               data-testid="booka-recorded-by">
            {t('booka.recordedBy', { who: view.confirmation.recordedBy,
                                     at: view.confirmation.recordedAt })}
          </div>
          <LegalFooter view={view} />
        </Card>
      )}
    </div>
  )
}
