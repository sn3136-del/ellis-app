// The H-1B guided walkthrough — the parent petition's two-party surface,
// drawn as a GRAPHIC vertical timeline (2026-08-13 redesign): each government
// step is a lit/grey node on a rail that fills as steps verify, step cards
// carry an illustration + colored status chip + who-acts avatar chip, and the
// counsel panels (RFE / evidence / narrative / packet) live behind a 2x2
// icon-card grid. Doctrine carried unchanged: the attorney disclaimer on
// every payload (one compact line, full text one tap away — never deleted),
// signature cells are human only, denied/blocked actions surface their honest
// backend reason, and the admin-only offline-evidence picker mirrors the
// server's administrator gate — UI hiding is a courtesy; the server is the
// wall.
import { useEffect, useState } from 'react'
import { useLocale } from '../../lib/locale.jsx'
import { Loading, ErrorNote, Empty } from '../ui.jsx'
import {
  detectPersona, partyForPersona, h1bStepMeta, h1bWhoActs, setActiveH1bCase
} from '../../lib/visaSession.js'
import FilingCockpit from './FilingCockpit.jsx'
import AppointmentCockpit from './AppointmentCockpit.jsx'
import {
  DocumentsIllustration, FormFillIllustration, AppointmentIllustration,
  PipelineIllustration, EnvelopeIllustration, ShieldIllustration
} from './Illustrations.jsx'

// Filing-step order and the government form each one prepares.
const STEP_ORDER = ['lca', 'registration', 'i129', 'ds160_consular']
// Mirrors backend/app/h1b/filing.py VISA_TYPE_BY_STEP (display metadata for
// the opened child case; the backend row stays authoritative).
const VISA_TYPE_BY_STEP = {
  lca: 'h1b_lca', registration: 'h1b_registration',
  i129: 'h1b_i129', ds160_consular: 'h1b_ds160'
}
// forms_api.FORM_KEYS — only the petitioner paper forms are preparable; the
// registration and DS-160 are portal wizards, not fillable blanks. The LCA step
// carries TWO: the ETA-9035 itself and the ETA-9141 prevailing wage request
// that precedes it (both are really filed in FLAG, so both print as
// preparation copies).
const FORM_KEYS_BY_STEP = { lca: ['eta-9035', 'eta-9141'], i129: ['i-129'] }
// The single form a step's cockpit opens on (the filing itself, not its
// prerequisite request).
const FORM_KEY_BY_STEP = { lca: 'eta-9035', i129: 'i-129' }
const FORM_LABEL_KEY = {
  'eta-9035': 'h1b.form.eta9035', 'eta-9141': 'h1b.form.eta9141', 'i-129': 'h1b.form.i129'
}
// Which receipt column labels a step's proven outcome (labels only — the
// backend refuses to verify on a receipt without evidence).
const RECEIPT_KEY_BY_STEP = {
  lca: 'lca_number', registration: 'beneficiary_confirmation_number',
  i129: 'uscis_receipt_number'
}
// Offline government artifacts the admin picker offers (mirrors
// steps._OFFLINE_EVIDENCE_DOC_TYPES).
const OFFLINE_EVIDENCE_TYPES = ['certified_lca', 'prior_i797']

const SEVERITY_COLOR = { high: '#d33', medium: '#c77700', low: 'var(--muted)' }

// One accent illustration per step card — from the shared library only.
const STEP_ILLO = {
  lca: FormFillIllustration, registration: FormFillIllustration,
  i129: EnvelopeIllustration, ds160_consular: AppointmentIllustration
}

// Status chip colors by h1bStepMeta tone (visual only; the meta stays the
// single honest mapping — unknown fails safe to muted, never ok).
const TONE_CHIP = {
  ok:      { bg: '#e8f8ee', fg: '#1d9e55' },
  ready:   { bg: '#eef4ff', fg: 'var(--trip-blue-dark, #1c66d9)' },
  active:  { bg: 'var(--trip-blue, #2681ff)', fg: '#fff' },
  pending: { bg: '#fdf3e0', fg: '#b97b0a' },
  bad:     { bg: '#fdf0ef', fg: '#c0392b' },
  muted:   { bg: '#eef1f5', fg: 'var(--muted, #5e6f88)' }
}

// Tiny UI glyphs (16px strokes; the section artwork stays in Illustrations.jsx).
const GLYPHS = {
  building: <><path d="M2.5 13.5h11M4.5 13.5v-10h5v10M11 13.5V7H9.5" />
    <path d="M6.2 6h1.6M6.2 8.5h1.6M6.2 11h1.6" /></>,
  person: <><circle cx="8" cy="5" r="2.5" />
    <path d="M3.5 13.5c.6-3 2.4-4.6 4.5-4.6s3.9 1.6 4.5 4.6" /></>,
  lock: <><rect x="4" y="7" width="8" height="6" rx="1.6" />
    <path d="M5.7 7V5.4a2.3 2.3 0 0 1 4.6 0V7" /></>,
  check: <path d="M3.5 8.5l3 3 6-6.5" />,
  doc: <><rect x="4" y="2.5" width="8" height="11" rx="1.4" />
    <path d="M6.2 6h3.6M6.2 8.5h3.6" /></>,
  chevron: <path d="M4.5 6.5 8 10l3.5-3.5" />
}
function Glyph({ name, size = 13 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true"
         stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
         style={{ flexShrink: 0, display: 'inline-block', verticalAlign: '-2px' }}>
      {GLYPHS[name] || null}
    </svg>
  )
}

