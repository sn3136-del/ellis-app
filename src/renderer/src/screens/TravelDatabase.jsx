// The Database — traveldoc-style requirements lookup, answered by the same
// Kimi-primary route decision the applicant journey trusts.
//
// One form (nationality, document, destination, purpose, optional date), one
// answer page carrying everything the decision knows. Repeat lookups are
// instant (decision cache); a stale entry serves at once and refreshes
// behind. The answer page shows TRIP INFORMATION ONLY — engine labels,
// cache flags and boundary prose are hidden by owner decision (theming).
import { useEffect, useMemo, useRef, useState } from 'react'
import { Loading } from '../components/ui.jsx'
import { createVisaClient } from '../lib/visaBackend.js'
import { newSession } from '../lib/visaSession.js'

const NAVY = 'var(--trip-navy, #0f294d)'
const GRAY = 'var(--trip-gray, #64748b)'
const BLUE = 'var(--trip-blue, #287dfa)'

const PURPOSES = [
  ['tourism', 'Tourism'], ['business', 'Business'], ['family_visit', 'Family visit'],
  ['study', 'Study'], ['work', 'Work'], ['transit', 'Transit'],
]

// The engine's own vocabulary (kimi_primary.DISPOSITIONS), in a traveller's
// words, each with its colour and tint.
const DISPOSITION_VIEW = {
  VISA_EXEMPT: { title: 'No visa needed', color: '#0f8a3d', tint: '#eefaf1' },
  ELECTRONIC_AUTHORIZATION_REQUIRED:
    { title: 'Electronic travel authorization required', color: '#9a6200', tint: '#fff7e8' },
  VISA_REQUIRED: { title: 'Visa required', color: '#b3261e', tint: '#fdeeed' },
  CONDITIONAL: { title: 'Depends on your situation', color: '#9a6200', tint: '#fff7e8' },
}

// The decision's fields arrive as strings, lists, or small objects — render
// them all as words, never as a raw object (a raw object crashes React).
function asText(v) {
  if (v === null || v === undefined || v === '') return null
  if (typeof v === 'string' || typeof v === 'number') return String(v)
  if (typeof v === 'boolean') return v ? 'Yes' : 'No'
  if (Array.isArray(v)) return v.map((x) => asText(x)).filter(Boolean).join('; ')
  if (typeof v === 'object') {
    if (v.field && v.reason) return `${v.field}: ${v.reason}`
    if (v.name) return [v.name, v.applicability ? `(${v.applicability})` : '']
      .filter(Boolean).join(' ')
    return Object.entries(v)
      .filter(([, x]) => x !== null && x !== undefined && x !== '')
      .map(([k, x]) => `${k.replace(/_/g, ' ')}: ${asText(x)}`).join(' — ')
  }
  return String(v)
}

function itemsOf(v) {
  if (!v) return []
  return (Array.isArray(v) ? v : [v]).map((x) => asText(x)).filter(Boolean)
}

function feeText(fee) {
  if (!fee || typeof fee !== 'object') return null
  const amt = fee.amount
  if (amt === 0) return 'None'
  if (!amt && amt !== 0) return null
  return `${amt} ${fee.currency || ''}`.trim()
}

