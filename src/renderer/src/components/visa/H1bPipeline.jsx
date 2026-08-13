// The H-1B guided walkthrough — the parent petition's two-party surface.
// Renders the walkthrough payload (falling back to the honest pipeline payload
// when the walkthrough endpoint is not live yet): step cards with real status,
// who acts (the OTHER party's step reads "waiting on the employer/worker",
// never as an action the viewer could take), blockers, and next-action buttons
// wired to release / verify / prepare-form / paper-packet. Doctrine carried
// here: the attorney disclaimer on every payload, signature cells are human
// only, denied/blocked actions surface their honest backend reason, and the
// admin-only offline-evidence picker mirrors the server's administrator gate —
// UI hiding is a courtesy; the server is the wall.
import { useEffect, useState } from 'react'
import { useLocale } from '../../lib/locale.jsx'
import { Loading, ErrorNote, Empty } from '../ui.jsx'
import {
  detectPersona, partyForPersona, h1bStepMeta, h1bWhoActs, setActiveH1bCase
} from '../../lib/visaSession.js'
import FilingCockpit from './FilingCockpit.jsx'
import AppointmentCockpit from './AppointmentCockpit.jsx'

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

function stepLabel(t, stepKey) {
  const known = STEP_ORDER.includes(stepKey)
  return known ? t(`h1b.step.${stepKey}`) : stepKey
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
    <div style={{ marginTop: 10, padding: 12, borderRadius: 10,
                  background: 'var(--bg-2, #f5f6f8)' }} data-testid="h1b-verify-panel">
      <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>{t('h1b.verify.title')}</div>
      {isAdmin && (
        <>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 8 }}>
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
                    onChanged, onOpenCase }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [verifyOpen, setVerifyOpen] = useState(false)
  const [cockpitOpen, setCockpitOpen] = useState(false)
  const meta = h1bStepMeta(step.status)
  const who = h1bWhoActs(step, viewerParty)
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
    <div className="card" style={{ padding: 16, marginBottom: 10 }}
         data-testid={`h1b-step-${step.step_key}`}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10,
                    alignItems: 'baseline', flexWrap: 'wrap' }}>
        <div style={{ fontWeight: 700 }}>{stepLabel(t, step.step_key)}</div>
        <span className={'chip' + (meta.tone === 'ok' ? ' chip--ink' : '')}>
          {t(meta.i18nKey)}
        </span>
      </div>
      {/* The walkthrough's own server-localized "waiting on the employer /
          worker" line wins; the local mapping is the pipeline fallback. */}
      <div style={{ fontSize: 12.5, color: who.waiting ? 'var(--muted)' : 'inherit', marginTop: 4 }}
           data-testid="h1b-who-acts">
        {step.waiting_on || t(who.i18nKey)}
      </div>
      {step.explain && (
        <div style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 4 }}>{step.explain}</div>
      )}
      {serverBlockers ? (
        <ul style={{ margin: '6px 0 0 18px', fontSize: 12, color: 'var(--muted)' }}>
          {serverBlockers.map((b, i) => <li key={i}>{String(b)}</li>)}
        </ul>
      ) : (fallbackDeps.length > 0 && step.status === 'blocked' && (
        <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 6 }}>
          {t('h1b.step.dependsOn').replace('{deps}', fallbackDeps.join(', '))}
        </div>
      ))}
      {error && <ErrorNote error={error} />}
      <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
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
    <div style={{ marginTop: 18 }}>
      <div className="eyebrow">{t('h1b.forms.title')}</div>
      <div style={{ fontSize: 12.5, color: 'var(--muted)', margin: '4px 0 10px' }}>
        {t('h1b.forms.sub')}
      </div>
      {error && <ErrorNote error={error} />}
      {formKeys.map((fk) => {
        const res = prepared[fk]
        const missing = res ? (res.missing_fields || res.missing || []) : null
        return (
          <div key={fk} className="card" style={{ padding: 14, marginBottom: 8 }}
               data-testid={`h1b-form-${fk}`}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10,
                          alignItems: 'center', flexWrap: 'wrap' }}>
              <div style={{ fontWeight: 600, fontSize: 13.5 }}>
                {t(FORM_LABEL_KEY[fk] || 'h1b.forms.title')}
              </div>
              <button className="btn btn--sm btn--ghost" disabled={busyKey === fk}
                      onClick={() => prepare(fk)}>
                {busyKey === fk ? t('h1b.form.preparing') : t('h1b.form.prepare')}
              </button>
            </div>
            {res && (
              <div style={{ marginTop: 8 }}>
                {res.filled_count != null && res.total_mapped != null && (
                  <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 4 }}>
                    {res.filled_count} / {res.total_mapped}
                  </div>
                )}
                {Array.isArray(missing) && missing.length > 0 ? (
                  <>
                    <div style={{ fontSize: 12.5 }}>{t('h1b.form.missing')}</div>
                    <ul style={{ margin: '4px 0 0 18px', fontSize: 12.5, color: 'var(--muted)' }}>
                      {missing.map((m, i) => (
                        <li key={i}>{typeof m === 'string' ? m : (m.label || m.question || m.key || '')}</li>
                      ))}
                    </ul>
                  </>
                ) : (
                  <div style={{ fontSize: 12.5, color: 'var(--muted)' }}>{t('h1b.form.noMissing')}</div>
                )}
                {/* The form's own human-only lines (signatures, declarations),
                    named by the backend, on top of the standing warning. */}
                {Array.isArray(res.human_only) && res.human_only.length > 0 && (
                  <ul style={{ margin: '6px 0 0 18px', fontSize: 12, color: '#c77700' }}>
                    {res.human_only.map((h, i) => (
                      <li key={i}>{typeof h === 'string' ? h : (h.label || h.key || '')}</li>
                    ))}
                  </ul>
                )}
                {res.preparation_notice && (
                  <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 6 }}>
                    {res.preparation_notice}
                  </div>
                )}
              </div>
            )}
            {/* The human-only signature warning is unconditional — it is the
                doctrine, not a payload detail. */}
            <div style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 8 }}
                 data-testid="h1b-signature-note">
              {t('h1b.form.signatureNote')}
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
    <div style={{ marginTop: 18 }} data-testid="h1b-rfe-panel">
      <div className="eyebrow">{t('h1b.rfe.title')}</div>
      <div style={{ fontSize: 12.5, color: 'var(--muted)', margin: '4px 0 10px' }}>
        {t('h1b.rfe.sub')}
      </div>
      {error && <ErrorNote error={error} />}
      {risks === null ? (
        <button className="btn btn--sm btn--ghost" disabled={busy} onClick={load}>
          {busy ? t('h1b.rfe.loading') : t('h1b.rfe.load')}
        </button>
      ) : risks.length === 0 ? (
        <div style={{ fontSize: 12.5, color: 'var(--muted)' }}>{t('h1b.rfe.none')}</div>
      ) : (
        risks.map((r, i) => {
          const sev = String(r.severity || '').toLowerCase()
          const color = SEVERITY_COLOR[sev] || 'var(--muted)'
          const curing = r.curing_evidence || r.curing || []
          return (
            <div key={r.id || i} className="card" style={{ padding: 12, marginBottom: 8,
                 borderLeft: `3px solid ${color}` }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
                <span style={{ fontSize: 11, fontWeight: 700, color }}>
                  {r.severity_label ||
                    (t(`h1b.severity.${sev}`) === `h1b.severity.${sev}` ? sev : t(`h1b.severity.${sev}`))}
                </span>
                <div style={{ fontWeight: 600, fontSize: 13 }}>{r.title || r.label || r.ground || ''}</div>
              </div>
              {(r.uscis_wording || r.detail || r.why) && (
                <div style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 4 }}>
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
    <div style={{ marginTop: 18 }} data-testid="h1b-evidence-panel">
      <div className="eyebrow">{t('h1b.evidence.title')}</div>
      <div style={{ fontSize: 12.5, color: 'var(--muted)', margin: '4px 0 10px' }}>
        {t('h1b.evidence.sub')}
      </div>
      {error && <ErrorNote error={error} />}
      {items === null ? (
        <button className="btn btn--sm btn--ghost" disabled={busy} onClick={load}>
          {busy ? t('common.loading') : t('h1b.evidence.load')}
        </button>
      ) : items.length === 0 ? (
        <div style={{ fontSize: 12.5, color: 'var(--muted)' }}>{t('h1b.evidence.empty')}</div>
      ) : (
        <div className="card" style={{ padding: 12 }}>
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
    <div style={{ marginTop: 18 }} data-testid="h1b-narrative-panel">
      <div className="eyebrow">{t('h1b.narrative.title')}</div>
      <div style={{ fontSize: 12.5, color: 'var(--muted)', margin: '4px 0 10px' }}>
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
        <button className="btn btn--sm btn--ghost" disabled={busy} onClick={generate}>
          {busy ? t('h1b.narrative.generating') : t('h1b.narrative.generate')}
        </button>
      </div>
      {draft != null && (
        <div className="card" style={{ padding: 14, marginTop: 10 }}>
          {/* DRAFT label + disclaimer, prominently, before the text. */}
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6 }}>
            <span className="chip" style={{ border: '1px solid #c77700', color: '#c77700' }}
                  data-testid="h1b-draft-label">
              {t('h1b.narrative.draft')}
            </span>
            <span style={{ fontSize: 11, color: 'var(--muted)' }}>{t('h1b.disclaimer')}</span>
          </div>
          <div style={{ fontSize: 13, whiteSpace: 'pre-wrap' }}>{draft}</div>
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
    <div style={{ marginTop: 18 }} data-testid="h1b-paper-panel">
      <div className="eyebrow">{t('h1b.paper.title')}</div>
      <div style={{ fontSize: 12.5, color: 'var(--muted)', margin: '4px 0 10px' }}>
        {t('h1b.paper.sub')}
      </div>
      {error && <ErrorNote error={error} />}
      <button className="btn btn--sm btn--ghost" disabled={busy} onClick={build}>
        {busy ? t('h1b.paper.building') : t('h1b.paper.get')}
      </button>
      {result && (
        <div className="card" style={{ padding: 12, marginTop: 10, fontSize: 12.5 }}>
          {(result.usps_address || result.courier_address) && (
            <div style={{ whiteSpace: 'pre-wrap', marginBottom: 6 }}>
              {[result.usps_address, result.courier_address].filter(Boolean).join('\n\n')}
            </div>
          )}
          {Array.isArray(result.wet_ink_warnings) && result.wet_ink_warnings.length > 0 && (
            <ul style={{ margin: '0 0 6px 18px', color: '#c77700' }}>
              {result.wet_ink_warnings.map((w, i) => <li key={i}>{String(w)}</li>)}
            </ul>
          )}
          {Array.isArray(exhibits) && exhibits.length > 0 && (
            <ul style={{ margin: '0 0 0 18px', color: 'var(--muted)' }}>
              {exhibits.map((c, i) => (
                <li key={i}>{typeof c === 'string' ? c : (c.title || c.label || c.name || '')}</li>
              ))}
            </ul>
          )}
          {notices.map((n, i) => (
            <div key={i} style={{ color: 'var(--muted)', marginTop: 6 }}>{n}</div>
          ))}
        </div>
      )}
    </div>
  )
}

// ---- The walkthrough surface ----------------------------------------------
export default function H1bPipeline({ client, caseId, persona, onOpenCase }) {
  const { t, lang } = useLocale()
  const viewerPersona = persona || detectPersona()
  const viewerParty = partyForPersona(viewerPersona)
  const isAdmin = viewerParty === 'admin'
  const [payload, setPayload] = useState(null)
  const [error, setError] = useState(null)

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

  return (
    <div className="fadeup-1" data-testid="h1b-pipeline">
      <div className="card" style={{ padding: 20, marginBottom: 12 }}>
        <div style={{ fontWeight: 700, fontSize: 16 }}>{t('h1b.pipeline.title')}</div>
        <div style={{ fontSize: 13, color: 'var(--muted)', marginTop: 4 }}>
          {t('h1b.pipeline.sub')}
        </div>
        {payload.case_kind && (
          <span className="chip" style={{ marginTop: 8 }}>
            {t(`h1b.kind.${payload.case_kind}`) === `h1b.kind.${payload.case_kind}`
              ? payload.case_kind : t(`h1b.kind.${payload.case_kind}`)}
          </span>
        )}
        <div style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 10 }}
             data-testid="h1b-pipeline-disclaimer">
          {disclaimer}
        </div>
      </div>

      {steps.length === 0
        ? <Empty title={t('h1b.pipeline.title')} sub={t('h1b.status.unknown')} />
        : steps.map((s) => (
            <StepCard key={s.step_key} t={t} client={client} caseId={caseId}
              step={s} byKey={byKey} viewerParty={viewerParty} isAdmin={isAdmin}
              onChanged={refresh} onOpenCase={onOpenCase} />
          ))}

      {/* Forms, evidence index, narrative drafts and the paper packet are
          petitioner acts server-side (forms_api/counsel_api authorize the
          petitioner party or an admin); the beneficiary view hides them as a
          courtesy — the server remains the wall. RFE risks are visible to
          both parties (the backend redacts the petitioner's private wage
          facts for a beneficiary-bound caller). */}
      {(viewerParty === 'petitioner' || isAdmin) && (
        <FormCards t={t} lang={lang} client={client} caseId={caseId} steps={steps} />
      )}
      <RfePanel t={t} lang={lang} client={client} caseId={caseId} />
      {(viewerParty === 'petitioner' || isAdmin) && (
        <EvidencePanel t={t} lang={lang} client={client} caseId={caseId} />
      )}
      {(viewerParty === 'petitioner' || isAdmin) && (
        <NarrativePanel t={t} lang={lang} client={client} caseId={caseId} />
      )}
      {(viewerParty === 'petitioner' || isAdmin) && (
        <PaperPacketPanel t={t} lang={lang} client={client} caseId={caseId} />
      )}
    </div>
  )
}
