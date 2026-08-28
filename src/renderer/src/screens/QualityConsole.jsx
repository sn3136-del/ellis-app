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
import { useCallback, useEffect, useMemo, useState } from 'react'
import { createVisaClient } from '../lib/visaBackend.js'
import { newSession } from '../lib/visaSession.js'

const NAVY = '#0f294d'
const BLUE = '#287dfa'
const GRAY = '#8592a6'
const GREEN = '#0a8a4a'
const AMBER = '#b26a00'
const RED = '#c62828'

const CONF_COLOR = { High: GREEN, Medium: AMBER, Low: RED }

function useOpsClient() {
  return useMemo(() => {
    const s = newSession()
    // The ops surface authenticates as the operator; the backend refuses
    // reader tokens on every endpoint this screen calls.
    return createVisaClient({ ...s, token: 'admin-token' })
  }, [])
}

function Chip({ children, color = GRAY }) {
  return (
    <span style={{ display: 'inline-block', padding: '2px 10px', borderRadius: 999,
                   fontSize: 11.5, fontWeight: 700, color: '#fff',
                   background: color }}>{children}</span>
  )
}

function FilterBar({ filters, onChange, onExport }) {
  const F = (k, ph, w = 110) => (
    <input value={filters[k]} placeholder={ph}
           onChange={(e) => onChange({ ...filters, [k]: e.target.value })}
           style={{ width: w, padding: '8px 10px', borderRadius: 10,
                    border: '1px solid #dfe5ee', fontSize: 13 }} />
  )
  return (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
      {F('nationality', 'Passport (CHN)')}
      {F('destination', 'Destination (JPN)')}
      {F('purpose', 'Purpose (tourism)', 130)}
      <select value={filters.requirement}
              onChange={(e) => onChange({ ...filters, requirement: e.target.value })}
              style={{ padding: '8px 10px', borderRadius: 10,
                       border: '1px solid #dfe5ee', fontSize: 13 }}>
        <option value="">Any requirement</option>
        <option>Visa-free</option>
        <option>Visa on Arrival</option>
        <option>Visa Required in Advance</option>
        <option>Conditional</option>
      </select>
      <select value={filters.confidence}
              onChange={(e) => onChange({ ...filters, confidence: e.target.value })}
              style={{ padding: '8px 10px', borderRadius: 10,
                       border: '1px solid #dfe5ee', fontSize: 13 }}>
        <option value="">Any confidence</option>
        <option>High</option>
        <option>Medium</option>
        <option>Low</option>
      </select>
      <button className="btn btn--sm" onClick={onExport}
              data-testid="ops-export"
              style={{ borderRadius: 999, fontWeight: 700 }}>
        Export Excel (.xlsx)
      </button>
    </div>
  )
}

function StatTile({ label, value, sub }) {
  return (
    <div className="card" style={{ padding: '14px 18px', borderRadius: 14 }}>
      <div style={{ fontSize: 10.5, fontWeight: 800, letterSpacing: 0.8,
                    color: GRAY, textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 800, color: NAVY, marginTop: 4 }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: 11.5, color: GRAY }}>{sub}</div>}
    </div>
  )
}

