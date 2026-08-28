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
const SEQ = { high: '#1d4ed8', medium: '#7db2f7', low: '#dbe8fb' }
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
const input = { padding: '9px 12px', borderRadius: 10, fontSize: 13,
                border: `1px solid #d9e1ec`, background: '#fff', color: NAVY,
                outline: 'none' }

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
                    color: pct == null ? accent : (hit ? GREEN : NAVY) }}>
        {typeof value === 'number' ? n.toLocaleString() : value}
      </div>
      {pct != null && (
        <div style={{ marginTop: 12, height: 6, borderRadius: 999,
                      background: '#dbe8fb', position: 'relative' }}>
          <div className="ops-bar"
               style={{ position: 'absolute', left: 0, top: 0, bottom: 0,
                        width: `${w}%`, borderRadius: 999,
                        background: hit ? GREEN
                          : 'linear-gradient(90deg, #1d4ed8, #4f8ef8)' }} />
          {target != null && (
            <div style={{ position: 'absolute', left: `${target}%`, top: -3,
                          bottom: -3, width: 2, background: NAVY,
                          borderRadius: 1, opacity: 0.45 }} />
          )}
        </div>
      )}
      <div style={{ fontSize: 11, color: GRAY, marginTop: 9,
                    whiteSpace: 'nowrap', overflow: 'hidden',
                    textOverflow: 'ellipsis' }}>{sub}</div>
    </div>
  )
}

