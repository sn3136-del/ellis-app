// The Database: a traveldoc-style requirements lookup, answered by the same
// Kimi-primary route decision the applicant journey trusts.
//
// One form, one designed answer page carrying trip information only. Static
// strings run through t() (so the top-right language picker translates them
// like every other screen), and the decision's own text is translated on
// demand through the same masked, cached Kimi K3 catalog pipe (fast, with
// an honest English fallback when a string cannot round-trip).
import { useEffect, useMemo, useRef, useState } from 'react'
import { Loading, TripPlane } from '../components/ui.jsx'
import { useLocale } from '../lib/locale.jsx'
import { useLocalizedCountries } from '../lib/countryNames.js'
import { createVisaClient } from '../lib/visaBackend.js'
import { newSession } from '../lib/visaSession.js'

const NAVY = 'var(--trip-navy, #0f294d)'
const GRAY = 'var(--trip-gray, #64748b)'
const BLUE = 'var(--trip-blue, #287dfa)'

const PURPOSES = [
  ['tourism', 'db.purpose.tourism'], ['business', 'db.purpose.business'],
  ['family_visit', 'db.purpose.family'], ['study', 'db.purpose.study'],
  ['work', 'db.purpose.work'], ['transit', 'db.purpose.transit'],
  ['other', 'db.purpose.other'],
]

// The engine's own vocabulary, in a traveller's words, each with a colour.
const DISPOSITION_VIEW = {
  VISA_EXEMPT: { key: 'db.verdict.exempt', color: '#0f8a3d', tint: '#eefaf1' },
  ELECTRONIC_AUTHORIZATION_REQUIRED:
    { key: 'db.verdict.eta', color: '#9a6200', tint: '#fff7e8' },
  VISA_REQUIRED: { key: 'db.verdict.required', color: '#b3261e', tint: '#fdeeed' },
  CONDITIONAL: { key: 'db.verdict.conditional', color: '#9a6200', tint: '#fff7e8' },
}

// The requirement subcategory (Trip.com's field spec: the primary
// classification splits further). Each maps to an i18n key.
const REQUIREMENT_DETAIL_KEYS = {
  unconditional_visa_free: 'db.detail.unconditionalVisaFree',
  conditional_visa_free: 'db.detail.conditionalVisaFree',
  transit_visa_free: 'db.detail.transitVisaFree',
  evisa_on_arrival: 'db.detail.evisaOnArrival',
  paper_visa_on_arrival: 'db.detail.paperVisaOnArrival',
  evisa: 'db.detail.evisa',
  paper_visa: 'db.detail.paperVisa',
  eta_electronic_authorization: 'db.detail.eta',
}

// Enum values the engine emits become words, never raw snake_case.
const CHANNEL_WORDS = {
  visa_center: 'Visa application centre',
  // Not a visa centre: the destination refuses individual filings and a
  // designated agency must lodge for the applicant. Trip.com rejected the
  // "visa application centre" label for exactly this case.
  authorised_agent: 'Designated authorised agent',
  authorized_agent: 'Designated authorised agent',
  embassy: 'Embassy or consulate',
  consulate: 'Embassy or consulate',
  evisa_portal: 'Official online portal',
  online: 'Official online portal',
  on_arrival: 'On arrival',
  mail: 'By mail',
}
// The link label should name the site it actually opens, not a generic
// category. Derived from the URL's own host so it can never drift from where
// the link goes: "Apply on ImmiAccount", "Apply on france-visas.gouv.fr".
const SITE_NAMES = {
  'ceac.state.gov': 'CEAC (DS-160)',
  'travel.state.gov': 'travel.state.gov',
  'immi.homeaffairs.gov.au': 'Home Affairs',
  'online.immi.gov.au': 'ImmiAccount',
  'france-visas.gouv.fr': 'France-Visas',
  'gov.uk': 'GOV.UK',
  'www.gov.uk': 'GOV.UK',
  'evisa.imigrasi.go.id': 'Indonesian e-Visa',
  'www.visa.go.kr': 'Korea Visa Portal',
  'www.k-eta.go.kr': 'K-ETA',
  'evisa.kdmid.ru': 'Russian e-Visa',
  'visawebapp.boca.gov.tw': 'BOCA online form',
  'www.ica.gov.sg': 'ICA Singapore',
  'www.immd.gov.hk': 'Hong Kong ImmD',
  'www.mofa.go.jp': 'MOFA Japan',
  'evisa.gov.vn': 'Vietnam e-Visa',
  'www.migracija.lt': 'MIGRIS Lithuania',
  'evisa.mn': 'Mongolia e-Visa',
  'indianvisaonline.gov.in': 'Indian Visa Online',
  'visa2egypt.gov.eg': 'Egypt e-Visa',
  'visa.gov.bd': 'Bangladesh Visa',
  'www.thaievisa.go.th': 'Thai e-Visa',
  'visa.mofa.gov.sa': 'Saudi MOFA',
  'www.exteriores.gob.es': 'Spain MFA',
  'cs.mfa.gov.cn': 'China MFA consular',
}
function siteLabel(url) {
  if (!url) return null
  let host
  try { host = new URL(url).hostname } catch { return null }
  if (SITE_NAMES[host]) return SITE_NAMES[host]
  const bare = host.replace(/^www\./, '')
  // A government host reads best as its own name: "evisa.gov.gh".
  return bare.length <= 34 ? bare : bare.split('.').slice(-3).join('.')
}

const humanizeEnum = (v) => {
  const s = String(v || '').trim()
  if (!s) return null
  return CHANNEL_WORDS[s.toLowerCase()] ||
    (/^[a-z0-9_]+$/.test(s) ? s.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()) : s)
}

// The decision's fields arrive as strings, lists, or small objects, always
// rendered as natural words: no raw objects, no snake_case keys.
function asText(v) {
  if (v === null || v === undefined || v === '') return null
  if (typeof v === 'string') return v
  if (typeof v === 'number') return String(v)
  if (Array.isArray(v)) return v.map((x) => asText(x)).filter(Boolean).join('; ')
  if (typeof v === 'object') {
    if (v.reason) return String(v.reason)          // uncertainty rows: the reason is the sentence
    if (v.name) return [v.name, v.applicability
      ? `(${String(v.applicability).replace(/_/g, ' ')})` : '']
      .filter(Boolean).join(' ')
    return Object.values(v).map((x) => asText(x)).filter(Boolean).join(', ')
  }
  return String(v)
}

// The engine writes compact reference prose. This turns it into plain
// sentences: semicolons become full stops, dashes become commas, each
// sentence starts with a capital, and longer lines end with a full stop.
// URLs are left exactly as they are.
const humanize = (v) => {
  if (!v) return v
  let out = String(v)
  if (/https?:\/\//.test(out)) return out
  out = out
    .replace(/([A-Za-z0-9])_+([A-Za-z0-9])/g, '$1 $2')
    .replace(/\s*[\u2014\u2013]\s*/g, ', ')
    .replace(/\s*;\s*/g, '. ')
    .replace(/\s{2,}/g, ' ')
    .trim()
  out = out.replace(/(^|[.!?]\s+)([a-z])/g, (m, lead, ch) => lead + ch.toUpperCase())
  if (out.length > 28 && !/[.!?)]$/.test(out)) out += '.'
  return out
}

const sentence = (s) => humanize(s)

