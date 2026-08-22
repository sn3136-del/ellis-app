// The Database: a traveldoc-style requirements lookup, answered by the same
// Kimi-primary route decision the applicant journey trusts.
//
// One form, one designed answer page carrying trip information only. Static
// strings run through t() (so the top-right language picker translates them
// like every other screen), and the decision's own text is translated on
// demand through the same masked, cached Kimi K3 catalog pipe (fast, with
// an honest English fallback when a string cannot round-trip).
import { useEffect, useMemo, useRef, useState } from 'react'
import { Loading } from '../components/ui.jsx'
import { useLocale } from '../lib/locale.jsx'
import { createVisaClient } from '../lib/visaBackend.js'
import { newSession } from '../lib/visaSession.js'

const NAVY = 'var(--trip-navy, #0f294d)'
const GRAY = 'var(--trip-gray, #64748b)'
const BLUE = 'var(--trip-blue, #287dfa)'

const PURPOSES = [
  ['tourism', 'db.purpose.tourism'], ['business', 'db.purpose.business'],
  ['family_visit', 'db.purpose.family'], ['study', 'db.purpose.study'],
  ['work', 'db.purpose.work'], ['transit', 'db.purpose.transit'],
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
  embassy: 'Embassy or consulate',
  consulate: 'Embassy or consulate',
  evisa_portal: 'Official online portal',
  online: 'Official online portal',
  on_arrival: 'On arrival',
  mail: 'By mail',
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

function Tile({ label, value, href }) {
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
  const [departureCity, setDepartureCity] = useState('')
  const [transit, setTransit] = useState([])       // ISO3 stopover countries
  const [issueOpen, setIssueOpen] = useState(false)
  const [issueNote, setIssueNote] = useState('')
  const [issueDone, setIssueDone] = useState(false)
  // Dynamic-content translation overlay: original guidance string -> lang.
  const [tx, setTx] = useState({})

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

  const countries = useMemo(() => (reg?.countries || []).map((c) => ({
    value: c.alpha_3, label: `${c.flag ? c.flag + ' ' : ''}${c.name}`,
    search: `${c.name} ${c.alpha_2 || ''} ${c.alpha_3}`.toLowerCase(),
  })), [reg])
  const docTypes = reg?.travel_document_types || []

  // An answer the engine itself rated low confidence is HELD until a person
  // confirms it. Holding means the reader does not see the claims at all —
  // showing them under a warning would still be showing them.
  const held = result?.review_required === true
  const g = (result && !held) ? (result.guidance || null) : null

  // Every user-facing string the decision carries, translated in ONE masked,
  // cached Kimi catalog call whenever the UI language is not English.
  useEffect(() => {
    if (!g || lang === 'en') { setTx({}); return }
    const texts = [...new Set([
      asText(g.visa_category), asText(g.permitted_stay),
      asText(g.processing_time), asText(g.passport_validity),
      asText(g.photo_requirements), asText(g.onward_travel_evidence),
      asText(g.accommodation_evidence), asText(g.financial_evidence),
      humanizeEnum(g.application_channel),
      ...itemsOf(g.required_documents), ...itemsOf(g.forms),
      ...itemsOf(g.account_registration_steps), ...itemsOf(g.payment_process),
      ...itemsOf(g.submission_process), ...itemsOf(g.health_requirements),
      ...itemsOf(g.exceptions), ...itemsOf(g.uncertainty),
      ...itemsOf(result?.advisories),
    ].filter(Boolean))]
    if (!texts.length) { setTx({}); return }
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
    }).catch(() => { /* honest English fallback */ })
    return () => { live = false }
  }, [g, lang])
  const T = (s) => (s && tx[s]) || s

  // AI Q&A: a plain-language question, answered by the same engine as the form.
  async function askQuestion() {
    const q = question.trim()
    if (!q) return
    setAskBusy(true); setAskMsg(''); setError(''); setResult(null)
    try {
      const out = await client.databaseAsk(q)
      if (out.understood === false) {
        setAskMsg(t('db.askUnclear'))
      } else {
        if (out.route) {
          setNat(out.route.nationality); setDest(out.route.destination)
          if (out.route.travel_purpose) setPurpose(out.route.travel_purpose)
          writeHash({ nat: out.route.nationality, dest: out.route.destination,
                      purpose: out.route.travel_purpose, doc })
        }
        setResult(out)
      }
    } catch (e) {
      setAskMsg(e?.detail?.reason || e?.message || t('db.error'))
    }
    setAskBusy(false)
  }

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
      writeHash({ nat: useNat, dest: useDest, purpose: usePurpose, doc: useDoc })
    } catch (e) {
      setError(e?.detail?.reason || e?.detail?.detail || e?.message || t('db.error'))
      if (override.keepResult) setResult(null)
    }
    setBusy(false)
  }

  // A switcher on the answer page: set the control, then re-ask for it.
  function switchDoc(v) { setDoc(v); lookUp({ doc: v, keepResult: true }) }
  function switchPurpose(v) { setPurpose(v); lookUp({ purpose: v, keepResult: true }) }

  async function reportIssue() {
    try {
      await client.databaseReportIssue({
        nationality: nat, destination: dest, travel_purpose: purpose,
        travel_document_type: doc, field: '', note: issueNote.slice(0, 1000),
      })
      setIssueDone(true); setIssueNote('')
    } catch { setIssueDone(true) }   // a flag is never lost to the reader
  }

  const disp = g ? (DISPOSITION_VIEW[g.disposition] || null) : null
  const countryName = (code) => countries.find((c) => c.value === code)?.label || code

  const applySteps = g ? [
    ...itemsOf(g.account_registration_steps),
    ...itemsOf(g.payment_process),
    ...itemsOf(g.submission_process),
  ] : []
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
    <div className="page" style={{ maxWidth: 900, margin: '0 auto',
                                   padding: '26px 20px 60px' }}
         data-testid="travel-database">
      <button className="btn btn--sm btn--ghost" onClick={onBack}
              data-testid="database-back">← {t('db.menu')}</button>

      {/* Heading only while searching; the answer page opens straight on
          the verdict (owner decision, theming). */}
      {!result && (
        <div style={{ margin: '16px 0 6px', textAlign: 'center' }}>
          <h1 style={{ fontSize: 32, fontWeight: 800, color: NAVY, margin: 0,
                       letterSpacing: -0.6 }}>{t('db.title')}</h1>
          <div style={{ fontSize: 14.5, color: GRAY, marginTop: 8 }}>
            {t('db.sub')}
          </div>
        </div>
      )}

      {!result && (
        <div className="card anim-rise" style={{ padding: '20px 24px',
                                                 borderRadius: 20, marginTop: 18,
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
          <div style={{ fontSize: 11.5, color: GRAY, marginTop: 10,
                        textAlign: 'center' }}>{t('db.askOr')}</div>
        </div>
      )}

      {!result && (
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
                    <option key={d.code || d} value={d.code || d}>{d.name || d}</option>
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
              <input className="input" type="date" value={arrival}
                     onChange={(e) => setArrival(e.target.value)}
                     style={{ fontSize: 14, padding: '10px 14px', borderRadius: 12 }} />
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
              {t(PURPOSES.find(([v]) => v === purpose)?.[1] || 'db.purpose.tourism')}
            </div>
            <div style={{ fontSize: 29, fontWeight: 800,
                          color: disp?.color || NAVY,
                          marginTop: 8, letterSpacing: -0.4 }}
                 data-testid="database-disposition">
              {disp ? t(disp.key) : asText(g.disposition)}
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
                  <option key={d.code || d} value={d.code || d}>{d.name || d}</option>
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

          {/* At a glance */}
          <div style={{ display: 'grid', gap: 16, marginTop: 20,
                        gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))' }}>
            <Tile label={t('db.stay')} value={T(asText(g.permitted_stay))} />
            <Tile label={t('db.fee')} value={feeText(g.government_fee)} />
            <Tile label={t('db.processing')} value={T(asText(g.processing_time))} />
            <Tile label={t('db.channel')}
                  value={T(humanizeEnum(g.application_channel))}
                  href={g.official_portal_url || undefined} />
          </div>
          {asText(g.application_channel_detail) && (
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
                      value={sentence(asText(g.transit_requirement.note))
                             || (g.transit_requirement.required
                                 ? t('db.transitNeeded') : t('db.transitNotNeeded'))}
                      pill={g.transit_requirement.required
                            ? <Pill tone="warn">{t('db.transitNeeded')}</Pill>
                            : <Pill tone="no">{t('db.transitNotNeeded')}</Pill>} />
              </Section>
            </div>
          )}

          {/* Available visa types — every product for this route, each with
              its own entry, validity, stay and fee (Trip.com feedback: never
              one generic product). */}
          {Array.isArray(g.visa_products) && g.visa_products.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <Section title={t('db.visaTypes')} accent="#0f8a3d">
                <div style={{ display: 'grid', gap: 10 }}>
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
                          {' · ' + asText(vp.entry)}</span> : null}
                      </div>
                      <div style={{ color: NAVY }}>{T(asText(vp.validity)) || '—'}</div>
                      <div style={{ color: NAVY }}>
                        {vp.max_stay_days ? `${vp.max_stay_days} days`
                          : (T(asText(vp.notes)) || '—')}</div>
                      <div style={{ color: NAVY, fontWeight: 600 }}>
                        {feeText(vp.fee) || '—'}</div>
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
                        `${g.arrival_card.name || t('db.arrivalCard')}${g.arrival_card.submission_window ? ', ' + g.arrival_card.submission_window : ''}`} />
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
            {/* Present but inert, per owner instruction. */}
            <button className="btn btn--primary" data-testid="database-process"
                    onClick={() => {}}
                    style={{ fontSize: 15, fontWeight: 800, padding: '13px 32px',
                             borderRadius: 999 }}>
              {t('db.process')}
            </button>
            <button className="btn btn--ghost" onClick={() => { setResult(null); clearHash() }}
                    data-testid="database-again"
                    style={{ fontSize: 14, borderRadius: 999 }}>
              {t('db.newSearch')}
            </button>
          </div>

          {/* Source + confidence — Trip.com's traceability requirement. */}
          {(g.source_url || g.confidence) && (
            <div style={{ fontSize: 12, color: GRAY, marginTop: 16,
                          textAlign: 'center' }}>
              {g.source_url && (
                <a href={g.source_url} target="_blank" rel="noreferrer"
                   style={{ color: GRAY, textDecoration: 'underline' }}>
                  {t('db.source')}
                </a>
              )}
              {g.source_url && g.confidence ? ' · ' : ''}
              {g.confidence ? t('db.confidence.' + g.confidence) : ''}
              {' · '}
              <span role="button" tabIndex={0}
                    data-testid="database-report"
                    onClick={() => setIssueOpen(!issueOpen)}
                    style={{ color: GRAY, textDecoration: 'underline',
                             cursor: 'pointer' }}>
                {t('db.reportIssue')}
              </span>
            </div>
          )}

          {/* Information-quality feedback: a reader flags what looks wrong.
              Ellis records the flag against this exact route for a person to
              work — it never silently rewrites the answer on a report. */}
          {issueOpen && (
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
                  <textarea className="input" rows={3} value={issueNote}
                            data-testid="database-report-note"
                            placeholder={t('db.reportPlaceholder')}
                            onChange={(e) => setIssueNote(e.target.value)}
                            style={{ fontSize: 13.5, padding: '10px 12px',
                                     borderRadius: 12, width: '100%',
                                     resize: 'vertical' }} />
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