function stepLabel(t, stepKey) {
  const known = STEP_ORDER.includes(stepKey)
  return known ? t(`h1b.step.${stepKey}`) : stepKey
}

// ---- Timeline node + filling rail ------------------------------------------
function TimelineNode({ status }) {
  const done = status === 'verified'
  const lit = ['ready', 'in_progress', 'awaiting_government'].includes(status)
  const failed = status === 'failed'
  const bg = done ? '#1d9e55' : lit ? 'var(--trip-blue, #2681ff)' : failed ? '#fdf0ef' : '#fff'
  const border = done ? '#1d9e55' : lit ? 'var(--trip-blue, #2681ff)'
    : failed ? '#c0392b' : '#d7dfea'
  return (
    <div style={{ position: 'relative', width: 34, height: 34, flexShrink: 0 }}>
      {/* soft attention halo on the step whose turn it is */}
      {lit && (
        <span className="illo-pulse" aria-hidden="true"
          style={{ position: 'absolute', inset: -6, borderRadius: '50%',
                   background: 'rgba(38,129,255,0.18)' }} />
      )}
      <div style={{ position: 'relative', width: 34, height: 34, borderRadius: '50%',
                    background: bg, border: `2.5px solid ${border}`,
                    display: 'grid', placeItems: 'center',
                    color: done || (lit && status !== 'ready') ? '#fff' : lit ? '#fff' : '#b6c2d4',
                    transition: 'background .5s ease, border-color .5s ease' }}>
        {done ? <Glyph name="check" size={15} />
          : failed ? <span style={{ color: '#c0392b', fontWeight: 800, fontSize: 15 }}>!</span>
          : <span style={{ width: 8, height: 8, borderRadius: '50%',
                           background: lit ? '#fff' : '#c9d4e3' }} />}
      </div>
    </div>
  )
}

// ---- Verify (admin: offline-evidence document picker) ----------------------
function VerifyPanel({ t, client, caseId, step, isAdmin, onDone, onError }) {
  const [docs, setDocs] = useState(null)
  const [docId, setDocId] = useState('')
  const [receipt, setReceipt] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!isAdmin) return
    let alive = true
    client.review(caseId)
      .then((r) => { if (alive) setDocs((r.documents || []).filter(
        (d) => OFFLINE_EVIDENCE_TYPES.includes(d.doc_type) && d.approved)) })
      .catch(() => { if (alive) setDocs([]) })
    return () => { alive = false }
  }, [caseId, isAdmin])

  async function submit() {
    setBusy(true)
    try {
      const receipts = {}
      const rk = RECEIPT_KEY_BY_STEP[step.step_key]
      if (rk && receipt.trim()) receipts[rk] = receipt.trim()
      await client.h1bVerifyStep(caseId, step.step_key, {
        receipts, offline_evidence_document_id: isAdmin ? docId : ''
      })
      onDone()
    } catch (e) {
      onError(e)
    }
    setBusy(false)
  }

  return (
    <div className="anim-rise" style={{ marginTop: 14, padding: 16, borderRadius: 14,
                  background: 'var(--bg-2, #f7fafd)' }} data-testid="h1b-verify-panel">
      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8 }}>{t('h1b.verify.title')}</div>
      {isAdmin && (
        <>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 10, lineHeight: 1.5 }}>
            {t('h1b.verify.adminNote')}
          </div>
          <div className="field">
            <label>{t('h1b.verify.docLabel')}</label>
            {docs === null ? <Loading label={t('common.loading')} /> : docs.length === 0
              ? <div style={{ fontSize: 12.5, color: 'var(--muted)' }}>{t('h1b.verify.noDocs')}</div>
              : (
                <select className="select" value={docId} onChange={(e) => setDocId(e.target.value)}
                        data-testid="h1b-offline-evidence-picker">
                  <option value="">—</option>
                  {docs.map((d) => (
                    <option key={d.id} value={d.id}>{d.name} · {d.doc_type}</option>
                  ))}
                </select>
              )}
          </div>
        </>
      )}
      {RECEIPT_KEY_BY_STEP[step.step_key] && (
        <div className="field">
          <label>{t('h1b.verify.receiptLabel')}</label>
          <input className="input" value={receipt} onChange={(e) => setReceipt(e.target.value)} />
        </div>
      )}
      <button className="btn btn--sm" disabled={busy} onClick={submit}>
        {t('h1b.verify.submit')}
      </button>
    </div>
  )
}

