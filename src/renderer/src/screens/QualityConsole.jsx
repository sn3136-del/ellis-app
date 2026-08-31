// The Information Quality Control Backend — Trip.com's P0 deliverable.
//
// Their acceptance standard: "the availability of the information-quality-
// control backend is the premise for judging whether the delivery meets
// requirements". This is that surface, for their OPERATIONS team (not
// travellers): spot-check records by station / passport / destination /
// requirement with combined filters, see every record's 25-field checklist
// with per-field fill status, confidence level and clickable official
// source, flag an error into the tracked correction queue, watch the change
// log (add / modify / delete, field diffs), and export the dataset as the
// two-sheet Excel their spec defines. Reached via #ops; admin token only —
// the backend enforces it, this screen just speaks it.
//
// The spot-check accepts "China" as readily as "CHN": the country inputs
// autocomplete from the registry (name, alias or either ISO form) and the
// server resolves whatever is typed. The chrome translates with the app's
// language picker; record VALUES stay as stored — they are the dataset.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

// Console-wide motion and polish. Bars grow, numbers count, cards lift —
// all suppressed for readers who ask for reduced motion.
const OPS_CSS = `
@keyframes ops-fade-up { from { opacity: 0; transform: translateY(6px); }
                         to { opacity: 1; transform: none; } }
.ops-fade { animation: ops-fade-up .45s cubic-bezier(.2,.7,.3,1) both; }
.ops-bar { transition: width .9s cubic-bezier(.25,.8,.25,1); }
.ops-seg { transition: width .9s cubic-bezier(.25,.8,.25,1); }
.ops-lift { transition: box-shadow .18s ease, transform .18s ease; }
.ops-lift:hover { box-shadow: 0 6px 18px rgba(15,41,77,.10);
                  transform: translateY(-1px); }
.ops-row { transition: background .15s ease; }
.ops-diff { display: grid; grid-template-columns: minmax(86px, 130px) 1fr 1fr; }
.ops-diff-cell-label { display: none; }
@media (max-width: 540px) {
  /* The three-column comparison is unreadable at phone width: each row
     stacks, and the two value cells announce which side they are. */
  .ops-diff { grid-template-columns: 1fr; }
  .ops-diff-head { display: none; }
  .ops-diff-cell-label { display: inline; font-size: 10.5px; font-weight: 700;
                         color: #5b6a80; margin-right: 6px;
                         text-transform: uppercase; letter-spacing: .4px; }
}
/* Seven slice controls plus the export action = eight cells, laid out
   four-up so the block closes as two even rows. auto-fill left a lone
   control stranded on row two with the blue button floating beside it. */
.ops-filters { display: grid; gap: 10px 12px;
               grid-template-columns: repeat(4, minmax(0, 1fr)); }
.ops-filters-act { display: flex; align-items: flex-end; }
.ops-export-btn { max-width: 220px; }
@media (max-width: 1080px) {
  .ops-filters { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  /* Eight cells over three columns leaves the action cell alone on the
     last row; span it so it ends flush with the controls above. */
  .ops-filters-act { grid-column: 2 / -1; justify-content: flex-end; }
}
@media (max-width: 760px) {
  .ops-filters { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .ops-filters-act { grid-column: 1 / -1; }
}
@media (max-width: 460px) {
  .ops-filters { grid-template-columns: minmax(0, 1fr); }
  /* A 220px button pinned right leaves a dead gap on a phone. */
  .ops-export-btn { max-width: none; }
}
/* Six freshness tiles. auto-fit landed on four, leaving two adrift on a
   second row; fixed breakpoints keep every row full. */
.ops-tiles { display: grid; gap: 12px;
             grid-template-columns: repeat(6, minmax(0, 1fr)); }
@media (max-width: 1560px) {
  .ops-tiles { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (max-width: 820px) {
  .ops-tiles { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 420px) {
  .ops-tiles { grid-template-columns: minmax(0, 1fr); }
}
/* The seven-column record table cannot survive a phone. Under 760px each row
   becomes a stacked card: the header row is hidden, every cell announces its
   own label, and the route line stops being a column of single letters. */
@media (max-width: 760px) {
  .ops-rt thead { display: none; }
  .ops-rt, .ops-rt tbody, .ops-rt tr, .ops-rt td { display: block; width: 100%; }
  .ops-rt tr { border-bottom: 8px solid #f4f7fb; padding: 4px 0 10px; }
  .ops-rt td { border: none; padding: 5px 14px; text-align: left !important; }
  .ops-rt td[data-label]::before {
    content: attr(data-label); display: block; font-size: 9px; font-weight: 700;
    letter-spacing: .7px; text-transform: uppercase; color: #7A8798;
    margin-bottom: 2px;
  }
  .ops-rt td.ops-route { padding-top: 10px; }
  .ops-rt td.ops-route strong { font-size: 14px; white-space: normal; }
}
@media (prefers-reduced-motion: reduce) {
  .ops-fade { animation: none; }
  .ops-bar, .ops-seg, .ops-lift { transition: none; }
}`

function useCountUp(target, ms = 700) {
  const [v, setV] = useState(0)
  useEffect(() => {
    if (target == null) return
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
      setV(target); return
    }
    let raf; const t0 = performance.now()
    const tick = (t) => {
      const k = Math.min(1, (t - t0) / ms)
      setV(Math.round(target * (1 - Math.pow(1 - k, 3))))
      if (k < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [target, ms])
  return v
}

// Confidence is ORDERED data, so it wears a sequential single-hue ramp
// (validated: monotonic lightness, CVD-safe by construction), never
// green/amber/red side by side.
// The assurance ladder: verified by a person, read by the machine, not yet
// checked. It is a status scale, so it wears status colours - the same
// meaning the confidence grades carry. The old blue ramp's lightest step
// failed the palette check twice over: below the chroma floor (reading
// grey) and 1.21:1 against the surface. This trio passes every check in
// both themes, worst adjacent pair ΔE 28.3.
const SEQ = { high: '#0b7a44', medium: '#2563eb', low: '#d97706' }
import { createVisaClient } from '../lib/visaBackend.js'
import { newSession } from '../lib/visaSession.js'
import { useLocale } from '../lib/locale.jsx'
import { useLocalizedCountries } from '../lib/countryNames.js'

const NAVY = '#0f294d'
const BLUE = '#287dfa'
const GRAY = '#8592a6'
const GREEN = '#0a8a4a'
const AMBER = '#b26a00'
const RED = '#c62828'
const BG = '#f6f8fb'
const BORDER = '#e8edf4'

const CONF_COLOR = { High: GREEN, Medium: AMBER, Low: RED }
const PURPOSES = ['tourism', 'business', 'family_visit', 'study', 'work',
                  'transit', 'other']
const PURPOSE_KEY = { tourism: 'db.purpose.tourism', business: 'db.purpose.business',
                      family_visit: 'db.purpose.family', study: 'db.purpose.study',
                      work: 'db.purpose.work', transit: 'db.purpose.transit',
                      other: 'db.purpose.other' }

function useOpsClient() {
  return useMemo(() => {
    const s = newSession()
    // The ops surface authenticates as the operator; the backend refuses
    // reader tokens on every endpoint this screen calls.
    return createVisaClient({ ...s, token: 'admin-token' })
  }, [])
}

const card = { background: '#fff', border: `1px solid ${BORDER}`,
               borderRadius: 16, boxShadow: '0 1px 3px rgba(15,41,77,0.05)' }
// Soft-filled controls: quiet until touched, a ring when focused (the
// hover/focus states live in theme.css under .ops-in).
const input = { padding: '11px 14px', borderRadius: 12, fontSize: 13,
                border: '1px solid transparent', background: '#f2f6fb',
                color: NAVY, outline: 'none' }

function Chip({ children, color = GRAY, filled = true }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center',
                   padding: '2px 10px', borderRadius: 999, fontSize: 11,
                   fontWeight: 700, letterSpacing: 0.2, whiteSpace: 'nowrap',
                   color: filled ? '#fff' : color,
                   background: filled ? color : `${color}18`,
                   border: filled ? 'none' : `1px solid ${color}55` }}>
      {children}
    </span>
  )
}

function StatCell({ label, value, sub, pct, target, accent = NAVY, delay = 0 }) {
  const [w, setW] = useState(0)
  useEffect(() => {
    const id = setTimeout(() => setW(pct != null ? Math.max(2, Math.min(100, pct)) : 0), 80 + delay)
    return () => clearTimeout(id)
  }, [pct, delay])
  const n = useCountUp(typeof value === 'number' ? value : null)
  const hit = pct != null && target != null && pct >= target
  return (
    <div className="ops-fade" style={{ padding: '20px 24px', minWidth: 0,
                                       animationDelay: `${delay}ms` }}>
      <div style={{ fontSize: 10.5, fontWeight: 800, letterSpacing: 1,
                    color: GRAY, textTransform: 'uppercase',
                    whiteSpace: 'nowrap' }}>{label}</div>
      <div style={{ fontSize: 30, fontWeight: 700, marginTop: 8, lineHeight: 1,
                    fontVariantNumeric: 'tabular-nums',
                    color: pct == null ? accent : (hit ? GREEN : NAVY) }}>
        {typeof value === 'number' ? n.toLocaleString() : value}
      </div>
      {/* The meter carries the tile's own colour. A fixed blue bar under an
          amber STALE number said two different things about one figure, and
          the share it represents was left for the reader to divide out. */}
      {pct != null && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 9,
                      marginTop: 12 }}>
          <div style={{ flex: 1, height: 5, borderRadius: 999, minWidth: 0,
                        background: '#e8edf5', position: 'relative' }}>
            <div className="ops-bar"
                 style={{ position: 'absolute', left: 0, top: 0, bottom: 0,
                          width: `${w}%`, borderRadius: 999,
                          background: hit ? GREEN : accent }} />
            {target != null && (
              <div style={{ position: 'absolute', left: `${target}%`, top: -3,
                            bottom: -3, width: 2, background: NAVY,
                            borderRadius: 1, opacity: 0.45 }} />
            )}
          </div>
          <span style={{ fontSize: 11, fontWeight: 700, color: GRAY,
                         fontVariantNumeric: 'tabular-nums',
                         flexShrink: 0 }}>
            {pct < 1 && pct > 0 ? '<1' : Math.round(pct)}%
          </span>
        </div>
      )}
      {/* The caption wraps: a clipped "past their recheck win..." explains
          nothing. Two short lines beat one amputated one. */}
      <div style={{ fontSize: 11, color: GRAY, marginTop: 9,
                    lineHeight: 1.5 }}>{sub}</div>
    </div>
  )
}

function StatTile({ label, value, sub, accent = NAVY }) {
  return (
    <div style={{ ...card, padding: '16px 20px' }}>
      <div style={{ fontSize: 10.5, fontWeight: 800, letterSpacing: 1,
                    color: GRAY, textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 800, color: accent,
                    marginTop: 6, lineHeight: 1 }}>{value}</div>
      {sub && <div style={{ fontSize: 11.5, color: GRAY, marginTop: 6 }}>{sub}</div>}
    </div>
  )
}

/** Country input with autocomplete: type a name ("China", "中国-adjacent
 *  aliases resolve server-side too") or a code ("CN", "CHN"); pick from the
 *  dropdown. The FILTER value sent to the API is whatever is committed —
 *  the server resolves names and codes alike. */
function SuggestInput({ value, placeholder, options, onCommit, testid }) {
  // A plain input re-filtered 1,100 records on every keystroke, so the table
  // re-rendered mid-word and typing felt like it was fighting back. The box
  // now owns its own text, suggests from values that actually exist in the
  // data, and only commits when the typing stops or the reader picks one.
  const [q, setQ] = useState(value || '')
  const [open, setOpen] = useState(false)
  const [hi, setHi] = useState(0)
  const timer = useRef(null)
  const blur = useRef(null)
  useEffect(() => { setQ(value || '') }, [value])
  useEffect(() => () => { clearTimeout(timer.current); clearTimeout(blur.current) }, [])

  const matches = useMemo(() => {
    const s2 = q.trim().toLowerCase()
    if (!s2) return options.slice(0, 10)
    const starts = [], rest = []
    for (const o of options) {
      const l = o.toLowerCase()
      if (!l.includes(s2)) continue
      ;(l.startsWith(s2) ? starts : rest).push(o)
    }
    return [...starts, ...rest].slice(0, 10)
  }, [q, options])

  const commit = (v) => { clearTimeout(timer.current); onCommit(v) }
  const type = (v) => {
    setQ(v); setOpen(true); setHi(0)
    // Commit after a pause, so a half-typed word never filters the table.
    clearTimeout(timer.current)
    timer.current = setTimeout(() => onCommit(v), 320)
  }
  const pick = (o) => { setQ(o); setOpen(false); commit(o) }

  return (
    <div style={{ position: 'relative' }}
         onBlur={() => { blur.current = setTimeout(() => setOpen(false), 120) }}
         onFocus={() => clearTimeout(blur.current)}>
      <input value={q} className="ops-in" data-testid={testid}
             placeholder={placeholder} autoComplete="off"
             style={{ ...input, width: '100%', boxSizing: 'border-box' }}
             onFocus={() => setOpen(true)}
             onChange={(e) => type(e.target.value)}
             onKeyDown={(e) => {
               if (e.key === 'ArrowDown') { e.preventDefault(); setOpen(true); setHi((i) => Math.min(i + 1, matches.length - 1)) }
               else if (e.key === 'ArrowUp') { e.preventDefault(); setHi((i) => Math.max(i - 1, 0)) }
               else if (e.key === 'Enter') { e.preventDefault(); if (open && matches[hi]) pick(matches[hi]); else commit(q); setOpen(false) }
               else if (e.key === 'Escape') { setOpen(false) }
             }} />
      {q && (
        <button onMouseDown={(e) => { e.preventDefault(); setQ(''); commit('') }}
                aria-label="clear"
                style={{ position: 'absolute', right: 8, top: 8, border: 'none',
                         background: 'none', color: GRAY, cursor: 'pointer',
                         fontSize: 14, lineHeight: 1, padding: 2 }}>×</button>
      )}
      {open && matches.length > 0 && (
        <div style={{ position: 'absolute', zIndex: 40, left: 0, right: 0, top: '100%',
                      marginTop: 4, background: '#fff', border: `1px solid ${BORDER}`,
                      borderRadius: 10, boxShadow: '0 10px 26px rgba(15,41,77,.12)',
                      maxHeight: 260, overflowY: 'auto' }}>
          {matches.map((o, i) => (
            <div key={o} onMouseDown={(e) => { e.preventDefault(); pick(o) }}
                 onMouseEnter={() => setHi(i)}
                 style={{ padding: '8px 11px', fontSize: 12.5, cursor: 'pointer',
                          color: NAVY, background: i === hi ? '#eef4ff' : '#fff',
                          overflowWrap: 'anywhere' }}>{o}</div>
          ))}
        </div>
      )}
    </div>
  )
}

