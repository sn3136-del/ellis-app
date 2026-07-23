// Applicant-facing secure-connector build (brief §10, §29). Shown when no
// verified live connector exists for the exact route. The applicant gives ONE
// explicit consent, then watches simple progress labels — never adapter code,
// never portal internals, never the release decision (which is internal-only).
import { useEffect, useRef, useState } from 'react'
import { useToast, Loading, ErrorNote } from '../ui.jsx'
import { useT, useLocale } from '../../lib/locale.jsx'

// Ordered applicant-facing labels the backend emits (adapter_factory
// statemachine.applicant_label). Anything unknown fails safe to manual_review.
const BUILD_STEPS = [
  'preparing', 'researching', 'verifying_portal', 'verifying_jurisdiction',
  'mapping_portal', 'generating', 'safety_checks', 'testing', 'repairing',
  'final_verification', 'ready'
]
const TERMINAL = new Set(['ready', 'manual_review', 'unavailable'])
const POLL_MS = 2500

export default function ConnectorBuild({ client, route, onReady }) {
  const t = useT()
  const { locale } = useLocale()
  const toast = useToast()
  const [phase, setPhase] = useState('intro')  // intro | consent | building
  const [disclosures, setDisclosures] = useState([])
  const [agree, setAgree] = useState(false)
  const [build, setBuild] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const timer = useRef(null)

  useEffect(() => {
    client.buildConsentCopy().then((c) => setDisclosures(c.disclosures || [])).catch(() => {})
    // Resume an in-flight build persisted from a previous session.
    const stored = route?.route_key && localStorage.getItem('ellis.build.' + route.route_key)
    if (stored) { setBuild({ id: stored }); setPhase('building'); poll(stored) }
    return () => timer.current && clearTimeout(timer.current)
  }, [])

  async function poll(id) {
    try {
      const s = await client.getAdapterBuild(id)
      setBuild(s)
      if (s.label === 'ready') { onReady && onReady(); return }
      if (!TERMINAL.has(s.label)) {
        timer.current = setTimeout(() => client.resumeAdapterBuild(id).then(setBuild)
          .catch(() => {}).finally(() => poll(id)), POLL_MS)
      }
    } catch (e) { setError({ message: e.message }) }
  }

  async function startBuild() {
    setBusy(true); setError(null)
    try {
      const req = await client.requestAdapterBuild({
        route_key: route.route_key, destination: route.destination || '',
        visa_type: route.visa_type || 'tourist',
        portal_evidence: route.portal_evidence || {} })
      localStorage.setItem('ellis.build.' + route.route_key, req.id)
      const s = await client.consentAdapterBuild(req.id, locale)
      setBuild(s); setPhase('building'); poll(req.id)
    } catch (e) { setError({ message: e.message }); setBusy(false) }
  }

  if (phase === 'intro') {
    return (
      <div className="card" style={{ padding: 22 }}>
        <div style={{ fontWeight: 700, marginBottom: 6 }}>{t('connector.introTitle')}</div>
        <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 12 }}>
          {t('connector.introBody')}
        </div>
        <button className="btn" onClick={() => setPhase('consent')}>{t('connector.build')}</button>
      </div>
    )
  }

  if (phase === 'consent') {
    return (
      <div className="card" style={{ padding: 22 }}>
        <div style={{ fontWeight: 700, marginBottom: 6 }}>{t('connector.consentTitle')}</div>
        <ul style={{ fontSize: 12.5, color: 'var(--muted)', margin: '8px 0 0 18px' }}>
          {disclosures.map((d) => <li key={d}>{t('connector.d.' + d)}</li>)}
        </ul>
        <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', margin: '12px 0', fontSize: 13 }}>
          <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
          <span>{t('connector.consentAgree')}</span>
        </label>
        {error && <ErrorNote error={error} />}
        <div style={{ display: 'flex', gap: 10 }}>
          <button className="btn btn--ghost" onClick={() => setPhase('intro')}>{t('connector.back')}</button>
          <button className="btn" disabled={!agree || busy} onClick={startBuild}>
            {busy ? t('connector.starting') : t('connector.startBuild')}
          </button>
        </div>
      </div>
    )
  }

  // building
  const label = build?.label || 'preparing'
  const idx = BUILD_STEPS.indexOf(label)
  const isReady = label === 'ready'
  const isManual = label === 'manual_review'
  const isUnavailable = label === 'unavailable'
  return (
    <div className="card" style={{ padding: 22 }}>
      <div style={{ fontWeight: 700, marginBottom: 10 }}>{t('connector.buildingTitle')}</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 14 }}>
        {BUILD_STEPS.filter((s) => s !== 'repairing' || label === 'repairing').map((s) => (
          <span key={s} className={'chip' + (BUILD_STEPS.indexOf(s) <= idx && idx >= 0 ? ' chip--ink' : '')}
                style={{ fontSize: 10 }}>{t('connector.step.' + s)}</span>
        ))}
      </div>
      {!TERMINAL.has(label) && <Loading label={t('connector.step.' + label)} />}
      {isReady && (
        <div className="card card--soft" style={{ padding: 14 }}>
          <div style={{ fontWeight: 700 }}>{t('connector.readyTitle')}</div>
          <div style={{ fontSize: 13, color: 'var(--muted)' }}>{t('connector.readyBody')}</div>
        </div>
      )}
      {isManual && (
        <div className="card card--soft" style={{ padding: 14, borderColor: '#f59e0b' }}>
          <div style={{ fontWeight: 700 }}>{t('connector.manualTitle')}</div>
          <div style={{ fontSize: 13, color: 'var(--muted)' }}>{t('connector.manualBody')}</div>
        </div>
      )}
      {isUnavailable && (
        <div className="card card--soft" style={{ padding: 14 }}>
          <div style={{ fontWeight: 700 }}>{t('connector.unavailableTitle')}</div>
          <div style={{ fontSize: 13, color: 'var(--muted)' }}>{t('connector.unavailableBody')}</div>
        </div>
      )}
      {(build?.progress_notes || []).length > 0 && (
        <div style={{ fontSize: 11.5, color: 'var(--muted-2)', marginTop: 10 }}>
          {build.progress_notes.map((n, i) => <div key={i}>· {n}</div>)}
        </div>
      )}
      <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 10 }}>{t('connector.closeSafe')}</div>
      {error && <ErrorNote error={error} />}
    </div>
  )
}