// ---- One pipeline step card ------------------------------------------------
function StepCard({ t, client, caseId, step, byKey, viewerParty, isAdmin,
                    onChanged, onOpenCase, index, last }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [verifyOpen, setVerifyOpen] = useState(false)
  const [cockpitOpen, setCockpitOpen] = useState(false)
  const meta = h1bStepMeta(step.status)
  const who = h1bWhoActs(step, viewerParty)
  const chip = TONE_CHIP[meta.tone] || TONE_CHIP.muted
  const Illo = STEP_ILLO[step.step_key] || FormFillIllustration
  // Blockers: the walkthrough payload names its own (localized, honest —
  // dependencies, missing documents, a closed statutory window); the pipeline
  // fallback derives unverified dependencies from the steps themselves.
  const serverBlockers = Array.isArray(step.blockers) && step.blockers.length
    ? step.blockers : null
  const fallbackDeps = (step.depends_on || []).filter((k) => {
    const dep = byKey[k]
    return !dep || dep.status !== 'verified'
  }).map((k) => stepLabel(t, k))
  const mayAct = who.mine || isAdmin

  async function release() {
    setBusy(true); setError(null)
    try {
      await client.h1bReleaseStep(caseId, step.step_key)
      onChanged()
    } catch (e) {
      setError({ message: e.message })
    }
    setBusy(false)
  }

  return (
    <div className={`anim-rise-${Math.min(index, 3)}`}
         style={{ display: 'flex', gap: 18, alignItems: 'stretch' }}
         data-testid={`h1b-step-${step.step_key}`}>
      {/* The rail: a node per step, a line that fills once the step verifies. */}
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center',
                    width: 36, flexShrink: 0 }} aria-hidden="true">
        <TimelineNode status={step.status} />
        {!last && (
          <div style={{ width: 3, flex: 1, borderRadius: 2, background: '#e3ebf5',
                        margin: '8px 0', overflow: 'hidden' }}>
            <div style={{ width: '100%', borderRadius: 2,
                          height: step.status === 'verified' ? '100%' : '0%',
                          background: 'linear-gradient(180deg, #1d9e55, var(--trip-blue, #2681ff))',
                          transition: 'height .8s cubic-bezier(.22,1,.36,1)' }} />
          </div>
        )}
      </div>

      <div className="card card-hover" style={{ flex: 1, minWidth: 0, padding: '20px 22px',
                    marginBottom: last ? 0 : 26 }}>
        <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
          <div style={{ width: 66, height: 56, borderRadius: 14, background: '#f0f6ff',
                        display: 'grid', placeItems: 'center', flexShrink: 0 }}>
            <Illo size={54} />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 800, fontSize: 15,
                          color: 'var(--trip-navy, #0f294d)' }}>
              {stepLabel(t, step.step_key)}
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
              <span className="chip" style={{ background: chip.bg, color: chip.fg, border: 'none' }}>
                {t(meta.i18nKey)}
              </span>
              {/* The walkthrough's own server-localized "waiting on the employer
                  / worker" line wins; the local mapping is the pipeline
                  fallback. Avatar: building = employer, person = worker. */}
              <span className="chip" data-testid="h1b-who-acts"
                    style={{ background: '#f4f7fb', border: 'none',
                             color: who.waiting ? 'var(--muted)' : 'var(--trip-navy, #0f294d)' }}>
                <Glyph name={who.acting === 'petitioner' ? 'building' : 'person'} size={13} />
                {step.waiting_on || t(who.i18nKey)}
              </span>
            </div>
          </div>
        </div>

        {step.explain && (
          <div style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 10, lineHeight: 1.55 }}>
            {step.explain}
          </div>
        )}

        {/* Blocked reasons stay visible and honest: the server's own localized
            sentences, or compact chips naming each blocking step. */}
        {serverBlockers ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 12 }}>
            {serverBlockers.map((b, i) => (
              <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start',
                            fontSize: 12, color: 'var(--muted)', lineHeight: 1.5,
                            background: '#f7fafd', borderRadius: 10, padding: '7px 11px' }}>
                <span style={{ color: '#b97b0a', marginTop: 1 }}><Glyph name="lock" size={12} /></span>
                <span>{String(b)}</span>
              </div>
            ))}
          </div>
        ) : (fallbackDeps.length > 0 && step.status === 'blocked' && (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}
               title={t('h1b.step.dependsOn', { deps: fallbackDeps.join(', ') })}>
            {fallbackDeps.map((d, i) => (
              <span key={i} className="chip-icon chip-icon--warn">
                <Glyph name="lock" size={13} />{d}
              </span>
            ))}
          </div>
        ))}

        {error && <ErrorNote error={error} />}

        <div style={{ display: 'flex', gap: 8, marginTop: 14, flexWrap: 'wrap' }}>
          {step.status === 'ready' && mayAct && (
            <button className="btn btn--sm" disabled={busy} onClick={release}>
              {busy ? t('h1b.action.releasing') : t('h1b.action.release')}
            </button>
          )}
          {step.child_case_id && onOpenCase && (
            <button className="btn btn--sm btn--ghost"
              onClick={() => onOpenCase({ id: step.child_case_id,
                full_name: stepLabel(t, step.step_key),
                destination_country: 'United States',
                visa_type: step.visa_type || VISA_TYPE_BY_STEP[step.step_key] || 'h1b' })}>
              {t('h1b.action.openFiling')}
            </button>
          )}
          {(step.child_case_id || isAdmin) && !['verified', 'blocked'].includes(step.status) && (
            <button className="btn btn--sm btn--ghost" onClick={() => setVerifyOpen((v) => !v)}>
              {t('h1b.action.verify')}
            </button>
          )}
          {/* The cockpit for THIS step: one screen with what Ellis prepared, what
              is missing, and the single action. Steps with a preparable form get
              the filing cockpit; the consular leg gets the appointment cockpit
              (its remaining act is an in-person one, not a form). */}
          {(FORM_KEY_BY_STEP[step.step_key] || step.step_key === 'ds160_consular') && (
            <button className="btn btn--sm btn--ghost" onClick={() => setCockpitOpen((v) => !v)}
                    data-testid={`h1b-cockpit-toggle-${step.step_key}`}>
              {step.step_key === 'ds160_consular' ? t('appt.open') : t('cockpit.open')}
            </button>
          )}
        </div>
        {verifyOpen && (
          <VerifyPanel t={t} client={client} caseId={caseId} step={step} isAdmin={isAdmin}
            onDone={() => { setVerifyOpen(false); onChanged() }}
            onError={(e) => setError({ message: e.message })} />
        )}
        {cockpitOpen && FORM_KEY_BY_STEP[step.step_key] && (
          <FilingCockpit client={client} caseId={caseId}
            formKey={FORM_KEY_BY_STEP[step.step_key]}
            // The secure window belongs to the CHILD filing case: the parent
            // petition is a container with no portal session of its own. Until
            // the step is released there is none, and the cockpit says so
            // instead of offering a button that cannot work.
            sessionCaseId={step.child_case_id || ''} />
        )}
        {cockpitOpen && step.step_key === 'ds160_consular' && (
          <AppointmentCockpit client={client} caseId={step.child_case_id || caseId}
                              showGroupRoster={false} />
        )}
      </div>
    </div>
  )
}

