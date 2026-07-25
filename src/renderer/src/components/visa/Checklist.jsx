// Route-specific document checklist (applicant journey). Items come from the
// backend; per-item status is computed server-side from the case's documents
// AND the applicant's explicit submissions. Each Needed requirement has its
// own Upload action that binds the file to that exact requirement; a primary
// Submit fulfils it only after the applicant reviews the assignment. Nothing
// is fulfilled by merely selecting a file. Applicant-facing only — no
// operator terminology, no internal identifiers.
import { useRef, useState } from 'react'
import { useToast, Loading } from '../ui.jsx'
import {
  checklistStatusMeta, checklistCounts, continueButtonMeta, docTypeLabelKey,
  MANUAL_DOC_TYPES
} from '../../lib/intake.js'
import DocPreview from './DocPreview.jsx'

const ALLOWED = { 'application/pdf': 1, 'image/jpeg': 1, 'image/png': 1, 'image/tiff': 1 }
const MAX_BYTES = 10 * 1024 * 1024

function readAsBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(String(r.result).split(',')[1] || '')
    r.onerror = () => reject(new Error('could not read file'))
    r.readAsDataURL(file)
  })
}

// One uploadable requirement row: Needed → Upload → Processing → review →
// Submit → Fulfilled, with Replace / Remove / Withdraw. Classification is
// ADVISORY only: a differing detected type warns with the exact wording and
// "Submit anyway" — the applicant's explicit choice always wins. Detected
// language + optional Kimi K3 machine translation live here too.
function DocumentItemRow({ t, client, caseId, item, translation, onChanged }) {
  const toast = useToast()
  const inputRef = useRef(null)
  const [busy, setBusy] = useState('')        // 'upload' | 'submit' | 'withdraw' | 'type' | 'translate'
  const [preview, setPreview] = useState(false)
  const [previewDoc, setPreviewDoc] = useState(null)   // {id, name} being previewed
  const [pickType, setPickType] = useState(false)
  const [askTranslate, setAskTranslate] = useState(false)
  const binding = item.binding || null
  const status = item.status
  const meta = checklistStatusMeta(status)
  const done = status === 'submitted'
  const detectedKey = binding ? docTypeLabelKey(binding.detected_type) : null
  const language = (binding && binding.language) || {}
  const target = translation || {}
  const canTranslate = !!binding && binding.has_text && language.code &&
    target.target && language.code !== target.target &&
    binding.detected_type !== 'translation'

  async function onFile(file) {
    if (!file) return
    if (!ALLOWED[file.type]) { toast(t('checklist.unsupportedType')); return }
    if (file.size > MAX_BYTES) { toast(t('checklist.tooLarge')); return }
    setBusy('upload')
    try {
      const b64 = await readAsBase64(file)
      const res = await client.addDocument(caseId, {
        name: file.name, mime: file.type, size_bytes: file.size,
        content_b64: b64, checklist_item_id: item.id
      })
      if (res && res.rejected) toast(res.message || t('checklist.unreadableToast'))
      else toast(t('checklist.uploadedToast'))
      onChanged && onChanged()
    } catch (e) {
      toast(typeof e.detail === 'object' && e.detail?.message ? e.detail.message : e.message)
    }
    setBusy('')
    if (inputRef.current) inputRef.current.value = ''
  }

  async function submit(confirm = false) {
    setBusy('submit')
    try {
      await client.submitChecklistDoc(caseId, item.id, binding?.document_id, confirm)
      toast(t('checklist.submittedToast'))
      onChanged && onChanged()
    } catch (e) {
      toast(typeof e.detail === 'object' && e.detail?.message ? e.detail.message : e.message)
    }
    setBusy('')
  }

  async function withdraw() {
    setBusy('withdraw')
    try {
      await client.withdrawChecklistDoc(caseId, item.id)
      toast(t('checklist.withdrawnToast'))
      onChanged && onChanged()
    } catch (e) { toast(e.message) }
    setBusy('')
  }

  async function chooseType(docType) {
    setBusy('type')
    setPickType(false)
    try {
      await client.setDocumentType(caseId, binding.document_id, docType)
      toast(t('checklist.typeSetToast'))
      onChanged && onChanged()
    } catch (e) {
      toast(typeof e.detail === 'object' && e.detail?.message ? e.detail.message : e.message)
    }
    setBusy('')
  }

  // Runs only after the applicant's explicit consent (the extracted TEXT —
  // never the image/PDF bytes — is sent to Kimi K3).
  async function translate() {
    setAskTranslate(false)
    setBusy('translate')
    try {
      await client.translateDocument(caseId, binding.document_id)
      toast(t('checklist.translatedToast'))
      onChanged && onChanged()
    } catch (e) {
      toast(typeof e.detail === 'object' && e.detail?.message ? e.detail.message : e.message)
    }
    setBusy('')
  }

  async function attachTranslation() {
    setBusy('type')
    try {
      await client.bindChecklistDoc(caseId, item.id, binding.translation_document_id)
      toast(t('checklist.translationAttached'))
      onChanged && onChanged()
    } catch (e) {
      toast(typeof e.detail === 'object' && e.detail?.message ? e.detail.message : e.message)
    }
    setBusy('')
  }

  return (
    <div className="row" style={{ alignItems: 'flex-start', gap: 10, flexWrap: 'wrap' }}
      data-testid={'checklist-' + item.id} data-status={status}>
      <span className={'sevbadge ' + (done ? 'sevbadge--ok'
        : meta.tone === 'blocked' ? 'sevbadge--bad' : 'sevbadge--mid')}>
        {done ? '✓' : meta.tone === 'blocked' ? '!' : '…'}
      </span>
      <div className="row__main" style={{ minWidth: 200, flex: 1 }}>
        <div className="row__title" style={{ opacity: done ? 0.75 : 1 }}>{item.label}</div>
        <div className="row__sub">
          {t(meta.i18nKey)}
          {!item.required ? ` · ${t('checklist.optionalTag')}` : ''}
        </div>

        {binding && (
          <div style={{ marginTop: 8, fontSize: 12.5 }}>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
              <span className="chip" title={binding.document_name}
                style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {binding.document_name}
              </span>
              <span className="chip">{t(detectedKey)}</span>
              {language.name && (
                <span className="chip" data-testid="language-chip">
                  {t('checklist.detectedLanguage', { language: language.name })}
                </span>
              )}
              {binding.match_verdict === 'match' && !done && (
                <span className="chip" data-testid="match-ok">{t('checklist.matchOk')}</span>
              )}
              {done && binding.submitted_at && (
                <span className="chip chip--ink" data-testid="submitted-chip">
                  {t('checklist.submitted')}
                  {binding.confirmed_by_applicant ? ` · ${t('checklist.applicantConfirmed')}` : ''}
                </span>
              )}
            </div>
            {/* Advisory only — never a blocking authority. */}
            {status === 'mismatch' && (
              <div style={{ color: '#9a3412', marginTop: 6 }} data-testid="mismatch-note">
                {t('checklist.advisoryNote', { detected: t(detectedKey), selected: item.label })}
              </div>
            )}
            {status === 'needs_review' && (
              <div style={{ color: '#9a3412', marginTop: 6 }} data-testid="review-note">
                {t('checklist.uncertainNote')}
              </div>
            )}
            {status === 'unreadable' && (
              <div style={{ color: '#9a3412', marginTop: 6 }}>
                {t('checklist.unreadableAdvisory')}
              </div>
            )}
            {pickType && (
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 8 }}>
                {MANUAL_DOC_TYPES.map((dt) => (
                  <button key={dt} type="button" className="chip" disabled={!!busy}
                    onClick={() => chooseType(dt)}>
                    {t(docTypeLabelKey(dt))}
                  </button>
                ))}
              </div>
            )}
            {canTranslate && !binding.translation_document_id && !askTranslate && (
              <div style={{ marginTop: 8 }}>
                <button className="btn btn--ghost btn--sm" disabled={!!busy}
                  onClick={() => setAskTranslate(true)} data-testid={'translate-' + item.id}>
                  {busy === 'translate' ? '…' : t('checklist.translateTo', { language: target.target_name })}
                </button>
              </div>
            )}
            {askTranslate && (
              <div className="card card--soft" style={{ padding: 10, marginTop: 8 }}
                data-testid="translate-consent">
                <div style={{ marginBottom: 8 }}>{t('checklist.translateConsent')}</div>
                <div style={{ display: 'flex', gap: 6 }}>
                  <button className="btn btn--sm" onClick={translate}>
                    {t('checklist.translateConfirm')}
                  </button>
                  <button className="btn btn--ghost btn--sm" onClick={() => setAskTranslate(false)}>
                    {t('checklist.translateCancel')}
                  </button>
                </div>
              </div>
            )}
            {busy === 'translate' && <div style={{ marginTop: 8 }}><Loading label={t('checklist.translating')} /></div>}
            {binding.translation_document_id && (
              <div style={{ marginTop: 8, display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}
                data-testid="translation-ready">
                <span className="chip">{t('checklist.machineTranslation')}</span>
                <button className="btn btn--ghost btn--sm"
                  onClick={() => { setPreviewDoc({ id: binding.translation_document_id,
                    name: t('checklist.machineTranslation') }); setPreview(true) }}>
                  {t('checklist.previewTranslation')}
                </button>
                {!done && (
                  <button className="btn btn--ghost btn--sm" disabled={!!busy}
                    onClick={attachTranslation}>
                    {t('checklist.useTranslation')}
                  </button>
                )}
              </div>
            )}
            {(canTranslate || binding.translation_document_id) && target.certified_note && (
              <div style={{ color: 'var(--muted)', marginTop: 6 }} data-testid="certified-note">
                {t('checklist.certifiedNote')}
              </div>
            )}
          </div>
        )}
      </div>

      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
        <input ref={inputRef} type="file" hidden accept=".pdf,.jpg,.jpeg,.png,.tiff"
               onChange={(e) => onFile(e.target.files && e.target.files[0])} />
        {busy === 'upload' && <Loading label={t('checklist.processing')} />}
        {!binding && busy !== 'upload' && (
          <button className="btn btn--sm" onClick={() => inputRef.current?.click()}
            data-testid={'upload-' + item.id}>
            {t('checklist.upload')}
          </button>
        )}
        {binding && (
          <>
            <button className="btn btn--ghost btn--sm" onClick={() => setPreview(true)}
              data-testid={'preview-' + item.id}>
              {t('checklist.preview')}
            </button>
            {status === 'ready_to_submit' && (
              <button className="btn btn--sm" disabled={!!busy} onClick={() => submit(false)}
                data-testid={'submit-' + item.id}>
                {busy === 'submit' ? '…' : t('checklist.submit')}
              </button>
            )}
            {status === 'needs_review' && (
              <>
                <button className="btn btn--sm" disabled={!!busy} onClick={() => submit(true)}
                  data-testid={'confirm-submit-' + item.id}>
                  {busy === 'submit' ? '…' : t('checklist.confirmSubmit')}
                </button>
                <button className="btn btn--ghost btn--sm" disabled={!!busy}
                  onClick={() => setPickType((v) => !v)}>
                  {t('checklist.chooseType')}
                </button>
              </>
            )}
            {(status === 'mismatch' || status === 'unreadable') && (
              <button className="btn btn--sm" disabled={!!busy} onClick={() => submit(true)}
                data-testid={'submit-anyway-' + item.id}>
                {busy === 'submit' ? '…' : t('checklist.submitAnyway')}
              </button>
            )}
            <button className="btn btn--ghost btn--sm" disabled={!!busy}
              onClick={() => inputRef.current?.click()} data-testid={'replace-' + item.id}>
              {t('checklist.replace')}
            </button>
            <button className="btn btn--ghost btn--sm" disabled={!!busy} onClick={withdraw}
              data-testid={'withdraw-' + item.id}>
              {done ? t('checklist.withdraw') : t('checklist.remove')}
            </button>
          </>
        )}
      </div>

      {preview && binding && (
        <DocPreview client={client} caseId={caseId}
          doc={previewDoc || { id: binding.document_id, name: binding.document_name }}
          onClose={() => { setPreview(false); setPreviewDoc(null) }} />
      )}
    </div>
  )
}