// Each "passport x destination" answer has its OWN address, so it can be
// linked, bookmarked and shared:  #database/CHN/JPN/tourism/ordinary_passport
// Purpose and document are part of the address because they change the answer.
function routeFromHash() {
  const raw = (typeof window !== 'undefined' && window.location.hash) || ''
  const m = raw.replace(/^#\/?/, '').split('/')
  if ((m[0] || '').toLowerCase() !== 'database') return null
  const [, nat, dest, purpose, doc] = m
  if (!/^[A-Za-z]{3}$/.test(nat || '') || !/^[A-Za-z]{3}$/.test(dest || '')) return null
  return {
    nat: nat.toUpperCase(), dest: dest.toUpperCase(),
    purpose: PURPOSES.some(([v]) => v === purpose) ? purpose : 'tourism',
    doc: /^[a-z_]+$/.test(doc || '') ? doc : 'ordinary_passport',
  }
}

function clearHash() {
  if (typeof window === 'undefined') return
  if ((window.location.hash || '').startsWith('#database/')) {
    window.history.replaceState(null, '', '#database')
  }
}

function writeHash({ nat, dest, purpose, doc }) {
  if (typeof window === 'undefined' || !nat || !dest) return
  const next = `#database/${nat}/${dest}/${purpose || 'tourism'}/${doc || 'ordinary_passport'}`
  if (window.location.hash !== next) {
    window.history.replaceState(null, '', next)
  }
}

function itemsOf(v) {
  if (!v) return []
  return (Array.isArray(v) ? v : [v]).map((x) => sentence(asText(x))).filter(Boolean)
}

function feeText(fee) {
  if (!fee || typeof fee !== 'object') return null
  const amt = fee.amount
  if (amt === 0) return 'None'
  if (!amt && amt !== 0) return null
  return `${amt} ${fee.currency || ''}`.trim()
}

// Type-ahead country picker: type to filter, click to choose.
// The landing hero: form column left, island scene right on wide screens.
// The scene folds away on phones, where every pixel belongs to the form.
const DB_CSS = `
.db-hero { display: flex; gap: 40px; align-items: center;
           justify-content: center; }
.db-hero-left { width: 100%; max-width: 560px; flex-shrink: 0; }
.db-scene { flex: 1; max-width: 470px; min-width: 280px;
            display: flex; flex-direction: column; align-items: center; }
.db-scene .planeload__sky { max-width: 100%; }
@media (max-width: 1020px) {
  /* Phones stack: the form first, the island scene as a compact closer. */
  .db-hero { display: block; }
  .db-scene { margin-top: 22px; max-width: 100%; }
}`

/** The SAME Trip.com plane the loading state flies (same SVG, same bob, same
 *  streaming dashed trail via the planeload classes). Underneath it: a flat
 *  black-and-white island drawn as a New York City skyline whose towers
 *  spell ELLIS, an Empire State spire on the I, blinking windows, and the
 *  Statue of Liberty on her own islet next door, the way Ellis Island
 *  actually sits in New York Harbor. */
function EllisIslandScene() {
  const reduced = typeof window !== 'undefined' &&
    window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  const INK = '#111'
  const BASE = 150                       // street level
  // Every letter is a cluster of blocks [x, y-up, w, h]; heights vary so
  // the roofline has a skyline's rhythm. The I is the Empire State Building.
  const LETTERS = [
    [22, [[0, 92, 13, 92], [0, 92, 40, 12], [0, 55, 32, 11], [0, 12, 40, 12]]],
    [84, [[0, 76, 13, 76], [0, 12, 36, 12]]],
    [138, [[0, 84, 13, 84], [0, 12, 36, 12]]],
    // the I is the Empire State Building: broad base, two setbacks,
    // crown tiers, then the mast and needle drawn separately
    [192, [[-2, 26, 19, 26], [0, 56, 15, 30], [2, 82, 11, 26],
           [4, 94, 7, 12], [5.5, 104, 4, 10]]],
    [228, [[0, 61, 38, 11], [0, 50, 12, 14], [0, 36, 38, 11],
           [26, 25, 12, 14], [0, 11, 38, 11]]],
  ]
  // Windows do the shaping: tower shafts wear aligned columns of tall slit
  // windows; every horizontal slab wears one continuous glass ribbon
  // tracing the letter's arm. A deterministic lit/dim mix gives the facade
  // life without noise. [x, y, w, h, dim]
  const winRects = []
  const addGrid = (bx, by, w, h) => {
    if (w <= 22 && h >= 24) {
      // one centered column on narrow shafts, two on wide ones; every row
      // snaps to ONE global floor grid so windows align across all towers
      const ww = 3.4, wh = 7.5, pitch = 16
      const cols = w >= 15 ? [w / 2 - 5, w / 2 + 1.6] : [(w - ww) / 2]
      const first = Math.ceil((by + 6) / pitch) * pitch
      for (let wy = first; wy <= by + h - wh - 5; wy += pitch) {
        cols.forEach((cx) => winRects.push([bx + cx, wy, ww, wh, 'v']))
      }
    } else if (w >= 30 && h >= 9) {
      const ww = 6.5, wh = Math.min(5.5, h - 4.5), gap = 5, mx = 4.5
      const n = Math.floor((w - 2 * mx + gap) / (ww + gap))
      const gx = n > 1 ? (w - 2 * mx - n * ww) / (n - 1) : 0
      for (let i = 0; i < n; i++) {
        winRects.push([bx + mx + i * (ww + gx), by + (h - wh) / 2, ww, wh,
                       'h'])
      }
    }
  }
  LETTERS.forEach(([lx, rects]) => {
    rects.forEach(([x, up, w, h]) => addGrid(lx + x, BASE - up, w, h))
  })
  // Long layered swells: one path each, spanning the whole harbor.
  const swell = (y, n) => 'M-12,' + y + ' q10,-3.5 20,0 ' +
    Array.from({ length: n }, () => 't20,0').join(' ')
  return (
    <div className="db-scene" aria-hidden="true">
      {/* the loading animation itself: same markup, same classes. The sky
          shares the island's exact width and overlaps it, so the plane and
          the trail fly just over the spire as one composition. */}
      <div className="planeload__sky"
           style={{ width: 'min(330px, 92vw)', height: 84 }}>
        <span className="planeload__trail" />
        <span className="planeload__plane" style={{ top: 4 }}>
          <TripPlane width={176} />
        </span>
      </div>
      <svg viewBox="0 0 300 200"
           style={{ display: 'block', width: 'min(330px, 92vw)',
                    marginTop: -20 }}>
        <g>
          {/* the island: a thin low shoreline, so the skyline stays the star */}
          <path d={`M8,${BASE + 6} C20,${BASE} 50,${BASE - 1} 80,${BASE - 1}
                    L220,${BASE - 1} C250,${BASE - 1} 280,${BASE} 292,${BASE + 6}
                    C294,${BASE + 8} 291,${BASE + 9} 286,${BASE + 9}
                    L14,${BASE + 9} C9,${BASE + 9} 6,${BASE + 8} 8,${BASE + 6} Z`}
                fill={INK} stroke={INK} strokeWidth="1.5"
                strokeLinejoin="round" />
                    {/* towers */}
          {LETTERS.map(([lx, rects], i) => (
            <g key={i}>
              {rects.map(([x, up, w, h], j) => (
                <rect key={j} x={lx + x} y={BASE - up} width={w} height={h}
                      rx="1.5" fill={INK} stroke={INK} strokeWidth="2" />
              ))}
            </g>
          ))}
                    {/* street doors on the widest ground blocks */}
          {[[42, 40], [102, 36], [156, 36], [199.5, 17], [247, 38]].map(([cx], i) => (
            <rect key={i} x={cx - 2.2} y={BASE - 7.5} width="4.4" height="7.5"
                  rx="1.2" fill="#fff" opacity="0.95" />
          ))}
          {/* the Empire State mast and needle */}
          <line x1="199.5" y1={BASE - 111} x2="199.5" y2={BASE - 132}
                stroke={INK} strokeWidth="2" strokeLinecap="round" />
          <circle cx="199.5" cy={BASE - 134} r="1.6" fill={INK} />
          {/* the facade grids */}
          {winRects.map(([x, y, w, h, kind], i) => (
            <g key={i}>
              <rect x={x} y={y} width={w} height={h} rx="0.8" fill="#fff"
                    opacity="0.95" />
              {kind === 'v' ? (
                <line x1={x} y1={y + h / 2} x2={x + w} y2={y + h / 2}
                      stroke={INK} strokeWidth="0.9" />
              ) : (
                <line x1={x + w / 2} y1={y} x2={x + w / 2} y2={y + h}
                      stroke={INK} strokeWidth="0.9" />
              )}
            </g>
          ))}
          {/* the whole harbor breathes */}
          {!reduced && (
            <animateTransform attributeName="transform" type="translate"
                              values="0 0; 0 -2.5; 0 0" dur="5.5s"
                              repeatCount="indefinite" />
          )}
        </g>
        {/* the water: long layered swells, nothing else */}
        {[[BASE + 22, 16, 0.4, 7], [BASE + 30, 16, 0.3, 9],
          [BASE + 38, 16, 0.22, 11]].map(([y, n, op, dur], i) => (
          <path key={i} d={swell(y, n)} fill="none" stroke={INK}
                strokeWidth="1.8" strokeLinecap="round" opacity={op}>
            {!reduced && (
              <animateTransform attributeName="transform" type="translate"
                                values={i % 2 ? '0 0; -10 0; 0 0' : '0 0; 10 0; 0 0'}
                                dur={`${dur}s`} repeatCount="indefinite" />
            )}
          </path>
        ))}
      </svg>
    </div>
  )
}

/** A localized calendar dropdown for the travel date. The native date input
 *  renders its text in the BROWSER language, which read as a translation
 *  failure; this one draws its month and weekday names from the app locale
 *  via Intl, works identically on laptop and phone, and still lets the
 *  reader type an ISO date by hand. */
function DateField({ value, onChange, lang, placeholder }) {
  const [open, setOpen] = useState(false)
  const boxRef = useRef(null)
  const parse = (s) => {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(s || '').trim())
    return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null
  }
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const sel = parse(value)
  const [view, setView] = useState(() => sel || today)
  useEffect(() => { const d = parse(value); if (d) setView(d) }, [value])
  useEffect(() => {
    const close = (e) => {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])
  const loc = lang || 'en'
  const monthName = new Intl.DateTimeFormat(loc, { year: 'numeric', month: 'long' }).format(view)
  // 2023-01-01 was a Sunday: a fixed base week gives narrow weekday names
  // in the app locale with Sunday first.
  const weekdays = [...Array(7)].map((_, i) =>
    new Intl.DateTimeFormat(loc, { weekday: 'narrow' }).format(new Date(2023, 0, 1 + i)))
  const first = new Date(view.getFullYear(), view.getMonth(), 1)
  const startPad = first.getDay()
  const daysIn = new Date(view.getFullYear(), view.getMonth() + 1, 0).getDate()
  const fmt = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  const nav = (delta) => setView((v) => new Date(v.getFullYear(), v.getMonth() + delta, 1))
  const navBtn = { border: 'none', background: 'transparent', color: NAVY,
                   fontSize: 16, fontWeight: 800, cursor: 'pointer',
                   padding: '4px 10px', borderRadius: 8 }
  return (
    <div ref={boxRef} style={{ position: 'relative' }}>
      <input className="input" value={value} inputMode="numeric"
             data-testid="database-date"
             placeholder={placeholder}
             onFocus={() => setOpen(true)}
             onChange={(e) => onChange(e.target.value.trim())}
             style={{ fontSize: 14, padding: '12px 46px 12px 14px',
                      borderRadius: 12, width: '100%', boxSizing: 'border-box' }} />
      <button type="button" aria-label="calendar"
              onClick={() => setOpen((o) => !o)}
              style={{ position: 'absolute', right: 2, top: '50%',
                       transform: 'translateY(-50%)', border: 'none',
                       background: 'transparent', cursor: 'pointer',
                       fontSize: 18, color: GRAY, padding: '10px 12px' }}>
        📅
      </button>
      {open && (
        <div className="card"
             style={{ position: 'absolute', zIndex: 40, top: '100%', left: 0,
                      marginTop: 4, padding: 12,
                      width: 'min(300px, calc(100vw - 48px))',
                      background: 'var(--bg, #fff)',
                      boxShadow: '0 8px 24px rgba(0,0,0,0.12)' }}>
          <div style={{ display: 'flex', alignItems: 'center',
                        justifyContent: 'space-between', marginBottom: 8 }}>
            <button type="button" style={navBtn} aria-label="previous month"
                    onClick={() => nav(-1)}>‹</button>
            <strong style={{ color: NAVY, fontSize: 13.5 }}>{monthName}</strong>
            <button type="button" style={navBtn} aria-label="next month"
                    onClick={() => nav(1)}>›</button>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)',
                        gap: 2, textAlign: 'center' }}>
            {weekdays.map((w, i) => (
              <span key={'w' + i} style={{ fontSize: 11, fontWeight: 700,
                                           color: GRAY, padding: '2px 0' }}>{w}</span>
            ))}
            {[...Array(startPad)].map((_, i) => <span key={'p' + i} />)}
            {[...Array(daysIn)].map((_, i) => {
              const d = new Date(view.getFullYear(), view.getMonth(), i + 1)
              const past = d < today
              const isSel = sel && d.getTime() === sel.getTime()
              const isToday = d.getTime() === today.getTime()
              return (
                <button key={'d' + i} type="button" disabled={past}
                        onClick={() => { onChange(fmt(d)); setOpen(false) }}
                        style={{ border: isToday && !isSel
                                   ? `1px solid ${BLUE}` : 'none',
                                 borderRadius: 8, fontSize: 13,
                                 padding: '7px 0', cursor: past ? 'default' : 'pointer',
                                 background: isSel ? NAVY : 'transparent',
                                 color: isSel ? '#fff' : past ? '#c3ccd9' : NAVY,
                                 fontWeight: isSel || isToday ? 700 : 500 }}>
                  {i + 1}
                </button>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

function CountryCombo({ value, options, onChange, placeholder, noMatch, testid }) {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const current = options.find((o) => o.value === value)
  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase()
    if (!s) return options.slice(0, 60)
    // Name-prefix matches rank first: "united sta" offers United States
    // before United States Minor Outlying Islands.
    const starts = [], contains = []
    for (const o of options) {
      if (!o.search.includes(s)) continue
      const name = o.label.replace(/^[^A-Za-z]*/, '').toLowerCase()
      ;(name.startsWith(s) ? starts : contains).push(o)
    }
    starts.sort((a, b) => a.label.length - b.label.length)
    return [...starts, ...contains].slice(0, 60)
  }, [q, options])
  return (
    <div style={{ position: 'relative' }}>
      <input className="input" data-testid={testid}
        style={{ fontSize: 14, padding: '11px 14px', borderRadius: 12, width: '100%' }}
        value={open ? q : (current ? current.label : '')}
        placeholder={current ? current.label : placeholder}
        onFocus={() => { setOpen(true); setQ('') }}
        onChange={(e) => setQ(e.target.value)}
        onBlur={() => setTimeout(() => setOpen(false), 150)} />
      {open && (
        <div className="card" style={{ position: 'absolute', zIndex: 40,
          top: '100%', left: 0, right: 0, maxHeight: 236, overflowY: 'auto',
          marginTop: 4, background: 'var(--bg)',
          boxShadow: '0 8px 24px rgba(0,0,0,0.12)' }}>
          {filtered.length === 0
            ? <div style={{ padding: 10, fontSize: 13, color: GRAY }}>{noMatch}</div>
            : filtered.map((o) => (
                <div key={o.value}
                  onMouseDown={(e) => { e.preventDefault(); onChange(o.value); setOpen(false) }}
                  style={{ padding: '8px 12px', cursor: 'pointer', fontSize: 13.5,
                           background: o.value === value ? 'var(--bg-soft)' : undefined }}>
                  {o.label}
                </div>
              ))}
        </div>
      )}
    </div>
  )
}

// ---- result-page building blocks -----------------------------------------

function Section({ title, accent, children }) {
  return (
    <div className="card" style={{ padding: '26px 30px', borderRadius: 20,
                                   textAlign: 'left', border: 'none',
                                   boxShadow: '0 1px 3px rgba(15,41,77,0.06)',
                                   breakInside: 'avoid', marginBottom: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8,
                    marginBottom: 16 }}>
        <span style={{ width: 8, height: 8, borderRadius: 3, flex: 'none',
                       background: accent || BLUE }} />
        <span style={{ fontSize: 12, fontWeight: 800, letterSpacing: 1.1,
                       color: GRAY, textTransform: 'uppercase' }}>{title}</span>
      </div>
      {children}
    </div>
  )
}

function Pill({ tone, children }) {
  const styles = tone === 'yes'
    ? { bg: '#eefaf1', fg: '#0f8a3d' }
    : tone === 'no' ? { bg: '#f2f4f8', fg: GRAY }
    : { bg: '#fff7e8', fg: '#9a6200' }
  return (
    <span style={{ fontSize: 12.5, fontWeight: 700, padding: '4px 12px',
                   borderRadius: 999, background: styles.bg, color: styles.fg,
                   whiteSpace: 'nowrap' }}>{children}</span>
  )
}

function Fact({ label, value, pill }) {
  const v = sentence(asText(value))
  if (!v) return null
  // A short answer sits opposite its label; a sentence gets its own line so
  // it reads left to right instead of wrapping against the right edge.
  const stacked = !pill && v.length > 42
  return (
    <div style={{ display: stacked ? 'block' : 'flex',
                  justifyContent: 'space-between', alignItems: 'center', gap: 20,
                  padding: '12px 0', fontSize: 13.5, lineHeight: 1.6,
                  borderBottom: '1px solid #f3f5f9' }}>
      <div style={{ color: GRAY, flex: 'none',
                    marginBottom: stacked ? 4 : 0 }}>{label}</div>
      {pill
        ? <Pill tone={pill}>{v}</Pill>
        : <div style={{ color: NAVY, fontWeight: 600,
                        textAlign: stacked ? 'left' : 'right' }}>{v}</div>}
    </div>
  )
}

function Bullets({ items, mark = '•', markColor = BLUE }) {
  if (!items.length) return null
  return (
    <ul style={{ margin: 0, paddingLeft: 0, listStyle: 'none' }}>
      {items.map((x, i) => (
        <li key={i} style={{ display: 'flex', gap: 12, alignItems: 'flex-start',
                             padding: '7px 0', fontSize: 13.5, color: NAVY,
                             lineHeight: 1.6 }}>
          <span style={{ color: markColor, fontWeight: 800, flex: 'none' }}>{mark}</span>
          <span>{x}</span>
        </li>
      ))}
    </ul>
  )
}

function Tile({ label, value, href, sub = null }) {
  const v = asText(value)
  if (!v) return null
  const inner = (
    <>
      <div style={{ fontSize: 10.5, fontWeight: 800, letterSpacing: 0.9,
                    color: GRAY, textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 14, fontWeight: 700, marginTop: 8, lineHeight: 1.45,
                    color: href ? BLUE : NAVY,
                    display: 'flex', alignItems: 'center', gap: 6 }}>
        {humanize(v)}{href ? <span aria-hidden>↗</span> : null}
      </div>
      {/* What kind of channel it is, under the site's own name. */}
      {sub && asText(sub) && (
        <div style={{ fontSize: 12, color: GRAY, marginTop: 3 }}>
          {humanize(asText(sub))}
        </div>
      )}
    </>
  )
  const style = { borderRadius: 18, padding: '18px 20px', textAlign: 'left',
                  minWidth: 0, border: 'none', display: 'block',
                  boxShadow: '0 1px 3px rgba(15,41,77,0.06)' }
  return href
    ? <a className="card" href={href} target="_blank" rel="noreferrer"
         style={{ ...style, textDecoration: 'none' }}>{inner}</a>
    : <div className="card" style={style}>{inner}</div>
}

export default function TravelDatabase({ onBack }) {
  const clientRef = useRef(null)
  if (!clientRef.current) clientRef.current = createVisaClient(newSession())
  const client = clientRef.current
  const { lang, t } = useLocale()

  const [reg, setReg] = useState(null)
  const [nat, setNat] = useState('')
  const [doc, setDoc] = useState('ordinary_passport')
  const [dest, setDest] = useState('')
  const [purpose, setPurpose] = useState('tourism')
  const [arrival, setArrival] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)
  const [question, setQuestion] = useState('')
  const [askBusy, setAskBusy] = useState(false)
  const [askMsg, setAskMsg] = useState('')
  const [askSlow, setAskSlow] = useState(false)   // a first-time route is being worked out
  // What the ANSWER on screen was asked for. The hero renders these, so an
  // in-flight switch can never show the old answer under a new heading.
  const [shown, setShown] = useState({ purpose: 'tourism', doc: 'ordinary_passport' })
  // When the question asked for ONE fact ("how much..."), the answer page
  // leads with it. Cleared on any form lookup.
  const [focus, setFocus] = useState(null)
  // A switch re-asks the engine for a DIFFERENT combination. Warm ones come
  // back in milliseconds; a first-time combination takes the model a moment,
  // and without this the page looked frozen under the old answer.
  const [switching, setSwitching] = useState(false)
  const [departureCity, setDepartureCity] = useState('')
  const [transit, setTransit] = useState([])       // ISO3 stopover countries
  const [issueOpen, setIssueOpen] = useState(false)
  const [issueNote, setIssueNote] = useState('')
  const [issueDone, setIssueDone] = useState(false)
  const [issueError, setIssueError] = useState('')
  const [issueField, setIssueField] = useState('')
  // Dynamic-content translation overlay: original guidance string -> lang.
  const [tx, setTx] = useState({})
  const [txPending, setTxPending] = useState(false)

  // Arriving on a linked answer opens it straight away — that link IS the
  // page. Runs once; a warm route resolves from cache immediately.
  const booted = useRef(false)
  useEffect(() => {
    if (booted.current) return
    booted.current = true
    const r = routeFromHash()
    if (!r) return
    setNat(r.nat); setDest(r.dest); setPurpose(r.purpose); setDoc(r.doc)
    lookUp({ nat: r.nat, dest: r.dest, purpose: r.purpose, doc: r.doc })
  }, [])

  useEffect(() => {
    let live = true
    client.snapshotRegistries().then((r) => { if (live) setReg(r) })
      .catch(() => { if (live) setReg({ countries: [], nationalities: [], travel_document_types: [] }) })
    return () => { live = false }
  }, [])

  const countries = useLocalizedCountries(client, reg, lang)
  const docTypes = reg?.travel_document_types || []
  const docLabel = (d) => {
    const code = d.code || d
    const k = 'db.doc.' + code
    const v = t(k)
    return v === k ? (d.name || d) : v
  }

  // An answer the engine itself rated low confidence is HELD until a person
  // confirms it. Holding means the reader does not see the claims at all —
  // showing them under a warning would still be showing them.
  const held = result?.held === true   // server decides; off by default
  const g = (result && !held) ? (result.guidance || null) : null

  // Every user-facing string the decision carries, translated in ONE masked,
  // cached Kimi catalog call whenever the UI language is not English.
  useEffect(() => {
    if (!g || lang === 'en') { setTx({}); return }
    const products = (g.visa_products || []).filter((x) => x && typeof x === 'object')
    const texts = [...new Set([
      asText(g.visa_category), asText(g.permitted_stay),
      asText(g.processing_time), asText(g.passport_validity),
      asText(g.photo_requirements), asText(g.onward_travel_evidence),
      asText(g.accommodation_evidence), asText(g.financial_evidence),
      humanizeEnum(g.application_channel),
      asText(g.application_channel_detail),
      asText(g.arrival_card && g.arrival_card.note),
      // The card's name and window render as a visible value; they must
      // switch languages too (the audit caught "Visit Japan Web, Register
      // before departure..." staying English under Chinese chrome).
      asText(g.arrival_card && g.arrival_card.name),
      asText(g.arrival_card && g.arrival_card.submission_window),
      asText(g.transit_requirement && g.transit_requirement.note),
      // The visa-type table is the reader's decision surface; it must switch
      // languages with everything else (it previously stayed English).
      ...products.flatMap((vp) => [asText(vp.type), asText(vp.validity),
                                   asText(vp.notes), asText(vp.entry)]),
      ...itemsOf(result?.apply_steps),
      ...itemsOf(g.required_documents), ...itemsOf(g.forms),
      ...itemsOf(g.account_registration_steps), ...itemsOf(g.payment_process),
      ...itemsOf(g.submission_process), ...itemsOf(g.health_requirements),
      ...itemsOf(g.exceptions), ...itemsOf(g.uncertainty),
      ...itemsOf(result?.advisories),
    ].filter(Boolean))]
    if (!texts.length) { setTx({}); return }
    setTxPending(true)
    const entries = {}
    texts.forEach((s, i) => { entries['g' + i] = s })
    let live = true
    client.i18nCatalog(lang, entries).then((out) => {
      if (!live) return
      const m = {}
      texts.forEach((s, i) => {
        const v = (out?.entries || {})['g' + i]
        if (v) m[s] = v
      })
      setTx(m)
      setTxPending(false)
    }).catch(() => { if (live) setTxPending(false) /* honest English fallback */ })
    return () => { live = false }
  }, [g, lang])
  const T = (s) => (s && tx[s]) || s

  // AI Q&A: a plain-language question, answered by the same engine as the form.
  async function askQuestion() {
    const q = question.trim()
    if (!q) return
    setAskBusy(true); setAskMsg(''); setError(''); setResult(null); setAskSlow(false)
    // Warm routes answer in milliseconds. If nothing is back after two
    // seconds, this is a first-time route: say so instead of looking stuck.
    const slowTimer = setTimeout(() => setAskSlow(true), 2000)
    try {
      // The route on screen travels with a follow-up, so "what about
      // business?" modifies it instead of being refused.
      const out = await client.databaseAsk(q, result && g ? {
        nationality: nat, destination: dest, travel_purpose: shown.purpose,
        travel_document_type: shown.doc,
      } : null)
      if (out.understood === false) {
        // The server names the exact missing fact in the asker's language.
        setAskMsg(out.clarify || t('db.askUnclear'))
      } else {
        if (out.route) {
          setNat(out.route.nationality); setDest(out.route.destination)
          if (out.route.travel_purpose) setPurpose(out.route.travel_purpose)
          // "with a diplomatic passport" must SHOW as a diplomatic passport:
          // the answer was computed for it, so the control follows the words.
          const askedDoc = out.route.travel_document_type || doc
          if (out.route.travel_document_type) setDoc(out.route.travel_document_type)
          setShown({ purpose: out.route.travel_purpose || 'tourism', doc: askedDoc })
          setTransit(out.route.transit_countries || [])
          setFocus(out.focus || null)
          writeHash({ nat: out.route.nationality, dest: out.route.destination,
                      purpose: out.route.travel_purpose, doc: askedDoc })
        }
        setResult(out)
      }
    } catch (e) {
      setAskMsg(e?.detail?.reason || e?.message || t('db.error'))
    }
    clearTimeout(slowTimer); setAskSlow(false)
    setAskBusy(false)
  }

  // Two-stage answers: a route nobody asked before paints its verdict first
  // and fills the detail sections in the background. Poll until they land
  // (the cached lookup is instant), then swap the fuller answer in.
  const pollRef = useRef(0)
  function pollDetail(body) {
    const mine = ++pollRef.current
    let tries = 0
    const tick = async () => {
      if (pollRef.current !== mine || tries++ > 12) return
      try {
        const out = await client.databaseLookup(body)
        if (pollRef.current !== mine) return
        if (!out.detail_pending) { setResult(out); return }
      } catch { /* keep what we have */ }
      setTimeout(tick, 2500)
    }
    setTimeout(tick, 2500)
  }

  // Prewarm: the moment the form names a complete route, start the engine in
  // the background. By the time the reader clicks the query button, a cold
  // route is already seconds into its one generation (the server coalesces
  // the prewarm and the real submit into a single Kimi call).
  const prewarmedRef = useRef('')
  useEffect(() => {
    if (!nat || !dest) return
    const sig = [nat, dest, purpose, doc, arrival, (transit || []).join(',')].join('|')
    if (prewarmedRef.current === sig) return
    const id = setTimeout(() => {
      prewarmedRef.current = sig
      client.databaseLookup({
        nationality: nat, destination: dest, travel_document_type: doc,
        travel_purpose: purpose, arrival_date: arrival || '',
        transit_countries: transit,
      }).catch(() => {})
    }, 500)
    return () => clearTimeout(id)
  }, [nat, dest, purpose, doc, arrival, transit, client])

  // One query path. The form calls it bare; the switchers on the answer page
  // call it with an override, so changing document type or purpose re-asks
  // the engine for THAT combination instead of showing the old answer under
  // a new label.
  async function lookUp(override = {}) {
    const useDoc = override.doc ?? doc
    const usePurpose = override.purpose ?? purpose
    // nat/dest can be passed explicitly by the deep-link boot, which runs
    // before the state it just set has flushed.
    const useNat = override.nat ?? nat
    const useDest = override.dest ?? dest
    if (!useNat || !useDest) return
    setBusy(true); setError(''); setIssueOpen(false); setIssueDone(false)
    if (!override.keepResult) setResult(null)
    try {
      const out = await client.databaseLookup({
        nationality: useNat, destination: useDest, travel_document_type: useDoc,
        travel_purpose: usePurpose, arrival_date: arrival || '',
        departure_city: departureCity || '',
        transit_countries: transit,
      })
      setResult(out)
      setShown({ purpose: usePurpose, doc: useDoc })
      if (!override.keepFocus) setFocus(null)
      writeHash({ nat: useNat, dest: useDest, purpose: usePurpose, doc: useDoc })
      setBusy(false)
      if (out.detail_pending) pollDetail({
        nationality: useNat, destination: useDest, travel_document_type: useDoc,
        travel_purpose: usePurpose, arrival_date: arrival || '',
        departure_city: departureCity || '', transit_countries: transit,
      })
      return true
    } catch (e) {
      setError(e?.detail?.reason || e?.detail?.detail || e?.message || t('db.error'))
      // A failed switch leaves the answer the reader was already looking at
      // on screen. Clearing it would throw away a good answer because a
      // DIFFERENT question could not be answered.
      setBusy(false)
      return false
    }
  }

  // A switcher on the answer page: set the control, then re-ask for it.
  async function switchDoc(v) {
    const was = doc
    setDoc(v); setSwitching(true)
    const ok = await lookUp({ doc: v, keepResult: true })
    setSwitching(false)
    if (!ok) setDoc(was)          // the page must never label an old answer anew
  }
  async function switchPurpose(v) {
    const was = purpose
    setPurpose(v); setSwitching(true)
    const ok = await lookUp({ purpose: v, keepResult: true })
    setSwitching(false)
    if (!ok) setPurpose(was)
  }

  async function reportIssue() {
    setIssueError('')
    try {
      await client.databaseReportIssue({
        nationality: nat, destination: dest, travel_purpose: purpose,
        travel_document_type: doc, field: issueField,
        note: issueNote.slice(0, 1000),
        // Bind the report to the answer actually on screen, not to a key
        // re-derived from a subset of the inputs.
        cache_key: result?.cache_key || '',
      })
      setIssueDone(true); setIssueNote('')
    } catch (e) {
      // Never claim a report landed when it did not: the reader would think
      // a wrong answer was flagged and it never was.
      setIssueError(e?.detail?.reason || e?.message || t('db.reportFailed'))
    }
  }

  const disp = g ? (DISPOSITION_VIEW[g.disposition] || null) : null
  const countryName = (code) => countries.find((c) => c.value === code)?.label || code

  // The 3-5 key steps, already deduplicated and ordered by the server (the
  // engine's three arrays overlap and repeat — one answer carried 135 steps).
  // The concatenation is only the fallback for an older cached answer.
  const applySteps = !g ? []
    : (Array.isArray(result?.apply_steps) && result.apply_steps.length
        ? result.apply_steps.map((x) => sentence(asText(x))).filter(Boolean)
        : [...itemsOf(g.account_registration_steps),
           ...itemsOf(g.payment_process),
           ...itemsOf(g.submission_process)].slice(0, 5))
  // The official-portal link rides ON the step it belongs to.
  const portalStepIndex = g?.official_portal_url
    ? applySteps.findIndex((x) =>
        /register|portal|online|website|e-?visa|application form|apply/i.test(x))
    : -1

  const yesNo = (v) => v === true ? [t('db.required'), 'yes']
    : v === false ? [t('db.notRequired'), 'no'] : null
  const entryFacts = g ? [
    [t('db.biometrics'), yesNo(g.biometrics_required)],
    [t('db.interview'), yesNo(g.interview_required)],
    [t('db.appointment'), yesNo(g.appointment_required)],
    [t('db.insurance'), yesNo(g.insurance_required)],
  ].filter(([, v]) => v) : []

  const documents = g ? itemsOf(g.required_documents).map(T) : []
  const health = g ? itemsOf((g.health_requirements || []).filter((h) =>
    !h || typeof h !== 'object' ||
    String(h.applicability || '') !== 'not_applicable')).map(T) : []

  const label = (k) => (
    <span style={{ fontSize: 13, fontWeight: 700, color: NAVY }}>{t(k)}</span>
  )

  return (
    <div className="page" style={{ maxWidth: result ? 900 : 1160,
                                   margin: '0 auto',
                                   padding: '26px 20px 60px' }}
         data-testid="travel-database">
      <style>{DB_CSS}</style>
      {onBack && (
        <button className="btn btn--sm btn--ghost" onClick={onBack}
                data-testid="database-back">← {t('db.menu')}</button>
      )}

      {/* Heading hidden entirely (owner instruction 2026-08-28): the page
          opens straight on the ask box and the route form. */}
      {false && !result && (
        <div style={{ margin: '16px 0 6px', textAlign: 'center' }}>
          <h1 style={{ fontSize: 32, fontWeight: 800, color: NAVY, margin: 0,
                       letterSpacing: -0.6 }}>{t('db.title')}</h1>
          <div style={{ fontSize: 14.5, color: GRAY, marginTop: 8 }}>
            {t('db.sub')}
          </div>
        </div>
      )}

      {!result && (
      <div className="db-hero" style={{ marginTop: 18 }}>
      <div className="db-hero-left">
        <div className="card anim-rise" style={{ padding: '20px 24px',
                                                 borderRadius: 20,
                                                 maxWidth: 560, marginLeft: 'auto',
                                                 marginRight: 'auto' }}
             data-testid="database-ask">
          <div style={{ fontSize: 13, fontWeight: 700, color: NAVY,
                        marginBottom: 8, textAlign: 'left' }}>{t('db.askTitle')}</div>
          <div style={{ display: 'flex', gap: 8 }}>
            <input className="input" value={question}
                   data-testid="database-question"
                   placeholder={t('db.askPlaceholder')}
                   onChange={(e) => setQuestion(e.target.value)}
                   onKeyDown={(e) => { if (e.key === 'Enter') askQuestion() }}
                   style={{ fontSize: 14, padding: '11px 14px', borderRadius: 12,
                            flex: 1 }} />
            <button className="btn btn--primary" onClick={askQuestion}
                    data-testid="database-ask-btn" disabled={askBusy || !question.trim()}
                    style={{ fontSize: 14, fontWeight: 800, borderRadius: 12,
                             padding: '0 18px' }}>
              {askBusy ? t('db.askBusy') : t('db.askBtn')}
            </button>
          </div>
          {askMsg && (
            <div style={{ fontSize: 12.5, color: NAVY, marginTop: 8,
                          textAlign: 'left' }}>{askMsg}</div>
          )}
          {/* While a first-time route is being worked out: the plane alone,
              no sentence (owner decision). Warm routes answer before it
              would appear. */}
          {askBusy && askSlow && (
            <div style={{ marginTop: 10 }} data-testid="database-ask-slow">
              <Loading label="" />
            </div>
          )}
          <div style={{ fontSize: 11.5, color: GRAY, marginTop: 10,
                        textAlign: 'center' }}>{t('db.askOr')}</div>
        </div>

        <div className="card anim-rise" style={{ padding: '26px 28px',
                                                 borderRadius: 20, marginTop: 14,
                                                 maxWidth: 560, marginLeft: 'auto',
                                                 marginRight: 'auto' }}
             data-testid="database-form">
          <div style={{ display: 'grid', gap: 15 }}>
            <label style={{ display: 'grid', gap: 6, textAlign: 'left' }}>
              {label('db.nationality')}
              <CountryCombo value={nat} options={countries} onChange={setNat}
                            placeholder={t('db.typeCountry')}
                            noMatch={t('db.noMatch')}
                            testid="database-nationality" />
            </label>
            <label style={{ display: 'grid', gap: 6, textAlign: 'left' }}>
              {label('db.travelDoc')}
              <select className="select" value={doc}
                      onChange={(e) => setDoc(e.target.value)}
                      style={{ fontSize: 14, padding: '11px 14px', borderRadius: 12 }}>
                {(docTypes.length ? docTypes : [{ code: 'ordinary_passport', name: 'Ordinary passport' }])
                  .map((d) => (
                    <option key={d.code || d} value={d.code || d}>{docLabel(d)}</option>
                  ))}
              </select>
            </label>
            <label style={{ display: 'grid', gap: 6, textAlign: 'left' }}>
              {label('db.destination')}
              <CountryCombo value={dest} options={countries} onChange={setDest}
                            placeholder={t('db.typeCountry')}
                            noMatch={t('db.noMatch')}
                            testid="database-destination" />
            </label>
            <label style={{ display: 'grid', gap: 6, textAlign: 'left' }}>
              {label('db.purpose')}
              <select className="select" value={purpose}
                      onChange={(e) => setPurpose(e.target.value)}
                      style={{ fontSize: 14, padding: '11px 14px', borderRadius: 12 }}>
                {PURPOSES.map(([v, k]) => <option key={v} value={v}>{t(k)}</option>)}
              </select>
            </label>
            <label style={{ display: 'grid', gap: 6, textAlign: 'left' }}>
              {/* Owner decision (2026-08-29): traveldoc-simple. The spec
                  table marks 出发地 required, but a blocking city field read
                  as friction; it stays optional like traveldoc's form. */}
              <span style={{ fontSize: 13, fontWeight: 700, color: NAVY }}>
                {t('db.departure')}{' '}
                <span style={{ color: GRAY, fontWeight: 400 }}>({t('db.optional')})</span>
              </span>
              <input className="input" value={departureCity}
                     data-testid="database-departure"
                     placeholder={t('db.departurePlaceholder')}
                     onChange={(e) => setDepartureCity(e.target.value)}
                     style={{ fontSize: 14, padding: '11px 14px', borderRadius: 12 }} />
            </label>
            <label style={{ display: 'grid', gap: 6, textAlign: 'left' }}>
              <span style={{ fontSize: 13, fontWeight: 700, color: NAVY }}>
                {t('db.transit')}{' '}
                <span style={{ color: GRAY, fontWeight: 400 }}>({t('db.optional')})</span>
              </span>
              {transit.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6,
                              marginBottom: 2 }}>
                  {transit.map((c) => (
                    <span key={c} style={{ fontSize: 12.5, fontWeight: 700,
                          padding: '4px 10px', borderRadius: 999,
                          background: '#eef4ff', color: NAVY,
                          display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                      {countryName(c)}
                      <span role="button" tabIndex={0}
                            onClick={() => setTransit(transit.filter((x) => x !== c))}
                            style={{ cursor: 'pointer', color: GRAY,
                                     fontWeight: 800 }}>×</span>
                    </span>
                  ))}
                </div>
              )}
              <CountryCombo value="" options={countries}
                            onChange={(v) => { if (v && !transit.includes(v))
                              setTransit([...transit, v].slice(0, 5)) }}
                            placeholder={t('db.transitPlaceholder')}
                            noMatch={t('db.noMatch')}
                            testid="database-transit" />
            </label>
            <label style={{ display: 'grid', gap: 6, textAlign: 'left' }}>
              <span style={{ fontSize: 13, fontWeight: 700, color: NAVY }}>
                {t('db.travelDate')}{' '}
                <span style={{ color: GRAY, fontWeight: 400 }}>({t('db.optional')})</span>
              </span>
              <DateField value={arrival} onChange={setArrival} lang={lang}
                         placeholder={t('db.datePlaceholder')} />
            </label>
          </div>
          <div style={{ marginTop: 20, textAlign: 'center' }}>
            <button className="btn btn--primary" onClick={lookUp}
                    disabled={busy || !nat || !dest}
                    data-testid="database-check"
                    style={{ fontSize: 15, fontWeight: 800, padding: '13px 32px',
                             borderRadius: 999 }}>
              {busy ? t('db.checking') : t('db.check')}
            </button>
            {error && (
              <div style={{ fontSize: 13, color: NAVY, fontWeight: 700, marginTop: 10 }}>
                {error}
              </div>
            )}
          </div>
          {busy && (
            <div style={{ display: 'flex', justifyContent: 'center', marginTop: 14 }}>
              {/* The plane alone, no explanatory sentence (owner decision). */}
              <Loading label="" />
            </div>
          )}
        </div>
      </div>
      <EllisIslandScene />
      </div>
      )}

      {result && g && (
        <div className="anim-rise" data-testid="database-result">
          {/* Verdict hero */}
          <div className="card" style={{ padding: '36px 36px', borderRadius: 22,
                                         marginTop: 20, textAlign: 'center',
                                         background: disp?.tint || '#eef4ff',
                                         border: 'none' }}>
            <div style={{ fontSize: 13, color: NAVY, fontWeight: 700,
                          opacity: 0.75 }}>
              {countryName(nat)} → {countryName(dest)} ·{' '}
              {t(PURPOSES.find(([v]) => v === shown.purpose)?.[1] || 'db.purpose.tourism')}
            </div>
            <div style={{ fontSize: 29, fontWeight: 800,
                          color: disp?.color || NAVY,
                          marginTop: 8, letterSpacing: -0.4 }}
                 data-testid="database-disposition">
              {['evisa_on_arrival', 'paper_visa_on_arrival']
                 .includes(g.requirement_detail)
                ? t('db.verdict.voa')
                : disp ? t(disp.key) : asText(g.disposition)}
            </div>
            {asText(g.visa_category) && (
              <div style={{ fontSize: 14, color: NAVY, marginTop: 8, opacity: 0.85 }}>
                {humanize(T(asText(g.visa_category)))}
              </div>
            )}
            {REQUIREMENT_DETAIL_KEYS[g.requirement_detail] && (
              <div style={{ marginTop: 12 }}>
                <span style={{ fontSize: 12, fontWeight: 800, letterSpacing: 0.4,
                               padding: '5px 14px', borderRadius: 999,
                               background: 'rgba(255,255,255,0.72)',
                               color: disp?.color || NAVY }}
                      data-testid="database-requirement-detail">
                  {t(REQUIREMENT_DETAIL_KEYS[g.requirement_detail])}
                </span>
              </div>
            )}
          </div>

          {/* The fact the question asked for, answered first. */}
          {focus && (() => {
            const v = focus === 'fee'
              ? (feeText(g.government_fee) || t('db.feeSeeProducts'))
              : focus === 'stay' ? T(asText(g.permitted_stay))
              : focus === 'processing' ? T(asText(g.processing_time))
              : focus === 'documents' ? (itemsOf(g.required_documents).length
                  ? itemsOf(g.required_documents).length + ' - ' + t('db.documents')
                  : null)
              : null
            return v ? (
              <div style={{ marginTop: 14, padding: '12px 18px', borderRadius: 14,
                            background: '#eef4ff', textAlign: 'center',
                            fontSize: 15, fontWeight: 700, color: NAVY }}
                   data-testid="database-focus">
                {t('db.focus.' + focus)}: {v}
              </div>
            ) : null
          })()}

          {/* Switchers, on the page itself: change the travel document or the
              purpose and the answer is re-asked for THAT combination — never
              the old answer relabelled. */}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10,
                        marginTop: 14, alignItems: 'center',
                        justifyContent: 'center' }}
               data-testid="database-switchers">
            <span style={{ fontSize: 12, color: GRAY, fontWeight: 700 }}>
              {t('db.travelDoc')}
            </span>
            <select className="select" value={doc} disabled={busy}
                    data-testid="database-switch-doc"
                    onChange={(e) => switchDoc(e.target.value)}
                    style={{ fontSize: 13, padding: '7px 12px', borderRadius: 10 }}>
              {(docTypes.length ? docTypes
                : [{ code: 'ordinary_passport', name: 'Ordinary passport' }])
                .map((d) => (
                  <option key={d.code || d} value={d.code || d}>{docLabel(d)}</option>
                ))}
            </select>
            <span style={{ fontSize: 12, color: GRAY, fontWeight: 700,
                           marginLeft: 6 }}>
              {t('db.purpose')}
            </span>
            <select className="select" value={purpose} disabled={busy}
                    data-testid="database-switch-purpose"
                    onChange={(e) => switchPurpose(e.target.value)}
                    style={{ fontSize: 13, padding: '7px 12px', borderRadius: 10 }}>
              {PURPOSES.map(([v, k]) => <option key={v} value={v}>{t(k)}</option>)}
            </select>
            {busy && (
              <span style={{ fontSize: 12, color: GRAY }}>{t('db.checking')}</span>
            )}
          </div>
          {/* A switch that could not be answered says so here, and the
              controls above have already snapped back to the combination
              actually on screen. */}
          {error && (
            <div style={{ fontSize: 12.5, color: NAVY, fontWeight: 600,
                          marginTop: 10, textAlign: 'center' }}
                 data-testid="database-switch-error">
              {error}
            </div>
          )}

          {/* While a switch is in flight the page shows the plane rather than
              the previous answer: that answer was for a DIFFERENT document or
              purpose, so leaving it up reads as the new one. */}
          {switching && (
            <div style={{ display: 'flex', justifyContent: 'center',
                          padding: '26px 0 6px' }}
                 data-testid="database-switching">
              <Loading label="" />
            </div>
          )}

          {/* Content translation in flight: say so, instead of showing
              English under a Chinese frame with no explanation. */}
          {!switching && txPending && lang !== 'en' && (
            <div style={{ textAlign: 'center', marginTop: 14, fontSize: 12.5,
                          color: GRAY, fontWeight: 600 }}
                 data-testid="database-translating">
              {t('db.translatingContent')}
            </div>
          )}

          {/* At a glance */}
          {!switching && (
          <div style={{ display: 'grid', gap: 16, marginTop: 20,
                        gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))' }}>
            <Tile label={t('db.stay')} value={T(asText(g.permitted_stay))} />
            <Tile label={t('db.fee')}
                  value={feeText(g.government_fee)
                         || ((g.visa_products || []).some((vp) => feeText(vp.fee))
                             ? t('db.feeSeeProducts') : null)} />
            <Tile label={t('db.processing')} value={T(asText(g.processing_time))} />
            <Tile label={t('db.channel')}
                  value={g.official_portal_url
                    ? (siteLabel(g.official_portal_url)
                       || T(humanizeEnum(g.application_channel)))
                    : T(humanizeEnum(g.application_channel))}
                  sub={g.official_portal_url
                    ? T(humanizeEnum(g.application_channel)) : null}
                  href={g.official_portal_url || undefined} />
          </div>
          )}
          {/* The channel sentence under the tiles is hidden by owner decision
              (theming) for every route: the "Where to apply" tile carries
              the honest channel label itself. The field still ships in the
              answer and still drives the channel contradiction check. */}
          {false && asText(g.application_channel_detail) && (
            <div style={{ fontSize: 13, color: NAVY, marginTop: 12,
                          padding: '12px 16px', borderRadius: 12,
                          background: 'var(--bg-soft, #f5f7fa)' }}>
              {sentence(asText(g.application_channel_detail))}
            </div>
          )}

          {/* Transit: answered ONLY when a stopover was named, so an empty
              answer is never dressed up as "no transit visa needed". */}
          {transit.length > 0 && g.transit_requirement
            && g.transit_requirement.required !== null && (
            <div style={{ marginTop: 16 }}>
              <Section title={t('db.transitReq')} accent={BLUE}>
                <Fact label={transit.map(countryName).join(', ')}
                      value={g.transit_requirement.required
                             ? t('db.transitNeeded') : t('db.transitNotNeeded')}
                      pill={g.transit_requirement.required ? 'warn' : 'no'} />
                {sentence(asText(g.transit_requirement.note)) && (
                  <div style={{ fontSize: 13, color: NAVY, lineHeight: 1.6,
                                paddingTop: 10 }}
                       data-testid="database-transit-note">
                    {T(sentence(asText(g.transit_requirement.note)))}
                  </div>
                )}
              </Section>
            </div>
          )}

          {/* Good to know: the facilitation policies and caveats the engine
              produces. These were being fetched and translated but never
              shown, so Trip.com's "special policies missing" defect survived
              in the UI even after the engine started answering it. */}
          {itemsOf(g.exceptions).length > 0 && (
            <div style={{ marginTop: 16 }}>
              <Section title={t('db.goodToKnow')} accent="#9a6200">
                <Bullets items={itemsOf(g.exceptions).map((x) => T(x))}
                         mark="•" markColor="#9a6200" />
              </Section>
            </div>
          )}

          {result.detail_pending && (
            <div style={{ fontSize: 12.5, color: GRAY, marginTop: 14,
                          textAlign: 'center' }} data-testid="database-detail-pending">
              {t('db.detailPending')}
            </div>
          )}

          {/* Available visa types — every product for this route, each with
              its own entry, validity, stay and fee (Trip.com feedback: never
              one generic product). */}
          {Array.isArray(g.visa_products) && g.visa_products.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <Section title={t('db.visaTypes')} accent="#0f8a3d">
                <div style={{ display: 'grid', gap: 10 }}>
                  <div style={{ display: 'grid',
                                gridTemplateColumns: '1.4fr 1fr 1fr 1fr', gap: 12,
                                fontSize: 11, fontWeight: 800, color: GRAY,
                                letterSpacing: 0.6, textTransform: 'uppercase',
                                paddingBottom: 6,
                                borderBottom: '1px solid var(--line, #eef1f5)' }}>
                    <div>{t('db.colType')}</div>
                    <div>{t('db.colValidity')}</div>
                    <div>{t('db.colStay')}</div>
                    <div>{t('db.colFee')}</div>
                  </div>
                  {g.visa_products.map((vp, i) => (
                    <div key={i} style={{ display: 'grid',
                         gridTemplateColumns: '1.4fr 1fr 1fr 1fr', gap: 12,
                         padding: '10px 0',
                         borderBottom: i < g.visa_products.length - 1
                           ? '1px solid var(--line, #eef1f5)' : 'none',
                         fontSize: 13.5, alignItems: 'baseline' }}>
                      <div style={{ fontWeight: 700, color: NAVY }}>
                        {T(asText(vp.type))}
                        {vp.entry ? <span style={{ color: GRAY, fontWeight: 400 }}>
                          {' · ' + T(asText(vp.entry))}</span> : null}
                      </div>
                      <div style={{ color: NAVY }}>{T(asText(vp.validity)) || '·'}</div>
                      <div style={{ color: NAVY }}>
                        {vp.max_stay_days
                          ? t('db.upToDays', { n: vp.max_stay_days })
                          : (T(asText(vp.notes)) ? '' : '·')}
                        {T(asText(vp.notes)) && (
                          <div style={{ color: GRAY, fontSize: 12,
                                        marginTop: 3, lineHeight: 1.45 }}>
                            {T(asText(vp.notes))}
                          </div>
                        )}
                      </div>
                      <div style={{ color: NAVY, fontWeight: 600 }}>
                        {feeText(vp.fee) || '·'}</div>
                    </div>
                  ))}
                </div>
              </Section>
            </div>
          )}

          {/* The brief: documents full width, then the narrow cards in a
              packed column flow, then the apply steps full width. */}
          <div style={{ marginTop: 16 }}>
            {documents.length > 0 && (
              <Section title={t('db.documents')} accent="#0f8a3d">
                <div style={{ columns: documents.length > 5 ? 2 : 1,
                              columnGap: 32 }}>
                  <Bullets items={documents} mark="✓" markColor="#0f8a3d" />
                </div>
              </Section>
            )}

            {(() => {
              // Balanced two-column pack: each card lands in the currently
              // shorter column (weighted by its row count), so short and
              // tall cards sit flush with no stranded white space.
              const cards = []
              if (asText(g.passport_validity) || asText(g.photo_requirements) ||
                  g.passport_validity_requirement?.months) {
                cards.push({ key: 'passport', weight: 3, node: (
                  <Section title={t('db.passportPhoto')} accent={BLUE} key="passport">
                    <Fact label={t('db.validity')} value={T(asText(g.passport_validity))} />
                    {g.passport_validity_requirement?.months ? (
                      <Fact label={t('db.validityRule')}
                            value={t('db.validityMonths',
                                     { n: g.passport_validity_requirement.months })} />
                    ) : null}
                    <Fact label={t('db.photo')} value={T(asText(g.photo_requirements))} />
                  </Section>
                ) })
              }
              if (entryFacts.length > 0 || g.arrival_card?.required || health.length > 0) {
                cards.push({ key: 'entry',
                  weight: entryFacts.length + (g.arrival_card?.required ? 2 : 0)
                          + health.length + 1, node: (
                  <Section title={t('db.entry')} accent={BLUE} key="entry">
                    {entryFacts.map(([l, [v, tone]]) => <Fact key={l} label={l} value={v} pill={tone} />)}
                    {g.arrival_card?.required ? (
                      <Fact label={t('db.arrivalCard')} value={
                        `${T(asText(g.arrival_card.name)) || t('db.arrivalCard')}${g.arrival_card.submission_window ? ', ' + T(asText(g.arrival_card.submission_window)) : ''}`} />
                    ) : null}
                    {health.length > 0 && (
                      <div style={{ marginTop: 8 }}>
                        <div style={{ fontSize: 12.5, color: GRAY, marginBottom: 4 }}>
                          {t('db.health')}
                        </div>
                        <Bullets items={health} />
                      </div>
                    )}
                  </Section>
                ) })
              }
              if (asText(g.onward_travel_evidence) || asText(g.accommodation_evidence) ||
                  asText(g.financial_evidence)) {
                cards.push({ key: 'evidence', weight: 3, node: (
                  <Section title={t('db.evidence')} accent={BLUE} key="evidence">
                    <Fact label={t('db.onward')} value={T(asText(g.onward_travel_evidence))} />
                    <Fact label={t('db.accommodation')} value={T(asText(g.accommodation_evidence))} />
                    <Fact label={t('db.finances')} value={T(asText(g.financial_evidence))} />
                  </Section>
                ) })
              }
              if (itemsOf(g.forms).length > 0 || g.official_portal_url) {
                cards.push({ key: 'forms', weight: itemsOf(g.forms).length + 2, node: (
                  <Section title={t('db.formsPortal')} accent={BLUE} key="forms">
                    <Bullets items={itemsOf(g.forms).map(T)} />
                    {g.official_portal_url && (
                      <a href={g.official_portal_url} target="_blank" rel="noreferrer"
                         style={{ display: 'inline-flex', alignItems: 'center',
                                  gap: 6, marginTop: 14, color: BLUE,
                                  fontSize: 13.5, fontWeight: 700,
                                  padding: '9px 16px', borderRadius: 999,
                                  background: 'rgba(40,125,250,0.08)' }}>
                        {t('db.portalStart')} ↗
                      </a>
                    )}
                  </Section>
                ) })
              }
              const left = [], right = []
              let lw = 0, rw = 0
              for (const c of cards) {
                if (lw <= rw) { left.push(c.node); lw += c.weight }
                else { right.push(c.node); rw += c.weight }
              }
              if (!right.length) return left
              return (
                <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start',
                              flexWrap: 'wrap' }}>
                  <div style={{ flex: '1 1 340px', minWidth: 340 }}>{left}</div>
                  <div style={{ flex: '1 1 340px', minWidth: 340 }}>{right}</div>
                </div>
              )
            })()}

            {applySteps.length > 0 && (
              <Section title={t('db.steps')} accent={NAVY}>
                <ol style={{ margin: 0, paddingLeft: 0, listStyle: 'none',
                             position: 'relative' }}>
                  {applySteps.map((x, i) => (
                    <li key={i} style={{ display: 'flex', gap: 14,
                                         alignItems: 'flex-start',
                                         padding: '8px 0', position: 'relative' }}>
                      {i < applySteps.length - 1 && (
                        <span style={{ position: 'absolute', left: 12, top: 34,
                                       bottom: -6, width: 2,
                                       background: 'var(--line, #e8edf3)' }} />
                      )}
                      <span style={{ flex: 'none', width: 26, height: 26,
                                     borderRadius: 999, background: BLUE,
                                     color: '#fff', fontSize: 13, fontWeight: 800,
                                     display: 'flex', alignItems: 'center',
                                     justifyContent: 'center', zIndex: 1 }}>{i + 1}</span>
                      <span style={{ fontSize: 13.5, color: NAVY,
                                     lineHeight: '26px' }}>
                        {T(x)}
                        {i === portalStepIndex && (
                          <a href={g.official_portal_url} target="_blank"
                             rel="noreferrer"
                             style={{ marginLeft: 8, color: BLUE, fontWeight: 700,
                                      whiteSpace: 'nowrap' }}>
                            {t('db.portalInline')} ↗
                          </a>
                        )}
                      </span>
                    </li>
                  ))}
                </ol>
                {g.official_portal_url && portalStepIndex === -1 && (
                  <a href={g.official_portal_url} target="_blank" rel="noreferrer"
                     style={{ display: 'inline-block', marginTop: 10, color: BLUE,
                              fontSize: 13.5, fontWeight: 700 }}>
                    {t('db.portalStart')} ↗
                  </a>
                )}
              </Section>
            )}
          </div>

          <div style={{ display: 'flex', gap: 12, justifyContent: 'center',
                        marginTop: 32, paddingTop: 24,
                        borderTop: '1px solid #eef1f6' }}>
            {/* This deliverable is an information base: no application
                buttons. Trip.com's review read the old "Process my visa"
                button as scope drift into visa processing. */}
            <button className="btn btn--ghost" onClick={() => { setResult(null); clearHash() }}
                    data-testid="database-again"
                    style={{ fontSize: 14, borderRadius: 999 }}>
              {t('db.newSearch')}
            </button>
          </div>

          {/* Checked against an official source: shown ONLY for the fields a
              person actually verified, with the source and the date. An
              answer without this badge is the engine's own, and says so
              rather than borrowing the authority of a checked one. */}
          {result.source_verified && (
            <div style={{ marginTop: 16, padding: '12px 16px', borderRadius: 12,
                          background: '#eefaf1', fontSize: 12.5, color: NAVY }}
                 data-testid="database-source-verified">
              <span style={{ fontWeight: 800 }}>{t('db.verifiedTitle')}</span>{' '}
              {t('db.verifiedOn', { date: result.source_verified.verified_at })}{' '}
              <a href={result.source_verified.source_url} target="_blank"
                 rel="noreferrer" style={{ color: '#0f8a3d' }}>
                {t('db.verifiedSource')}
              </a>
              {/* The correction note (what the model had wrong) is hidden by
                  owner decision (theming); the badge, date and source stay. */}
              {false && result.source_verified.note && (
                <div style={{ color: GRAY, marginTop: 6, lineHeight: 1.5 }}>
                  {result.source_verified.note}
                </div>
              )}
            </div>
          )}

          {/* Machine provenance, deliberately weaker than the green human
              badge: the official page was READ on this date and agreed (or
              corrections were applied) — never "a person verified this". */}
          {!result.source_verified && result.grounded_check?.at
            && result.grounded_check.consistent === true && (
            <div style={{ fontSize: 12, color: GRAY, marginTop: 14,
                          textAlign: 'center' }}
                 data-testid="database-grounded">
              {t('db.groundedOn', { date: String(result.grounded_check.at).slice(0, 10) })}
              {result.grounded_check.source_url && (
                <>
                  {' '}
                  <a href={result.grounded_check.source_url} target="_blank"
                     rel="noreferrer" style={{ color: GRAY,
                                               textDecoration: 'underline' }}>
                    {t('db.verifiedSource')}
                  </a>
                </>
              )}
            </div>
          )}

          {/* Source link only. The confidence label and the report-an-issue
              link are hidden by owner decision (theming): the low-confidence
              hold still runs server-side, and the report endpoint and
              operator queue stay for staff. */}
          {g.source_url && (
            <div style={{ fontSize: 12, color: GRAY, marginTop: 16,
                          textAlign: 'center' }}>
              <a href={g.source_url} target="_blank" rel="noreferrer"
                 style={{ color: GRAY, textDecoration: 'underline' }}>
                {t('db.source')}
              </a>
            </div>
          )}

          {/* Information-quality feedback: a reader flags what looks wrong.
              Ellis records the flag against this exact route for a person to
              work — it never silently rewrites the answer on a report. */}
          {false && issueOpen && (
            <div className="card" style={{ padding: '18px 20px', borderRadius: 16,
                                           marginTop: 12, maxWidth: 560,
                                           marginLeft: 'auto', marginRight: 'auto' }}>
              {issueDone ? (
                <div style={{ fontSize: 13, color: NAVY, textAlign: 'center' }}
                     data-testid="database-report-done">
                  {t('db.reportThanks')}
                </div>
              ) : (
                <>
                  <div style={{ fontSize: 13, fontWeight: 700, color: NAVY,
                                marginBottom: 8 }}>{t('db.reportTitle')}</div>
                  <select className="select" value={issueField}
                          data-testid="database-report-field"
                          onChange={(e) => setIssueField(e.target.value)}
                          style={{ fontSize: 13, padding: '9px 12px',
                                   borderRadius: 10, width: '100%',
                                   marginBottom: 8 }}>
                    <option value="">{t('db.reportWhich')}</option>
                    {[['visa_products', 'db.visaTypes'],
                      ['permitted_stay', 'db.stay'],
                      ['government_fee', 'db.fee'],
                      ['application_channel', 'db.channel'],
                      ['processing_time', 'db.processing'],
                      ['required_documents', 'db.documents'],
                      ['official_portal_url', 'db.formsPortal'],
                      ['other', 'db.reportOther']].map(([v, k]) => (
                        <option key={v} value={v}>{t(k)}</option>
                      ))}
                  </select>
                  <textarea className="input" rows={3} value={issueNote}
                            data-testid="database-report-note"
                            placeholder={t('db.reportPlaceholder')}
                            onChange={(e) => setIssueNote(e.target.value)}
                            style={{ fontSize: 13.5, padding: '10px 12px',
                                     borderRadius: 12, width: '100%',
                                     resize: 'vertical' }} />
                  {issueError && (
                    <div style={{ fontSize: 12.5, color: NAVY, fontWeight: 600,
                                  marginTop: 8 }}
                         data-testid="database-report-error">
                      {issueError}
                    </div>
                  )}
                  <div style={{ marginTop: 10, textAlign: 'right' }}>
                    <button className="btn btn--primary" onClick={reportIssue}
                            data-testid="database-report-send"
                            disabled={!issueNote.trim()}
                            style={{ fontSize: 13, fontWeight: 800,
                                     borderRadius: 999, padding: '8px 20px' }}>
                      {t('db.reportSend')}
                    </button>
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      )}

      {result && held && (
        <div className="card anim-rise" style={{ padding: '30px 28px',
                                                 borderRadius: 20, marginTop: 18,
                                                 maxWidth: 560, marginLeft: 'auto',
                                                 marginRight: 'auto',
                                                 textAlign: 'center' }}
             data-testid="database-held">
          <div style={{ fontSize: 15, fontWeight: 800, color: NAVY }}>
            {t('db.heldTitle')}
          </div>
          <div style={{ fontSize: 13.5, color: GRAY, marginTop: 10,
                        lineHeight: 1.55 }}>
            {t('db.heldBody', { route: `${countryName(nat)} → ${countryName(dest)}` })}
          </div>
          <div style={{ marginTop: 18 }}>
            <button className="btn btn--ghost" onClick={() => { setResult(null); clearHash() }}
                    data-testid="database-held-again"
                    style={{ fontSize: 14, borderRadius: 999 }}>
              {t('db.newSearch')}
            </button>
          </div>
        </div>
      )}

      {result && !held && !g && (
        <div className="card anim-rise" style={{ padding: 22, borderRadius: 20,
                                                 marginTop: 16, maxWidth: 560,
                                                 marginLeft: 'auto',
                                                 marginRight: 'auto' }}>
          <div style={{ fontSize: 14, color: NAVY, fontWeight: 700 }}>
            {t('db.noDecision')}
          </div>
          <button className="btn btn--ghost" style={{ marginTop: 12 }}
                  onClick={() => { setResult(null); clearHash() }}>{t('db.newSearch')}</button>
        </div>
      )}
    </div>
  )
}