function ConfidenceCell({ high, medium, low, label, sub, t, delay = 0 }) {
  const total = (high + medium + low) || 1
  const [go, setGo] = useState(false)
  useEffect(() => { const id = setTimeout(() => setGo(true), 80 + delay)
    return () => clearTimeout(id) }, [delay])
  const seg = (n, color, last) => (
    <div key={color} className="ops-seg"
         style={{ width: go ? `${(n / total) * 100}%` : '0%',
                  background: color,
                  borderRight: last ? 'none' : '2px solid #fff' }} />
  )
  const Key = ({ color, n, name }) => (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      <span style={{ width: 8, height: 8, borderRadius: '50%',
                     background: color, border: '1px solid #c9d6ea' }} />
      <span style={{ color: NAVY, fontWeight: 700 }}>{n.toLocaleString()}</span>
      <span style={{ color: GRAY }}>{name}</span>
    </span>
  )
  return (
    <div className="ops-fade" style={{ padding: '20px 24px', minWidth: 0,
                                       animationDelay: `${delay}ms` }}>
      <div style={{ fontSize: 10.5, fontWeight: 800, letterSpacing: 1,
                    color: GRAY, textTransform: 'uppercase' }}>{label}</div>
      <div style={{ display: 'flex', gap: 12, marginTop: 12, fontSize: 12,
                    flexWrap: 'wrap' }}>
        <Key color={SEQ.high} n={high} name={t('ops.conf.high')} />
        <Key color={SEQ.medium} n={medium} name={t('ops.conf.medium')} />
        <Key color={SEQ.low} n={low} name={t('ops.conf.low')} />
      </div>
      <div style={{ marginTop: 12, height: 6, borderRadius: 999,
                    overflow: 'hidden', display: 'flex',
                    background: '#eef2f8' }}>
        {seg(high, SEQ.high, false)}{seg(medium, SEQ.medium, false)}
        {seg(low, SEQ.low, true)}
      </div>
      <div style={{ fontSize: 11, color: GRAY, marginTop: 9 }}>{sub}</div>
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
function CountryFilter({ value, placeholder, onCommit, countries }) {
  const [text, setText] = useState(value)
  const [open, setOpen] = useState(false)
  const boxRef = useRef(null)
  useEffect(() => { setText(value) }, [value])
  const matches = useMemo(() => {
    const q = text.trim().toLowerCase()
    if (!q) return []
    return countries.filter((c) => c.search.includes(q)).slice(0, 8)
  }, [text, countries])
  useEffect(() => {
    const close = (e) => {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])
  const commit = (v) => {
    // Whatever is committed resolves to a clean value: a picked label goes
    // back to its code, a flag emoji is stripped, free text passes through
    // for the server-side resolver.
    const clean = String(v || '').replace(/^[^\p{L}\p{N}]+/u, '').trim()
    const byLabel = countries.find((c) => c.label === v || c.label.endsWith(clean))
    onCommit(byLabel ? byLabel.value : clean)
    setOpen(false)
  }
  return (
    <div ref={boxRef} style={{ position: 'relative' }}>
      <input value={text} placeholder={placeholder}
             onChange={(e) => { setText(e.target.value); setOpen(true) }}
             onKeyDown={(e) => {
               if (e.key === 'Enter') commit(matches[0]?.value ?? text)
               if (e.key === 'Escape') setOpen(false)
             }}
             onBlur={() => { if (!open) commit(text) }}
             style={{ ...input, width: 150 }} />
      {text && (
        <button onClick={() => { setText(''); commit('') }}
                style={{ position: 'absolute', right: 6, top: 7, border: 'none',
                         background: 'transparent', color: GRAY,
                         cursor: 'pointer', fontSize: 13 }}>×</button>
      )}
      {open && matches.length > 0 && (
        <div style={{ position: 'absolute', top: '110%', left: 0, zIndex: 30,
                      minWidth: 210, ...card, padding: 6, maxHeight: 260,
                      overflowY: 'auto' }}>
          {matches.map((c) => (
            <div key={c.value}
                 onMouseDown={() => { setText(c.label); commit(c.value) }}
                 style={{ padding: '7px 10px', borderRadius: 8, fontSize: 13,
                          color: NAVY, cursor: 'pointer', display: 'flex',
                          justifyContent: 'space-between', gap: 10 }}
                 onMouseEnter={(e) => { e.currentTarget.style.background = BG }}
                 onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}>
              <span>{c.label}</span>
              <span style={{ color: GRAY, fontFamily: 'ui-monospace, monospace',
                             fontSize: 11 }}>{c.value}</span>
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

function FieldGrid({ rec, t, typeNames = {} }) {
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
  const show = (f) => {
    const v = rec[f]
    if (v == null || v === '') return '·'
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
    <div style={{ display: 'grid', gap: '6px 18px', marginTop: 4,
                  gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))' }}>
      {Object.entries(rec.field_status).map(([f, st]) => (
        <div key={f} style={{ display: 'flex', gap: 8, fontSize: 12,
                              alignItems: 'baseline', minWidth: 0 }}>
          <span style={{ color: st === 'missing' ? RED : st === 'filled' ? GREEN : '#c3ccd9',
                         fontWeight: 700, width: 12, flexShrink: 0 }}>
            {st === 'missing' ? '✗' : st === 'filled' ? '✓' : '·'}
          </span>
          <span title={f}
                style={{ color: GRAY, width: 148, flexShrink: 0,
                         fontSize: 11.5 }}>{fx(t, f)}</span>
          <span title={String(rec[f] ?? '')}
                style={{ color: NAVY, fontWeight: 600, overflow: 'hidden',
                         textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {show(f)}
          </span>
        </div>
      ))}
    </div>
  )
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

function RecordsTable({ records, total, onFlag, onRelease, t, flagOf, typeNames = {} }) {
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
    'human-quote': [t('ops.check.quoted'), GREEN],
    'grounded-consistent': [t('ops.check.grounded'), BLUE],
    reference: [t('ops.check.reference'), GRAY],
    unchecked: [t('ops.check.none'), RED],
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
      <div style={{ overflowX: 'auto', maxHeight: '62vh', overflowY: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse',
                        fontSize: 13 }}>
          <thead>
            <tr>
              <SortHeader label={t('ops.col.route')} k="route" sort={sort} onSort={onSort} width={140} />
              <SortHeader label={t('ops.col.requirement')} k="requirement" sort={sort} onSort={onSort} width={110} />
              <SortHeader label={t('ops.col.type')} k="type" sort={sort} onSort={onSort} />
              <SortHeader label={t('ops.col.stay')} k="stay" sort={sort} onSort={onSort} align="right" width={80} />
              <SortHeader label={t('ops.col.fee')} k="fee" sort={sort} onSort={onSort} align="right" width={90} />
              <SortHeader label={t('ops.col.quality')} k="check" sort={sort} onSort={onSort} width={150} />
              <th style={{ position: 'sticky', top: 0, background: '#fff',
                           zIndex: 5, borderBottom: `2px solid ${BORDER}`,
                           width: 90 }} />
            </tr>
          </thead>
          <tbody>
            {sorted.map((rec, i) => {
              const id = rec.cache_key + (rec.visa_type_name || '') + i
              const [reqLabel, reqColor] = REQ[rec.visa_requirement] || ['·', GRAY]
              const [checkLabel, checkColor] = CHECKS[rec.source_check] || CHECKS.reference
              const missing = Object.entries(rec.field_status)
                .filter(([, v]) => v === 'missing').map(([k]) => k)
              const opened = open === id
              const confKey = 'ops.conf.' + String(rec.confidence_level || '').toLowerCase()
              const confLabel = t(confKey) !== confKey ? t(confKey) : rec.confidence_level
              const pctDone = Math.round(rec.completeness * 100)
              return [
                <tr key={id} onClick={() => setOpen(opened ? null : id)}
                    style={{ cursor: 'pointer',
                             background: opened ? '#f4f8ff'
                               : i % 2 ? '#fbfcfe' : '#fff' }}>
                  <td style={{ padding: '10px 12px', whiteSpace: 'nowrap' }}>
                    <span style={{ display: 'inline-block', color: '#9aa8bd',
                                   fontSize: 10, marginRight: 7,
                                   transition: 'transform .15s ease',
                                   transform: opened ? 'rotate(90deg)' : 'none' }}>
                      ▶
                    </span>
                    <strong style={{ color: NAVY }}>
                      {flagOf(rec.travel_document_country)} {rec.travel_document_country}
                      {' → '}
                      {flagOf(rec.destination_country)} {rec.destination_country}
                    </strong>
                    <div style={{ color: GRAY, fontSize: 11, paddingLeft: 17 }}>
                      {t(PURPOSE_KEY[rec.travel_purpose] || '') || rec.travel_purpose}
                      {rec.travel_document_type !== 'ordinary_passport'
                        ? ' · ' + ((t('db.doc.' + rec.travel_document_type)
                            !== 'db.doc.' + rec.travel_document_type)
                            ? t('db.doc.' + rec.travel_document_type)
                            : rec.travel_document_type.replace(/_/g, ' '))
                        : ''}
                    </div>
                  </td>
                  <td style={{ padding: '10px 12px' }}>
                    <Chip color={reqColor} filled={false}>{reqLabel}</Chip>
                  </td>
                  <td style={{ padding: '10px 12px', color: NAVY, fontWeight: 600,
                               maxWidth: 300, overflow: 'hidden',
                               textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {typeNames[rec.visa_type_name] || rec.visa_type_name || '·'}
                  </td>
                  <td style={{ padding: '10px 12px', textAlign: 'right',
                               color: NAVY, whiteSpace: 'nowrap',
                               fontVariantNumeric: 'tabular-nums' }}>
                    {rec.max_stay_duration != null
                      ? `${rec.max_stay_duration} ${rec.max_stay_unit === 'Hour'
                          ? t('ops.u.hour') : t('ops.u.day')}`
                      : '·'}
                  </td>
                  <td style={{ padding: '10px 12px', textAlign: 'right',
                               color: NAVY, whiteSpace: 'nowrap',
                               fontVariantNumeric: 'tabular-nums' }}>
                    {rec.visa_fee_amount != null
                      ? `${rec.visa_fee_amount} ${rec.visa_fee_currency || ''}`
                      : '·'}
                  </td>
                  <td style={{ padding: '10px 12px' }}>
                    <Chip color={checkColor} filled={false}>{checkLabel}</Chip>
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
                  <td style={{ padding: '10px 12px', whiteSpace: 'nowrap' }}>
                    {rec.source_url && (
                      <a href={rec.source_url} target="_blank" rel="noreferrer"
                         onClick={(e) => e.stopPropagation()}
                         style={{ fontSize: 12, color: BLUE, fontWeight: 700 }}>
                        {t('ops.source')} ↗
                      </a>
                    )}
                    {rec.confidence_level === 'Low' && (
                      <button onClick={(e) => { e.stopPropagation(); onRelease(rec) }}
                              data-testid="ops-release"
                              style={{ marginLeft: 8, border: `1px solid ${GREEN}`,
                                       background: '#fff', color: GREEN,
                                       borderRadius: 999, fontSize: 11,
                                       fontWeight: 700, padding: '2px 10px',
                                       cursor: 'pointer' }}>
                        {t('ops.release')}
                      </button>
                    )}
                  </td>
                </tr>,
                opened && (
                  <tr key={id + ':detail'}>
                    <td colSpan={7} style={{ background: '#fbfcfe',
                        borderBottom: `1px solid ${BORDER}`,
                        padding: '14px 18px' }}>
                      <FieldGrid rec={rec} t={t} typeNames={typeNames} />
                      {missing.length > 0 && (
                        <MissingLine missing={missing} t={t} />
                      )}
                      <FlagForm rec={rec} onFlag={onFlag} t={t} />
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
             style={{ ...input, flex: 1, minWidth: 220 }}
             data-testid="ops-resolution" />
      <button disabled={!res.trim()} data-testid="ops-corrected"
              onClick={() => onResolve(issue.id, 'corrected', res.trim())}
              style={{ borderRadius: 999, fontSize: 12, fontWeight: 700,
                       border: 'none', cursor: 'pointer', padding: '7px 14px',
                       background: GREEN, color: '#fff',
                       opacity: res.trim() ? 1 : 0.5 }}>
        ✓ {t('ops.markCorrected')}
      </button>
      <button disabled={!res.trim()} data-testid="ops-dismiss"
              onClick={() => onResolve(issue.id, 'dismissed', res.trim())}
              style={{ borderRadius: 999, fontSize: 12, fontWeight: 700,
                       border: `1px solid ${GRAY}`, cursor: 'pointer',
                       padding: '7px 14px', background: '#fff', color: GRAY,
                       opacity: res.trim() ? 1 : 0.5 }}>
        {t('ops.dismiss')}
      </button>
    </div>
  )
}

function BandCell({ children, delay = 0 }) {
  return (
    <div className="ops-fade" style={{ padding: '20px 24px', minWidth: 0,
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

function BandBig({ children, color = NAVY }) {
  return (
    <div style={{ fontSize: 30, fontWeight: 700, marginTop: 8,
                  lineHeight: 1, color }}>{children}</div>
  )
}

function RingGauge({ pct, target, size = 74, hitColor = GREEN }) {
  const [go, setGo] = useState(false)
  useEffect(() => { const id = setTimeout(() => setGo(true), 140)
    return () => clearTimeout(id) }, [])
  const R = (size - 12) / 2, C = 2 * Math.PI * R
  const hit = target != null && pct >= target
  const val = Math.max(0, Math.min(100, pct || 0))
  const tickAngle = target != null ? (target / 100) * 360 - 90 : null
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden>
      <circle cx={size/2} cy={size/2} r={R} fill="none" stroke="#e7eefb"
              strokeWidth="9" />
      <circle cx={size/2} cy={size/2} r={R} fill="none"
              stroke={hit ? hitColor : '#2f6ef2'} strokeWidth="9"
              strokeLinecap="round"
              strokeDasharray={`${go ? (val / 100) * C : 0} ${C}`}
              transform={`rotate(-90 ${size/2} ${size/2})`}
              style={{ transition: 'stroke-dasharray 1s cubic-bezier(.25,.8,.25,1)' }} />
      {tickAngle != null && (
        <line x1={size/2 + (R - 7) * Math.cos(tickAngle * Math.PI / 180)}
              y1={size/2 + (R - 7) * Math.sin(tickAngle * Math.PI / 180)}
              x2={size/2 + (R + 7) * Math.cos(tickAngle * Math.PI / 180)}
              y2={size/2 + (R + 7) * Math.sin(tickAngle * Math.PI / 180)}
              stroke={NAVY} strokeWidth="2" opacity="0.5" />
      )}
      <text x={size/2} y={size/2 + 5} textAnchor="middle"
            style={{ fontSize: 16, fontWeight: 700,
                     fill: hit ? hitColor : NAVY }}>
        {Math.round(val)}%
      </text>
    </svg>
  )
}

function MicroStack({ segs, height = 8, legend = true }) {
  const [go, setGo] = useState(false)
  useEffect(() => { const id = setTimeout(() => setGo(true), 140)
    return () => clearTimeout(id) }, [])
  const total = segs.reduce((a, [n]) => a + n, 0) || 1
  return (
    <div>
      <div style={{ display: 'flex', height, borderRadius: 999,
                    overflow: 'hidden', background: '#eef2f8' }}>
        {segs.map(([n, color], i) => (
          <div key={i} className="ops-seg"
               style={{ width: go ? `${(n / total) * 100}%` : '0%',
                        background: color,
                        borderRight: i < segs.length - 1 ? '2px solid #fff' : 'none' }} />
        ))}
      </div>
      {legend && (
        <div style={{ display: 'flex', gap: 10, marginTop: 7, fontSize: 10.5,
                      flexWrap: 'wrap' }}>
          {segs.map(([n, color, name], i) => (
            <span key={i} style={{ display: 'inline-flex', gap: 4,
                                   alignItems: 'center', color: GRAY }}>
              <span style={{ width: 7, height: 7, borderRadius: 2,
                             background: color }} />
              <strong style={{ color: NAVY }}>{n.toLocaleString()}</strong> {name}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function CoverageRing({ segs, total, centerLabel, centerSub }) {
  // An SVG ring, animated by stroke-dasharray after mount. 2px surface gaps
  // between segments; the ordered blue ramp carries the tiers.
  const [go, setGo] = useState(false)
  useEffect(() => { const id = setTimeout(() => setGo(true), 120)
    return () => clearTimeout(id) }, [])
  const R = 62, C = 2 * Math.PI * R
  const gap = 3
  let offset = 0
  const arcs = segs.map(([n, color]) => {
    const frac = total ? n / total : 0
    const len = Math.max(0, frac * C - gap)
    const a = { color, len, offset }
    offset += frac * C
    return a
  })
  const pctText = total ? Math.round(((segs[0][0] + segs[1][0]) / total) * 100) : 0
  const shown = useCountUp(go ? pctText : 0, 900)
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 28,
                  flexWrap: 'wrap' }}>
      <svg width="170" height="170" viewBox="0 0 170 170"
           role="img" aria-label={centerLabel}>
        <circle cx="85" cy="85" r={R} fill="none" stroke="#eef2f8"
                strokeWidth="20" />
        {arcs.map((a, i) => (
          <circle key={i} cx="85" cy="85" r={R} fill="none" stroke={a.color}
                  strokeWidth="20" strokeLinecap="butt"
                  strokeDasharray={`${go ? a.len : 0} ${C}`}
                  strokeDashoffset={-a.offset}
                  transform="rotate(-90 85 85)"
                  style={{ transition: 'stroke-dasharray 1s cubic-bezier(.25,.8,.25,1)' }} />
        ))}
        <text x="85" y="82" textAnchor="middle"
              style={{ fontSize: 30, fontWeight: 700, fill: NAVY }}>
          {shown}%
        </text>
        <text x="85" y="102" textAnchor="middle"
              style={{ fontSize: 9, fontWeight: 700, fill: GRAY,
                       letterSpacing: 0.3, textTransform: 'uppercase' }}>
          {centerSub}
        </text>
      </svg>
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

export default function QualityConsole() {
  const client = useOpsClient()
  const { t, lang } = useLocale()
  const [tab, setTab] = useState('records')
  const [filters, setFilters] = useState({ nationality: '', destination: '',
                                           purpose: '', requirement: '',
                                           confidence: '' })
  const [reg, setReg] = useState(null)
  const [data, setData] = useState(null)
  const [changes, setChanges] = useState(null)
  const [issues, setIssues] = useState(null)
  const [freshness, setFreshness] = useState(null)
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
      else if (tab === 'freshness') setFreshness(await client.get('/database/freshness'))
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
    const src = t0.filter((r) => r.source_url).length
    const sub = t0.filter((r) => r.source_check === 'human-quote'
      || r.source_check === 'grounded-consistent').length
    return { total: t0.length,
             completeness_rate: t0.length ? complete / t0.length : null,
             source_coverage: t0.length ? src / t0.length : null,
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
        <div style={{ display: 'flex', gap: 6, margin: '20px 0 18px',
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
            {/* Spot-check filter bar: country autocomplete + enum dropdowns */}
            <div style={{ ...card, padding: '14px 18px', display: 'flex',
                          gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
              <span style={{ fontSize: 11, fontWeight: 800, letterSpacing: 1,
                             color: GRAY, textTransform: 'uppercase' }}>
                {t('ops.spotCheck')}
              </span>
              <CountryFilter value={filters.nationality} countries={countries}
                             placeholder={t('ops.passport')}
                             onCommit={set('nationality')} />
              <CountryFilter value={filters.destination} countries={countries}
                             placeholder={t('ops.destination')}
                             onCommit={set('destination')} />
              <select value={filters.purpose} onChange={(e) => set('purpose')(e.target.value)}
                      style={{ ...input, color: filters.purpose ? NAVY : GRAY }}>
                <option value="">{t('ops.anyPurpose')}</option>
                {PURPOSES.map((p) => (
                  <option key={p} value={p}>{t(PURPOSE_KEY[p])}</option>
                ))}
              </select>
              <select value={filters.requirement}
                      onChange={(e) => set('requirement')(e.target.value)}
                      style={{ ...input, color: filters.requirement ? NAVY : GRAY }}>
                <option value="">{t('ops.anyRequirement')}</option>
                <option value="Visa-free">{t('ops.req.free')}</option>
                <option value="Visa on Arrival">{t('ops.req.voa')}</option>
                <option value="Visa Required in Advance">{t('ops.req.advance')}</option>
                <option value="Conditional">{t('ops.req.conditional')}</option>
              </select>
              <select value={filters.confidence}
                      onChange={(e) => set('confidence')(e.target.value)}
                      style={{ ...input, color: filters.confidence ? NAVY : GRAY }}>
                <option value="">{t('ops.anyConfidence')}</option>
                <option value="High">{t('ops.conf.high')}</option>
                <option value="Medium">{t('ops.conf.medium')}</option>
                <option value="Low">{t('ops.conf.low')}</option>
              </select>
              <div style={{ flex: 1 }} />
              <button className="btn btn--sm" onClick={exportXlsx} disabled={busy}
                      data-testid="ops-export"
                      style={{ borderRadius: 999, fontWeight: 700,
                               background: BLUE, color: '#fff',
                               padding: '9px 18px' }}>
                ⬇ {t('ops.export')}
              </button>
            </div>

            {s && (() => {
              const recs = data?.records || []
              const req = { free: 0, voa: 0, adv: 0, cond: 0 }
              const tiers = { hq: 0, gc: 0, ref: 0, un: 0 }
              for (const r of recs) {
                if (r.visa_requirement === 'Visa-free') req.free++
                else if (r.visa_requirement === 'Visa on Arrival') req.voa++
                else if (r.visa_requirement === 'Conditional') req.cond++
                else req.adv++
                if (r.source_check === 'human-quote') tiers.hq++
                else if (r.source_check === 'grounded-consistent') tiers.gc++
                else if (r.source_check === 'reference') tiers.ref++
                else tiers.un++
              }
              return (
                <div style={{ ...card, display: 'grid',
                              gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))' }}>
                  <BandCell delay={0}>
                    <BandLabel>{t('ops.stat.records')}</BandLabel>
                    <BandBig>{totalCount.toLocaleString()}</BandBig>
                    <div style={{ marginTop: 12 }}>
                      <MicroStack segs={[
                        [req.free, '#1d4ed8', t('ops.req.free')],
                        [req.voa, '#4f8ef8', t('ops.req.voa')],
                        [req.adv, '#9cc2fb', t('ops.req.advance')],
                        [req.cond, '#d9e7fd', t('ops.req.conditional')],
                      ]} />
                    </div>
                  </BandCell>
                  <BandCell delay={80}>
                    <BandLabel>{t('ops.stat.complete')}</BandLabel>
                    <div style={{ display: 'flex', alignItems: 'center',
                                  gap: 14, marginTop: 6 }}>
                      <RingGauge pct={(s.completeness_rate || 0) * 100} target={99} />
                      <div style={{ fontSize: 11, color: GRAY, lineHeight: 1.5 }}>
                        {t('ops.stat.completeSub')}
                        <div style={{ color: NAVY, fontWeight: 700 }}>
                          {t('ops.stat.target')} 99%
                        </div>
                      </div>
                    </div>
                  </BandCell>
                  <BandCell delay={160}>
                    <BandLabel>{t('ops.stat.sources')}</BandLabel>
                    <div style={{ display: 'flex', alignItems: 'center',
                                  gap: 14, marginTop: 6 }}>
                      <RingGauge pct={(s.source_coverage || 0) * 100} target={100} />
                      <div style={{ fontSize: 11, color: GRAY, lineHeight: 1.5 }}>
                        {t('ops.stat.sourcesSub')}
                        <div style={{ color: NAVY, fontWeight: 700 }}>
                          {t('ops.stat.target')} 100%
                        </div>
                      </div>
                    </div>
                  </BandCell>
                  <BandCell delay={240}>
                    <BandLabel>{t('ops.stat.substantiated')}</BandLabel>
                    <BandBig color={BLUE}>{(s.substantiated ?? 0).toLocaleString()}</BandBig>
                    <div style={{ marginTop: 12 }}>
                      <MicroStack segs={[
                        [tiers.hq, '#1d4ed8', t('ops.check.quoted')],
                        [tiers.gc, '#4f8ef8', t('ops.check.grounded')],
                        [tiers.ref, '#9cc2fb', t('ops.check.reference')],
                        [tiers.un, '#e4edfc', t('ops.check.none')],
                      ]} />
                    </div>
                  </BandCell>
                  <BandCell delay={320}>
                    <BandLabel>{t('ops.stat.confidence')}</BandLabel>
                    <div style={{ marginTop: 12 }}>
                      <MicroStack height={10} segs={[
                        [s.high, SEQ.high, t('ops.conf.high')],
                        [s.medium, SEQ.medium, t('ops.conf.medium')],
                        [s.low, SEQ.low, t('ops.conf.low')],
                      ]} />
                    </div>
                    <div style={{ fontSize: 11, color: GRAY, marginTop: 9 }}>
                      {t('ops.stat.confidenceSub')}
                    </div>
                  </BandCell>
                </div>
              )
            })()}
            {busy && <div style={{ color: GRAY, fontSize: 13 }}>{t('ops.loading')}</div>}
            <RecordsTable records={records.slice(0, shown)} total={records.length} onFlag={flag} onRelease={release} t={t} flagOf={flagOf} typeNames={typeNames} />
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
          const auto = all.filter((i) => isOpen(i) && i.reported_by === 'freshness_monitor')
          const open = [...human, ...auto]
          const closed = all.filter((i) => !isOpen(i)).reverse()
          const parseAuto = (note) => {
            const m = /^Automatic source check against (\S+?): (.*)$/s.exec(note || '')
            if (!m) return null
            const segs = []
            for (const part of m[2].split(/;\s+(?=[a-z_]+: page says)/)) {
              const pm = /^([a-z_,\s]+): page says\s*([\s\S]*?)(?:\s*\(quote:\s*([\s\S]*?)\)?)?$/.exec(part.trim())
              if (pm) segs.push({ field: pm[1].trim(), says: pm[2].trim(),
                                  quote: (pm[3] || '').trim() })
            }
            return { url: m[1].replace(/[:;,]$/, ''), segs }
          }
          const fmtSays = (field, says, note) => {
            let v = String(says || '').trim().replace(/^"|"$/g, '')
            // The monitor writes literal "null"/"None" when the page proposes
            // clearing a value; show a placeholder, not the token.
            if (/^(null|none|undefined)$/i.test(v)) return '·'
            if (/^[\[{]/.test(v)) {
              // A product-table proposal: name the visa types instead of
              // printing JSON (the note is capped, so parse may not work).
              const types = [...v.matchAll(/"type":\s*"([^"]+)"/g)]
                .map((m) => typeNames?.[m[1]] || m[1])
              if (types.length) {
                return `${types.length} ${t('ops.productsProposed')}: ${types.join(' · ')}`
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
                  <div style={{ display: 'grid',
                                gridTemplateColumns: 'minmax(86px, 130px) 1fr 1fr',
                                background: '#f7f9fc', fontSize: 11.5,
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
                      return v.length > 160 ? v.slice(0, 160) + '…' : v
                    })()
                    const same = cur != null &&
                      String(cur).trim().toLowerCase() ===
                      String(page).trim().toLowerCase()
                    return (
                      <div key={i} style={{ borderTop: `1px solid ${BORDER}` }}>
                        <div style={{ display: 'grid',
                                      gridTemplateColumns:
                                        'minmax(86px, 130px) 1fr 1fr',
                                      fontSize: 12.5,
                                      background: i % 2 ? '#fbfcfe' : '#fff' }}>
                          <span style={{ padding: '8px 10px', fontWeight: 700,
                                         color: NAVY }}>
                            {fieldLabel(g.field)}
                          </span>
                          <span style={{ padding: '8px 10px' }}>
                            {cur == null
                              ? diffChip(t('ops.diff.empty'), 'empty')
                              : diffChip(cur, same ? 'new' : 'old')}
                          </span>
                          <span style={{ padding: '8px 10px' }}>
                            {diffChip(page, 'new')}
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
          const Card = ({ it, active }) => (
            <div className="ops-lift" style={{ ...card, padding: '14px 18px',
                          borderLeft: `3px solid ${active ? RED : '#d6dee9'}` }}>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center',
                            flexWrap: 'wrap' }}>
                <strong style={{ color: NAVY, fontSize: 13.5 }}>
                  {flagOf((it.route || {}).nationality || (it.route || {}).passport_nationality)}
                  {' '}
                  {(it.route || {}).nationality || (it.route || {}).passport_nationality}
                  {' → '}
                  {flagOf((it.route || {}).destination || (it.route || {}).destination_country)}
                  {' '}
                  {(it.route || {}).destination || (it.route || {}).destination_country}
                </strong>
                {it.reported_by === 'freshness_monitor'
                  ? <Chip color={BLUE} filled={false}>{t('ops.autoCheck')}</Chip>
                  : <Chip color={AMBER} filled={false}>{t('ops.readerReport')}</Chip>}
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
                  {(it.created_at || '').slice(0, 16).replace('T', ' ')}
                </span>
              </div>
              {it.reported_by === 'freshness_monitor'
                ? <AutoBody note={it.note} issue={it} />
                : <div style={{ fontSize: 13, color: NAVY, marginTop: 8 }}>{it.note}</div>}
              {it.resolution && (
                <div style={{ fontSize: 12.5, color: GREEN, marginTop: 6 }}>
                  ✓ {it.resolution}
                </div>
              )}
              {active && <IssueActions issue={it} onResolve={resolveIssue} t={t} />}
            </div>
          )
          return (
            <div style={{ display: 'grid', gap: 10 }} className="ops-fade">
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                <span style={{ fontSize: 13, fontWeight: 800, color: NAVY }}>
                  {t('ops.issues.open')}
                </span>
                <Chip color={open.length ? RED : GREEN}>{open.length}</Chip>
              </div>
              {open.length === 0 && !busy && (
                <div style={{ ...card, padding: 22, color: GRAY, fontSize: 13.5,
                              textAlign: 'center' }}>{t('ops.noReports')}</div>
              )}
              {open.map((it) => <Card key={it.id} it={it} active />)}
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
            return sv.length > 56 ? sv.slice(0, 56) + '…' : sv
          }
          const all = changes?.changes || []
          const counts = { '': all.length }
          for (const c of all) counts[c.action] = (counts[c.action] || 0) + 1
          const list = all.filter((c) => !changeFilter || c.action === changeFilter)
          const byDay = []
          for (const c of list) {
            const day = (c.at || '').slice(0, 10)
            const g = byDay[byDay.length - 1]
            if (g && g.day === day) g.items.push(c)
            else byDay.push({ day, items: [c] })
          }
          const ValueChip = ({ v, kind }) => (
            <span title={typeof v === 'string' ? v : undefined}
                  style={{ display: 'inline-block', padding: '2px 8px',
                           borderRadius: 7, fontSize: 12, maxWidth: 340,
                           overflow: 'hidden', textOverflow: 'ellipsis',
                           whiteSpace: 'nowrap', verticalAlign: 'bottom',
                           ...(kind === 'old'
                             ? { background: '#fdf1f4', color: '#a13d55',
                                 textDecoration: 'line-through' }
                             : kind === 'del'
                               ? { background: '#fdf1f4', color: RED, fontWeight: 700 }
                               : { background: '#eefaf3', color: '#0b7a44',
                                   fontWeight: 700 }) }}>
              {v}
            </span>
          )
          return (
            <div className="ops-fade" style={{ display: 'grid', gap: 14 }}>
              {/* Segmented action filter with live counts */}
              <div style={{ display: 'inline-flex', background: '#fff',
                            border: `1px solid ${BORDER}`, borderRadius: 999,
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
                          {flagOf((c.route || {}).passport_nationality)} {(c.route || {}).passport_nationality}
                          {' → '}
                          {flagOf((c.route || {}).destination_country)} {(c.route || {}).destination_country}
                        </strong>
                        <Chip color={AC[c.action] || GRAY}>
                          {t(`ops.act.${c.action}`) === `ops.act.${c.action}`
                            ? c.action : t(`ops.act.${c.action}`)}
                        </Chip>
                        <span style={{ color: GRAY, fontSize: 11.5 }}>
                          {c.origin === 'grounded_recheck' ? t('ops.autoCheck')
                            : c.origin === 'engine' ? t('ops.origin.engine') : c.origin}
                        </span>
                        <span style={{ marginLeft: 'auto', color: '#9aa8bd',
                                       fontSize: 11.5,
                                       fontFamily: 'ui-monospace, monospace' }}>
                          {(c.at || '').slice(11, 16)}
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
                          const cap = c.action === 'add' ? 4 : 5
                          const shownE = entries.slice(0, cap)
                          return [
                            ...shownE.map(([f, a, b]) => (
                              <div key={f} style={{ display: 'grid',
                                    gridTemplateColumns: '175px 1fr', gap: 10,
                                    alignItems: 'center', fontSize: 12 }}>
                                <span title={f}
                                      style={{ color: '#5b6a80',
                                               fontSize: 11.5, fontWeight: 700,
                                               overflow: 'hidden',
                                               textOverflow: 'ellipsis',
                                               whiteSpace: 'nowrap' }}>{fieldLabel(f)}</span>
                                <span style={{ minWidth: 0, display: 'flex',
                                               gap: 6, alignItems: 'center',
                                               flexWrap: 'wrap' }}>
                                  {a != null && c.action !== 'add' && [
                                    <ValueChip key="o" v={a} kind="old" />,
                                    <span key="s" style={{ color: '#b6c2d4' }}>→</span>,
                                  ]}
                                  <ValueChip v={b ?? '·'}
                                             kind={c.action === 'delete' ? 'del' : 'new'} />
                                </span>
                              </div>
                            )),
                            entries.length > cap && (
                              <div key="more" style={{ fontSize: 11.5,
                                    color: GRAY }}>
                                +{entries.length - cap} {t('ops.items')}
                              </div>
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
              <div style={{ display: 'grid', gap: 12,
                            gridTemplateColumns:
                              'repeat(auto-fit, minmax(175px, 1fr))' }}>
                {[
                  { label: t('ops.fresh.answers'), value: f.total,
                    sub: t('ops.stat.recordsSub'), accent: NAVY },
                  { label: t('ops.fresh.human'), value: f.human_verified,
                    accent: GREEN,
                    pct: f.total ? (f.human_verified / f.total) * 100 : null,
                    sub: t('ops.check.quoted') },
                  { label: t('ops.fresh.grounded'), value: f.grounded,
                    accent: BLUE,
                    pct: f.total ? (f.grounded / f.total) * 100 : null,
                    sub: t('ops.fresh.groundedSub') },
                  { label: t('ops.fresh.stale'), value: f.stale,
                    accent: f.stale ? AMBER : GREEN,
                    sub: t('ops.fresh.staleSub') },
                  { label: t('ops.fresh.disputed'), value: f.disputed,
                    accent: f.disputed ? RED : GREEN,
                    sub: t('ops.fresh.disputedSub') },
                ].map((p, i) => (
                  <div key={i} className="ops-lift"
                       style={{ ...card, padding: 0 }}>
                    <StatCell {...p} delay={i * 70} />
                  </div>
                ))}
              </div>
              <div style={{ ...card, padding: '24px 28px', display: 'flex',
                            flexWrap: 'wrap', gap: 26,
                            alignItems: 'center' }}>
                <CoverageRing segs={segs} total={f.total}
                              centerLabel={t('ops.fresh.coverage')}
                              centerSub={t('ops.fresh.coveredShort')} />
                <div style={{ display: 'grid', gap: 16, minWidth: 0,
                              flex: '1 1 260px' }}>
                  <div style={{ fontSize: 10.5, fontWeight: 800,
                                letterSpacing: 1, color: GRAY,
                                textTransform: 'uppercase' }}>
                    {t('ops.fresh.coverage')}
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
            </div>
          )
        })()}
      </div>
    </div>
  )
}