// ---- Form preparation cards ------------------------------------------------
function FormCards({ t, lang, client, caseId, steps }) {
  const [prepared, setPrepared] = useState({})   // formKey -> result
  const [busyKey, setBusyKey] = useState('')
  const [error, setError] = useState(null)
  const formKeys = steps.flatMap((s) => FORM_KEYS_BY_STEP[s.step_key] || [])

  async function prepare(formKey) {
    setBusyKey(formKey); setError(null)
    try {
      // The PWD request has its own named client method (it carries the
      // derivation provenance and the DOL-block notice the generic prepare
      // does not); everything else goes through the generic forms path.
      const res = formKey === 'eta-9141'
        ? await client.prepareEta9141(caseId, lang)
        : await client.h1bPrepareForm(caseId, formKey, lang)
      setPrepared((p) => ({ ...p, [formKey]: res || {} }))
    } catch (e) {
      setError({ message: e.message })
    }
    setBusyKey('')
  }

  if (formKeys.length === 0) return null
  return (
    <div style={{ marginTop: 48 }}>
      <div className="eyebrow">{t('h1b.forms.title')}</div>
      <div style={{ fontSize: 12.5, color: 'var(--muted)', margin: '2px 0 16px' }}>
        {t('h1b.forms.sub')}
      </div>
      {error && <ErrorNote error={error} />}
      {formKeys.map((fk) => {
        const res = prepared[fk]
        const missing = res ? (res.missing_fields || res.missing || []) : null
        const pct = res && res.filled_count != null && res.total_mapped
          ? Math.round((res.filled_count / res.total_mapped) * 100) : null
        return (
          <div key={fk} className="card card-hover" style={{ padding: '16px 20px', marginBottom: 12 }}
               data-testid={`h1b-form-${fk}`}>
            <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
              <div style={{ width: 48, height: 42, borderRadius: 12, background: '#f0f6ff',
                            display: 'grid', placeItems: 'center', flexShrink: 0 }}>
                <FormFillIllustration size={40} />
              </div>
              <div style={{ fontWeight: 700, fontSize: 13.5, flex: 1, minWidth: 140,
                            color: 'var(--trip-navy, #0f294d)' }}>
                {t(FORM_LABEL_KEY[fk] || 'h1b.forms.title')}
              </div>
              <button className="btn btn--sm" disabled={busyKey === fk}
                      onClick={() => prepare(fk)}>
                {busyKey === fk ? t('h1b.form.preparing') : t('h1b.form.prepare')}
              </button>
            </div>
            {res && (
              <div className="anim-rise" style={{ marginTop: 14 }}>
                {res.filled_count != null && res.total_mapped != null && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
                    <div className="trip-minibar" style={{ flex: 1, maxWidth: 240, marginTop: 0 }}>
                      <div className="trip-minibar__fill" style={{ width: `${pct || 0}%` }} />
                    </div>
                    <span style={{ fontSize: 11.5, fontWeight: 700,
                                   color: 'var(--trip-navy, #0f294d)' }}>
                      {res.filled_count}/{res.total_mapped}
                    </span>
                  </div>
                )}
                {Array.isArray(missing) && missing.length > 0 ? (
                  // The long bullet list becomes a count chip; the list itself
                  // is one tap away.
                  <details>
                    <summary style={{ listStyle: 'none', display: 'inline-flex', cursor: 'pointer' }}>
                      <span className="chip-icon chip-icon--warn">
                        <Glyph name="doc" size={13} />
                        {t('h1b.form.missingCount', { n: missing.length })}
                        <Glyph name="chevron" size={11} />
                      </span>
                    </summary>
                    <div style={{ fontSize: 11.5, fontWeight: 700, color: 'var(--muted)',
                                  margin: '10px 0 0 2px' }}>
                      {t('h1b.form.missing')}
                    </div>
                    <ul style={{ margin: '4px 0 0 18px', fontSize: 12.5, color: 'var(--muted)',
                                 lineHeight: 1.7 }}>
                      {missing.map((m, i) => (
                        <li key={i}>{typeof m === 'string' ? m : (m.label || m.question || m.key || '')}</li>
                      ))}
                    </ul>
                  </details>
                ) : (
                  <span className="chip-icon chip-icon--ok" style={{ whiteSpace: 'normal' }}>
                    <Glyph name="check" size={13} />{t('h1b.form.noMissing')}
                  </span>
                )}
                {/* The form's own human-only lines (signatures, declarations),
                    named by the backend, on top of the standing warning. */}
                {Array.isArray(res.human_only) && res.human_only.length > 0 && (
                  <ul style={{ margin: '10px 0 0 18px', fontSize: 12, color: '#c77700' }}>
                    {res.human_only.map((h, i) => (
                      <li key={i}>{typeof h === 'string' ? h : (h.label || h.key || '')}</li>
                    ))}
                  </ul>
                )}
                {res.preparation_notice && (
                  <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 8, lineHeight: 1.5 }}>
                    {res.preparation_notice}
                  </div>
                )}
              </div>
            )}
            {/* The human-only signature warning is unconditional — it is the
                doctrine, not a payload detail. */}
            <div style={{ display: 'flex', gap: 7, alignItems: 'flex-start', fontSize: 11.5,
                          color: 'var(--muted)', marginTop: 12, lineHeight: 1.5 }}
                 data-testid="h1b-signature-note">
              <span style={{ color: '#b97b0a', marginTop: 1 }}><Glyph name="lock" size={12} /></span>
              <span>{t('h1b.form.signatureNote')}</span>
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ---- RFE risks (severity-colored, curing evidence expandable) --------------
function RfePanel({ t, lang, client, caseId }) {
  const [risks, setRisks] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function load() {
    setBusy(true); setError(null)
    try {
      const res = await client.h1bRfeRisks(caseId, lang)
      setRisks(Array.isArray(res && res.risks) ? res.risks : [])
    } catch (e) {
      setError({ message: e.message })
    }
    setBusy(false)
  }

  return (
    <div className="card anim-rise" style={{ marginTop: 14, padding: 18 }}
         data-testid="h1b-rfe-panel">
      <div style={{ fontSize: 12.5, color: 'var(--muted)', marginBottom: 12 }}>
        {t('h1b.rfe.sub')}
      </div>
      {error && <ErrorNote error={error} />}
      {risks === null ? (
        <button className="btn btn--sm" disabled={busy} onClick={load}>
          {busy ? t('h1b.rfe.loading') : t('h1b.rfe.load')}
        </button>
      ) : risks.length === 0 ? (
        <span className="chip-icon chip-icon--ok" style={{ whiteSpace: 'normal' }}>
          <Glyph name="check" size={13} />{t('h1b.rfe.none')}
        </span>
      ) : (
        risks.map((r, i) => {
          const sev = String(r.severity || '').toLowerCase()
          const color = SEVERITY_COLOR[sev] || 'var(--muted)'
          const curing = r.curing_evidence || r.curing || []
          return (
            <div key={r.id || i} className="card" style={{ padding: 14, marginBottom: 10,
                 borderLeft: `3px solid ${color}` }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
                <span style={{ fontSize: 11, fontWeight: 700, color }}>
                  {r.severity_label ||
                    (t(`h1b.severity.${sev}`) === `h1b.severity.${sev}` ? sev : t(`h1b.severity.${sev}`))}
                </span>
                <div style={{ fontWeight: 600, fontSize: 13 }}>{r.title || r.label || r.ground || ''}</div>
              </div>
              {(r.uscis_wording || r.detail || r.why) && (
                <div style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 4, lineHeight: 1.5 }}>
                  {r.uscis_wording || r.detail || r.why}
                </div>
              )}
              {Array.isArray(r.signals) && r.signals.length > 0 && (
                <ul style={{ margin: '4px 0 0 18px', fontSize: 12, color: 'var(--muted)' }}>
                  {r.signals.map((sg, j) => (
                    <li key={j}>{typeof sg === 'string' ? sg : (sg.label || sg.fact || '')}</li>
                  ))}
                </ul>
              )}
              {Array.isArray(curing) && curing.length > 0 && (
                <details style={{ marginTop: 6 }}>
                  <summary style={{ fontSize: 12.5, cursor: 'pointer' }}>{t('h1b.rfe.curing')}</summary>
                  <ul style={{ margin: '4px 0 0 18px', fontSize: 12.5, color: 'var(--muted)' }}>
                    {curing.map((c, j) => <li key={j}>{typeof c === 'string' ? c : (c.label || '')}</li>)}
                  </ul>
                </details>
              )}
            </div>
          )
        })
      )}
    </div>
  )
}

// ---- Evidence index --------------------------------------------------------
function EvidencePanel({ t, lang, client, caseId }) {
  const [items, setItems] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function load() {
    setBusy(true); setError(null)
    try {
      const res = await client.h1bEvidenceIndex(caseId, lang)
      setItems(Array.isArray(res && (res.items || res.exhibits))
        ? (res.items || res.exhibits) : [])
    } catch (e) {
      setError({ message: e.message })
    }
    setBusy(false)
  }

  return (
    <div className="card anim-rise" style={{ marginTop: 14, padding: 18 }}
         data-testid="h1b-evidence-panel">
      <div style={{ fontSize: 12.5, color: 'var(--muted)', marginBottom: 12 }}>
        {t('h1b.evidence.sub')}
      </div>
      {error && <ErrorNote error={error} />}
      {items === null ? (
        <button className="btn btn--sm" disabled={busy} onClick={load}>
          {busy ? t('common.loading') : t('h1b.evidence.load')}
        </button>
      ) : items.length === 0 ? (
        <div style={{ fontSize: 12.5, color: 'var(--muted)' }}>{t('h1b.evidence.empty')}</div>
      ) : (
        <div>
          {items.map((it, i) => (
            <div key={it.item_id || i} className="row">
              <div className="row__main">
                <div className="row__title" style={{ fontSize: 13 }}>
                  {it.exhibit_no != null ? `${it.exhibit_no}. ` : ''}
                  {it.title || it.label || it.name || it.doc_type || ''}
                </div>
                {(it.doc_type || it.party) && (
                  <div className="row__sub">
                    {[it.doc_type, it.party].filter(Boolean).join(' · ')}
                  </div>
                )}
              </div>
              {(it.status_label || it.status) && (
                <span className={'chip' + (it.status === 'accepted' ? ' chip--ink' : '')}>
                  {it.status_label || it.status}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ---- Narrative drafts (petitioner/admin only) ------------------------------
function NarrativePanel({ t, lang, client, caseId }) {
  const [kind, setKind] = useState('support_letter')
  const [draft, setDraft] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function generate() {
    setBusy(true); setError(null)
    try {
      const res = await client.h1bNarrative(caseId, kind, lang)
      // Backend contract (counsel.draft_narrative): draft_text carries the
      // DRAFT-labeled body; older shapes tolerated.
      setDraft((res && (res.draft_text || res.draft || res.text || res.narrative)) || '')
    } catch (e) {
      setError({ message: e.message })
    }
    setBusy(false)
  }

  return (
    <div className="card anim-rise" style={{ marginTop: 14, padding: 18 }}
         data-testid="h1b-narrative-panel">
      <div style={{ fontSize: 12.5, color: 'var(--muted)', marginBottom: 12 }}>
        {t('h1b.narrative.sub')}
      </div>
      {error && <ErrorNote error={error} />}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        {/* counsel.NARRATIVE_KINDS pins the offered kinds; new kinds join the
            backend tuple first, then this list. */}
        <select className="select" style={{ maxWidth: 260 }} value={kind}
                onChange={(e) => setKind(e.target.value)}>
          <option value="support_letter">{t('h1b.narrative.kind.support_letter')}</option>
        </select>
        <button className="btn btn--sm" disabled={busy} onClick={generate}>
          {busy ? t('h1b.narrative.generating') : t('h1b.narrative.generate')}
        </button>
      </div>
      {draft != null && (
        <div className="card anim-rise" style={{ padding: 16, marginTop: 12 }}>
          {/* DRAFT label + disclaimer, prominently, before the text. */}
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8,
                        flexWrap: 'wrap' }}>
            <span className="chip" style={{ background: 'transparent',
                    border: '1px solid #c77700', color: '#c77700' }}
                  data-testid="h1b-draft-label">
              {t('h1b.narrative.draft')}
            </span>
            <span style={{ fontSize: 11, color: 'var(--muted)' }}>{t('h1b.disclaimer.short')}</span>
          </div>
          <div style={{ fontSize: 13, whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>{draft}</div>
        </div>
      )}
    </div>
  )
}

// ---- Paper packet ----------------------------------------------------------
function PaperPacketPanel({ t, lang, client, caseId }) {
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function build() {
    setBusy(true); setError(null)
    try {
      setResult(await client.h1bPaperPacket(caseId, lang))
    } catch (e) {
      setError({ message: e.message })
    }
    setBusy(false)
  }

  // forms_api.build_paper_packet contract: lockbox addresses, wet-ink
  // warnings, the verify-before-mailing and nothing-submitted notices, and
  // the exhibit list — all rendered honestly, none invented client-side.
  const exhibits = result && (result.exhibits || result.contents || result.items)
  const notices = result
    ? [result.verify_address_notice, result.dependents_paper_notice,
       result.nothing_submitted_notice].filter(Boolean)
    : []
  return (
    <div className="card anim-rise" style={{ marginTop: 14, padding: 18 }}
         data-testid="h1b-paper-panel">
      <div style={{ fontSize: 12.5, color: 'var(--muted)', marginBottom: 12 }}>
        {t('h1b.paper.sub')}
      </div>
      {error && <ErrorNote error={error} />}
      <button className="btn btn--sm" disabled={busy} onClick={build}>
        {busy ? t('h1b.paper.building') : t('h1b.paper.get')}
      </button>
      {result && (
        <div className="card anim-rise" style={{ padding: 16, marginTop: 12, fontSize: 12.5 }}>
          {(result.usps_address || result.courier_address) && (
            <div style={{ whiteSpace: 'pre-wrap', marginBottom: 8, lineHeight: 1.5 }}>
              {[result.usps_address, result.courier_address].filter(Boolean).join('\n\n')}
            </div>
          )}
          {Array.isArray(result.wet_ink_warnings) && result.wet_ink_warnings.length > 0 && (
            <ul style={{ margin: '0 0 8px 18px', color: '#c77700', lineHeight: 1.6 }}>
              {result.wet_ink_warnings.map((w, i) => <li key={i}>{String(w)}</li>)}
            </ul>
          )}
          {Array.isArray(exhibits) && exhibits.length > 0 && (
            <ul style={{ margin: '0 0 0 18px', color: 'var(--muted)', lineHeight: 1.7 }}>
              {exhibits.map((c, i) => (
                <li key={i}>{typeof c === 'string' ? c : (c.title || c.label || c.name || '')}</li>
              ))}
            </ul>
          )}
          {notices.map((n, i) => (
            <div key={i} style={{ color: 'var(--muted)', marginTop: 8, lineHeight: 1.5 }}>{n}</div>
          ))}
        </div>
      )}
    </div>
  )
}

// The counsel toolbox: a 2x2 icon-card grid; each card opens its panel on tap.
const TOOLS = [
  { id: 'rfe', titleKey: 'h1b.rfe.title', Illo: ShieldIllustration, petitionerOnly: false },
  { id: 'evidence', titleKey: 'h1b.evidence.title', Illo: DocumentsIllustration, petitionerOnly: true },
  { id: 'narrative', titleKey: 'h1b.narrative.title', Illo: FormFillIllustration, petitionerOnly: true },
  { id: 'paper', titleKey: 'h1b.paper.title', Illo: EnvelopeIllustration, petitionerOnly: true }
]

// ---- The walkthrough surface ----------------------------------------------
export default function H1bPipeline({ client, caseId, persona, onOpenCase }) {
  const { t, lang } = useLocale()
  const viewerPersona = persona || detectPersona()
  const viewerParty = partyForPersona(viewerPersona)
  const isAdmin = viewerParty === 'admin'
  const [payload, setPayload] = useState(null)
  const [error, setError] = useState(null)
  const [openTool, setOpenTool] = useState('')

  // Register the open case for the floating Ask Ellis assistant.
  useEffect(() => {
    setActiveH1bCase(caseId)
    return () => setActiveH1bCase('')
  }, [caseId])

  async function refresh() {
    try {
      // Prefer the walkthrough payload; fall back to the pipeline payload so
      // the surface stays honest while sibling endpoints land.
      let data
      try {
        data = await client.h1bWalkthrough(caseId, lang)
      } catch {
        data = await client.h1bPipeline(caseId)
      }
      setPayload(data)
      setError(null)
    } catch (e) {
      setError({ message: e.message })
    }
  }
  useEffect(() => { setPayload(null); refresh() }, [caseId, lang])

  if (error) return <ErrorNote error={error} />
  if (!payload) return <Loading label={t('common.loading')} />

  const steps = Array.isArray(payload.steps) ? payload.steps : []
  const byKey = Object.fromEntries(steps.map((s) => [s.step_key, s]))
  const disclaimer = payload.attorney_disclaimer || t('h1b.disclaimer')
  // Forms, evidence index, narrative drafts and the paper packet are
  // petitioner acts server-side (forms_api/counsel_api authorize the
  // petitioner party or an admin); the beneficiary view hides them as a
  // courtesy — the server remains the wall. RFE risks are visible to both
  // parties (the backend redacts the petitioner's private wage facts for a
  // beneficiary-bound caller).
  const canPetition = viewerParty === 'petitioner' || isAdmin
  const tools = TOOLS.filter((tool) => canPetition || !tool.petitionerOnly)

  return (
    <div className="fadeup-1" data-testid="h1b-pipeline">
      {/* Hero: four words, one line, one illustration. */}
      <div className="card anim-rise" style={{ padding: '26px 30px', marginBottom: 14,
                    display: 'flex', alignItems: 'center', gap: 24,
                    justifyContent: 'space-between', flexWrap: 'wrap' }}>
        <div style={{ minWidth: 200 }}>
          <div style={{ fontWeight: 800, fontSize: 23, letterSpacing: '-0.4px',
                        color: 'var(--trip-navy, #0f294d)' }}>
            {t('h1b.pipeline.title')}
          </div>
          <div style={{ fontSize: 13, color: 'var(--muted)', marginTop: 6 }}>
            {t('h1b.pipeline.sub')}
          </div>
          {payload.case_kind && (
            <span className="chip" style={{ marginTop: 14 }}>
              {t(`h1b.kind.${payload.case_kind}`) === `h1b.kind.${payload.case_kind}`
                ? payload.case_kind : t(`h1b.kind.${payload.case_kind}`)}
            </span>
          )}
        </div>
        <PipelineIllustration size={190} />
      </div>

      {/* The attorney disclaimer: always visible as one compact line, the
          full text one tap away — never deleted, never buried. */}
      <details className="card anim-rise-1" data-testid="h1b-pipeline-disclaimer"
               style={{ padding: '10px 16px', marginBottom: 44 }}>
        <summary style={{ listStyle: 'none', display: 'flex', alignItems: 'center',
                          gap: 12, cursor: 'pointer' }}>
          <ShieldIllustration size={34} />
          <span style={{ fontSize: 12, color: 'var(--muted)', flex: 1, minWidth: 0 }}>
            {t('h1b.disclaimer.short')}
          </span>
          <span style={{ color: 'var(--muted-2, #8592a6)' }}><Glyph name="chevron" size={12} /></span>
        </summary>
        <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.6,
                      padding: '10px 4px 4px 46px' }}>
          {disclaimer}
        </div>
      </details>

      {steps.length === 0
        ? <Empty title={t('h1b.pipeline.title')} sub={t('h1b.status.unknown')} />
        : steps.map((s, i) => (
            <StepCard key={s.step_key} t={t} client={client} caseId={caseId}
              step={s} byKey={byKey} viewerParty={viewerParty} isAdmin={isAdmin}
              onChanged={refresh} onOpenCase={onOpenCase}
              index={i} last={i === steps.length - 1} />
          ))}

      {canPetition && (
        <FormCards t={t} lang={lang} client={client} caseId={caseId} steps={steps} />
      )}

      {/* Counsel toolbox: icon cards, each opening on tap. */}
      <div style={{ marginTop: 48 }}>
        <div style={{ display: 'grid', gap: 14,
                      gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))' }}>
          {tools.map((tool, i) => {
            const active = openTool === tool.id
            const Illo = tool.Illo
            return (
              <button key={tool.id} type="button"
                className={`card card-hover anim-rise-${Math.min(i, 3)}`}
                aria-expanded={active}
                onClick={() => setOpenTool((o) => (o === tool.id ? '' : tool.id))}
                style={{ padding: '18px 12px 14px', textAlign: 'center', cursor: 'pointer',
                         background: '#fff',
                         border: active ? '2px solid var(--trip-blue, #2681ff)'
                                        : '2px solid transparent' }}>
                <span style={{ display: 'grid', placeItems: 'center' }}>
                  <Illo size={96} />
                </span>
                <div style={{ fontSize: 13, fontWeight: 700, marginTop: 6,
                              color: 'var(--trip-navy, #0f294d)' }}>
                  {t(tool.titleKey)}
                </div>
              </button>
            )
          })}
        </div>
        {openTool === 'rfe' && (
          <RfePanel t={t} lang={lang} client={client} caseId={caseId} />
        )}
        {openTool === 'evidence' && canPetition && (
          <EvidencePanel t={t} lang={lang} client={client} caseId={caseId} />
        )}
        {openTool === 'narrative' && canPetition && (
          <NarrativePanel t={t} lang={lang} client={client} caseId={caseId} />
        )}
        {openTool === 'paper' && canPetition && (
          <PaperPacketPanel t={t} lang={lang} client={client} caseId={caseId} />
        )}
      </div>
    </div>
  )
}