function CountryFilter({ value, placeholder, onCommit, countries }) {
  // The interaction model is the customer combo\'s, which is proven: the box
  // EMPTIES on focus so typing always starts fresh (the committed country
  // stays visible as the placeholder), suggestions appear once something is
  // typed, and blur resolves exactly once. While typing, the table follows
  // only RESOLVED text (an exact code, an exact name, or a single match);
  // half-typed words never commit, so the table cannot flash empty mid-word.
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const [hi, setHi] = useState(0)
  const typed = useRef(false)
  const blurTimer = useRef(null)
  const liveTimer = useRef(null)
  const committedLabel = (() => {
    const hit = value && countries.find((c) => c.value === value)
    return hit ? hit.label : (value || '')
  })()

  const resolve = (raw, { final }) => {
    const clean = String(raw || '').replace(/^[^\p{L}\p{N}]+/u, '').trim()
    if (!clean) return { kind: 'empty' }
    const lc = clean.toLowerCase()
    const codeHit = clean === clean.toUpperCase() &&
      countries.find((c) => c.value === clean.toUpperCase())
    const nameHit = countries.find((c) =>
      c.label.replace(/^[^\p{L}\p{N}]+/u, '').trim().toLowerCase() === lc)
    const pool = countries.filter((c) => c.search.includes(lc))
    const pick = codeHit || nameHit || (pool.length === 1 ? pool[0] : null)
    if (pick) return { kind: 'pick', pick }
    // Unknown text long enough to be a real name goes to the server\'s
    // resolver, but only as a FINAL act (blur/Enter) — never mid-word.
    if (final && pool.length === 0 && clean.length >= 4) {
      return { kind: 'raw', raw: clean }
    }
    return { kind: 'ambiguous' }
  }

  const matches = useMemo(() => {
    const s2 = q.trim().toLowerCase()
    if (!s2) return []
    const starts = [], rest = []
    for (const c of countries) {
      if (!c.search.includes(s2)) continue
      const name = c.label.replace(/^[^\p{L}\p{N}]+/u, '').toLowerCase()
      ;(name.startsWith(s2) || c.value.toLowerCase().startsWith(s2)
        ? starts : rest).push(c)
    }
    starts.sort((a, b) => a.label.length - b.label.length)
    return [...starts, ...rest].slice(0, 8)
  }, [q, countries])

  const choose = (c) => {
    if (liveTimer.current) clearTimeout(liveTimer.current)
    typed.current = false
    setQ(''); setOpen(false)
    onCommit(c.value)
  }
  const settle = () => {
    if (liveTimer.current) clearTimeout(liveTimer.current)
    const wasTyped = typed.current
    typed.current = false
    const r = resolve(q, { final: true })
    setQ(''); setOpen(false)
    if (r.kind === 'pick') onCommit(r.pick.value)
    else if (r.kind === 'raw') onCommit(r.raw)
    else if (r.kind === 'empty') {
      // Focusing empties the box too: only a DELIBERATE deletion clears
      // the committed filter.
      if (wasTyped && value) onCommit('')
    }
    // ambiguous: the committed filter stands; the label returns via q=''.
  }

  useEffect(() => () => {
    if (blurTimer.current) clearTimeout(blurTimer.current)
    if (liveTimer.current) clearTimeout(liveTimer.current)
  }, [])

  return (
    <div style={{ position: 'relative' }}>
      <input className="ops-in"
             value={open ? q : committedLabel}
             placeholder={committedLabel || placeholder}
             onFocus={() => {
               if (blurTimer.current) { clearTimeout(blurTimer.current); blurTimer.current = null }
               setOpen(true); setQ(''); setHi(0); typed.current = false
             }}
             onChange={(e) => {
               const v = e.target.value
               setQ(v); setHi(0); typed.current = true
               if (!open) setOpen(true)
               if (liveTimer.current) clearTimeout(liveTimer.current)
               liveTimer.current = setTimeout(() => {
                 const r = resolve(v, { final: false })
                 if (r.kind === 'pick') onCommit(r.pick.value)
                 else if (r.kind === 'empty' && value) onCommit('')
               }, 250)
             }}
             onKeyDown={(e) => {
               if (e.key === 'Escape') { setQ(''); setOpen(false); return }
               if (!open) return
               if (e.key === 'ArrowDown') { e.preventDefault(); setHi((h) => Math.min(h + 1, Math.max(0, matches.length - 1))) }
               else if (e.key === 'ArrowUp') { e.preventDefault(); setHi((h) => Math.max(h - 1, 0)) }
               else if (e.key === 'Enter') {
                 e.preventDefault()
                 if (matches.length > 0 && q.trim()) choose(matches[Math.min(hi, matches.length - 1)])
                 else settle()
               }
             }}
             onBlur={() => { blurTimer.current = setTimeout(settle, 150) }}
             style={{ ...input, width: '100%', boxSizing: 'border-box',
                      paddingRight: 30 }} />
      {(value || '') !== '' && !open && (
        <button onMouseDown={(e) => e.preventDefault()}
                onClick={() => { typed.current = false; setQ(''); onCommit('') }}
                title={placeholder}
                style={{ position: 'absolute', right: 4, top: '50%',
                         transform: 'translateY(-50%)', border: 'none',
                         background: 'transparent', color: GRAY,
                         cursor: 'pointer', fontSize: 14,
                         padding: '8px 9px', lineHeight: 1 }}>×</button>
      )}
      {open && matches.length > 0 && (
        <div style={{ position: 'absolute', top: '110%', left: 0, zIndex: 30,
                      minWidth: 210, ...card, padding: 6, maxHeight: 260,
                      overflowY: 'auto' }}>
          {matches.map((c, i) => (
            <div key={c.value}
                 onMouseDown={(e) => { e.preventDefault(); choose(c) }}
                 onMouseEnter={() => setHi(i)}
                 style={{ padding: '8px 10px', borderRadius: 8, fontSize: 13,
                          color: NAVY, cursor: 'pointer', display: 'flex',
                          justifyContent: 'space-between', gap: 10,
                          background: i === hi ? BG : 'transparent' }}>
              <span>{c.label}</span>
              <span style={{ color: GRAY, fontSize: 12 }}>{c.value}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function MissingLine({ missing, t }) {
  const [open, setOpen] = useState(false)
  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ fontSize: 12, color: RED, display: 'flex',
                    alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
        <span>{t('ops.missingRequired')}: {missing.map((f) => fx(t, f)).join(', ')}</span>
        <button onClick={(e) => { e.stopPropagation(); setOpen((o) => !o) }}
                aria-label="why" data-testid="ops-missing-info"
                style={{ width: 16, height: 16, borderRadius: '50%',
                         border: `1px solid ${GRAY}`, background: '#fff',
                         color: GRAY, fontSize: 10, fontWeight: 800,
                         lineHeight: 1, cursor: 'pointer', padding: 0 }}>
          i
        </button>
      </div>
      {open && (
        <div style={{ marginTop: 8, padding: '10px 14px', borderRadius: 10,
                      background: '#f4f6fa', border: `1px solid ${BORDER}`,
                      fontSize: 12, color: NAVY, lineHeight: 1.6,
                      maxWidth: 620 }}>
          {t('ops.missingWhy')}
        </div>
      )}
    </div>
  )
}

// Translated label for one of the 25 T-Station field keys; falls back to the
// humanized key so an unmapped field is still readable, never snake_case.
function fx(t, f) {
  const k = 'ops.fx.' + f
  return t(k) !== k ? t(k) : f.replace(/_/g, ' ')
}

function FieldGrid({ rec, t, typeNames = {}, tvv = (x) => x }) {
  const UNIT = { Day: t('ops.u.day'), Hour: t('ops.u.hour'),
                 Month: t('ops.u.month'), Year: t('ops.u.year'),
                 'Calendar Day': t('ops.u.calDay'),
                 'Working Day': t('ops.u.workDay') }
  const METHOD = { 'Online Application': t('ops.ch.online'),
                   'Embassy Submission': t('ops.ch.embassy'),
                   'On-arrival Processing': t('ops.ch.arrival'),
                   'Agency Service': t('ops.m.agency'),
                   Other: t('ops.m.other') }
  const REQV = { 'Visa-free': t('ops.req.free'),
                 'Visa on Arrival': t('ops.req.voa'),
                 'Visa Required in Advance': t('ops.req.advance'),
                 Conditional: t('ops.req.conditional') }
  const ENTRIES = { Single: t('ops.e.single'), Multiple: t('ops.e.multiple'),
                    Unlimited: t('ops.e.unlimited') }
  // Unit and currency ride WITH their value: a reader meets "90 days",
  // never a naked "90" whose unit lives two tiles away.
  const PAIR = { validity_duration: 'validity_unit',
                 max_stay_duration: 'max_stay_unit',
                 processing_min_days: 'processing_unit',
                 visa_fee_amount: 'visa_fee_currency' }
  const PAIRED = new Set(Object.values(PAIR))
  const show = (f) => {
    const v = rec[f]
    if (v == null || v === '') return '·'
    if (f === 'data_source') {
      if (v === 'Ellis source audit') return t('ops.src.audit')
      if (v === 'Ellis verified route engine') return t('ops.src.engine')
      return String(v)
    }
    if (PAIR[f]) {
      const u = rec[PAIR[f]]
      const uv = u == null || u === '' ? ''
        : (f === 'visa_fee_amount' ? String(u) : (UNIT[u] || String(u)))
      return `${v} ${uv}`.trim()
    }
    if (f === 'travel_document_type') {
      const k = 'db.doc.' + v
      return t(k) !== k ? t(k) : String(v).replace(/_/g, ' ')
    }
    if (f === 'travel_purpose') {
      const k = PURPOSE_KEY[v]
      return k && t(k) !== k ? t(k) : String(v)
    }
    if (f === 'visa_requirement') return REQV[v] || String(v)
    if (f === 'visa_type_name') return typeNames[v] || String(v)
    if (f === 'validity_unit' || f === 'max_stay_unit' ||
        f === 'processing_unit') return UNIT[v] || String(v)
    if (f === 'entries') return ENTRIES[v] || String(v)
    if (f === 'application_method') return METHOD[v] || String(v)
    if (f === 'confidence_level') {
      const k = 'ops.conf.' + String(v).toLowerCase()
      return t(k) !== k ? t(k) : String(v)
    }
    return String(v)
  }
  return (
    <div style={{ display: 'grid', gap: 8, marginTop: 4,
                  gridTemplateColumns: 'repeat(auto-fill, minmax(215px, 1fr))' }}>
      {/* Label-over-value tiles: every value wraps in full. An operator
          checking a record must never meet "Ordinary p..." where the fact
          should be. */}
      {Object.entries(rec.field_status)
        .filter(([f]) => !PAIRED.has(f))
        .map(([f, st]) => (
        <div key={f} style={{ background: '#fff', border: '1px solid #eef2f8',
                              borderRadius: 10, padding: '8px 12px',
                              minWidth: 0 }}>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <span style={{ color: st === 'missing' ? RED
                             : st === 'filled' ? GREEN
                             : st === 'pending-review' ? AMBER
                             : st === 'not-published' ? '#7A8798' : '#c3ccd9',
                           fontWeight: 700, fontSize: 11, flexShrink: 0 }}
                  title={st === 'pending-review' ? t('ops.pendingReview') : undefined}>
              {st === 'missing' ? '✗' : st === 'filled' ? '✓'
                : st === 'pending-review' ? '?'
                : st === 'not-published' ? '—' : '·'}
            </span>
            <span title={f}
                  style={{ color: GRAY, fontSize: 10.5, fontWeight: 700,
                           letterSpacing: 0.3,
                           textTransform: 'uppercase' }}>{fx(t, f)}</span>
          </div>
          <div style={{ color: st === 'missing' ? AMBER
                          : st === 'optional-empty' ? GRAY : NAVY,
                        fontWeight: 600, fontSize: 12.5,
                        marginTop: 4, lineHeight: 1.45,
                        fontStyle: (st === 'missing' || st === 'optional-empty')
                          && (rec[f] == null || rec[f] === '')
                          ? 'italic' : 'normal',
                        overflowWrap: 'anywhere' }}>
            {(rec[f] == null || rec[f] === '')
              ? (st === 'missing' ? t('ops.missingCounts')
                 : st === 'not-published' ? t('ops.notPublished')
                 : st === 'not-applicable' ? t('ops.notApplicable')
                 : st === 'optional-empty' ? t('ops.notPublished') : tvv(show(f)))
              : /^https?:\/\//.test(String(rec[f]))
                /* A source is only traceable if it can be OPENED: the
                   acceptance standard asks for a clickable source. */
                ? <a href={String(rec[f])} target="_blank" rel="noreferrer"
                     onClick={(e) => e.stopPropagation()}
                     style={{ color: BLUE, fontWeight: 600,
                              textDecoration: 'underline',
                              overflowWrap: 'anywhere' }}>
                    {show(f)} ↗
                  </a>
                : tvv(show(f))}
          </div>
        </div>
      ))}
      {/* §4.2.1's cross-validation, one URL per source. Field 22 holds a
          single source_url by their dictionary, so a route checked against
          three ministries could show one of them and the rest were
          unauditable. These sit beside the 25 fields, each clickable. */}
      {(rec.corroborating_sources || []).length > 0 && (
        <div style={{ gridColumn: '1 / -1', background: '#fff',
                      border: '1px solid #eef2f8', borderRadius: 10,
                      padding: '10px 12px', minWidth: 0 }}>
          <div style={{ fontSize: 10, fontWeight: 800, letterSpacing: 0.7,
                        textTransform: 'uppercase', color: GRAY }}>
            {t('ops.crossChecked')}
          </div>
          <ul style={{ margin: '7px 0 0', paddingLeft: 16, fontSize: 12,
                       color: NAVY, lineHeight: 1.5 }}>
            {(rec.corroborating_sources || []).map((c, i) => (
              <li key={i} style={{ marginBottom: 5, overflowWrap: 'anywhere' }}>
                <a href={c.url} target="_blank" rel="noreferrer"
                   style={{ color: BLUE, fontWeight: 600 }}>
                  {c.authority || siteOf(c.url)} ↗
                </a>
                {c.quote ? <span style={{ color: GRAY }}> — “{c.quote}”</span> : null}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function siteOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, '') } catch { return url }
}

const CONF_RANK = { High: 3, Medium: 2, Low: 1 }
const CHECK_RANK = { 'human-quote': 3, 'grounded-consistent': 2,
                     reference: 1, unchecked: 0 }

function SortHeader({ label, k, sort, onSort, align = 'left', width }) {
  const active = sort.key === k
  return (
    <th onClick={() => onSort(k)}
        style={{ padding: '10px 12px', fontSize: 10.5, fontWeight: 800,
                 letterSpacing: 0.8, textTransform: 'uppercase',
                 color: active ? NAVY : GRAY, textAlign: align, width,
                 cursor: 'pointer', userSelect: 'none',
                 whiteSpace: 'nowrap', position: 'sticky', top: 0,
                 background: '#fff', zIndex: 5,
                 borderBottom: `2px solid ${active ? BLUE : BORDER}` }}>
      {label}{active ? (sort.dir === 1 ? ' ↑' : ' ↓') : ''}
    </th>
  )
}

function FlagForm({ rec, onFlag, t }) {
  const [note, setNote] = useState('')
  const [sent, setSent] = useState(false)
  if (sent) {
    return <div style={{ fontSize: 12.5, color: GREEN, marginTop: 10 }}>
      ✓ {t('ops.flagged')}</div>
  }
  return (
    <div style={{ display: 'flex', gap: 8, marginTop: 12, maxWidth: 560 }}>
      <input value={note} placeholder={t('ops.flagPlaceholder')}
             onChange={(e) => setNote(e.target.value)}
             className="ops-in"
             style={{ ...input, flex: 1 }} data-testid="ops-flag-note" />
      <button className="btn btn--sm" data-testid="ops-flag"
              disabled={!note.trim()}
              style={{ borderRadius: 999, fontSize: 12, background: NAVY,
                       color: '#fff', opacity: note.trim() ? 1 : 0.5 }}
              onClick={async () => { await onFlag(rec, note.trim()); setSent(true) }}>
        ⚑ {t('ops.flagSubmit')}
      </button>
    </div>
  )
}

function RecordsTable({ records, total, onFlag, onRelease, t, flagOf, typeNames = {}, tvv = (x) => x }) {
  const [sort, setSort] = useState({ key: 'route', dir: 1 })
  const [open, setOpen] = useState(null)
  const onSort = (k) => setSort((s0) => ({ key: k, dir: s0.key === k ? -s0.dir : 1 }))
  const val = (r, k) => {
    if (k === 'route') return `${r.travel_document_country}${r.destination_country}`
    if (k === 'confidence') return CONF_RANK[r.confidence_level] || 0
    if (k === 'check') return CHECK_RANK[r.source_check] ?? 0
    if (k === 'stay') return r.max_stay_duration ?? -1
    if (k === 'fee') return r.visa_fee_amount ?? -1
    if (k === 'complete') return r.completeness
    if (k === 'requirement') return r.visa_requirement || ''
    if (k === 'type') return r.visa_type_name || ''
    return ''
  }
  const sorted = useMemo(() => [...records].sort((a, b) => {
    const x = val(a, sort.key), y = val(b, sort.key)
    return (x < y ? -1 : x > y ? 1 : 0) * sort.dir
  }), [records, sort])
  const REQ = {
    'Visa-free': [t('ops.req.free'), GREEN],
    'Visa on Arrival': [t('ops.req.voa'), AMBER],
    'Visa Required in Advance': [t('ops.req.advance'), NAVY],
    Conditional: [t('ops.req.conditional'), AMBER],
  }
  const CHECKS = {
    'human-quote': [t('ops.check.quoted'), GREEN, t('ops.tip.quoted')],
    'grounded-consistent': [t('ops.check.grounded'), BLUE, t('ops.tip.grounded')],
    reference: [t('ops.check.reference'), GRAY, t('ops.tip.reference')],
    unchecked: [t('ops.check.none'), RED, t('ops.tip.none')],
  }
  return (
    <div style={{ ...card, overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10,
                    padding: '12px 16px', flexWrap: 'wrap',
                    borderBottom: `1px solid ${BORDER}` }}>
        <strong style={{ color: NAVY, fontSize: 13 }}>
          {(total ?? records.length).toLocaleString()} {t('ops.items')}
        </strong>
        <span style={{ color: GRAY, fontSize: 12 }}>{t('ops.rowsHint')}</span>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table className="ops-rt"
               style={{ width: '100%', borderCollapse: 'collapse',
                        tableLayout: 'fixed', fontSize: 13 }}>
          <thead>
            <tr>
              <SortHeader label={t('ops.col.route')} k="route" sort={sort} onSort={onSort} width="15%" />
              <SortHeader label={t('ops.col.requirement')} k="requirement" sort={sort} onSort={onSort} width="11%" />
              <SortHeader label={t('ops.col.type')} k="type" sort={sort} onSort={onSort} width="24%" />
              <SortHeader label={t('ops.col.stay')} k="stay" sort={sort} onSort={onSort} align="right" width="9%" />
              <SortHeader label={t('ops.col.fee')} k="fee" sort={sort} onSort={onSort} align="right" width="11%" />
              <SortHeader label={t('ops.col.quality')} k="check" sort={sort} onSort={onSort} width="14%" />
              <th style={{ position: 'sticky', top: 0, background: '#fff',
                           zIndex: 5, borderBottom: `2px solid ${BORDER}`,
                           padding: '10px 12px', fontSize: 10.5,
                           fontWeight: 800, letterSpacing: 0.8,
                           textTransform: 'uppercase', color: GRAY,
                           whiteSpace: 'nowrap', width: '16%',
                           textAlign: 'left' }}>
                {t('ops.col.site')}
              </th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((rec, i) => {
              const id = rec.cache_key + (rec.visa_type_name || '') + i
              const [reqLabel, reqColor] = REQ[rec.visa_requirement] || ['·', GRAY]
              const [checkLabel, checkColor, checkTip] = CHECKS[rec.source_check] || CHECKS.reference
              const missing = Object.entries(rec.field_status)
                .filter(([, v]) => v === 'missing').map(([k]) => k)
              const opened = open === id
              const confKey = 'ops.conf.' + String(rec.confidence_level || '').toLowerCase()
              const confLabel = t(confKey) !== confKey ? t(confKey) : rec.confidence_level
              const pctDone = Math.round(rec.completeness * 100)
              const held = rec.confidence_level === 'Low' && !rec.operator_released
              return [
                <tr key={id} onClick={() => setOpen(opened ? null : id)}
                    style={{ cursor: 'pointer',
                             background: opened ? '#f4f8ff'
                               : i % 2 ? '#fbfcfe' : '#fff' }}>
                  {/* The pair itself never wraps; the purpose line under it
                      does. Held together, a long document type ("Tourism ·
                      Refugee travel document") burst the column. */}
                  <td className="ops-cell ops-route"
                      style={{ padding: '10px 12px' }}>
                    <span style={{ display: 'inline-block', color: '#9aa8bd',
                                   fontSize: 10, marginRight: 7,
                                   transition: 'transform .15s ease',
                                   transform: opened ? 'rotate(90deg)' : 'none' }}>
                      ▶
                    </span>
                    <strong style={{ color: NAVY, whiteSpace: 'nowrap' }}>
                      {flagOf(rec.travel_document_country)} {rec.travel_document_country}
                      {' → '}
                      {flagOf(rec.destination_country)} {rec.destination_country}
                    </strong>
                    <div style={{ color: GRAY, fontSize: 11, paddingLeft: 17,
                                  lineHeight: 1.45, overflowWrap: 'anywhere' }}>
                      {t(PURPOSE_KEY[rec.travel_purpose] || '') || rec.travel_purpose}
                      {rec.travel_document_type !== 'ordinary_passport'
                        ? ' · ' + ((t('db.doc.' + rec.travel_document_type)
                            !== 'db.doc.' + rec.travel_document_type)
                            ? t('db.doc.' + rec.travel_document_type)
                            : rec.travel_document_type.replace(/_/g, ' '))
                        : ''}
                    </div>
                  </td>
                  <td className="ops-cell" data-label={t('ops.col.requirement')}
                      style={{ padding: '10px 12px' }}>
                    <Chip color={reqColor} filled={false}>{reqLabel}</Chip>
                  </td>
                  <td className="ops-cell" data-label={t('ops.col.type')}
                      style={{ padding: '10px 12px', color: NAVY, fontWeight: 600,
                               lineHeight: 1.45, overflowWrap: 'anywhere' }}>
                    {typeNames[rec.visa_type_name] || rec.visa_type_name || '·'}
                  </td>
                  <td className="ops-cell" data-label={t('ops.col.stay')}
                      style={{ padding: '10px 12px', textAlign: 'right',
                               color: NAVY, whiteSpace: 'nowrap',
                               fontVariantNumeric: 'tabular-nums' }}>
                    {rec.max_stay_duration != null
                      ? `${rec.max_stay_duration} ${rec.max_stay_unit === 'Hour'
                          ? t('ops.u.hour') : t('ops.u.day')}`
                      : '·'}
                  </td>
                  <td className="ops-cell" data-label={t('ops.col.fee')}
                      style={{ padding: '10px 12px', textAlign: 'right',
                               color: NAVY, whiteSpace: 'nowrap',
                               fontVariantNumeric: 'tabular-nums' }}>
                    {rec.visa_fee_amount != null
                      ? `${rec.visa_fee_amount} ${rec.visa_fee_currency || ''}`
                      : '·'}
                  </td>
                  <td className="ops-cell" data-label={t('ops.col.quality')}
                      style={{ padding: '10px 12px' }}>
                    <span title={checkTip} style={{ cursor: 'help' }}>
                      <Chip color={checkColor} filled={false}>{checkLabel}</Chip>
                    </span>
                    {/* The sub-line appears only when it says something: a
                        confidence below High, or an incomplete record. A row
                        that is High and 100% needs no extra annotation. */}
                    {(rec.confidence_level !== 'High' || pctDone < 100) && (
                      <div style={{ display: 'flex', alignItems: 'center',
                                    gap: 5, marginTop: 4, fontSize: 10.5,
                                    color: GRAY }}>
                        {rec.confidence_level !== 'High' && (
                          <span>{t('ops.col.confidence')} {confLabel}</span>
                        )}
                        {rec.confidence_level !== 'High' && pctDone < 100 && (
                          <span style={{ color: '#c3cddd' }}>·</span>
                        )}
                        {pctDone < 100 && (
                          <span style={{ fontWeight: 700, color: AMBER }}>
                            {pctDone}%
                          </span>
                        )}
                      </div>
                    )}
                  </td>
                  {/* One column, one question: is this record in front of
                      customers? A held record answers no and offers the one
                      control that changes it. The source link sits under the
                      answer rather than beside it, which is what jammed the
                      link, the chip and the button onto the same short line. */}
                  <td className="ops-cell" data-label={t('ops.col.site')}
                      style={{ padding: '10px 12px', verticalAlign: 'top' }}>
                    <div style={{ display: 'flex', flexDirection: 'column',
                                  alignItems: 'flex-start', gap: 6 }}>
                      {held ? (
                        <>
                          <span title={t('ops.heldTip')}
                                style={{ display: 'inline-flex', gap: 5,
                                         alignItems: 'center',
                                         fontSize: 11, fontWeight: 700,
                                         color: '#9a5b00',
                                         background: '#fdf3e2',
                                         borderRadius: 999,
                                         padding: '3px 10px',
                                         whiteSpace: 'nowrap',
                                         cursor: 'help' }}>
                            <span style={{ width: 6, height: 6, borderRadius: 3,
                                           background: AMBER, flexShrink: 0 }} />
                            {t('ops.heldChip')}
                          </span>
                          <button onClick={(e) => { e.stopPropagation(); onRelease(rec) }}
                                  data-testid="ops-release"
                                  title={t('ops.heldTip')}
                                  style={{ border: `1px solid ${GREEN}`,
                                           background: '#fff', color: GREEN,
                                           borderRadius: 8, fontSize: 11,
                                           fontWeight: 700, padding: '4px 12px',
                                           whiteSpace: 'nowrap',
                                           cursor: 'pointer' }}>
                            {t('ops.releaseAction')}
                          </button>
                        </>
                      ) : (
                        <span style={{ display: 'inline-flex', gap: 5,
                                       alignItems: 'center', fontSize: 11,
                                       fontWeight: 700, color: '#1c6b45',
                                       whiteSpace: 'nowrap' }}>
                          <span style={{ width: 6, height: 6, borderRadius: 3,
                                         background: GREEN, flexShrink: 0 }} />
                          {t('ops.liveChip')}
                        </span>
                      )}
                      {rec.source_url && (
                        <a href={rec.source_url} target="_blank" rel="noreferrer"
                           onClick={(e) => e.stopPropagation()}
                           style={{ fontSize: 11.5, color: BLUE,
                                    fontWeight: 700, whiteSpace: 'nowrap' }}>
                          {t('ops.source')} ↗
                        </a>
                      )}
                    </div>
                  </td>
                </tr>,
                opened && (
                  <tr key={id + ':detail'}>
                    <td colSpan={7} style={{ background: '#fbfcfe',
                        borderBottom: `1px solid ${BORDER}`,
                        padding: '14px 18px' }}>
                      {/* Pinned to the viewport: without this the 25-field
                          card and the flag form span the table's full
                          scroll width and a phone only ever sees a third. */}
                      <div className="ops-rowdetail">
                        <FieldGrid rec={rec} t={t} typeNames={typeNames}
                                   tvv={tvv} />
                        {missing.length > 0 && (
                          <MissingLine missing={missing} t={t} />
                        )}
                        <FlagForm rec={rec} onFlag={onFlag} t={t} />
                      </div>
                    </td>
                  </tr>
                ),
              ]
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function IssueActions({ issue, onResolve, t }) {
  const [res, setRes] = useState('')
  return (
    <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
      <input value={res} placeholder={t('ops.resolutionPlaceholder')}
             onChange={(e) => setRes(e.target.value)}
             className="ops-in"
             style={{ ...input, flex: 1, minWidth: 220 }}
             data-testid="ops-resolution" />
      <button disabled={!res.trim()} data-testid="ops-corrected"
              title={t('ops.correctedHint')}
              onClick={() => onResolve(issue.id, 'corrected', res.trim())}
              style={{ borderRadius: 999, fontSize: 12.5, fontWeight: 700,
                       border: 'none', cursor: 'pointer', padding: '11px 18px',
                       background: GREEN, color: '#fff',
                       opacity: res.trim() ? 1 : 0.5 }}>
        {t('ops.markCorrected')}
      </button>
      <button disabled={!res.trim()} data-testid="ops-dismiss"
              title={t('ops.dismissHint')}
              onClick={() => onResolve(issue.id, 'dismissed', res.trim())}
              style={{ borderRadius: 999, fontSize: 12.5, fontWeight: 700,
                       border: `1px solid ${GRAY}`, cursor: 'pointer',
                       padding: '11px 18px', background: '#fff', color: GRAY,
                       opacity: res.trim() ? 1 : 0.5 }}>
        {t('ops.dismiss')}
      </button>
      <div style={{ flexBasis: '100%', fontSize: 11, color: GRAY,
                    lineHeight: 1.5 }}>
        {t('ops.correctedHint')} · {t('ops.dismissHint')}
      </div>
    </div>
  )
}

function BandCell({ children, delay = 0, first = false }) {
  return (
    <div className="ops-fade band-cell"
         style={{ padding: '22px 26px', minWidth: 0,
                  borderLeft: first ? 'none' : `1px solid ${BORDER}`,
                  animationDelay: `${delay}ms` }}>
      {children}
    </div>
  )
}

function BandLabel({ children }) {
  return (
    <div style={{ fontSize: 10.5, fontWeight: 800, letterSpacing: 1,
                  color: GRAY, textTransform: 'uppercase',
                  whiteSpace: 'nowrap' }}>{children}</div>
  )
}

function BandBig({ children, color = NAVY, of = null }) {
  return (
    <div style={{ marginTop: 10, lineHeight: 1.05, display: 'flex',
                  alignItems: 'baseline', gap: 7, flexWrap: 'wrap' }}>
      <span style={{ fontSize: 30, fontWeight: 700, color,
                     fontVariantNumeric: 'tabular-nums',
                     letterSpacing: '-0.5px' }}>{children}</span>
      {of && (
        <span style={{ fontSize: 13, fontWeight: 600, color: GRAY,
                       fontVariantNumeric: 'tabular-nums' }}>{of}</span>
      )}
    </div>
  )
}

// Rounding each share on its own makes them sum to 101. Largest remainder
// keeps every figure within a point of its true value AND the column adding
// to exactly 100, which is what a reader checks first.
function shares(values, total) {
  if (!total) return values.map(() => 0)
  const exact = values.map((v) => (v / total) * 100)
  const floor = exact.map(Math.floor)
  let left = 100 - floor.reduce((a, b) => a + b, 0)
  const order = exact.map((v, i) => [v - Math.floor(v), i])
                     .sort((a, b) => b[0] - a[0])
  const out = floor.slice()
  for (const [, i] of order) { if (left <= 0) break; out[i] += 1; left -= 1 }
  return out
}

// Value against a target, drawn as a meter rather than a dial: the bar
// language matches the splits beside it, the shortfall is a visible gap,
// and the target is a mark on the same scale instead of a second ring.
function Meter({ pct, target, hitColor = GREEN, targetLabel, valueTitle }) {
  const [go, setGo] = useState(false)
  useEffect(() => { const id = setTimeout(() => setGo(true), 140)
    return () => clearTimeout(id) }, [])
  const val = Math.max(0, Math.min(100, pct || 0))
  const hit = target != null && val >= target
  const color = hit ? hitColor : '#2563eb'
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span title={valueTitle || undefined}
              style={{ fontSize: 30, fontWeight: 700, color,
                       fontVariantNumeric: 'tabular-nums',
                       letterSpacing: '-0.6px', lineHeight: 1,
                       cursor: valueTitle ? 'help' : 'default' }}>
          {val.toFixed(val < 100 ? 1 : 0)}%
        </span>
      </div>
      <div style={{ position: 'relative', height: 10, marginTop: 11,
                    borderRadius: 999, background: '#eef2f8' }}>
        <div style={{ position: 'absolute', inset: 0, width: go ? `${val}%` : '0%',
                      background: color, borderRadius: 999,
                      transition: 'width .9s cubic-bezier(.22,.8,.28,1)' }} />
        {target != null && (
          <div title={targetLabel || t0(target)}
               style={{ position: 'absolute', left: `${target}%`, top: -3,
                        bottom: -3, width: 2, borderRadius: 1,
                        background: NAVY, opacity: 0.55 }} />
        )}
      </div>
    </div>
  )
}
const t0 = (n) => `${n}%`

function MicroStack({ segs, height = 10, legend = true }) {
  const [go, setGo] = useState(false)
  const [hi, setHi] = useState(-1)
  useEffect(() => { const id = setTimeout(() => setGo(true), 140)
    return () => clearTimeout(id) }, [])
  const total = segs.reduce((a, [n]) => a + n, 0) || 1
  const shown = segs.filter(([n]) => n > 0)
  const pcts = shares(shown.map(([n]) => n), total)
  return (
    <div>
      {/* One bar, parts separated by the surface itself rather than by a
          stroke: a 2px gap of the card colour reads cleaner than a border
          and keeps the segment colours honest against each other. */}
      <div style={{ display: 'flex', height, borderRadius: 999,
                    overflow: 'hidden', background: '#eef2f8', gap: 2 }}>
        {shown.map(([n, color, name], i) => (
          <div key={i} title={`${name}: ${n.toLocaleString()} (${Math.round((n / total) * 100)}%)`}
               onMouseEnter={() => setHi(i)} onMouseLeave={() => setHi(-1)}
               className="ops-seg"
               style={{ width: go ? `${(n / total) * 100}%` : '0%',
                        background: color, borderRadius: 3,
                        opacity: hi === -1 || hi === i ? 1 : 0.35,
                        transition: 'width .9s cubic-bezier(.22,.8,.28,1), opacity .15s ease',
                        cursor: 'default' }} />
        ))}
      </div>
      {legend && (
        <div style={{ display: 'grid', gap: 4, marginTop: 9, fontSize: 11 }}>
          {shown.map(([n, color, name, tip], i) => (
            <div key={i}
                 onMouseEnter={() => setHi(i)} onMouseLeave={() => setHi(-1)}
                 style={{ display: 'flex', alignItems: 'center', gap: 7,
                          color: GRAY,
                          opacity: hi === -1 || hi === i ? 1 : 0.45,
                          transition: 'opacity .15s ease' }}>
              <span style={{ width: 8, height: 8, borderRadius: 2.5,
                             background: color, flexShrink: 0 }} />
              <span title={tip || undefined}
                    style={{ flex: 1, minWidth: 0, overflow: 'hidden',
                             textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                             cursor: tip ? 'help' : 'default' }}>{name}</span>
              <strong style={{ color: NAVY, fontVariantNumeric: 'tabular-nums' }}>
                {n.toLocaleString()}
              </strong>
              <span style={{ width: 30, textAlign: 'right', color: '#9aa8bd',
                             fontVariantNumeric: 'tabular-nums' }}>
                {pcts[i]}%
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const PAGE = 50

function useTypeNames(client, data, lang) {
  const [map, setMap] = useState({})
  useEffect(() => {
    const records = data?.records || []
    if (lang === 'en' || !records.length) {
      setMap((m) => (Object.keys(m).length ? {} : m))
      return
    }
    const names = [...new Set(records.map((r) => r.visa_type_name)
      .filter((v) => v && /[A-Za-z]{3}/.test(v)))].slice(0, 120)
    if (!names.length) {
      setMap((m) => (Object.keys(m).length ? {} : m))
      return
    }
    const entries = {}
    names.forEach((n, i) => { entries['n' + i] = n })
    let live = true
    client.i18nCatalog(lang, entries).then((out) => {
      if (!live || !out?.entries) return
      const m = {}
      names.forEach((n, i) => { const v = out.entries['n' + i]; if (v) m[n] = v })
      setMap(m)
    }).catch(() => { /* English stays */ })
    return () => { live = false }
  }, [client, data, lang])
  return map
}

/** Dynamic DATA values (record content, the monitor's page readings, reader
 *  notes) arrive from the engine in English. This translates them into the
 *  UI language through the server's masked, cached catalog: batched, cached
 *  in localStorage per language, English shown only until the translation
 *  lands, and NEVER fabricated (the server returns strings it cannot
 *  round-trip unchanged). Verbatim source quotes are exempt: they are
 *  evidence and stay in the page's own words. */
function useValueTranslations(client, lang) {
  const LSK = `ellis.opsvals.v2.${lang}`
  const [map, setMap] = useState(() => {
    if (lang === 'en') return {}
    try { return JSON.parse(localStorage.getItem(LSK) || '{}') } catch { return {} }
  })
  const queue = useRef(new Set())
  const flushRef = useRef(() => {})
  const inflight = useRef(new Set())
  const timer = useRef(null)
  useEffect(() => {
    if (lang === 'en') { setMap({}); return }
    try { setMap(JSON.parse(localStorage.getItem(LSK) || '{}')) } catch { setMap({}) }
  }, [lang])  // eslint-disable-line react-hooks/exhaustive-deps
  const flush = useCallback(() => {
    const batch = [...queue.current].slice(0, 120)
    if (!batch.length) return
    batch.forEach((v) => { queue.current.delete(v); inflight.current.add(v) })
    const entries = {}
    batch.forEach((v, i) => { entries['v' + i] = v })
    client.i18nCatalog(lang, entries).then((out) => {
      if (!out?.entries) return
      setMap((m) => {
        const next = { ...m }
        batch.forEach((v, i) => {
          const tr = out.entries['v' + i]
          if (tr && tr !== v) next[v.slice(0, 400)] = tr
        })
        try {
          const keys = Object.keys(next)
          localStorage.setItem(LSK, JSON.stringify(
            keys.length > 1500 ? {} : next))
        } catch { /* quota: cache resets next load */ }
        return next
      })
    }).catch(() => { /* English stays visible */ })
      .finally(() => {
        batch.forEach((v) => inflight.current.delete(v))
        // A page can queue more than one batch; without this the tail after
        // the first 120 strings stayed English until something re-rendered.
        if (queue.current.size) {
          if (timer.current) clearTimeout(timer.current)
          timer.current = setTimeout(flushRef.current, 200)
        }
      })
  }, [client, lang])  // eslint-disable-line react-hooks/exhaustive-deps
  flushRef.current = flush
  // A document checklist or a conditions paragraph can run past the
  // catalog's per-string cap; those are exactly the fields an operator must
  // read. Long values translate SEGMENT BY SEGMENT (sentence and list
  // boundaries) and are rejoined, so length never means untranslated.
  const CAP = 360
  const segment = (v) => {
    const out = []
    let rest = v
    while (rest.length > CAP) {
      const win = rest.slice(0, CAP)
      let cut = Math.max(win.lastIndexOf('. '), win.lastIndexOf('; '),
                         win.lastIndexOf(', '))
      if (cut < 60) cut = win.lastIndexOf(' ')
      if (cut < 60) cut = CAP
      out.push(rest.slice(0, cut + 1).trim())
      rest = rest.slice(cut + 1)
    }
    if (rest.trim()) out.push(rest.trim())
    return out
  }
  const want = useCallback((piece) => {
    if (!queue.current.has(piece) && !inflight.current.has(piece)) {
      queue.current.add(piece)
      if (timer.current) clearTimeout(timer.current)
      timer.current = setTimeout(flush, 350)
    }
  }, [flush])
  return useCallback((sIn) => {
    if (lang === 'en') return sIn
    const v = String(sIn ?? '')
    if (!v || v.length < 3) return sIn
    if (/^https?:\/\//.test(v)) return sIn
    if (/^[\d\s.,:;%/()+·-]*$/.test(v)) return sIn
    if (!/[A-Za-z]{2}/.test(v)) return sIn
    if (v.length <= CAP) {
      const hit = map[v]
      if (hit) return hit
      want(v)
      return sIn
    }
    const pieces = segment(v)
    const done = pieces.map((x) => map[x])
    pieces.forEach((x, i) => { if (!done[i]) want(x) })
    if (done.every(Boolean)) return done.join(' ')
    return sIn
  }, [lang, map, want])
}

const EMPTY_FILTERS = { nationality: '', destination: '', purpose: '',
                        requirement: '', confidence: '', visaType: '',
                        fieldMissing: '', document: '' }

export default function QualityConsole() {
  const client = useOpsClient()
  const { t, lang } = useLocale()
  const tv = useValueTranslations(client, lang)
  const [tab, setTab] = useState('records')
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const activeFilters = Object.values(filters).filter(Boolean).length
  const [reg, setReg] = useState(null)
  const [data, setData] = useState(null)
  // Suggestions come from the visa type names that actually exist, so the box
  // can never propose a filter that returns nothing.
  const documentOptions = useMemo(() => {
    const seen = new Set()
    for (const r of (data?.records || [])) {
      if (r.travel_document_type) seen.add(r.travel_document_type)
    }
    return [...seen].sort()
  }, [data])
  const visaTypeOptions = useMemo(() => {
    const seen = new Map()
    for (const r of (data?.records || [])) {
      const v = String(r.visa_type_name || '').trim()
      if (v) seen.set(v, (seen.get(v) || 0) + 1)
    }
    return [...seen.entries()].sort((a, b) => b[1] - a[1]).map(([v]) => v)
  }, [data])
  const [changes, setChanges] = useState(null)
  const [issues, setIssues] = useState(null)
  const [freshness, setFreshness] = useState(null)
  const [uptime, setUptime] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [shown, setShown] = useState(PAGE)
  const [changeFilter, setChangeFilter] = useState('')
  // Hoisted: hooks may not live inside the conditional band IIFE.
  const typeNames = useTypeNames(client, data, lang)

  useEffect(() => {
    let live = true
    client.snapshotRegistries().then((r) => { if (live) setReg(r) })
      .catch(() => { if (live) setReg({ countries: [] }) })
    return () => { live = false }
  }, [client])
  const countries = useLocalizedCountries(client, reg, lang)
  const flagOf = useMemo(() => {
    const m = {}
    for (const c of (reg?.countries || [])) m[c.alpha_3] = c.flag || ''
    return (code) => m[code] || ''
  }, [reg])

  // Human, translated names for the snapshot engine's machine field keys.
  // Used by the correction queue and the change log alike.
  const fieldLabel = useCallback((f) => {
    const M = {
      disposition: t('ops.col.requirement'),
      visa_category: t('ops.col.type'),
      permitted_stay: t('ops.col.stay'),
      permitted_stay_days: t('ops.col.stay'),
      government_fee: t('ops.col.fee'),
      confidence: t('ops.col.confidence'),
      application_channel: t('ops.f.channel'),
      application_channel_detail: t('ops.f.channelDetail'),
      official_portal_url: t('ops.f.portal'),
      requirement_detail: t('ops.f.reqDetail'),
      processing_time: t('ops.f.processing'),
      required_documents: t('ops.f.docs'),
      exceptions: t('ops.f.exceptions'),
      visa_products: t('ops.f.products'),
      operator_spot_check: t('ops.f.spotCheck'),
      arrival_card: t('ops.f.arrivalCard'),
      forms: t('ops.f.forms'),
    }
    const labels = String(f || '').split(',')
      .map((x) => M[x.trim()] || x.trim().replace(/_/g, ' '))
    return [...new Set(labels)].join(' · ')
  }, [t])
  // Translate the closed enum vocabularies the engine writes, so the change
  // log and the queue read in the operator's language instead of snake_case.
  const valueLabel = useCallback((field, v) => {
    if (v == null || typeof v !== 'string') return v
    const key = String(field || '').split(',')[0].trim()
    const MAPS = {
      disposition: {
        VISA_EXEMPT: t('ops.req.free'), VISA_ON_ARRIVAL: t('ops.req.voa'),
        ELECTRONIC_AUTHORIZATION_REQUIRED: t('ops.v.eta'),
        VISA_REQUIRED: t('ops.req.advance'),
        CONDITIONAL: t('ops.req.conditional'),
      },
      requirement_detail: {
        unconditional_visa_free: t('db.detail.unconditionalVisaFree'),
        conditional_visa_free: t('db.detail.conditionalVisaFree'),
        eta_electronic_authorization: t('ops.v.eta'),
        evisa: t('ops.v.evisa'), evisa_on_arrival: t('ops.v.evoa'),
        paper_visa: t('ops.v.paper'),
        paper_visa_on_arrival: t('ops.v.paperVoa'),
      },
      application_channel: {
        authorised_agent: t('ops.ch.agent'), embassy: t('ops.ch.embassy'),
        not_required: t('ops.ch.none'), on_arrival: t('ops.ch.arrival'),
        online_portal: t('ops.ch.online'), visa_center: t('ops.ch.center'),
      },
      confidence: {
        high: t('ops.conf.high'), medium: t('ops.conf.medium'),
        low: t('ops.conf.low'),
      },
      visa_category: {
        ...{
          tourist_visa: t('ops.vc.tourist'), business_visa: t('ops.vc.business'),
          work_visa: t('ops.vc.work'), student_visa: t('ops.vc.student'),
          study_visa: t('ops.vc.student'), transit_visa: t('ops.vc.transit'),
          family_visit_visa: t('ops.vc.family'),
        },
        ...(typeNames || {}),
      },
    }
    return (MAPS[key] || {})[v] ?? (MAPS[key] || {})[v.trim?.()] ?? v
  }, [t, typeNames])

  const qs = useCallback(() => new URLSearchParams(
    Object.fromEntries(Object.entries(filters).filter(([, v]) => v))
  ).toString(), [filters])

  const load = useCallback(async () => {
    setBusy(true); setError('')
    try {
      if (tab === 'records') { setData(await client.get('/database/records')); setShown(PAGE) }
      else if (tab === 'changes') {
        // Records ride along so visa type names can render translated.
        const [chg, recs] = await Promise.all([
          client.get('/database/changes?limit=300'),
          client.get('/database/records')])
        setChanges(chg); setData(recs)
      }
      else if (tab === 'issues') {
        // The dispute cards compare the page's claim against the current
        // record, so the record set must be present on this tab too.
        const [iss, recs] = await Promise.all([
          client.get('/database/issues'), client.get('/database/records')])
        setIssues(iss); setData(recs)
      }
      else if (tab === 'freshness') {
        setFreshness(await client.get('/database/freshness'))
        try { setIssues(await client.get('/database/issues')) } catch { /* tile falls back */ }
        // The availability record rides along: the acceptance metric should
        // be visible where the acceptance runs, not only as raw JSON.
        try { setUptime(await client.get('/health/uptime')) } catch { /* tile hides */ }
      }
    } catch (e) {
      setError(String(e?.message || e))
    } finally {
      setBusy(false)
    }
  }, [client, tab, qs])
  useEffect(() => { load() }, [load])

  async function flag(rec, note) {
    await client.databaseReportIssue({
      nationality: rec.travel_document_country,
      destination: rec.destination_country,
      field: 'operator_spot_check', note, cache_key: rec.cache_key,
    })
  }

  async function release(rec) {
    // Approving releases THIS cached answer to the main site: the customer
    // hold lifts for the exact row the operator reviewed.
    if (!window.confirm(t('ops.releaseConfirm'))) return
    try {
      await client.databaseApprove({
        nationality: rec.travel_document_country,
        destination: rec.destination_country,
        cache_key: rec.cache_key,
        note: 'released from the quality console',
      })
      load()
    } catch (e) { setError(String(e?.message || e)) }
  }

  async function resolveIssue(id, status, resolution) {
    try {
      await client.databaseIssueUpdate(id, status, resolution)
      load()
    } catch (e) { setError(String(e?.message || e)) }
  }

  // Server timestamps are UTC; the operator's clock is not. Everything
  // user-visible renders in the viewer's local timezone.
  const localDate = (ts) => {
    if (!ts) return null
    const iso = /[zZ]|[+-]\d\d:?\d\d$/.test(ts) ? ts : ts + 'Z'
    const d = new Date(iso)
    return isNaN(d) ? null : d
  }
  const localTs = (ts) => {
    const d = localDate(ts)
    if (!d) return String(ts || '')
    try {
      return new Intl.DateTimeFormat(lang === 'en' ? 'en' : lang,
        { month: 'short', day: 'numeric',
          hour: '2-digit', minute: '2-digit' }).format(d)
    } catch { return d.toLocaleString() }
  }

  async function resolveGroup(ids, status, resolution) {
    // One route, many automatic checks: resolving the route resolves every
    // report filed against it, so the queue never demands N identical clicks.
    try {
      for (const id of ids) await client.databaseIssueUpdate(id, status, resolution)
      load()
    } catch (e) { setError(String(e?.message || e)) }
  }

  async function exportXlsx() {
    // An authenticated fetch, saved as a file: window.open cannot carry the
    // operator token, so the workbook is fetched and handed to the browser.
    setBusy(true)
    try {
      const res = await fetch(`${client.baseUrl}/database/export.xlsx?${qs()}`, {
        headers: { authorization: 'Bearer admin-token',
                   'x-org-id': 'ops', 'x-user-id': 'ops' },
      })
      if (!res.ok) throw new Error(`export failed (${res.status})`)
      const blob = await res.blob()
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = `tstation_visa_dataset_${new Date().toISOString().slice(0, 10)}.xlsx`
      a.click()
      URL.revokeObjectURL(a.href)
    } catch (e) {
      setError(String(e?.message || e))
    } finally {
      setBusy(false)
    }
  }

  async function exportChangesCsv() {
    // Deliverable 6: the change list itself is exportable, not only shown.
    setBusy(true)
    try {
      const res = await fetch(`${client.baseUrl}/database/changes.csv`, {
        headers: { authorization: 'Bearer admin-token',
                   'x-org-id': 'ops', 'x-user-id': 'ops' },
      })
      if (!res.ok) throw new Error(`export failed (${res.status})`)
      const blob = await res.blob()
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = `tstation_change_log_${new Date().toISOString().slice(0, 10)}.csv`
      a.click()
      URL.revokeObjectURL(a.href)
    } catch (e) {
      setError(String(e?.message || e))
    } finally {
      setBusy(false)
    }
  }

  // Filtering is LOCAL and instant: the records are already here, and a
  // round-trip per keystroke made the console feel broken. The server-side
  // params remain only for the Excel export.
  const filtered = useMemo(() => {
    const all = data?.records || []
    const term = (raw) => {
      const q = String(raw || '').trim().toLowerCase()
      if (!q) return null
      const hit = countries.find((c) => c.value.toLowerCase() === q
        || c.search.includes(q))
      return hit ? hit.value : String(raw).toUpperCase()
    }
    const natT = term(filters.nationality)
    const destT = term(filters.destination)
    return all.filter((r) => {
      if (natT && r.travel_document_country !== natT) return false
      if (destT && r.destination_country !== destT) return false
      if (filters.purpose && r.travel_purpose !== filters.purpose) return false
      if (filters.requirement && r.visa_requirement !== filters.requirement) return false
      if (filters.confidence && r.confidence_level !== filters.confidence) return false
      if (filters.visaType && !String(r.visa_type_name || '')
            .toLowerCase().includes(filters.visaType.trim().toLowerCase())) return false
      if (filters.document && r.travel_document_type !== filters.document) return false
      if (filters.fieldMissing &&
          (r.field_status || {})[filters.fieldMissing] !== 'missing') return false
      return true
    })
  }, [data, filters, countries])
  const s = useMemo(() => {
    if (!data?.summary) return null
    const t0 = filtered
    const high = t0.filter((r) => r.confidence_level === 'High').length
    const medium = t0.filter((r) => r.confidence_level === 'Medium').length
    const low = t0.filter((r) => r.confidence_level === 'Low').length
    const complete = t0.filter((r) => r.completeness === 1).length
    // The standard defines completeness twice: section 6.1 counts filled
    // CELLS over required cells (the acceptance metric with the 99% bar),
    // section 4.2.2 counts records with every required field filled. Showing
    // the record figure against the cell target mispaired the two.
    // Two readings of the same clause, and the console used to show one while
    // the acceptance archive reported the other, so the product contradicted
    // itself by seven points. Both are computed here and both are shown.
    // "Fillable" is the headline because 6.1 asks for filled over fields that
    // SHOULD be filled: a visa-free route has no visa validity to state, and
    // 4.2.1 forbids inventing what no government publishes. The raw count is
    // shown beside it so the exclusions are auditable rather than asserted.
    const reqNames = data?.required_fields || []
    let cells = 0, filledCells = 0, na = 0, unpub = 0, gaps = 0
    for (const r of t0) {
      const fs = r.field_status || {}
      for (const f of reqNames) {
        cells += 1
        const v = fs[f]
        if (v === 'filled' || v === 'pending-review') filledCells += 1
        else if (v === 'not-applicable') na += 1
        else if (v === 'not-published') unpub += 1
        else if (v === 'missing') gaps += 1
      }
    }
    const fillable = filledCells + gaps
    const src = t0.filter((r) => r.source_url).length
    const sub = t0.filter((r) => r.source_check === 'human-quote'
      || r.source_check === 'grounded-consistent').length
    return { total: t0.length,
             completeness_rate: fillable ? filledCells / fillable : null,
             completeness_literal: cells ? filledCells / cells : null,
             cells, filledCells, na, unpub, gaps,
             record_completeness: t0.length ? complete / t0.length : null,
             source_coverage: t0.length ? src / t0.length : null,
             sourced: src,
             substantiated: sub, high, medium, low }
  }, [data, filtered])
  const records = filtered
  // Counts the FILTERED view, so picking High shows the High-only numbers.
  const totalCount = useCountUp(s?.total ?? 0)
  const set = (k) => (v) => setFilters((f) => ({ ...f, [k]: v }))
  return (
    <div style={{ background: BG, minHeight: '100vh' }}>
      <style>{OPS_CSS}</style>
      <div className="page" style={{ maxWidth: 1160, margin: '0 auto',
                                     padding: '30px 24px 80px' }}
           data-testid="quality-console">
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 14,
                      flexWrap: 'wrap' }}>
          <h1 style={{ fontSize: 26, fontWeight: 800, color: NAVY, margin: 0,
                       letterSpacing: -0.4 }}>
            {t('ops.title')}
          </h1>
          {/* Subtitle hidden per owner instruction: the title stands alone. */}
        </div>

        {/* Tabs */}
        <div className="ops-tabs"
             style={{ display: 'flex', gap: 6, margin: '20px 0 18px',
                      background: '#fff', border: `1px solid ${BORDER}`,
                      borderRadius: 999, padding: 4, width: 'fit-content',
                      maxWidth: '100%', overflowX: 'auto' }}>
          {[['records', t('ops.tab.records')], ['issues', t('ops.tab.issues')],
            ['changes', t('ops.tab.changes')], ['freshness', t('ops.tab.freshness')]]
            .map(([id, label]) => (
            <button key={id} onClick={() => setTab(id)}
                    data-testid={`ops-tab-${id}`}
                    style={{ border: 'none', cursor: 'pointer', fontSize: 13,
                             fontWeight: 700, padding: '8px 18px',
                             borderRadius: 999, whiteSpace: 'nowrap',
                             flexShrink: 0,
                             background: tab === id ? NAVY : 'transparent',
                             color: tab === id ? '#fff' : GRAY }}>
              {label}
            </button>
          ))}
        </div>

        {error && (
          <div style={{ ...card, padding: '12px 18px', color: RED,
                        fontSize: 13, marginBottom: 14 }}>{error}</div>
        )}

        {tab === 'records' && (
          <div style={{ display: 'grid', gap: 14 }}>
            {/* Spot-check filter bar: a header row (label + export), then the
                seven slice controls in equal grid columns, so nothing wraps
                into a lonely orphan with the export floating in space. */}
            <div style={{ ...card, padding: '16px 20px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10,
                            marginBottom: 12, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 11, fontWeight: 800, letterSpacing: 1,
                               color: GRAY, textTransform: 'uppercase',
                               display: 'inline-flex', alignItems: 'center',
                               gap: 7 }}>
                  <span style={{ width: 7, height: 7, borderRadius: 3,
                                 background: BLUE, flexShrink: 0 }} />
                  {t('ops.spotCheck')}
                </span>
                {/* How many slices are active, and one way to undo them.
                    Without this the only clue a filter is on is a shrunken
                    row count further down the page. */}
                {activeFilters > 0 && (
                  <>
                    <span style={{ fontSize: 11, fontWeight: 700,
                                   color: BLUE, background: '#eef4ff',
                                   borderRadius: 999, padding: '2px 9px' }}>
                      {t('ops.flt.on').replace('{n}', activeFilters)}
                    </span>
                    <button onClick={() => setFilters(EMPTY_FILTERS)}
                            data-testid="ops-filters-clear"
                            style={{ border: 'none', background: 'none',
                                     color: GRAY, fontSize: 11.5,
                                     fontWeight: 700, cursor: 'pointer',
                                     padding: 0, textDecoration: 'underline',
                                     textUnderlineOffset: 3 }}>
                      {t('ops.flt.clear')}
                    </button>
                  </>
                )}
              </div>
              {/* Each control carries its own label: the reader sees WHAT
                  each field filters before touching it (the last one slices
                  by records MISSING a field, which a bare select hid). */}
              <div className="ops-filters">
              {(() => {
                const F = ({ label, children }) => (
                  <label style={{ display: 'grid', gap: 5, minWidth: 0 }}>
                    <span style={{ fontSize: 10.5, fontWeight: 700,
                                   letterSpacing: 0.4, color: GRAY,
                                   textTransform: 'uppercase',
                                   paddingLeft: 2 }}>{label}</span>
                    {children}
                  </label>
                )
                return (<>
              <F label={t('ops.flt.passport')}>
              <CountryFilter value={filters.nationality} countries={countries}
                             placeholder={t('ops.passport')}
                             onCommit={set('nationality')} />
              </F>
              <F label={t('ops.flt.destination')}>
              <CountryFilter value={filters.destination} countries={countries}
                             placeholder={t('ops.destination')}
                             onCommit={set('destination')} />
              </F>
              <F label={t('ops.flt.purpose')}>
              <select value={filters.purpose} onChange={(e) => set('purpose')(e.target.value)}
                      className="ops-in"
                      style={{ ...input, color: filters.purpose ? NAVY : GRAY }}>
                <option value="">{t('ops.anyPurpose')}</option>
                {PURPOSES.map((p) => (
                  <option key={p} value={p}>{t(PURPOSE_KEY[p])}</option>
                ))}
              </select>
              </F>
              <F label={t('ops.flt.requirement')}>
              <select value={filters.requirement}
                      onChange={(e) => set('requirement')(e.target.value)}
                      className="ops-in"
                      style={{ ...input, color: filters.requirement ? NAVY : GRAY }}>
                <option value="">{t('ops.anyRequirement')}</option>
                <option value="Visa-free">{t('ops.req.free')}</option>
                <option value="Visa on Arrival">{t('ops.req.voa')}</option>
                <option value="Visa Required in Advance">{t('ops.req.advance')}</option>
                <option value="Conditional">{t('ops.req.conditional')}</option>
              </select>
              </F>
              <F label={t('ops.flt.confidence')}>
              <select value={filters.confidence}
                      onChange={(e) => set('confidence')(e.target.value)}
                      className="ops-in"
                      style={{ ...input, color: filters.confidence ? NAVY : GRAY }}>
                <option value="">{t('ops.anyConfidence')}</option>
                <option value="High">{t('ops.conf.high')}</option>
                <option value="Medium">{t('ops.conf.medium')}</option>
                <option value="Low">{t('ops.conf.low')}</option>
              </select>
              </F>
              {/* The acceptance standard's extra slice dimensions (4.1.2):
                  by visa type, and by a specific field's gaps. */}
              <F label={t('ops.flt.visaType')}>
              <SuggestInput value={filters.visaType}
                            placeholder={t('ops.typeFilter')}
                            testid="ops-filter-visatype"
                            options={visaTypeOptions}
                            onCommit={(v) => setFilters((f) => ({ ...f, visaType: v }))} />
              </F>
              <F label={t('ops.flt.document')}>
              <select className="ops-in" value={filters.document}
                      data-testid="ops-filter-document"
                      style={{ ...input, width: '100%', boxSizing: 'border-box',
                               color: filters.document ? NAVY : GRAY }}
                      onChange={(e) => setFilters((f) =>
                        ({ ...f, document: e.target.value }))}>
                <option value="">{t('ops.anyDocument')}</option>
                {documentOptions.map((d) => (
                  <option key={d} value={d}>
                    {t('db.doc.' + d) !== 'db.doc.' + d
                      ? t('db.doc.' + d) : d.replace(/_/g, ' ')}
                  </option>
                ))}
              </select>
              </F>
              <F label={t('ops.flt.gap')}>
              <select className="ops-in" value={filters.fieldMissing}
                      data-testid="ops-filter-fieldmissing"
                      style={{ ...input, width: '100%',
                               boxSizing: 'border-box' }}
                      onChange={(e) => setFilters((f) =>
                        ({ ...f, fieldMissing: e.target.value }))}>
                <option value="">{t('ops.anyGap')}</option>
                {(data?.required_fields || []).map((f) => (
                  <option key={f} value={f}>{fx(t, f)}</option>
                ))}
              </select>
              </F>
                <div className="ops-filters-act">
                  <button className="btn btn--sm ops-export-btn"
                          onClick={exportXlsx}
                          disabled={busy} data-testid="ops-export"
                          style={{ borderRadius: 10, fontWeight: 700,
                                   background: BLUE, color: '#fff',
                                   border: 'none', width: '100%',
                                   padding: '11px 18px',
                                   display: 'inline-flex', gap: 8,
                                   alignItems: 'center',
                                   justifyContent: 'center',
                                   boxShadow: '0 1px 2px rgba(29,78,216,.28)',
                                   opacity: busy ? 0.6 : 1,
                                   cursor: busy ? 'default' : 'pointer' }}>
                    <span aria-hidden="true" style={{ fontSize: 13 }}>⬇</span>
                    {t('ops.export')}
                  </button>
                </div>
                </>)
              })()}
              </div>
            </div>

            {s && (() => {
              // The bands describe exactly what the table shows: with a
              // filter on, every headline AND sub-split is filtered scope.
              const recs = records
              const allN = (data?.records || []).length
              const anyFilter = Object.values(filters).some(Boolean)
              const req = { free: 0, voa: 0, adv: 0, cond: 0 }
              for (const r of recs) {
                if (r.visa_requirement === 'Visa-free') req.free++
                else if (r.visa_requirement === 'Visa on Arrival') req.voa++
                else if (r.visa_requirement === 'Conditional') req.cond++
                else req.adv++
              }
              return (
                <div style={{ display: 'grid', gap: 8 }}>
                <div style={{ fontSize: 11.5, fontWeight: 700, color: GRAY,
                              paddingLeft: 4 }}>
                  {anyFilter
                    ? t('ops.scope.filtered').replace('{n}', recs.length.toLocaleString()).replace('{m}', allN.toLocaleString())
                    : t('ops.scope.all').replace('{n}', allN.toLocaleString())}
                </div>
                <div style={{ ...card, display: 'grid', overflow: 'hidden',
                              gridTemplateColumns:
                                'repeat(auto-fit, minmax(246px, 1fr))' }}>
                  <BandCell delay={0} first>
                    <BandLabel>{t('ops.stat.records')}</BandLabel>
                    <BandBig>{totalCount.toLocaleString()}</BandBig>
                    <div style={{ marginTop: 12 }}>
                      {/* Four requirement CATEGORIES, so four distinct
                          hues rather than one hue's ramp. The old four
                          blues failed the palette check outright: two sat
                          below the chroma floor (reading grey) and adjacent
                          pairs measured ΔE 13, under the 15 floor for
                          ordinary colour vision. This set passes every
                          check, worst adjacent pair ΔE 19.6. */}
                      <MicroStack segs={[
                        [req.free, '#1d4ed8', t('ops.req.free')],
                        [req.voa, '#0891b2', t('ops.req.voa')],
                        [req.adv, '#7c3aed', t('ops.req.advance')],
                        [req.cond, '#be185d', t('ops.req.conditional')],
                      ]} />
                    </div>
                  </BandCell>
                  <BandCell delay={80}>
                    <BandLabel>{t('ops.stat.complete')}</BandLabel>
                    <div style={{ marginTop: 8 }}>
                      <Meter pct={(s.completeness_rate || 0) * 100} target={99}
                             targetLabel={`${t('ops.stat.target')} 99%`}
                             valueTitle={`${t('ops.stat.recordComplete')
                               .replace('{p}', Math.round((s.record_completeness || 0) * 100))} · ${t('ops.stat.recordCompleteTip')}`} />
                      {/* Two short lines: the fraction, then what sits outside
                          it and why — checkable without being a paragraph. */}
                      <div style={{ marginTop: 7, fontSize: 11, lineHeight: 1.55,
                                    color: GRAY, fontVariantNumeric: 'tabular-nums' }}>
                        {s.filledCells.toLocaleString()} / {(s.filledCells + s.gaps).toLocaleString()}
                        {' '}{t('ops.stat.fillable')}
                        <br />
                        {t('ops.stat.excluded')
                          .replace('{na}', s.na.toLocaleString())
                          .replace('{unpub}', s.unpub.toLocaleString())}
                      </div>
                    </div>
                  </BandCell>
                  <BandCell delay={160}>
                    <BandLabel>{t('ops.stat.sources')}</BandLabel>
                    <div style={{ marginTop: 8 }}>
                      <Meter pct={(s.source_coverage || 0) * 100} target={100}
                             targetLabel={`${t('ops.stat.target')} 100%`} />
                      {/* One line: what the 100% is a count of. */}
                      <div style={{ marginTop: 7, fontSize: 11, lineHeight: 1.55,
                                    color: GRAY, fontVariantNumeric: 'tabular-nums' }}>
                        {t('ops.stat.srcLine')
                          .replace('{n}', s.sourced.toLocaleString())
                          .replace('{total}', s.total.toLocaleString())}
                      </div>
                    </div>
                  </BandCell>
                  <BandCell delay={240}>
                    <BandLabel>{t('ops.stat.confidence')}</BandLabel>
                    <div style={{ marginTop: 12 }}>
                      {/* Traffic-light semantics: Low must not look as calm
                          as High. */}
                      {/* Confidence is a STATUS scale, so it keeps the
                          reserved good/warning/critical colours; the
                          categorical set above never borrows them. */}
                      <MicroStack height={10} segs={[
                        [s.high, '#0b7a44', t('ops.conf.high'), t('ops.tip.quoted')],
                        [s.medium, '#d97706', t('ops.conf.medium'), t('ops.tip.grounded')],
                        [s.low, '#b3261e', t('ops.conf.low'), t('ops.heldTip')],
                      ]} />
                    </div>

                  </BandCell>
                </div>
                </div>
              )
            })()}
            {busy && <div style={{ color: GRAY, fontSize: 13 }}>{t('ops.loading')}</div>}
            <RecordsTable records={records.slice(0, shown)} total={records.length} onFlag={flag} onRelease={release} t={t} flagOf={flagOf} typeNames={typeNames} tvv={tv} />
            {records.length > shown && (
              <button className="btn btn--ghost"
                      style={{ borderRadius: 999, justifySelf: 'center' }}
                      onClick={() => setShown((n) => n + PAGE)}>
                {t('ops.showMore')} ({records.length - shown})
              </button>
            )}
          </div>
        )}

        {tab === 'issues' && (() => {
          const all = issues?.issues || []
          const isOpen = (i) => i.status === 'open' || i.status === 'acknowledged'
          const human = all.filter((i) => isOpen(i) && i.reported_by !== 'freshness_monitor')
          const autoAll = all.filter((i) => isOpen(i) && i.reported_by === 'freshness_monitor')
          // One card per ROUTE: the monitor may have checked the same route
          // several times; the newest report carries the current page reading.
          const groups = (() => {
            const byKey = new Map()
            for (const i of autoAll) {
              const k = i.cache_key || JSON.stringify(i.route || {})
              const g = byKey.get(k)
              if (!g) byKey.set(k, { rep: i, ids: [i.id], count: 1 })
              else {
                g.ids.push(i.id); g.count += 1
                if ((i.created_at || '') > (g.rep.created_at || '')) g.rep = i
              }
            }
            return [...byKey.values()]
          })()
          const openCount = human.length + groups.length
          const closed = all.filter((i) => !isOpen(i)).reverse()
          const dayAgo = Date.now() - 24 * 3600 * 1000
          const resolvedToday = closed.filter((i) => {
            const d = localDate(i.resolved_at || i.updated_at)
            return d && d.getTime() >= dayAgo
          }).length
          const nameOf = (iso) =>
            (countries.find((c) => c.value === iso) || {}).label || iso || '·'
          const parseAuto = (note) => {
            const m = /^Automatic source check against (\S+?): (.*)$/s.exec(note || '')
            if (!m) return null
            const segMap = new Map()
            for (const part of m[2].split(/;\s+(?=[a-z_]+: page says)/)) {
              const pm = /^([a-z_,\s]+): page says\s*([\s\S]*?)(?:\s*\(quote:\s*([\s\S]*?)\)?)?$/.exec(part.trim())
              // Same field twice (merged repeat checks): the LAST reading
              // wins, so one field never shows two competing rows.
              if (pm) segMap.set(pm[1].trim(), { field: pm[1].trim(),
                                                 says: pm[2].trim(),
                                                 quote: (pm[3] || '').trim() })
            }
            return { url: m[1].replace(/[:;,]$/, ''), segs: [...segMap.values()] }
          }
          const fmtSays = (field, says, note) => {
            let v = String(says || '').trim().replace(/^"|"$/g, '')
            // The monitor writes literal "null"/"None" when the page proposes
            // clearing a value; show a plain sentence, not the token.
            if (/^(null|none|undefined)\b/i.test(v) || v === '' || v === '·') {
              return t('ops.pageNoValue')
            }
            if (/^[\[{]/.test(v)) {
              // A product-table proposal: name the visa types instead of
              // printing JSON (the note is capped, so parse may not work).
              // Outside the visa-products row it is extractor spillover, not
              // a value for THIS field.
              if (!String(field || '').includes('visa_products')) {
                return t('ops.pageNoValue')
              }
              const types = [...v.matchAll(/"type":\s*"([^"]+)"/g)]
                .map((m) => typeNames?.[m[1]] || m[1])
              if (types.length) {
                const noun = types.length === 1
                  ? t('ops.productProposed') : t('ops.productsProposed')
                return `${types.length} ${noun}: ${types.join(' · ')}`
              }
              return t('ops.productsProposed')
            }
            const mapped = valueLabel(field, v)
            if (mapped !== v) return mapped
            if (/^[a-z0-9_]+$/i.test(v) && v.includes('_')) {
              v = v.replace(/_/g, ' ').toLowerCase()
              v = v.charAt(0).toUpperCase() + v.slice(1)
            }
            if (/\w$/.test(says || '') && (note || '').endsWith(says || '')) v += '…'
            return v
          }
          // The record the page disagrees with, so both sides can be shown.
          const recFor = (it) => {
            const r = it.route || {}
            const nat = r.passport_nationality || r.nationality
            const dest = r.destination_country || r.destination
            return (data?.records || []).find((x) =>
              x.travel_document_country === nat &&
              x.destination_country === dest &&
              (!r.travel_purpose || x.travel_purpose === r.travel_purpose) &&
              (!r.travel_document_type ||
                x.travel_document_type === r.travel_document_type))
          }
          const reqName = (v) => ({
            'Visa-free': t('ops.req.free'),
            'Visa on Arrival': t('ops.req.voa'),
            'Visa Required in Advance': t('ops.req.advance'),
            Conditional: t('ops.req.conditional'),
          }[v] || v)
          const unitName = (u) => ({
            Day: t('ops.u.day'), Hour: t('ops.u.hour'),
            Month: t('ops.u.month'), Year: t('ops.u.year'),
            'Calendar Day': t('ops.u.calDay'),
            'Working Day': t('ops.u.workDay'),
          }[u] || u || '')
          const methodName = (v) => ({
            'Online Application': t('ops.ch.online'),
            'Embassy Submission': t('ops.ch.embassy'),
            'On-arrival Processing': t('ops.ch.arrival'),
            'Agency Service': t('ops.m.agency'),
            Other: t('ops.m.other'),
          }[v] || v)
          const currentOf = (rec, field) => {
            if (!rec) return null
            const j = (a, b) => (a == null || a === '' ? null
              : `${a} ${b || ''}`.trim())
            switch (String(field || '').split(',')[0].trim()) {
              case 'disposition': return reqName(rec.visa_requirement)
              case 'visa_category':
                return typeNames?.[rec.visa_type_name] || rec.visa_type_name
              case 'permitted_stay': case 'permitted_stay_days':
                return j(rec.max_stay_duration, unitName(rec.max_stay_unit))
              case 'government_fee':
                return j(rec.visa_fee_amount, rec.visa_fee_currency)
              case 'processing_time':
                return j(rec.processing_min_days, unitName(rec.processing_unit))
              case 'application_channel': return methodName(rec.application_method)
              case 'required_documents': return rec.required_documents
              case 'official_portal_url': return rec.source_url
              case 'confidence': return valueLabel('confidence',
                String(rec.confidence_level || '').toLowerCase()) || rec.confidence_level
              default: return null
            }
          }
          const diffChip = (v, kind) => (
            <span style={{ display: 'inline-block', padding: '3px 9px',
                           borderRadius: 7, fontSize: 12.5,
                           overflowWrap: 'anywhere',
                           ...(kind === 'old'
                             ? { background: '#fdf1f4', color: '#a13d55',
                                 textDecoration: 'line-through' }
                             : kind === 'empty'
                               ? { color: GRAY, fontStyle: 'italic',
                                   padding: '3px 0' }
                               : { background: '#eefaf3', color: '#0b7a44',
                                   fontWeight: 700 }) }}>
              {v}
            </span>
          )
          const AutoBody = ({ note, issue }) => {
            const parsed = parseAuto(note)
            if (!parsed) return <div style={{ fontSize: 13, color: NAVY, marginTop: 8 }}>{note}</div>
            const rec = recFor(issue)
            let host = ''
            try { host = new URL(parsed.url).hostname.replace(/^www\./, '') } catch { host = parsed.url }
            return (
              <div style={{ marginTop: 10 }}>
                {/* One plain sentence saying what this card is. */}
                <div style={{ fontSize: 12.5, color: GRAY, marginBottom: 8,
                              display: 'flex', gap: 8, alignItems: 'baseline',
                              flexWrap: 'wrap' }}>
                  <span>{t('ops.diff.summary').replace('{n}', parsed.segs.length)}</span>
                  <a href={parsed.url} target="_blank" rel="noreferrer"
                     style={{ color: BLUE, fontWeight: 700,
                              textDecoration: 'none' }}>
                    {host} ↗
                  </a>
                </div>
                <div style={{ border: `1px solid ${BORDER}`, borderRadius: 10,
                              overflow: 'hidden' }}>
                  <div className="ops-diff ops-diff-head"
                       style={{ background: '#f7f9fc', fontSize: 11.5,
                                fontWeight: 800, color: GRAY,
                                textTransform: 'uppercase',
                                letterSpacing: 0.4 }}>
                    <span style={{ padding: '7px 10px' }}>{t('ops.diff.field')}</span>
                    <span style={{ padding: '7px 10px' }}>{t('ops.diff.db')}</span>
                    <span style={{ padding: '7px 10px' }}>{t('ops.diff.page')}</span>
                  </div>
                  {parsed.segs.map((g, i) => {
                    const cur = currentOf(rec, g.field)
                    const page = (() => {
                      const v = fmtSays(g.field, g.says, note)
                      return v.length > 320 ? v.slice(0, 320) + '…' : v
                    })()
                    const same = cur != null &&
                      String(cur).trim().toLowerCase() ===
                      String(page).trim().toLowerCase()
                    return (
                      <div key={i} style={{ borderTop: `1px solid ${BORDER}` }}>
                        <div className="ops-diff"
                             style={{ fontSize: 12.5,
                                      background: i % 2 ? '#fbfcfe' : '#fff' }}>
                          <span style={{ padding: '8px 10px', fontWeight: 700,
                                         color: NAVY }}>
                            {fieldLabel(g.field)}
                          </span>
                          <span style={{ padding: '8px 10px' }}>
                            <span className="ops-diff-cell-label">{t('ops.diff.db')}</span>
                            {cur == null
                              ? diffChip(t('ops.diff.empty'), 'empty')
                              : diffChip(typeof cur === 'string' ? tv(cur) : cur,
                                         same ? 'new' : 'old')}
                          </span>
                          <span style={{ padding: '8px 10px' }}>
                            <span className="ops-diff-cell-label">{t('ops.diff.page')}</span>
                            {diffChip(tv(page), 'new')}
                            {g.quote && (
                              <details style={{ fontSize: 12, marginTop: 4 }}>
                                <summary style={{ color: BLUE, cursor: 'pointer',
                                                  fontWeight: 600 }}>
                                  {t('ops.showQuote')}
                                </summary>
                                <blockquote style={{ margin: '6px 0 0',
                                             padding: '8px 12px',
                                             borderLeft: `3px solid ${BLUE}`,
                                             background: '#f7faff', color: NAVY,
                                             borderRadius: 6,
                                             overflowWrap: 'anywhere' }}>
                                  {g.quote.slice(0, 400)}
                                </blockquote>
                              </details>
                            )}
                          </span>
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          }
          const Card = ({ it, active, group }) => (
            <div className="ops-lift" style={{ ...card, padding: '14px 18px',
                          borderLeft: `3px solid ${active
                            ? (it.reported_by === 'freshness_monitor' ? BLUE : AMBER)
                            : '#d6dee9'}` }}>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center',
                            flexWrap: 'wrap' }}>
                <strong style={{ color: NAVY, fontSize: 13.5 }}>
                  {nameOf((it.route || {}).nationality || (it.route || {}).passport_nationality)}
                  {' → '}
                  {nameOf((it.route || {}).destination || (it.route || {}).destination_country)}
                </strong>
                {(it.route || {}).travel_purpose && PURPOSE_KEY[(it.route || {}).travel_purpose] && (
                  <span style={{ color: '#5b6a80', fontSize: 11.5,
                                 background: '#f1f4f9', borderRadius: 6,
                                 padding: '2px 8px' }}>
                    {t(PURPOSE_KEY[(it.route || {}).travel_purpose])}
                  </span>
                )}
                {it.reported_by === 'freshness_monitor'
                  ? <Chip color={BLUE} filled={false}>{t('ops.autoCheck')}</Chip>
                  : <Chip color={AMBER} filled={false}>{t('ops.readerReport')}</Chip>}
                {group && group.count > 1 && (
                  <span title={t('ops.checksHint')}
                        style={{ color: GRAY, fontSize: 11.5, cursor: 'help' }}>
                    {group.count} {t('ops.q.checks')}
                  </span>
                )}
                {it.reported_by !== 'freshness_monitor' && (
                  <span style={{ color: '#5b6a80', fontSize: 11.5,
                                 background: '#f1f4f9', borderRadius: 6,
                                 padding: '2px 8px' }}>{fieldLabel(it.field)}</span>
                )}
                {!active && (
                  <Chip color={it.status === 'corrected' ? GREEN : GRAY} filled={false}>
                    {t(`ops.st.${it.status}`) === `ops.st.${it.status}`
                      ? it.status : t(`ops.st.${it.status}`)}
                  </Chip>
                )}
                <span style={{ marginLeft: 'auto', color: GRAY, fontSize: 11.5 }}>
                  {localTs(it.created_at)}
                </span>
              </div>
              {it.reported_by === 'freshness_monitor'
                ? <AutoBody note={it.note} issue={it} />
                : <div style={{ fontSize: 13, color: NAVY, marginTop: 8 }}>{tv(it.note)}</div>}
              {it.resolution && (
                <div style={{ fontSize: 12.5, color: GREEN, marginTop: 6 }}>
                  ✓ {tv(it.resolution)}
                </div>
              )}
              {active && (
                <IssueActions issue={it} t={t}
                  onResolve={group
                    ? (_, st, res) => resolveGroup(group.ids, st, res)
                    : resolveIssue} />
              )}
            </div>
          )
          return (
            <div style={{ display: 'grid', gap: 10 }} className="ops-fade">
              {/* What this queue IS, in one sentence. */}
              <div style={{ ...card, padding: '12px 18px', fontSize: 12.5,
                            color: GRAY, lineHeight: 1.6, display: 'flex',
                            gap: 12, alignItems: 'flex-start' }}>
                <span style={{ width: 22, height: 22, borderRadius: 99,
                               background: `${BLUE}18`, color: BLUE,
                               fontWeight: 800, fontSize: 12, flexShrink: 0,
                               display: 'inline-flex', alignItems: 'center',
                               justifyContent: 'center' }}>i</span>
                <span>{t('ops.queueIntro')}</span>
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center',
                            flexWrap: 'wrap' }}>
                <span style={{ fontSize: 13, fontWeight: 800, color: NAVY }}>
                  {t('ops.issues.open')}
                </span>
                <Chip color={openCount ? BLUE : GREEN}>{openCount}</Chip>
                {groups.length > 0 && (
                  <span style={{ fontSize: 11.5, color: GRAY }}>
                    {groups.length} {t('ops.q.machineOpen')}
                  </span>
                )}
                {human.length > 0 && (
                  <span style={{ fontSize: 11.5, color: '#9a5b00' }}>
                    {human.length} {t('ops.q.humanOpen')}
                  </span>
                )}
                <span style={{ marginLeft: 'auto', fontSize: 11.5,
                               color: GREEN, fontWeight: 700 }}>
                  ✓ {resolvedToday} {t('ops.recent24h')}
                </span>
              </div>
              {openCount === 0 && !busy && (
                <div style={{ ...card, padding: 22, color: GRAY, fontSize: 13.5,
                              textAlign: 'center' }}>{t('ops.noReports')}</div>
              )}
              {human.map((it) => <Card key={it.id} it={it} active />)}
              {groups.map((g) => (
                <Card key={g.rep.id} it={g.rep} active group={g} />
              ))}
              {closed.length > 0 && (
                <details style={{ marginTop: 6 }}>
                  <summary style={{ fontSize: 12.5, color: GRAY, cursor: 'pointer',
                                    fontWeight: 700 }}>
                    {t('ops.issues.resolved')} ({closed.length})
                  </summary>
                  <div style={{ display: 'grid', gap: 10, marginTop: 10 }}>
                    {closed.map((it) => <Card key={it.id} it={it} active={false} />)}
                  </div>
                </details>
              )}
            </div>
          )
        })()}

        {tab === 'changes' && (() => {
          const AC = { add: GREEN, modify: AMBER, delete: RED }
          const nameOfC = (iso) =>
            (countries.find((x) => x.value === iso) || {}).label || iso || '·'
          // Every correction names the official page it was made against;
          // the log shows it as a link so any change can be re-checked at
          // its source in one click.
          // The server now resolves a source for every entry: the URL the
          // change itself set, else the human-verified override, else the
          // note, else the source on the answer as it stands. Reading it out
          // of the note was only ever finding the minority that mentioned one.
          const sourceOf = (c) => c.source_url
            || (/(https?:\/\/[^\s)]+)/.exec(c.note || '') || [])[1]?.replace(/[).,;:]+$/, '')
            || null
          const hostOf = (u) => {
            try { return new URL(u).hostname.replace(/^www\./, '') }
            catch { return u }
          }
          const fmt = (v) => {
            if (v == null) return null
            if (typeof v === 'object') {
              if (Array.isArray(v)) return `${v.length} ${t('ops.items')}`
              if ('amount' in v) {
                return v.amount == null ? null
                  : `${v.amount} ${v.currency || ''}`.trim()
              }
              const j = JSON.stringify(v)
              return j === '{}' ? null : j.slice(0, 40)
            }
            const sv = String(v).replace(/^"|"$/g, '')
            return sv.length > 220 ? sv.slice(0, 220) + '…' : sv
          }
          const all = changes?.changes || []
          const counts = { '': all.length }
          for (const c of all) counts[c.action] = (counts[c.action] || 0) + 1
          const list = all.filter((c) => !changeFilter || c.action === changeFilter)
          const byDay = []
          for (const c of list) {
            const d = localDate(c.at)
            const day = d ? `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}` : (c.at || '').slice(0, 10)
            const g = byDay[byDay.length - 1]
            if (g && g.day === day) g.items.push(c)
            else byDay.push({ day, items: [c] })
          }
          const ValueChip = ({ v, kind }) => (
            <span title={typeof v === 'string' ? v : undefined}
                  style={{ display: 'inline-block', padding: '2px 8px',
                           borderRadius: 7, fontSize: 12,
                           overflowWrap: 'anywhere', lineHeight: 1.45,
                           verticalAlign: 'bottom',
                           ...(kind === 'empty'
                             ? { color: GRAY, fontStyle: 'italic',
                                 padding: '2px 0' }
                             : {}),
                           ...(kind === 'old'
                             ? { background: '#fdf1f4', color: '#a13d55',
                                 textDecoration: 'line-through' }
                             : kind === 'del'
                               ? { background: '#fdf1f4', color: RED, fontWeight: 700 }
                               : { background: '#eefaf3', color: '#0b7a44',
                                   fontWeight: 700 }) }}>
              {typeof v === 'string' ? tv(v) : v}
            </span>
          )
          return (
            <div className="ops-fade" style={{ display: 'grid', gap: 14 }}>
              {/* Segmented action filter with live counts + list export */}
              <div style={{ display: 'flex', gap: 10, alignItems: 'center',
                            flexWrap: 'wrap' }}>
              <div style={{ display: 'inline-flex', flexWrap: 'wrap',
                            background: '#fff',
                            border: `1px solid ${BORDER}`, borderRadius: 18,
                            padding: 4, width: 'fit-content' }}>
                {[['', t('ops.all')], ['add', t('ops.act.add')],
                  ['modify', t('ops.act.modify')],
                  ['delete', t('ops.act.delete')]].map(([id, label]) => (
                  <button key={id} onClick={() => setChangeFilter(id)}
                          style={{ border: 'none', cursor: 'pointer',
                                   borderRadius: 999, fontSize: 12.5,
                                   fontWeight: 700, padding: '7px 16px',
                                   background: changeFilter === id ? NAVY : 'transparent',
                                   color: changeFilter === id ? '#fff' : GRAY }}>
                    {label}
                    <span style={{ marginLeft: 6, opacity: 0.65,
                                   fontWeight: 600 }}>{counts[id] || 0}</span>
                  </button>
                ))}
              </div>
              <button onClick={exportChangesCsv} disabled={busy}
                      data-testid="ops-export-changes"
                      style={{ borderRadius: 999, fontWeight: 700,
                               fontSize: 12.5, cursor: 'pointer',
                               border: `1px solid ${BORDER}`,
                               background: '#fff', color: NAVY,
                               padding: '9px 16px' }}>
                ⬇ {t('ops.export')}
              </button>
              </div>
              <div style={{ ...card, padding: '12px 18px', fontSize: 12.5,
                            color: GRAY, lineHeight: 1.6, display: 'flex',
                            gap: 12, alignItems: 'flex-start' }}>
                <span style={{ width: 22, height: 22, borderRadius: 99,
                               background: `${BLUE}18`, color: BLUE,
                               fontWeight: 800, fontSize: 12, flexShrink: 0,
                               display: 'inline-flex', alignItems: 'center',
                               justifyContent: 'center' }}>i</span>
                <span>{t('ops.logIntro')}</span>
              </div>
              {list.length === 0 && !busy && (
                <div style={{ ...card, padding: 22, color: GRAY,
                              fontSize: 13.5, textAlign: 'center' }}>
                  {t('ops.noChanges')}
                </div>
              )}
              {byDay.map((g) => (
                <div key={g.day} style={{ display: 'grid', gap: 8 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <span title={g.day}
                          style={{ fontSize: 12, fontWeight: 800,
                                   color: NAVY,
                                   whiteSpace: 'nowrap' }}>
                      {(() => {
                        try {
                          return new Intl.DateTimeFormat(
                            lang === 'en' ? 'en' : lang,
                            { month: 'short', day: 'numeric',
                              weekday: 'short' }).format(new Date(g.day + 'T00:00:00'))
                        } catch { return g.day }
                      })()}
                    </span>
                    <span style={{ flex: 1, height: 1, background: BORDER }} />
                    <span style={{ fontSize: 11, color: GRAY }}>
                      {g.items.length} {t('ops.items')}
                    </span>
                  </div>
                  {g.items.map((c) => (
                    <div key={c.id} className="ops-lift"
                         style={{ ...card, padding: '12px 16px',
                                  borderLeft: `3px solid ${AC[c.action] || GRAY}` }}>
                      <div style={{ display: 'flex', gap: 10,
                                    alignItems: 'center', flexWrap: 'wrap' }}>
                        <strong style={{ color: NAVY, fontSize: 13 }}>
                          {nameOfC((c.route || {}).passport_nationality)}
                          {' → '}
                          {nameOfC((c.route || {}).destination_country)}
                        </strong>
                        {(c.route || {}).travel_purpose && PURPOSE_KEY[(c.route || {}).travel_purpose] && (
                          <span style={{ color: '#5b6a80', fontSize: 11.5,
                                         background: '#f1f4f9', borderRadius: 6,
                                         padding: '2px 8px' }}>
                            {t(PURPOSE_KEY[(c.route || {}).travel_purpose])}
                          </span>
                        )}
                        {(() => { const dd = (c.route || {}).travel_document_type
                          return dd && dd !== 'ordinary_passport' && (
                            <span style={{ color: '#5b6a80', fontSize: 11.5,
                                           background: '#f1f4f9',
                                           borderRadius: 6,
                                           padding: '2px 8px' }}>
                              {t('db.doc.' + dd) !== 'db.doc.' + dd
                                ? t('db.doc.' + dd) : dd.replace(/_/g, ' ')}
                            </span>
                          ) })()}
                        <Chip color={AC[c.action] || GRAY}>
                          {t(`ops.act.${c.action}`) === `ops.act.${c.action}`
                            ? c.action : t(`ops.act.${c.action}`)}
                        </Chip>
                        {/* WHO changed it, in plain words, not a token. */}
                        <span title={c.origin === 'grounded_recheck'
                                ? t('ops.originRecheckTip')
                                : c.origin === 'engine' ? t('ops.originEngineTip')
                                  : t('ops.originHumanTip')}
                              style={{ cursor: 'help' }}>
                        <Chip filled={false}
                              color={c.origin === 'grounded_recheck' ? BLUE
                                : c.origin === 'engine' ? GRAY : GREEN}>
                          {c.origin === 'grounded_recheck' ? t('ops.origin.recheck')
                            : c.origin === 'engine' ? t('ops.origin.engine')
                              : t('ops.origin.human')}
                        </Chip>
                        </span>
                        {sourceOf(c) ? (
                          <a href={sourceOf(c)} target="_blank" rel="noreferrer"
                             title={`${c.source_kind || t('ops.chg.srcTitle')}\n${sourceOf(c)}`}
                             style={{ display: 'inline-flex', alignItems: 'center',
                                      gap: 5, fontSize: 11.5, fontWeight: 700,
                                      color: BLUE, textDecoration: 'none',
                                      overflowWrap: 'anywhere' }}>
                            {/* A reviewer must be able to see at a glance that
                                the proof is a government page, not any page. */}
                            <span style={{ fontSize: 9, fontWeight: 800,
                                           letterSpacing: 0.4,
                                           borderRadius: 3, padding: '1px 5px',
                                           color: c.source_official === false ? '#9a5b00' : '#0b7a44',
                                           background: c.source_official === false ? '#fdf3e2' : '#e8f5ee' }}>
                              {c.source_official === false ? t('ops.chg.srcOther') : t('ops.chg.srcGov')}
                            </span>
                            {hostOf(sourceOf(c))} ↗
                          </a>
                        ) : (
                          <span title={t('ops.chg.noSrcTip')}
                                style={{ fontSize: 11, fontWeight: 700,
                                         color: '#9a5b00', background: '#fdf3e2',
                                         borderRadius: 3, padding: '2px 7px',
                                         cursor: 'help' }}>
                            {t('ops.chg.noSrc')}
                          </span>
                        )}
                        <span style={{ marginLeft: 'auto', color: '#9aa8bd',
                                       fontSize: 11.5,
                                       fontFamily: 'ui-monospace, monospace' }}>
                          {(() => { const d = localDate(c.at)
                            return d ? `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}` : (c.at || '').slice(11, 16) })()}
                        </span>
                      </div>
                      <div style={{ marginTop: 8, display: 'grid', gap: 5 }}>
                        {(() => {
                          const HEAD = ['disposition', 'visa_category',
                                        'permitted_stay', 'government_fee']
                          const fv = (f, v) =>
                            fmt(typeof v === 'string' ? valueLabel(f, v) : v)
                          let entries = Object.entries(c.changes || {})
                            .map(([f, d]) => [f, fv(f, d.from), fv(f, d.to)])
                            .filter(([, a, b]) => a != null || b != null)
                          if (c.action === 'add') {
                            // A brand-new record: lead with the headline facts
                            // instead of listing every stored field.
                            entries = entries.sort((x, y) => {
                              const ix = HEAD.indexOf(x[0]), iy = HEAD.indexOf(y[0])
                              return (ix < 0 ? 9 : ix) - (iy < 0 ? 9 : iy)
                            })
                          }
                          // A row whose two sides render identically claims a
                          // change while showing none: drop it.
                          if (c.action === 'modify') {
                            entries = entries.filter(([, a, b]) => a !== b)
                          }
                          const cap = c.action === 'add' ? 4 : 5
                          const Row = ([f, a, b]) => (
                              <div key={f} style={{ display: 'grid',
                                    gridTemplateColumns: '175px 1fr', gap: 10,
                                    alignItems: 'center', fontSize: 12 }}>
                                <span title={f}
                                      style={{ color: '#5b6a80',
                                               fontSize: 11.5, fontWeight: 700,
                                               lineHeight: 1.4 }}>{fieldLabel(f)}</span>
                                <span style={{ minWidth: 0, display: 'flex',
                                               gap: 6, alignItems: 'center',
                                               flexWrap: 'wrap' }}>
                                  {c.action === 'modify' && [
                                    <ValueChip key="o" v={a ?? t('ops.emptyVal')}
                                               kind={a == null ? 'empty' : 'old'} />,
                                    <span key="s" style={{ color: '#b6c2d4' }}>→</span>,
                                  ]}
                                  <ValueChip v={b ?? t('ops.emptyVal')}
                                             kind={c.action === 'delete' ? 'del'
                                               : b == null ? 'empty' : 'new'} />
                                </span>
                              </div>
                          )
                          return [
                            ...entries.slice(0, cap).map(Row),
                            entries.length > cap && (
                              <details key="more" style={{ marginTop: 2 }}>
                                <summary style={{ fontSize: 11.5, color: BLUE,
                                      fontWeight: 700, cursor: 'pointer' }}>
                                  {t('ops.showMore').replace('{n}', entries.length - cap)}
                                </summary>
                                <div style={{ display: 'grid', gap: 5,
                                      marginTop: 6 }}>
                                  {entries.slice(cap).map(Row)}
                                </div>
                              </details>
                            ),
                          ]
                        })()}
                      </div>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          )
        })()}

        {tab === 'freshness' && freshness && (() => {
          const f = freshness.summary
          const covered = f.human_verified + f.grounded
          const rest = Math.max(0, f.total - covered)
          const segs = [
            [f.human_verified, SEQ.high, t('ops.fresh.human')],
            [f.grounded, SEQ.medium, t('ops.fresh.grounded')],
            [rest, SEQ.low, t('ops.fresh.notYet')],
          ]
          return (
            <div style={{ display: 'grid', gap: 14 }} className="ops-fade">
              <div className="ops-tiles">
                {[
                  // Every tile states its own unit. An answer is one cached
                  // route decision; a record is one visa product within it,
                  // which is why this page counts fewer things than Records
                  // does - a difference that reads as a bug when unstated.
                  { label: t('ops.fresh.answers'), value: f.total,
                    sub: t('ops.fresh.answersSub'), accent: NAVY },
                  { label: t('ops.fresh.human'), value: f.human_verified,
                    accent: GREEN,
                    pct: f.total ? (f.human_verified / f.total) * 100 : null,
                    sub: t('ops.fresh.humanSub') },
                  { label: t('ops.fresh.grounded'), value: f.grounded,
                    accent: BLUE,
                    pct: f.total ? (f.grounded / f.total) * 100 : null,
                    sub: t('ops.fresh.groundedSub2') },
                  { label: t('ops.fresh.stale'), value: f.stale,
                    accent: f.stale ? AMBER : GREEN,
                    pct: f.total ? (f.stale / f.total) * 100 : null,
                    sub: t('ops.fresh.staleSub') },
                  // The caption used to carry the queue's open-report count,
                  // so the tile showed two different numbers at once and read
                  // as broken. It now describes only what it counts.
                  { label: t('ops.fresh.disputed'), value: f.disputed,
                    accent: f.disputed ? RED : GREEN,
                    pct: f.total ? (f.disputed / f.total) * 100 : null,
                    onClick: () => setTab('issues'),
                    sub: t('ops.fresh.disputedSub') },
                  ...(() => {
                    const m = uptime?.months?.[uptime.months.length - 1]
                    if (!m) return []
                    return [{ label: t('ops.fresh.avail'),
                              value: `${m.availability_pct}%`,
                              accent: m.availability_pct >= 99.99 ? GREEN : AMBER,
                              sub: t('ops.fresh.availSub')
                                     .replace('{ms}', m.median_latency_ms ?? '·') }]
                  })(),
                ].map((p, i) => (
                  <div key={i} className="ops-lift"
                       onClick={p.onClick}
                       style={{ ...card, padding: 0,
                                cursor: p.onClick ? 'pointer' : 'default' }}>
                    <StatCell {...p} delay={i * 70} />
                  </div>
                ))}
              </div>
              <div style={{ ...card, padding: '16px 20px', fontSize: 12.5,
                            color: GRAY, lineHeight: 1.65, display: 'flex',
                            gap: 12, alignItems: 'flex-start' }}>
                <span style={{ width: 22, height: 22, borderRadius: 99,
                               background: `${BLUE}18`, color: BLUE,
                               fontWeight: 800, fontSize: 12, flexShrink: 0,
                               display: 'inline-flex', alignItems: 'center',
                               justifyContent: 'center' }}>i</span>
                <span>{t('ops.fresh.note')}</span>
              </div>
              <div style={{ ...card, padding: '24px 28px' }}>
                <div style={{ display: 'grid', gap: 16, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'baseline',
                                gap: 12, flexWrap: 'wrap' }}>
                    <div style={{ fontSize: 10.5, fontWeight: 800,
                                  letterSpacing: 1, color: GRAY,
                                  textTransform: 'uppercase', flex: 1 }}>
                      {t('ops.fresh.coverage')}
                    </div>
                    <div style={{ fontSize: 26, fontWeight: 700, color: NAVY,
                                  lineHeight: 1,
                                  fontVariantNumeric: 'tabular-nums' }}>
                      {f.total ? Math.round((covered / f.total) * 100) : 0}%
                    </div>
                    <div style={{ fontSize: 11, fontWeight: 700, color: GRAY,
                                  textTransform: 'uppercase',
                                  letterSpacing: 0.6 }}>
                      {t('ops.fresh.coveredShort')}
                    </div>
                  </div>
                  <MicroStack segs={segs} height={22} legend={false} />
                  <div style={{ display: 'grid', gap: 8 }}>
                    {segs.map(([n, color, name], i) => (
                      <div key={i} style={{ display: 'flex',
                                    alignItems: 'center', gap: 10,
                                    fontSize: 13 }}>
                        <span style={{ width: 10, height: 10, borderRadius: 3,
                                       background: color, flexShrink: 0 }} />
                        <span style={{ color: GRAY, flex: 1 }}>{name}</span>
                        <strong style={{ color: NAVY,
                                         fontVariantNumeric: 'tabular-nums' }}>
                          {n.toLocaleString()}
                        </strong>
                        <span style={{ color: GRAY, fontSize: 12, width: 44,
                                       textAlign: 'right',
                                       fontVariantNumeric: 'tabular-nums' }}>
                          {f.total ? Math.round((n / f.total) * 100) : 0}%
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )
        })()}
      </div>
    </div>
  )
}
