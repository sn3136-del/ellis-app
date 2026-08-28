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

function FieldGrid({ rec }) {
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
          <span style={{ color: GRAY, width: 148, flexShrink: 0,
                         fontFamily: 'ui-monospace, monospace', fontSize: 11 }}>{f}</span>
          <span style={{ color: NAVY, fontWeight: 600, overflow: 'hidden',
                         textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {String(rec[f] ?? '·')}
          </span>
        </div>
      ))}
    </div>
  )
}

function RecordRow({ rec, onFlag, t }) {
  const [open, setOpen] = useState(false)
  const missing = Object.entries(rec.field_status)
    .filter(([, v]) => v === 'missing').map(([k]) => k)
  const CHECKS = {
    'human-quote': [t('ops.check.quoted'), GREEN],
    'grounded-consistent': [t('ops.check.grounded'), BLUE],
    reference: [t('ops.check.reference'), GRAY],
    unchecked: [t('ops.check.none'), RED],
  }
  const REQ = {
    'Visa-free': [t('ops.req.free'), GREEN],
    'Visa on Arrival': [t('ops.req.voa'), AMBER],
    'Visa Required in Advance': [t('ops.req.advance'), NAVY],
    Conditional: [t('ops.req.conditional'), AMBER],
  }
  const [checkLabel, checkColor] = CHECKS[rec.source_check] || CHECKS.reference
  const [reqLabel, reqColor] = REQ[rec.visa_requirement] || ['·', GRAY]
  return (
    <div style={{ ...card, overflow: 'hidden' }}>
      <div style={{ display: 'grid', alignItems: 'center', cursor: 'pointer',
                    gridTemplateColumns: '148px 96px 1fr auto auto auto auto 18px',
                    gap: 12, padding: '12px 18px' }}
           onClick={() => setOpen((o) => !o)}>
        <strong style={{ color: NAVY, fontSize: 13.5, whiteSpace: 'nowrap' }}>
          {rec.travel_document_country} → {rec.destination_country}
          <span style={{ color: GRAY, fontWeight: 500, fontSize: 11.5,
                         display: 'block' }}>
            {t(PURPOSE_KEY[rec.travel_purpose] || '') || rec.travel_purpose}
            {rec.travel_document_type !== 'ordinary_passport'
              ? ` · ${(t('db.doc.' + rec.travel_document_type) !== 'db.doc.' + rec.travel_document_type
                  ? t('db.doc.' + rec.travel_document_type)
                  : rec.travel_document_type.replace(/_/g, ' '))}` : ''}
          </span>
        </strong>
        <Chip color={reqColor} filled={false}>{reqLabel}</Chip>
        <span style={{ color: NAVY, fontSize: 12.5, fontWeight: 600,
                       overflow: 'hidden', textOverflow: 'ellipsis',
                       whiteSpace: 'nowrap' }}>
          {rec.visa_type_name || '·'}
        </span>
        <Chip color={CONF_COLOR[rec.confidence_level] || GRAY}>
          {rec.confidence_level}
        </Chip>
        <Chip color={rec.completeness === 1 ? GREEN : AMBER} filled={false}>
          {Math.round(rec.completeness * 100)}%
        </Chip>
        <Chip color={checkColor} filled={false}>{checkLabel}</Chip>
        {rec.source_url ? (
          <a href={rec.source_url} target="_blank" rel="noreferrer"
             onClick={(e) => e.stopPropagation()}
             style={{ fontSize: 12, color: BLUE, fontWeight: 700,
                      whiteSpace: 'nowrap' }}>
            {t('ops.source')} ↗
          </a>
        ) : <span />}
        <span style={{ color: '#c3ccd9', fontSize: 11 }}>{open ? '▲' : '▼'}</span>
      </div>
      {open && (
        <div style={{ borderTop: `1px solid ${BORDER}`, padding: '14px 18px',
                      background: '#fbfcfe' }}>
          <FieldGrid rec={rec} />
          {missing.length > 0 && (
            <div style={{ marginTop: 10, fontSize: 12, color: RED }}>
              {t('ops.missingRequired')}: {missing.join(', ')}
            </div>
          )}
          <button className="btn btn--sm btn--ghost" data-testid="ops-flag"
                  style={{ marginTop: 12, borderRadius: 999, fontSize: 12 }}
                  onClick={() => onFlag(rec)}>
            ⚑ {t('ops.flag')}
          </button>
        </div>
      )}
    </div>
  )
}

