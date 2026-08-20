// The Database — traveldoc-style requirements lookup, answered by the same
// Kimi-primary route decision the applicant journey trusts.
//
// One form (nationality, document, destination, purpose, optional date), one
// answer page carrying EVERYTHING the decision knows: disposition, permitted
// stay, passport validity, documents, forms, fee, processing time, channel,
// arrival card, health requirements, advisories — with the honest freshness
// flags (cached / stale / just decided). Repeat lookups are instant: the
// decision cache serves them without a model call, and a stale entry is
// served at once while a background refresh runs for the next reader.
import { useEffect, useMemo, useRef, useState } from 'react'
import { Loading } from '../components/ui.jsx'
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

const PURPOSES = [
  ['tourism', 'Tourism'], ['business', 'Business'], ['family_visit', 'Family visit'],
  ['study', 'Study'], ['work', 'Work'], ['transit', 'Transit'],
]

// The disposition, in a traveller's words and a colour.
// The engine's own vocabulary (kimi_primary.DISPOSITIONS), in a traveller's
// words and a colour.
const DISPOSITION_VIEW = {
  VISA_EXEMPT: { title: 'No visa needed', color: '#0f8a3d' },
  ELECTRONIC_AUTHORIZATION_REQUIRED:
    { title: 'Electronic travel authorization required', color: '#b06f00' },
  VISA_REQUIRED: { title: 'Visa required', color: '#b3261e' },
  CONDITIONAL: { title: 'Depends on your situation — see the details', color: '#b06f00' },
}

function Row({ k, children }) {
  if (children === null || children === undefined || children === '' ||
      (Array.isArray(children) && children.length === 0)) return null
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '210px 1fr', gap: 12,
                  padding: '10px 0', borderBottom: '1px solid var(--line, #eef1f5)',
                  fontSize: 13.5, textAlign: 'left' }}>
      <div style={{ color: GRAY, fontWeight: 700 }}>{k}</div>
      <div style={{ color: NAVY }}>{children}</div>
    </div>
  )
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

function listOf(v) {
  if (!v) return null
  const items = (Array.isArray(v) ? v : [v]).map((x) => asText(x)).filter(Boolean)
  if (!items.length) return null
  return (
    <ul style={{ margin: 0, paddingLeft: 18 }}>
      {items.map((x, i) => <li key={i} style={{ marginBottom: 3 }}>{x}</li>)}
    </ul>
  )
}

// Type-ahead country picker: type to filter, click to choose — the same
// behaviour StartVisa's SearchSelect gives the intake.
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
        style={{ fontSize: 14, padding: '10px 12px', borderRadius: 10, width: '100%' }}
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