export default function Checklist({ t, client, caseId, checklist, counts, translation, onChanged }) {
  const items = Array.isArray(checklist) ? checklist : []
  if (items.length === 0) return null
  const c = counts || checklistCounts(items)
  return (
    <div className="card" style={{ padding: 18, marginBottom: 14 }} data-testid="route-checklist">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <div style={{ fontWeight: 700 }}>{t('checklist.title')}</div>
          <div style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 2 }}>{t('checklist.sub')}</div>
        </div>
        <span className="chip" data-testid="checklist-progress">
          {(c.required ?? 0) - (c.missing ?? 0)}/{c.required ?? 0}
        </span>
      </div>
      <div style={{ marginTop: 10 }}>
        {items.map((item) => {
          if (item.kind !== 'document') {
            const meta = checklistStatusMeta(item.status)
            return (
              <div key={item.id} className="row" style={{ alignItems: 'center', gap: 10 }}
                data-testid={'checklist-' + item.id} data-status={item.status}>
                <span className="sevbadge sevbadge--mid">◦</span>
                <div className="row__main" style={{ minWidth: 0 }}>
                  <div className="row__title">{item.label}</div>
                  <div className="row__sub">{t(meta.i18nKey)}</div>
                </div>
              </div>
            )
          }
          return <DocumentItemRow key={item.id} t={t} client={client} caseId={caseId}
                                  item={item} translation={translation}
                                  onChanged={onChanged} />
        })}
      </div>
    </div>
  )
}