function RecordRow({ rec, onFlag }) {
  const [open, setOpen] = useState(false)
  const missing = Object.entries(rec.field_status)
    .filter(([, v]) => v === 'missing').map(([k]) => k)
  return (
    <div className="card" style={{ borderRadius: 14, padding: '12px 16px' }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center',
                    flexWrap: 'wrap', cursor: 'pointer' }}
           onClick={() => setOpen((o) => !o)}>
        <strong style={{ color: NAVY, fontSize: 13.5 }}>
          {rec.travel_document_country} → {rec.destination_country}
        </strong>
        <span style={{ color: GRAY, fontSize: 12.5 }}>{rec.travel_purpose}</span>
        <span style={{ color: NAVY, fontSize: 12.5, fontWeight: 600 }}>
          {rec.visa_type_name || '—'}
        </span>
        <Chip color={CONF_COLOR[rec.confidence_level] || GRAY}>
          {rec.confidence_level}
        </Chip>
        <Chip color={rec.completeness === 1 ? GREEN : AMBER}>
          {Math.round(rec.completeness * 100)}% complete
        </Chip>
        {rec.source_url && (
          <a href={rec.source_url} target="_blank" rel="noreferrer"
             onClick={(e) => e.stopPropagation()}
             style={{ fontSize: 12.5, color: BLUE, fontWeight: 600 }}>
            official source ↗
          </a>
        )}
        <span style={{ marginLeft: 'auto', color: GRAY, fontSize: 12 }}>
          {open ? '▲' : '▼'}
        </span>
      </div>
      {open && (
        <div style={{ marginTop: 12, borderTop: '1px solid #eef1f6', paddingTop: 12 }}>
          <div style={{ display: 'grid', gap: 6,
                        gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))' }}>
            {Object.entries(rec.field_status).map(([f, st]) => (
              <div key={f} style={{ fontSize: 12, display: 'flex', gap: 6 }}>
                <span style={{ color: st === 'missing' ? RED : GREEN }}>
                  {st === 'missing' ? '✗' : '✓'}
                </span>
                <span style={{ color: GRAY, minWidth: 150 }}>{f}</span>
                <span style={{ color: NAVY, fontWeight: 600, overflow: 'hidden',
                               textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {String(rec[f] ?? '—')}
                </span>
              </div>
            ))}
          </div>
          {missing.length > 0 && (
            <div style={{ marginTop: 8, fontSize: 12, color: RED }}>
              Missing required: {missing.join(', ')}
            </div>
          )}
          <button className="btn btn--sm btn--ghost" data-testid="ops-flag"
                  style={{ marginTop: 10, borderRadius: 999 }}
                  onClick={() => onFlag(rec)}>
            Flag an error on this record
          </button>
        </div>
      )}
    </div>
  )
}

export default function QualityConsole() {
  const client = useOpsClient()
  const [tab, setTab] = useState('records')
  const [filters, setFilters] = useState({ nationality: '', destination: '',
                                           purpose: '', requirement: '',
                                           confidence: '' })
  const [data, setData] = useState(null)
  const [changes, setChanges] = useState(null)
  const [issues, setIssues] = useState(null)
  const [freshness, setFreshness] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const qs = useCallback(() => new URLSearchParams(
    Object.fromEntries(Object.entries(filters).filter(([, v]) => v))
  ).toString(), [filters])

  const load = useCallback(async () => {
    setBusy(true); setError('')
    try {
      if (tab === 'records') setData(await client.get(`/database/records?${qs()}`))
      else if (tab === 'changes') setChanges(await client.get('/database/changes?limit=300'))
      else if (tab === 'issues') setIssues(await client.get('/database/issues'))
      else if (tab === 'freshness') setFreshness(await client.get('/database/freshness'))
    } catch (e) {
      setError(String(e?.message || e))
    } finally {
      setBusy(false)
    }
  }, [client, tab, qs])
  useEffect(() => { load() }, [load])

  async function flag(rec) {
    const note = window.prompt(
      `Describe what is wrong for ${rec.travel_document_country} → ` +
      `${rec.destination_country} (${rec.visa_type_name || 'route'}):`)
    if (!note) return
    await client.databaseReportIssue({
      nationality: rec.travel_document_country,
      destination: rec.destination_country,
      field: 'operator_spot_check', note, cache_key: rec.cache_key,
    })
    window.alert('Flagged. It is now in the correction queue.')
    if (tab === 'issues') load()
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

  const s = data?.summary
  return (
    <div className="page" style={{ maxWidth: 1080, margin: '0 auto',
                                   padding: '26px 20px 60px' }}
         data-testid="quality-console">
      <h1 style={{ fontSize: 26, fontWeight: 800, color: NAVY, margin: 0 }}>
        Information Quality Control
      </h1>
      <div style={{ fontSize: 13.5, color: GRAY, marginTop: 6 }}>
        Spot checks, per-field verification, change history and Excel export —
        every record bound to its official source.
      </div>

      <div style={{ display: 'flex', gap: 8, margin: '18px 0' }}>
        {[['records', 'Records'], ['issues', 'Correction queue'],
          ['changes', 'Change log'], ['freshness', 'Freshness']].map(([id, label]) => (
          <button key={id} className={'btn btn--sm' + (tab === id ? '' : ' btn--ghost')}
                  style={{ borderRadius: 999 }} onClick={() => setTab(id)}
                  data-testid={`ops-tab-${id}`}>
            {label}
          </button>
        ))}
      </div>

      {error && <div style={{ color: RED, fontSize: 13, marginBottom: 12 }}>{error}</div>}
      {busy && <div style={{ color: GRAY, fontSize: 13, marginBottom: 12 }}>Loading…</div>}

      {tab === 'records' && (
        <>
          <FilterBar filters={filters} onChange={setFilters} onExport={exportXlsx} />
          {s && (
            <div style={{ display: 'grid', gap: 10, margin: '16px 0',
                          gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))' }}>
              <StatTile label="Records" value={s.total} />
              <StatTile label="Complete" value={s.completeness_rate != null
                ? `${Math.round(s.completeness_rate * 100)}%` : '—'}
                        sub="all required fields filled" />
              <StatTile label="Source coverage" value={s.source_coverage != null
                ? `${Math.round(s.source_coverage * 100)}%` : '—'}
                        sub="records with an official URL" />
              <StatTile label="High / Med / Low"
                        value={`${s.high} / ${s.medium} / ${s.low}`}
                        sub="confidence levels" />
            </div>
          )}
          <div style={{ display: 'grid', gap: 10 }}>
            {(data?.records || []).map((rec, i) => (
              <RecordRow key={rec.cache_key + i} rec={rec} onFlag={flag} />
            ))}
          </div>
        </>
      )}

      {tab === 'issues' && (
        <div style={{ display: 'grid', gap: 10 }}>
          {(issues?.issues || []).length === 0 && !busy && (
            <div style={{ color: GRAY, fontSize: 13.5 }}>No open reports.</div>
          )}
          {(issues?.issues || []).map((it) => (
            <div key={it.id} className="card" style={{ borderRadius: 14,
                                                       padding: '12px 16px' }}>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                <strong style={{ color: NAVY, fontSize: 13.5 }}>
                  {(it.route || {}).nationality || (it.route || {}).passport_nationality}
                  {' → '}
                  {(it.route || {}).destination || (it.route || {}).destination_country}
                </strong>
                <Chip color={it.status === 'open' ? RED
                  : it.status === 'corrected' ? GREEN : AMBER}>{it.status}</Chip>
                <span style={{ color: GRAY, fontSize: 12 }}>{it.field}</span>
              </div>
              <div style={{ fontSize: 13, color: NAVY, marginTop: 6 }}>{it.note}</div>
              {it.resolution && (
                <div style={{ fontSize: 12.5, color: GREEN, marginTop: 4 }}>
                  Resolution: {it.resolution}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {tab === 'changes' && (
        <div style={{ display: 'grid', gap: 10 }}>
          {(changes?.changes || []).length === 0 && !busy && (
            <div style={{ color: GRAY, fontSize: 13.5 }}>No recorded changes yet.</div>
          )}
          {(changes?.changes || []).map((c) => (
            <div key={c.id} className="card" style={{ borderRadius: 14,
                                                      padding: '12px 16px' }}>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                <Chip color={c.action === 'add' ? GREEN
                  : c.action === 'delete' ? RED : AMBER}>{c.action}</Chip>
                <strong style={{ color: NAVY, fontSize: 13.5 }}>
                  {(c.route || {}).passport_nationality} → {(c.route || {}).destination_country}
                </strong>
                <span style={{ color: GRAY, fontSize: 12 }}>{c.origin}</span>
                <span style={{ marginLeft: 'auto', color: GRAY, fontSize: 12 }}>
                  {(c.at || '').slice(0, 16).replace('T', ' ')}
                </span>
              </div>
              {Object.entries(c.changes || {}).slice(0, 6).map(([f, d]) => (
                <div key={f} style={{ fontSize: 12.5, marginTop: 5 }}>
                  <span style={{ color: GRAY }}>{f}: </span>
                  <span style={{ color: RED, textDecoration: 'line-through' }}>
                    {JSON.stringify(d.from)?.slice(0, 70)}
                  </span>
                  {' → '}
                  <span style={{ color: GREEN, fontWeight: 600 }}>
                    {JSON.stringify(d.to)?.slice(0, 90)}
                  </span>
                </div>
              ))}
              {c.note && <div style={{ fontSize: 12, color: GRAY, marginTop: 5 }}>{c.note}</div>}
            </div>
          ))}
        </div>
      )}

      {tab === 'freshness' && freshness && (
        <>
          <div style={{ display: 'grid', gap: 10, margin: '4px 0 16px',
                        gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))' }}>
            <StatTile label="Answers" value={freshness.summary.total} />
            <StatTile label="Source-checked" value={freshness.summary.grounded} />
            <StatTile label="Human-verified" value={freshness.summary.human_verified} />
            <StatTile label="Stale" value={freshness.summary.stale} />
          </div>
          <div style={{ fontSize: 12.5, color: GRAY }}>
            Every answer is re-checked against its official page after it is
            first generated and again on access when its window lapses;
            disputes go to the correction queue for a person.
          </div>
        </>
      )}
    </div>
  )
}
