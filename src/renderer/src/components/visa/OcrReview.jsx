// Document upload + OCR review (Phase 2). The applicant selects/drops files;
// each is validated (MIME, size), uploaded to the backend, and processed by
// Google Document AI (backend-only). The applicant then reviews every extracted
// field — with confidence, provenance, MRZ checksum status, and any MRZ /
// visible-zone disagreement highlighted — and accepts, edits, rejects, or
// replaces each value before approving. Only applicant-approved values become
// canonical, and a material change invalidates any prior signature.
//
// Raw provider responses are never shown — only classified, structured results.
import { useEffect, useRef, useState } from 'react'
import { useToast, Loading, ErrorNote, Empty } from '../ui.jsx'
import { fieldRows, documentReady } from '../../lib/visaSession.js'

const ALLOWED = { 'application/pdf': 'pdf', 'image/jpeg': 'jpg', 'image/png': 'png', 'image/tiff': 'tiff' }
const MAX_BYTES = 10 * 1024 * 1024

function readAsBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(String(r.result).split(',')[1] || '')
    r.onerror = () => reject(new Error('could not read file'))
    r.readAsDataURL(file)
  })
}

function ConfBadge({ level }) {
  const cls = level === 'ok' ? 'sevbadge--ok' : level === 'bad' ? 'sevbadge--bad' : 'sevbadge--mid'
  const sym = level === 'ok' ? '✓' : level === 'bad' ? '!' : '~'
  return <span className={'sevbadge ' + cls} title={'confidence: ' + level}>{sym}</span>
}

// One reviewable field row: accept (default) / edit / reject.
function FieldRow({ row, edit, onEdit }) {
  const state = edit?.state || (row.needsAttention ? 'review' : 'accepted')
  return (
    <div className="row" style={{ alignItems: 'center', gap: 10,
      background: row.conflict ? 'rgba(200,0,0,0.05)' : undefined, borderRadius: 8, padding: '6px 8px' }}>
      <ConfBadge level={row.level} />
      <div className="row__main" style={{ minWidth: 0 }}>
        <div className="row__title" style={{ textTransform: 'capitalize' }}>{row.key.replace(/_/g, ' ')}</div>
        <div className="row__sub">
          {row.source}{row.confidence != null && ` · ${Math.round(row.confidence * 100)}%`}
          {row.conflict && <strong style={{ color: 'var(--crit)' }}> · MRZ / visible-zone disagree</strong>}
        </div>
      </div>
      {state === 'edit'
        ? <input className="input" style={{ maxWidth: 260 }} defaultValue={row.value}
                 onChange={(e) => onEdit({ state: 'edit', value: e.target.value })} autoFocus />
        : <div style={{ fontFamily: 'var(--font)', fontSize: 14, maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis',
            textDecoration: state === 'rejected' ? 'line-through' : undefined, opacity: state === 'rejected' ? 0.5 : 1 }}>
            {(edit?.value ?? row.value) || <span style={{ color: 'var(--muted-2)' }}>— missing —</span>}
          </div>}
      <div style={{ display: 'flex', gap: 4 }}>
        <button className={'btn btn--sm' + (state === 'edit' ? '' : ' btn--ghost')}
                onClick={() => onEdit(state === 'edit' ? { state: 'accepted', value: edit?.value ?? row.value } : { state: 'edit', value: row.value })}>
          {state === 'edit' ? 'Save' : 'Edit'}
        </button>
        <button className="btn btn--sm btn--ghost"
                onClick={() => onEdit({ state: state === 'rejected' ? 'accepted' : 'rejected', value: row.value })}>
          {state === 'rejected' ? 'Undo' : 'Reject'}
        </button>
      </div>
    </div>
  )
}

function DocumentCard({ doc, conflicts, client, caseId, onApproved }) {
  const toast = useToast()
  const [edits, setEdits] = useState({})     // key -> {state, value}
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const rows = fieldRows(doc.extracted_fields, conflicts)
  const rejectedMissing = rows.some((r) => (edits[r.key]?.state === 'rejected'))

  async function approve() {
    setBusy(true); setError(null)
    // Only edited/replaced values are sent; rejected fields are blanked so the
    // applicant is forced to supply a value rather than approve a bad OCR read.
    const payload = []
    for (const r of rows) {
      const e = edits[r.key]
      if (!e) continue
      if (e.state === 'edit' || e.state === 'accepted') {
        if (e.value !== undefined && e.value !== r.value) payload.push({ key: r.key, value: e.value })
      } else if (e.state === 'rejected') {
        payload.push({ key: r.key, value: '' })
      }
    }
    try {
      const res = await client.approveDocument(caseId, doc.id, payload)
      toast(res.signatures_invalidated ? 'Approved — prior signature invalidated' : 'Document approved')
      onApproved(res)
    } catch (e) { setError({ message: e.message }); setBusy(false) }
  }

  return (
    <div className="card" style={{ padding: 18, marginBottom: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <div>
          <div style={{ fontWeight: 700 }}>{doc.name}</div>
          <div style={{ fontSize: 12, color: 'var(--muted)' }}>
            <span className="chip">{doc.doc_type || 'unclassified'}</span>
            {doc.approved && <span className="chip chip--ink" style={{ marginLeft: 6 }}>approved</span>}
          </div>
        </div>
      </div>
      {(doc.quality_warnings || []).length > 0 && (
        <div className="card card--soft" style={{ padding: 10, marginBottom: 10, fontSize: 12 }}>
          <strong>Quality notes:</strong> {doc.quality_warnings.join(', ')}
        </div>
      )}
      {rows.length === 0
        ? <div className="chip">No fields extracted</div>
        : rows.map((r) => (
            <FieldRow key={r.key} row={r} edit={edits[r.key]}
                      onEdit={(e) => setEdits((m) => ({ ...m, [r.key]: e }))} />
          ))}
      {error && <ErrorNote error={error} />}
      {!doc.approved && (
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 10 }}>
          <button className="btn" disabled={busy} onClick={approve}>
            {busy ? 'Saving…' : 'Approve canonical data'}
          </button>
        </div>
      )}
    </div>
  )
}