// The case-level Continue at the bottom of the Documents page (Part 6):
// disabled with the exact reason while mandatory items remain; on click the
// backend re-validates, persists the completed stage, and the EXISTING case
// advances to its route's next step.
export function ContinuePanel({ t, client, caseId, journey, onAdvanced }) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const meta = continueButtonMeta(journey)
  if (!meta.visible) return null

  async function go() {
    setBusy(true)
    try {
      const res = await client.completeDocuments(caseId)
      toast(t('checklist.continueDone'))
      onAdvanced && onAdvanced(res)
    } catch (e) {
      toast(typeof e.detail === 'object' && e.detail?.message ? e.detail.message : e.message)
    }
    setBusy(false)
  }

  return (
    <div className="card" style={{ padding: 18, marginTop: 4 }} data-testid="continue-panel">
      {!meta.enabled && (
        <div style={{ fontSize: 12.5, color: 'var(--muted)', marginBottom: 8 }}
          data-testid="continue-reason">
          {t('checklist.continueBlocked', { n: meta.remaining })}
        </div>
      )}
      {meta.enabled && meta.completed && (
        <div style={{ fontSize: 12.5, color: 'var(--muted)', marginBottom: 8 }}>
          {t('checklist.continueAgainNote')}
        </div>
      )}
      <button className="btn" disabled={!meta.enabled || busy} onClick={go}
        data-testid="continue-case">
        {busy ? '…' : t(meta.labelKey)}
      </button>
    </div>
  )
}