const PAGE = 50

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

  useEffect(() => {
    let live = true
    client.snapshotRegistries().then((r) => { if (live) setReg(r) })
      .catch(() => { if (live) setReg({ countries: [] }) })
    return () => { live = false }
  }, [client])
  const countries = useLocalizedCountries(client, reg, lang)

  const qs = useCallback(() => new URLSearchParams(
    Object.fromEntries(Object.entries(filters).filter(([, v]) => v))
  ).toString(), [filters])

  const load = useCallback(async () => {
    setBusy(true); setError('')
    try {
      if (tab === 'records') { setData(await client.get(`/database/records?${qs()}`)); setShown(PAGE) }
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
      `${t('ops.flagPrompt')} ${rec.travel_document_country} → ` +
      `${rec.destination_country} (${rec.visa_type_name || ''}):`)
    if (!note) return
    await client.databaseReportIssue({
      nationality: rec.travel_document_country,
      destination: rec.destination_country,
      field: 'operator_spot_check', note, cache_key: rec.cache_key,
    })
    window.alert(t('ops.flagged'))
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
  const records = data?.records || []
  const set = (k) => (v) => setFilters((f) => ({ ...f, [k]: v }))
  return (
    <div style={{ background: BG, minHeight: '100vh' }}>
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
          <span style={{ fontSize: 12.5, color: GRAY }}>{t('ops.subtitle')}</span>
        </div>

        {/* Tabs */}
        <div style={{ display: 'flex', gap: 6, margin: '20px 0 18px',
                      background: '#fff', border: `1px solid ${BORDER}`,
                      borderRadius: 999, padding: 4, width: 'fit-content' }}>
          {[['records', t('ops.tab.records')], ['issues', t('ops.tab.issues')],
            ['changes', t('ops.tab.changes')], ['freshness', t('ops.tab.freshness')]]
            .map(([id, label]) => (
            <button key={id} onClick={() => setTab(id)}
                    data-testid={`ops-tab-${id}`}
                    style={{ border: 'none', cursor: 'pointer', fontSize: 13,
                             fontWeight: 700, padding: '8px 18px',
                             borderRadius: 999,
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

            {s && (
              <div style={{ display: 'grid', gap: 12,
                            gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))' }}>
                <StatTile label={t('ops.stat.records')} value={s.total} />
                <StatTile label={t('ops.stat.complete')}
                          accent={s.completeness_rate >= 0.99 ? GREEN : AMBER}
                          value={s.completeness_rate != null
                            ? `${Math.round(s.completeness_rate * 100)}%` : '·'}
                          sub={t('ops.stat.completeSub')} />
                <StatTile label={t('ops.stat.sources')}
                          accent={s.source_coverage >= 0.999 ? GREEN : AMBER}
                          value={s.source_coverage != null
                            ? `${Math.round(s.source_coverage * 100)}%` : '·'}
                          sub={t('ops.stat.sourcesSub')} />
                <StatTile label={t('ops.stat.substantiated')} accent={BLUE}
                          value={s.substantiated ?? '·'}
                          sub={t('ops.stat.substantiatedSub')} />
                <StatTile label={t('ops.stat.confidence')}
                          value={`${s.high} / ${s.medium} / ${s.low}`}
                          sub={t('ops.stat.confidenceSub')} />
              </div>
            )}
            {busy && <div style={{ color: GRAY, fontSize: 13 }}>{t('ops.loading')}</div>}
            <div style={{ display: 'grid', gap: 8 }}>
              {records.slice(0, shown).map((rec, i) => (
                <RecordRow key={rec.cache_key + rec.visa_type_name + i}
                           rec={rec} onFlag={flag} t={t} />
              ))}
            </div>
            {records.length > shown && (
              <button className="btn btn--ghost"
                      style={{ borderRadius: 999, justifySelf: 'center' }}
                      onClick={() => setShown((n) => n + PAGE)}>
                {t('ops.showMore')} ({records.length - shown})
              </button>
            )}
          </div>
        )}

        {tab === 'issues' && (
          <div style={{ display: 'grid', gap: 10 }}>
            {(issues?.issues || []).length === 0 && !busy && (
              <div style={{ ...card, padding: 24, color: GRAY, fontSize: 13.5,
                            textAlign: 'center' }}>{t('ops.noReports')}</div>
            )}
            {(issues?.issues || []).map((it) => (
              <div key={it.id} style={{ ...card, padding: '14px 18px' }}>
                <div style={{ display: 'flex', gap: 10, alignItems: 'center',
                              flexWrap: 'wrap' }}>
                  <strong style={{ color: NAVY, fontSize: 13.5 }}>
                    {(it.route || {}).nationality || (it.route || {}).passport_nationality}
                    {' → '}
                    {(it.route || {}).destination || (it.route || {}).destination_country}
                  </strong>
                  <Chip color={it.status === 'open' ? RED
                    : it.status === 'corrected' ? GREEN : AMBER}>{it.status}</Chip>
                  <span style={{ color: GRAY, fontSize: 12,
                                 fontFamily: 'ui-monospace, monospace' }}>{it.field}</span>
                  <span style={{ marginLeft: 'auto', color: GRAY, fontSize: 11.5 }}>
                    {(it.created_at || '').slice(0, 16).replace('T', ' ')}
                  </span>
                </div>
                <div style={{ fontSize: 13, color: NAVY, marginTop: 8 }}>{it.note}</div>
                {it.resolution && (
                  <div style={{ fontSize: 12.5, color: GREEN, marginTop: 6 }}>
                    ✓ {it.resolution}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {tab === 'changes' && (
          <div style={{ display: 'grid', gap: 10 }}>
            {(changes?.changes || []).length === 0 && !busy && (
              <div style={{ ...card, padding: 24, color: GRAY, fontSize: 13.5,
                            textAlign: 'center' }}>{t('ops.noChanges')}</div>
            )}
            {(changes?.changes || []).map((c) => (
              <div key={c.id} style={{ ...card, padding: '14px 18px' }}>
                <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                  <Chip color={c.action === 'add' ? GREEN
                    : c.action === 'delete' ? RED : AMBER}>{c.action}</Chip>
                  <strong style={{ color: NAVY, fontSize: 13.5 }}>
                    {(c.route || {}).passport_nationality} → {(c.route || {}).destination_country}
                  </strong>
                  <span style={{ color: GRAY, fontSize: 12 }}>{c.origin}</span>
                  <span style={{ marginLeft: 'auto', color: GRAY, fontSize: 11.5 }}>
                    {(c.at || '').slice(0, 16).replace('T', ' ')}
                  </span>
                </div>
                {Object.entries(c.changes || {}).slice(0, 6).map(([f, d]) => (
                  <div key={f} style={{ fontSize: 12.5, marginTop: 6,
                                        display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    <span style={{ color: GRAY,
                                   fontFamily: 'ui-monospace, monospace',
                                   fontSize: 11 }}>{f}</span>
                    <span style={{ color: RED, textDecoration: 'line-through' }}>
                      {JSON.stringify(d.from)?.slice(0, 60)}
                    </span>
                    <span style={{ color: GRAY }}>→</span>
                    <span style={{ color: GREEN, fontWeight: 600 }}>
                      {JSON.stringify(d.to)?.slice(0, 80)}
                    </span>
                  </div>
                ))}
                {c.note && (
                  <div style={{ fontSize: 12, color: GRAY, marginTop: 6 }}>{c.note}</div>
                )}
              </div>
            ))}
          </div>
        )}

        {tab === 'freshness' && freshness && (
          <div style={{ display: 'grid', gap: 14 }}>
            <div style={{ display: 'grid', gap: 12,
                          gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))' }}>
              <StatTile label={t('ops.fresh.answers')} value={freshness.summary.total} />
              <StatTile label={t('ops.fresh.grounded')} accent={BLUE}
                        value={freshness.summary.grounded} />
              <StatTile label={t('ops.fresh.human')} accent={GREEN}
                        value={freshness.summary.human_verified} />
              <StatTile label={t('ops.fresh.stale')}
                        accent={freshness.summary.stale ? AMBER : GREEN}
                        value={freshness.summary.stale} />
              <StatTile label={t('ops.fresh.disputed')}
                        accent={freshness.summary.disputed ? RED : GREEN}
                        value={freshness.summary.disputed} />
            </div>
            <div style={{ ...card, padding: '16px 20px', fontSize: 12.5,
                          color: GRAY, lineHeight: 1.6 }}>
              {t('ops.fresh.note')}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
