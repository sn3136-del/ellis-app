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
    if (v.name) return [v.name, v.applicability ? `(${v.applicability})` : '']
      .filter(Boolean).join(' ')
    return Object.values(v).map((x) => asText(x)).filter(Boolean).join(', ')
  }
  return String(v)
}

const sentence = (s) =>
  s && /^[a-z]/.test(s) ? s.charAt(0).toUpperCase() + s.slice(1) : s

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
    const list = s ? options.filter((o) => o.search.includes(s)) : options
    return list.slice(0, 60)
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

function Section({ title, accent, children, wide }) {
  return (
    <div className="card" style={{ padding: '26px 30px', borderRadius: 20,
                                   textAlign: 'left', border: 'none',
                                   boxShadow: '0 1px 3px rgba(15,41,77,0.06)',
                                   gridColumn: wide ? '1 / -1' : undefined }}>
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

function Fact({ label, value }) {
  const v = sentence(asText(value))
  if (!v) return null
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 20,
                  padding: '11px 0', fontSize: 13.5, lineHeight: 1.55,
                  borderBottom: '1px solid #f3f5f9' }}>
      <div style={{ color: GRAY, flex: 'none' }}>{label}</div>
      <div style={{ color: NAVY, fontWeight: 600, textAlign: 'right' }}>{v}</div>
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

function Tile({ label, value }) {
  const v = asText(value)
  if (!v) return null
  return (
    <div className="card" style={{ borderRadius: 18, padding: '18px 20px',
                                   textAlign: 'left', minWidth: 0, border: 'none',
                                   boxShadow: '0 1px 3px rgba(15,41,77,0.06)' }}>
      <div style={{ fontSize: 10.5, fontWeight: 800, letterSpacing: 0.9,
                    color: GRAY, textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 14, fontWeight: 700, color: NAVY, marginTop: 8,
                    lineHeight: 1.45 }}>{sentence(v)}</div>
    </div>
  )
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
  // Dynamic-content translation overlay: original guidance string -> lang.
  const [tx, setTx] = useState({})

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

  const g = result?.guidance || null

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

  async function lookUp() {
    if (!nat || !dest) return
    setBusy(true); setError(''); setResult(null)
    try {
      const out = await client.databaseLookup({
        nationality: nat, destination: dest, travel_document_type: doc,
        travel_purpose: purpose, arrival_date: arrival || '',
      })
      setResult(out)
    } catch (e) {
      setError(e?.detail?.reason || e?.detail?.detail || e?.message || t('db.error'))
    }
    setBusy(false)
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

  const yesNo = (v) => v === true ? t('db.required') : v === false ? t('db.notRequired') : null
  const entryFacts = g ? [
    [t('db.biometrics'), yesNo(g.biometrics_required)],
    [t('db.interview'), yesNo(g.interview_required)],
    [t('db.appointment'), yesNo(g.appointment_required)],
    [t('db.insurance'), yesNo(g.insurance_required)],
  ].filter(([, v]) => v) : []

  const documents = g ? itemsOf(g.required_documents).map(T) : []
  const goodToKnow = g ? [...itemsOf(g.exceptions), ...itemsOf(result.advisories || []),
                          ...itemsOf(g.uncertainty)].map(T) : []
  const health = g ? itemsOf(g.health_requirements).map(T) : []

  const label = (k) => (
    <span style={{ fontSize: 13, fontWeight: 700, color: NAVY }}>{t(k)}</span>
  )

  return (
    <div className="page" style={{ maxWidth: 900, margin: '0 auto',
                                   padding: '26px 20px 60px' }}
         data-testid="travel-database">
      <button className="btn btn--sm btn--ghost" onClick={onBack}
              data-testid="database-back">← {t('db.menu')}</button>

      <div style={{ margin: '16px 0 6px', textAlign: 'center' }}>
        <h1 style={{ fontSize: 32, fontWeight: 800, color: NAVY, margin: 0,
                     letterSpacing: -0.6 }}>{t('db.title')}</h1>
        <div style={{ fontSize: 14.5, color: GRAY, marginTop: 8 }}>
          {t('db.sub')}
        </div>
      </div>

      {!result && (
        <div className="card anim-rise" style={{ padding: '26px 28px',
                                                 borderRadius: 20, marginTop: 18,
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
                {T(asText(g.visa_category))}
              </div>
            )}
          </div>

          {/* At a glance */}
          <div style={{ display: 'grid', gap: 16, marginTop: 20,
                        gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))' }}>
            <Tile label={t('db.stay')} value={T(asText(g.permitted_stay))} />
            <Tile label={t('db.fee')} value={feeText(g.government_fee)} />
            <Tile label={t('db.processing')} value={T(asText(g.processing_time))} />
            <Tile label={t('db.channel')} value={T(humanizeEnum(g.application_channel))} />
          </div>

          {/* Organized two-column brief */}
          <div style={{ display: 'grid', gap: 16, marginTop: 16,
                        gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))' }}>
            {documents.length > 0 && (
              <Section title={t('db.documents')} accent="#0f8a3d" wide>
                <div style={{ columns: documents.length > 5 ? 2 : 1,
                              columnGap: 32 }}>
                  <Bullets items={documents} mark="✓" markColor="#0f8a3d" />
                </div>
              </Section>
            )}

            {(asText(g.passport_validity) || asText(g.photo_requirements) ||
              g.passport_validity_requirement?.months) && (
              <Section title={t('db.passportPhoto')} accent={BLUE}>
                <Fact label={t('db.validity')} value={T(asText(g.passport_validity))} />
                {g.passport_validity_requirement?.months ? (
                  <Fact label={t('db.validityRule')}
                        value={t('db.validityMonths',
                                 { n: g.passport_validity_requirement.months })} />
                ) : null}
                <Fact label={t('db.photo')} value={T(asText(g.photo_requirements))} />
              </Section>
            )}

            {(entryFacts.length > 0 || g.arrival_card?.required || health.length > 0) && (
              <Section title={t('db.entry')} accent={BLUE}>
                {entryFacts.map(([l, v]) => <Fact key={l} label={l} value={v} />)}
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
            )}

            {(asText(g.onward_travel_evidence) || asText(g.accommodation_evidence) ||
              asText(g.financial_evidence)) && (
              <Section title={t('db.evidence')} accent={BLUE}>
                <Fact label={t('db.onward')} value={T(asText(g.onward_travel_evidence))} />
                <Fact label={t('db.accommodation')} value={T(asText(g.accommodation_evidence))} />
                <Fact label={t('db.finances')} value={T(asText(g.financial_evidence))} />
              </Section>
            )}

            {(itemsOf(g.forms).length > 0 || g.official_portal_url) && (
              <Section title={t('db.formsPortal')} accent={BLUE}>
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
            )}

            {goodToKnow.length > 0 && (
              <Section title={t('db.goodToKnow')} accent="#9a6200" wide>
                <Bullets items={goodToKnow} mark="→" markColor="#9a6200" />
              </Section>
            )}

            {applySteps.length > 0 && (
              <Section title={t('db.steps')} accent={NAVY} wide>
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
            <button className="btn btn--ghost" onClick={() => { setResult(null) }}
                    data-testid="database-again"
                    style={{ fontSize: 14, borderRadius: 999 }}>
              {t('db.newSearch')}
            </button>
          </div>
        </div>
      )}

      {result && !g && (
        <div className="card anim-rise" style={{ padding: 22, borderRadius: 20,
                                                 marginTop: 16, maxWidth: 560,
                                                 marginLeft: 'auto',
                                                 marginRight: 'auto' }}>
          <div style={{ fontSize: 14, color: NAVY, fontWeight: 700 }}>
            {t('db.noDecision')}
          </div>
          <button className="btn btn--ghost" style={{ marginTop: 12 }}
                  onClick={() => setResult(null)}>{t('db.newSearch')}</button>
        </div>
      )}
    </div>
  )
}
