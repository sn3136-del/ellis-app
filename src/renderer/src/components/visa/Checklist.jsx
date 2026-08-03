// Route-specific document checklist (applicant journey). Items come from the
// backend; per-item status is computed server-side from the case's documents
// AND the applicant's explicit submissions. Each Needed requirement has its
// own Upload action that binds the file to that exact requirement; a primary
// Submit fulfils it only after the applicant reviews the assignment. Nothing
// is fulfilled by merely selecting a file. Applicant-facing only — no
// operator terminology, no internal identifiers.
import { useRef, useState } from 'react'
import { useToast, Loading, UploadTick } from '../ui.jsx'
import {
  checklistStatusMeta, checklistCounts, continueButtonMeta, docTypeLabelKey,
  formatDateUS, MANUAL_DOC_TYPES
} from '../../lib/intake.js'
import DocPreview from './DocPreview.jsx'

export const ALLOWED = { 'application/pdf': 1, 'image/jpeg': 1, 'image/png': 1, 'image/tiff': 1 }
export const MAX_BYTES = 10 * 1024 * 1024

export function readAsBase64(file) {
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

  // One press translates. The applicant already chose to translate by pressing
  // it; a second confirmation explaining that extracted TEXT (never the image
  // or PDF bytes) goes to the translator was describing an internal boundary
  // to someone who just wanted their document readable. That boundary is still
  // enforced — in the backend, where it belongs.
  async function translate() {
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
            {canTranslate && !binding.translation_document_id && (
              <div style={{ marginTop: 8 }}>
                <button className="btn btn--ghost btn--sm" disabled={!!busy}
                  onClick={translate} data-testid={'translate-' + item.id}>
                  {busy === 'translate' ? '…' : t('checklist.translateTo', { language: target.target_name })}
                </button>
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
      // completeDocuments only RECORDS the stage. Routes with an authorize
      // step get their start from the Authorize card; entry preparation and
      // renewal have no such card, so this press was their only path to a
      // run — and without this it recorded a row and stopped, leaving
      // "Entry preparation complete" on screen with the arrival card never
      // filed (2026-08-03). Start is idempotent and answers honestly when
      // the destination's filing window has not opened yet.
      let started = null
      if (meta.startsRun) {
        try {
          started = await client.start(caseId)
        } catch (e) {
          toast(typeof e.detail === 'object' && e.detail?.message
            ? e.detail.message : e.message)
        }
      }
      const filing = started && started.entry_filing
      toast(filing && filing.status === 'waiting' && filing.opens_on
        ? t('checklist.filingScheduled',
            { name: filing.name, date: formatDateUS(filing.opens_on) })
        : t('checklist.continueDone'))
      onAdvanced && onAdvanced(started || res)
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

// ---------------------------------------------------------------------------
// Illustrated document cards (2026-07-27) — the applicant-facing upload
// surface: one picture-card per requirement, click (or drop) to upload, a
// green-tick pop + "uploaded" when it lands. No OCR percentages, no binding
// internals. The explicit-submit doctrine is preserved: when a document
// needs the applicant's confirmation, the card offers exactly that choice —
// nothing is fulfilled by merely selecting a file.
const DOC_ART = [
  [/passport.*photo|photo|portrait|picture/i, '🤳'],
  [/passport|biodata/i, '🛂'],
  [/ticket|flight|onward|return|itinerar/i, '✈️'],
  [/hotel|accommodation|stay|booking/i, '🏨'],
  [/insurance/i, '🛡️'],
  [/fund|bank|statement|financ/i, '💳'],
  [/invitation|letter/i, '✉️'],
  [/employment|work|job/i, '💼'],
  [/vaccin|health|medical/i, '💉'],
]

function artFor(item) {
  const hay = `${item.id || ''} ${item.label || ''} ${item.doc_type || ''}`
  for (const [re, emoji] of DOC_ART) if (re.test(hay)) return emoji
  return '📄'
}

// Why the translate control is off, in the applicant's words. A dimmed button
// with no explanation reads like a bug; this says which of the real reasons
// applies (already in the right language, nothing readable to translate, or
// the document IS a translation).
function translateOffReason(t, binding, language, target) {
  if (!binding) return ''
  if (binding.detected_type === 'translation') return t('doccard.whyIsTranslation')
  if (!binding.has_text) return t('doccard.whyNoText')
  if (language.code && target.target && language.code === target.target) {
    return t('doccard.whySameLanguage')
  }
  return t('doccard.whyNotNeeded')
}

function DocCard({ t, client, caseId, item, translation, onChanged }) {
  const toast = useToast()
  const inputRef = useRef(null)
  const [busy, setBusy] = useState('')
  const [justUploaded, setJustUploaded] = useState(false)
  const [preview, setPreview] = useState(false)
  const [previewDoc, setPreviewDoc] = useState(null)   // {id, name} being previewed
  const binding = item.binding || null
  const status = item.status
  const done = status === 'submitted'
  const uploaded = !!binding
  const language = (binding && binding.language) || {}
  const target = translation || {}
  // Consent-gated machine translation stays available: the extracted TEXT
  // (never the file bytes) goes to Kimi K3, only on an explicit click.
  const canTranslate = !!binding && binding.has_text && language.code &&
    target.target && language.code !== target.target &&
    binding.detected_type !== 'translation' && !binding.translation_document_id

  async function translate() {
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
      else { setJustUploaded(true); setTimeout(() => setJustUploaded(false), 2400) }
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

  // Status honesty: a green tick means SUBMITTED. An uploaded-but-unconfirmed
  // document shows its real state, and mismatch/unreadable documents keep the
  // portal's own words plus the explicit "use it anyway" choice — the
  // applicant's decision always wins, but never silently.
  const meta = checklistStatusMeta(status)
  const blocked = status === 'mismatch' || status === 'unreadable'
  const awaiting = uploaded && !done && !blocked &&
    (status === 'ready_to_submit' || status === 'needs_review')
  const clickable = !done && busy !== 'upload'
  const detected = binding && binding.detected_type
    ? t(docTypeLabelKey(binding.detected_type)) : ''
  return (
    <div className={'doccard' + (done ? ' doccard--done' : '')}
      data-testid={'doccard-' + item.id} data-status={status}
      role={clickable ? 'button' : undefined} tabIndex={clickable ? 0 : undefined}
      onClick={() => clickable && inputRef.current?.click()}
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => { e.preventDefault(); if (clickable) onFile(e.dataTransfer.files?.[0]) }}>
      <div className="doccard__art">
        {busy === 'upload'
          ? <span className="spinner spinner--ink" />
          : done ? <UploadTick />
          : blocked ? <span aria-hidden="true">⚠️</span>
          : <span aria-hidden="true">{artFor(item)}</span>}
      </div>
      <div className="doccard__name">{item.label}</div>
      {!uploaded && !done && (
        <div className="doccard__sub">{t('doccard.tapToUpload')}</div>
      )}
      {done && (
        <div data-testid={'uploaded-' + item.id}>
          <div className="doccard__file" title={binding?.document_name || ''}>
            {binding?.document_name || item.label}
          </div>
          <span className="doccard__pill">✓ {t('doccard.uploaded')}</span>
        </div>
      )}
      {uploaded && !done && (
        <div className="doccard__sub" data-testid={'status-' + item.id}
          style={{ color: blocked ? '#9a3412' : 'var(--muted)', fontWeight: blocked ? 700 : 500 }}>
          {binding?.document_name ? binding.document_name + ' · ' : ''}{t(meta.i18nKey)}
        </div>
      )}
      {/* What the confirmation is ABOUT: the portal's detected type when it
          differs from the requirement. Hiding it would make the confirm
          button a blind click. */}
      {uploaded && !done && detected && (status === 'needs_review' || blocked) && (
        <div className="doccard__sub" style={{ marginTop: 4 }}>
          {t('doccard.detectedAs', { type: detected })}
        </div>
      )}
      {(awaiting || blocked) && (
        <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 6 }}
          onClick={(e) => e.stopPropagation()}>
          <button className="btn btn--sm" disabled={busy === 'submit'}
            data-testid={'submit-' + item.id}
            onClick={() => submit(status !== 'ready_to_submit')}>
            {busy === 'submit' ? '…'
              : blocked ? t('doccard.useAnyway') : t('doccard.confirm')}
          </button>
          <button className="btn btn--sm btn--ghost" disabled={busy === 'upload'}
            data-testid={'replace-' + item.id}
            onClick={() => inputRef.current?.click()}>
            {t('doccard.replace')}
          </button>
        </div>
      )}
      {done && (
        <div style={{ marginTop: 8 }} onClick={(e) => e.stopPropagation()}>
          <button className="btn btn--sm btn--ghost" disabled={busy === 'withdraw'}
            data-testid={'withdraw-' + item.id}
            onClick={async () => {
              setBusy('withdraw')
              try {
                await client.withdrawChecklistDoc(caseId, item.id)
                onChanged && onChanged()
              } catch (e) {
                toast(typeof e.detail === 'object' && e.detail?.message ? e.detail.message : e.message)
              }
              setBusy('')
            }}>
            {busy === 'withdraw' ? '…' : t('doccard.change')}
          </button>
        </div>
      )}
      {uploaded && (
        <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column',
          gap: 6, alignItems: 'center' }} onClick={(e) => e.stopPropagation()}>
          <button className="doccard__link" data-testid={'view-' + item.id}
            onClick={() => setPreview(true)}>{t('doccard.view')}</button>
          {/* Translation sits UNDER View and is always visible, so the
              applicant never wonders whether it exists. When the document is
              already in the language the post accepts, the control is dimmed
              and says so outright rather than disappearing. */}
          {binding.translation_document_id ? (
            <button className="doccard__link"
              data-testid={'translated-' + item.id}
              onClick={() => { setPreviewDoc({ id: binding.translation_document_id,
                name: t('checklist.machineTranslation') }); setPreview(true) }}>
              {t('doccard.viewTranslation')}
            </button>
          ) : canTranslate ? (
            <button className="doccard__link" disabled={busy === 'translate'}
              data-testid={'translate-' + item.id}
              onClick={translate}>
              {busy === 'translate' ? t('doccard.translating') : t('doccard.translate')}
            </button>
          ) : (
            <span className="doccard__link doccard__link--off"
              data-testid={'translate-off-' + item.id}
              title={translateOffReason(t, binding, language, target)}>
              {t('doccard.translateNotNeeded')}
            </span>
          )}
        </div>
      )}
      {preview && binding && (
        <div onClick={(e) => e.stopPropagation()}>
          {/* previewDoc set means the TRANSLATION is being shown; its viewer
              carries the Download button, which is how the applicant takes
              the translation to the post. */}
          <DocPreview client={client} caseId={caseId}
            doc={previewDoc || { id: binding.document_id, name: binding.document_name }}
            onClose={() => { setPreview(false); setPreviewDoc(null) }} />
        </div>
      )}
      <input ref={inputRef} type="file" hidden accept=".pdf,.jpg,.jpeg,.png,.tiff"
        onChange={(e) => { onFile(e.target.files?.[0]); }} />
    </div>
  )
}

export function DocCards({ t, client, caseId, checklist, translation, onChanged }) {
  const items = (checklist || []).filter((i) => i.kind === 'document')
  const infos = (checklist || []).filter((i) => i.kind !== 'document')
  if (!items.length && !infos.length) return null
  return (
    <div className="card fadeup" style={{ padding: 24, marginBottom: 16 }} data-testid="doc-cards">
      <div style={{ fontWeight: 800, fontSize: 17, marginBottom: 4 }}>{t('checklist.title')}</div>
      <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 18 }}>{t('doccard.sub')}</div>
      <div className="docgrid">
        {items.map((item) => (
          <DocCard key={item.id} t={t} client={client} caseId={caseId}
            item={item} translation={translation} onChanged={onChanged} />
        ))}
      </div>
      {infos.length > 0 && (
        <div style={{ marginTop: 16, fontSize: 12.5, color: 'var(--muted)' }}>
          {infos.map((i) => <div key={i.id} style={{ margin: '3px 0' }}>◦ {i.label}</div>)}
        </div>
      )}
    </div>
  )
}