// Type-ahead country picker: type to filter, click to choose.
function CountryCombo({ value, options, onChange, placeholder, testid }) {
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
        placeholder={current ? current.label : (placeholder || 'Type to search…')}
        onFocus={() => { setOpen(true); setQ('') }}
        onChange={(e) => setQ(e.target.value)}
        onBlur={() => setTimeout(() => setOpen(false), 150)} />
      {open && (
        <div className="card" style={{ position: 'absolute', zIndex: 40,
          top: '100%', left: 0, right: 0, maxHeight: 236, overflowY: 'auto',
          marginTop: 4, background: 'var(--bg)',
          boxShadow: '0 8px 24px rgba(0,0,0,0.12)' }}>
          {filtered.length === 0
            ? <div style={{ padding: 10, fontSize: 13, color: GRAY }}>No match — keep typing</div>
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

function Section({ title, children }) {
  if (children === null || children === undefined) return null
  return (
    <div className="card" style={{ padding: '18px 22px', borderRadius: 18,
                                   marginTop: 14, textAlign: 'left' }}>
      <div style={{ fontSize: 11.5, fontWeight: 800, letterSpacing: 1,
                    color: GRAY, textTransform: 'uppercase', marginBottom: 10 }}>
        {title}
      </div>
      {children}
    </div>
  )
}

function Fact({ label, value }) {
  const v = asText(value)
  if (!v) return null
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '200px 1fr', gap: 12,
                  padding: '8px 0', fontSize: 13.5 }}>
      <div style={{ color: GRAY }}>{label}</div>
      <div style={{ color: NAVY, fontWeight: 600 }}>{v}</div>
    </div>
  )
}

function Bullets({ items }) {
  const list = itemsOf(items)
  if (!list.length) return null
  return (
    <ul style={{ margin: 0, paddingLeft: 0, listStyle: 'none' }}>
      {list.map((x, i) => (
        <li key={i} style={{ display: 'flex', gap: 10, alignItems: 'flex-start',
                             padding: '5px 0', fontSize: 13.5, color: NAVY }}>
          <span style={{ color: BLUE, fontWeight: 800, lineHeight: '20px' }}>•</span>
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
    <div style={{ background: 'var(--bg-soft, #f5f7fa)', borderRadius: 14,
                  padding: '14px 16px', textAlign: 'left', minWidth: 0 }}>
      <div style={{ fontSize: 11, fontWeight: 800, letterSpacing: 0.8,
                    color: GRAY, textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 14.5, fontWeight: 700, color: NAVY, marginTop: 5,
                    lineHeight: 1.35 }}>{v}</div>
    </div>
  )
}

export default function TravelDatabase({ onBack }) {
  const clientRef = useRef(null)
  if (!clientRef.current) clientRef.current = createVisaClient(newSession())
  const client = clientRef.current

  const [reg, setReg] = useState(null)
  const [nat, setNat] = useState('')
  const [doc, setDoc] = useState('ordinary_passport')
  const [dest, setDest] = useState('')
  const [purpose, setPurpose] = useState('tourism')
  const [arrival, setArrival] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)

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
      setError(e?.detail?.reason || e?.detail?.detail || e?.message
               || 'The lookup did not complete — try again.')
    }
    setBusy(false)
  }

  const g = result?.guidance || null
  const disp = g ? (DISPOSITION_VIEW[g.disposition] ||
                    { title: String(g.disposition || 'Route decision'),
                      color: NAVY, tint: '#eef4ff' }) : null
  const countryName = (code) => countries.find((c) => c.value === code)?.label || code

  // "How to apply", in the applicant's own sequence: registration, payment,
  // submission — the route's real steps from the decision itself.
  const applySteps = g ? [
    ...itemsOf(g.account_registration_steps),
    ...itemsOf(g.payment_process),
    ...itemsOf(g.submission_process),
  ] : []
  // The official-portal link rides ON the step it belongs to: the first step
  // that happens on the portal (register / apply / online / the form). When
  // no step reads that way, the link stands after the list instead.
  const portalStepIndex = g?.official_portal_url
    ? applySteps.findIndex((x) =>
        /register|portal|online|website|e-?visa|application form|apply/i.test(x))
    : -1

  const entryFacts = g ? [
    ['Biometrics', g.biometrics_required],
    ['Interview', g.interview_required],
    ['Appointment', g.appointment_required],
    ['Travel insurance', g.insurance_required],
  ].filter(([, v]) => v !== null && v !== undefined) : []

  return (
    <div className="page" style={{ maxWidth: 880, margin: '0 auto',
                                   padding: '26px 20px 60px' }}
         data-testid="travel-database">
      <button className="btn btn--sm btn--ghost" onClick={onBack}
              data-testid="database-back">← Menu</button>

      <div style={{ margin: '14px 0 4px' }}>
        <h1 style={{ fontSize: 30, fontWeight: 800, color: NAVY, margin: 0,
                     letterSpacing: -0.6 }}>Database</h1>
        <div style={{ fontSize: 14, color: GRAY, marginTop: 6 }}>
          What does this trip need? Pick the route — the answer covers the
          latest requirements end to end.
        </div>
      </div>

      {!result && (
        <div className="card anim-rise" style={{ padding: 22, borderRadius: 20,
                                                 marginTop: 16 }}
             data-testid="database-form">
          <div style={{ display: 'grid', gap: 14 }}>
            <label style={{ display: 'grid', gap: 5, textAlign: 'left' }}>
              <span style={{ fontSize: 13, fontWeight: 700, color: NAVY }}>Nationality *</span>
              <CountryCombo value={nat} options={countries} onChange={setNat}
                            placeholder="Type a country or code…"
                            testid="database-nationality" />
            </label>
            <label style={{ display: 'grid', gap: 5, textAlign: 'left' }}>
              <span style={{ fontSize: 13, fontWeight: 700, color: NAVY }}>Travel document</span>
              <select className="select" value={doc}
                      onChange={(e) => setDoc(e.target.value)}
                      style={{ fontSize: 14, padding: '11px 14px', borderRadius: 12 }}>
                {(docTypes.length ? docTypes : [{ code: 'ordinary_passport', name: 'Ordinary passport' }])
                  .map((d) => (
                    <option key={d.code || d} value={d.code || d}>{d.name || d}</option>
                  ))}
              </select>
            </label>
            <label style={{ display: 'grid', gap: 5, textAlign: 'left' }}>
              <span style={{ fontSize: 13, fontWeight: 700, color: NAVY }}>Destination *</span>
              <CountryCombo value={dest} options={countries} onChange={setDest}
                            placeholder="Type a country or code…"
                            testid="database-destination" />
            </label>
            <label style={{ display: 'grid', gap: 5, textAlign: 'left' }}>
              <span style={{ fontSize: 13, fontWeight: 700, color: NAVY }}>Purpose of travel</span>
              <select className="select" value={purpose}
                      onChange={(e) => setPurpose(e.target.value)}
                      style={{ fontSize: 14, padding: '11px 14px', borderRadius: 12 }}>
                {PURPOSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
            <label style={{ display: 'grid', gap: 5, textAlign: 'left' }}>
              <span style={{ fontSize: 13, fontWeight: 700, color: NAVY }}>
                Travel date <span style={{ color: GRAY, fontWeight: 400 }}>(optional)</span>
              </span>
              <input className="input" type="date" value={arrival}
                     onChange={(e) => setArrival(e.target.value)}
                     style={{ fontSize: 14, padding: '10px 14px', borderRadius: 12 }} />
            </label>
          </div>
          <div style={{ marginTop: 18, textAlign: 'center' }}>
            <button className="btn btn--primary" onClick={lookUp}
                    disabled={busy || !nat || !dest}
                    data-testid="database-check"
                    style={{ fontSize: 15, fontWeight: 800, padding: '12px 28px' }}>
              {busy ? 'Checking the latest requirements…' : 'Check requirements'}
            </button>
            {error && (
              <div style={{ fontSize: 13, color: NAVY, fontWeight: 700, marginTop: 10 }}>
                {error}
              </div>
            )}
          </div>
          {busy && (
            <div style={{ display: 'flex', justifyContent: 'center', marginTop: 14 }}>
              {/* The plane alone — the explanatory sentence is hidden by
                  owner decision (theming). */}
              <Loading label="" />
            </div>
          )}
        </div>
      )}

      {result && g && (
        <div className="anim-rise" data-testid="database-result">
          {/* Verdict hero */}
          <div className="card" style={{ padding: '26px 28px', borderRadius: 20,
                                         marginTop: 16, textAlign: 'left',
                                         background: disp.tint, border: 'none' }}>
            <div style={{ fontSize: 12.5, color: GRAY, fontWeight: 600 }}>
              {countryName(nat)} → {countryName(dest)} ·{' '}
              {PURPOSES.find(([v]) => v === purpose)?.[1]}
            </div>
            <div style={{ fontSize: 27, fontWeight: 800, color: disp.color,
                          marginTop: 6, letterSpacing: -0.4 }}
                 data-testid="database-disposition">
              {disp.title}
            </div>
            {asText(g.visa_category) && (
              <div style={{ fontSize: 14, color: NAVY, marginTop: 6 }}>
                {asText(g.visa_category)}
              </div>
            )}
          </div>

          {/* At a glance */}
          <div style={{ display: 'grid', gap: 10, marginTop: 14,
                        gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))' }}>
            <Tile label="Permitted stay" value={g.permitted_stay} />
            <Tile label="Government fee" value={feeText(g.government_fee)} />
            <Tile label="Processing time" value={g.processing_time} />
            <Tile label="Passport validity" value={
              g.passport_validity_requirement?.months
                ? `${g.passport_validity_requirement.months}+ months`
                : g.passport_validity} />
          </div>

          {itemsOf(g.required_documents).length > 0 && (
            <Section title="Documents you need">
              <Bullets items={g.required_documents} />
            </Section>
          )}

          {(itemsOf(g.forms).length > 0 || g.official_portal_url) && (
            <Section title="Forms & official portal">
              <Bullets items={g.forms} />
              {g.official_portal_url && (
                <a href={g.official_portal_url} target="_blank" rel="noreferrer"
                   style={{ display: 'inline-block', marginTop: 8, color: BLUE,
                            fontSize: 13.5, fontWeight: 700 }}>
                  {g.official_portal_url} ↗
                </a>
              )}
            </Section>
          )}

          {(asText(g.passport_validity) || asText(g.photo_requirements)) && (
            <Section title="Passport & photo">
              <Fact label="Passport validity" value={g.passport_validity} />
              {g.passport_validity_requirement?.months ? (
                <Fact label="Validity rule" value={
                  `At least ${g.passport_validity_requirement.months} months (${String(g.passport_validity_requirement.kind || '').replace(/_/g, ' ')})`} />
              ) : null}
              <Fact label="Photo" value={g.photo_requirements} />
            </Section>
          )}

          {(entryFacts.length > 0 || g.arrival_card?.required ||
            itemsOf(g.health_requirements).length > 0) && (
            <Section title="Entry formalities">
              {entryFacts.map(([l, v]) => <Fact key={l} label={l} value={v} />)}
              {g.arrival_card?.required ? (
                <Fact label="Arrival card" value={
                  `${g.arrival_card.name || 'Arrival card'}${g.arrival_card.submission_window ? ' — ' + g.arrival_card.submission_window : ''}`} />
              ) : null}
              {itemsOf(g.health_requirements).length > 0 && (
                <div style={{ marginTop: 6 }}>
                  <div style={{ fontSize: 12.5, color: GRAY, marginBottom: 4 }}>Health</div>
                  <Bullets items={g.health_requirements} />
                </div>
              )}
            </Section>
          )}

          {(asText(g.onward_travel_evidence) || asText(g.accommodation_evidence) ||
            asText(g.financial_evidence)) && (
            <Section title="Evidence to carry">
              <Fact label="Onward travel" value={g.onward_travel_evidence} />
              <Fact label="Accommodation" value={g.accommodation_evidence} />
              <Fact label="Finances" value={g.financial_evidence} />
            </Section>
          )}

          {(itemsOf(g.exceptions).length > 0 || itemsOf(g.uncertainty).length > 0 ||
            itemsOf(result.advisories).length > 0) && (
            <Section title="Good to know">
              <Bullets items={[...itemsOf(g.exceptions),
                               ...itemsOf(result.advisories),
                               ...itemsOf(g.uncertainty)]} />
            </Section>
          )}

          {applySteps.length > 0 && (
            <Section title="Steps to apply">
              <ol style={{ margin: 0, paddingLeft: 0, listStyle: 'none' }}>
                {applySteps.map((x, i) => (
                  <li key={i} style={{ display: 'flex', gap: 12,
                                       alignItems: 'flex-start', padding: '7px 0' }}>
                    <span style={{ flex: 'none', width: 24, height: 24,
                                   borderRadius: 999, background: BLUE,
                                   color: '#fff', fontSize: 12.5, fontWeight: 800,
                                   display: 'flex', alignItems: 'center',
                                   justifyContent: 'center' }}>{i + 1}</span>
                    <span style={{ fontSize: 13.5, color: NAVY,
                                   lineHeight: '24px' }}>
                      {x}
                      {i === portalStepIndex && (
                        <a href={g.official_portal_url} target="_blank"
                           rel="noreferrer"
                           style={{ marginLeft: 8, color: BLUE, fontWeight: 700,
                                    whiteSpace: 'nowrap' }}>
                          official portal ↗
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
                  Start on the official portal ↗
                </a>
              )}
            </Section>
          )}

          <div style={{ display: 'flex', gap: 10, justifyContent: 'center',
                        marginTop: 20 }}>
            {/* Present but inert, per owner instruction. */}
            <button className="btn btn--primary" data-testid="database-process"
                    onClick={() => {}}
                    style={{ fontSize: 15, fontWeight: 800, padding: '13px 30px',
                             borderRadius: 999 }}>
              Process my visa
            </button>
            <button className="btn btn--ghost" onClick={() => { setResult(null) }}
                    data-testid="database-again"
                    style={{ fontSize: 14, borderRadius: 999 }}>
              New search
            </button>
          </div>
        </div>
      )}

      {result && !g && (
        <div className="card anim-rise" style={{ padding: 22, borderRadius: 20,
                                                 marginTop: 16 }}>
          <div style={{ fontSize: 14, color: NAVY, fontWeight: 700 }}>
            No decision is available for this route right now.
          </div>
          <button className="btn btn--ghost" style={{ marginTop: 12 }}
                  onClick={() => setResult(null)}>New search</button>
        </div>
      )}
    </div>
  )
}