function feeText(fee) {
  if (!fee || typeof fee !== 'object') return null
  const amt = fee.amount
  if (amt === 0) return 'None'
  if (!amt && amt !== 0) return null
  return `${amt} ${fee.currency || ''}`.trim() + (fee.notes ? ` — ${fee.notes}` : '')
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
                    { title: String(g.disposition || 'Route decision'), color: NAVY }) : null
  const countryName = (code) => countries.find((c) => c.value === code)?.label || code

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
        <Card className="anim-rise" style={{ marginTop: 16 }}
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
                      style={{ fontSize: 14, padding: '10px 12px', borderRadius: 10 }}>
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
                      style={{ fontSize: 14, padding: '10px 12px', borderRadius: 10 }}>
                {PURPOSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
            <label style={{ display: 'grid', gap: 5, textAlign: 'left' }}>
              <span style={{ fontSize: 13, fontWeight: 700, color: NAVY }}>
                Travel date <span style={{ color: GRAY, fontWeight: 400 }}>(optional)</span>
              </span>
              <input className="input" type="date" value={arrival}
                     onChange={(e) => setArrival(e.target.value)}
                     style={{ fontSize: 14, padding: '9px 12px', borderRadius: 10 }} />
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
              <Loading label="Reading the current rules for this route — a route checked for the first time can take up to a minute; repeats are instant" />
            </div>
          )}
        </Card>
      )}

      {result && g && (
        <Card className="anim-rise" style={{ marginTop: 16 }}
              data-testid="database-result">
          <div style={{ textAlign: 'left', fontSize: 12.5, color: GRAY }}>
            {countryName(nat)} → {countryName(dest)} · {PURPOSES.find(([v]) => v === purpose)?.[1]}
          </div>
          <div style={{ fontSize: 24, fontWeight: 800, color: disp.color,
                        margin: '8px 0 2px', textAlign: 'left' }}
               data-testid="database-disposition">
            {disp.title}
          </div>
          <div style={{ fontSize: 12, color: GRAY, textAlign: 'left', marginBottom: 8 }}>
            {result.label}{result.cached ? ' · served from the decision cache' : ' · decided just now'}
            {result.stale ? ' · refreshing in the background' : ''}
          </div>

          <Row k="Visa category">{asText(g.visa_category)}</Row>
          <Row k="Permitted stay">{asText(g.permitted_stay)}</Row>
          <Row k="Passport validity">{asText(g.passport_validity)}</Row>
          {g.passport_validity_requirement?.months ? (
            <Row k="Validity rule">
              {`At least ${g.passport_validity_requirement.months} months (${(g.passport_validity_requirement.kind || '').replace(/_/g, ' ')})`}
            </Row>
          ) : null}
          <Row k="Application channel">{asText(g.application_channel)}</Row>
          {g.official_portal_url ? (
            <Row k="Official portal">
              <a href={g.official_portal_url} target="_blank" rel="noreferrer"
                 style={{ color: BLUE }}>{g.official_portal_url}</a>
            </Row>
          ) : null}
          <Row k="Required documents">{listOf(g.required_documents)}</Row>
          <Row k="Forms">{listOf(g.forms)}</Row>
          <Row k="Government fee">{feeText(g.government_fee)}</Row>
          <Row k="Processing time">{asText(g.processing_time)}</Row>
          <Row k="Photo requirements">{asText(g.photo_requirements)}</Row>
          <Row k="Biometrics">{g.biometrics_required === true ? 'Required'
            : g.biometrics_required === false ? 'Not required' : null}</Row>
          <Row k="Interview">{g.interview_required === true ? 'Required'
            : g.interview_required === false ? 'Not required' : null}</Row>
          <Row k="Appointment">{g.appointment_required === true ? 'Required'
            : g.appointment_required === false ? 'Not required' : null}</Row>
          <Row k="Onward travel evidence">{asText(g.onward_travel_evidence)}</Row>
          <Row k="Accommodation evidence">{asText(g.accommodation_evidence)}</Row>
          <Row k="Financial evidence">{asText(g.financial_evidence)}</Row>
          <Row k="Travel insurance">{g.insurance_required === true ? 'Required'
            : g.insurance_required === false ? 'Not required' : null}</Row>
          {g.arrival_card?.required ? (
            <Row k="Arrival card">
              {`${g.arrival_card.name || 'Arrival card'}${g.arrival_card.submission_window ? ' — ' + g.arrival_card.submission_window : ''}`}
            </Row>
          ) : null}
          <Row k="Health requirements">{listOf(g.health_requirements)}</Row>
          <Row k="Exceptions">{listOf(g.exceptions)}</Row>
          <Row k="Account registration">{listOf(g.account_registration_steps)}</Row>
          <Row k="Payment">{listOf(g.payment_process)}</Row>
          <Row k="Submission">{listOf(g.submission_process)}</Row>
          <Row k="Uncertainty">{listOf(g.uncertainty)}</Row>

          {(result.advisories || []).length > 0 && (
            <div style={{ marginTop: 12, textAlign: 'left' }}>
              <div style={{ fontSize: 12, fontWeight: 800, letterSpacing: 0.6,
                            color: GRAY, textTransform: 'uppercase' }}>Advisories</div>
              {listOf(result.advisories)}
            </div>
          )}
          {result.safety_boundary || result.boundary ? (
            <div style={{ fontSize: 11.5, color: GRAY, marginTop: 12, textAlign: 'left' }}>
              {result.safety_boundary || result.boundary}
            </div>
          ) : null}

          <div style={{ display: 'flex', gap: 10, justifyContent: 'center',
                        marginTop: 18 }}>
            {/* Present but inert, per owner instruction: the button exists so
                the flow reads right; it processes nothing yet. */}
            <button className="btn btn--primary" data-testid="database-process"
                    onClick={() => {}}
                    style={{ fontSize: 15, fontWeight: 800, padding: '12px 26px' }}>
              Process my visa
            </button>
            <button className="btn btn--ghost" onClick={() => { setResult(null) }}
                    data-testid="database-again"
                    style={{ fontSize: 14 }}>
              New search
            </button>
          </div>
        </Card>
      )}

      {result && !g && (
        <Card className="anim-rise" style={{ marginTop: 16 }}>
          <div style={{ fontSize: 14, color: NAVY, fontWeight: 700 }}>
            No decision is available for this route right now.
          </div>
          <div style={{ fontSize: 13, color: GRAY, marginTop: 6 }}>
            {result.status || ''} — try again in a moment.
          </div>
          <button className="btn btn--ghost" style={{ marginTop: 12 }}
                  onClick={() => setResult(null)}>New search</button>
        </Card>
      )}
    </div>
  )
}