// Missing information (Phase 7): required applicant fields the destination
// adapter needs but OCR didn't capture. Saving merges them into the canonical
// answers and invalidates any prior signature.
function MissingInfo({ client, caseId, missing, answers, onSaved }) {
  const toast = useToast()
  const [vals, setVals] = useState(() => Object.fromEntries(missing.map((k) => [k, answers[k] || ''])))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  async function save() {
    setBusy(true); setError(null)
    try {
      const res = await client.updateAnswers(caseId, vals)
      toast(res.signatures_invalidated ? 'Saved — prior signature invalidated' : 'Information saved')
      onSaved && onSaved(res)
    } catch (e) { setError({ message: e.message }); setBusy(false) }
  }
  return (
    <div className="card" style={{ padding: 16, marginTop: 12, borderColor: 'var(--line-strong)' }}>
      <div className="eyebrow">Missing information required for this destination</div>
      <div style={{ fontSize: 12, color: 'var(--muted)', margin: '4px 0 10px' }}>
        Ellis couldn't read these from your documents. Please provide them.
      </div>
      <div className="grid grid-2" style={{ gap: 12 }}>
        {missing.map((k) => (
          <div key={k} className="field">
            <label style={{ textTransform: 'capitalize' }}>{k.replace(/_/g, ' ')}</label>
            <input className="input" value={vals[k]} onChange={(e) => setVals((m) => ({ ...m, [k]: e.target.value }))} />
          </div>
        ))}
      </div>
      {error && <ErrorNote error={error} />}
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 10 }}>
        <button className="btn" disabled={busy} onClick={save}>{busy ? 'Saving…' : 'Save information'}</button>
      </div>
    </div>
  )
}

export default function OcrReview({ client, caseId, onChanged }) {
  const toast = useToast()
  const inputRef = useRef(null)
  const [review, setReview] = useState(null)
  const [error, setError] = useState(null)
  const [uploading, setUploading] = useState(null) // {name, status}

  async function load() {
    try { setReview(await client.review(caseId)) }
    catch (e) { setError({ message: e.message }) }
  }
  useEffect(() => { load() }, [caseId])

  async function onFiles(fileList) {
    const files = Array.from(fileList || [])
    for (const f of files) {
      if (!ALLOWED[f.type]) { toast(`Unsupported type: ${f.name}`); continue }
      if (f.size > MAX_BYTES) { toast(`Too large (max 10MB): ${f.name}`); continue }
      setUploading({ name: f.name, status: 'Reading file…' })
      try {
        const b64 = await readAsBase64(f)
        setUploading({ name: f.name, status: 'Uploading + scanning…' })
        await client.addDocument(caseId, { name: f.name, mime: f.type, size_bytes: f.size, content_b64: b64 })
        setUploading({ name: f.name, status: 'Processing with Document AI…' })
        await load()
        toast(`${f.name} processed`)
      } catch (e) { setError({ message: e.message }) }
    }
    setUploading(null)
    onChanged && onChanged()
  }

  return (
    <div>
      <div
        className="card card--soft"
        style={{ padding: 24, textAlign: 'center', border: '1.5px dashed var(--line-strong)', cursor: 'pointer' }}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => { e.preventDefault(); onFiles(e.dataTransfer.files) }}
      >
        <div style={{ fontWeight: 600 }}>Drop your passport & supporting documents</div>
        <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 4 }}>
          PDF, JPG, PNG, or TIFF · up to 10&nbsp;MB · processed by Google Document AI on Ellis's secure backend
        </div>
        <input ref={inputRef} type="file" hidden multiple accept=".pdf,.jpg,.jpeg,.png,.tiff"
               onChange={(e) => onFiles(e.target.files)} />
      </div>

      {uploading && (
        <div className="card" style={{ padding: 14, marginTop: 12, display: 'flex', gap: 12, alignItems: 'center' }}>
          <Loading label={uploading.status} />
          <div style={{ fontSize: 13 }}>{uploading.name}</div>
        </div>
      )}

      {error && <ErrorNote error={error} />}

      {review && review.missing_fields && review.missing_fields.length > 0 && (
        <MissingInfo client={client} caseId={caseId}
                     missing={review.missing_fields} answers={review.answers || {}}
                     onSaved={() => { load(); onChanged && onChanged() }} />
      )}

      {review?.conflicts?.length > 0 && (
        <div className="card" style={{ padding: 12, marginTop: 12, borderColor: 'var(--crit)' }}>
          <strong style={{ color: 'var(--crit)' }}>Cross-document conflicts</strong>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 4 }}>
            {review.conflicts.map((c, i) => (
              <div key={i}>{c.key || (c.keys || []).join(', ')}: {c.values ? c.values.join(' vs ') : 'disagreement'}</div>
            ))}
          </div>
        </div>
      )}

      <div style={{ marginTop: 16 }}>
        {!review ? <Loading label="Loading documents" />
          : review.documents.length === 0
            ? <Empty title="No documents yet" sub="Add your passport to begin — Ellis will read and check it." />
            : review.documents.map((d) => (
                <DocumentCard key={d.id} doc={d} conflicts={review.conflicts} client={client}
                              caseId={caseId} onApproved={() => { load(); onChanged && onChanged() }} />
              ))}
      </div>
    </div>
  )
}

export { documentReady }
