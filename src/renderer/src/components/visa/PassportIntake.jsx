// Passport upload at intake Step 1 (applicant journey, Part 1). The applicant
// chooses "Upload passport" or "Enter details manually"; an upload runs the
// backend OCR/MRZ pipeline (Document AI → flagged Kimi vision → deterministic
// local parser) and the extracted profile — every field with provenance,
// confidence, and a needs-confirmation flag — is shown for review. Only after
// the applicant confirms ("Use these details") do the values prefill the
// wizard. MRZ values are authoritative; conflicts are highlighted, never
// silently resolved. Nothing here can invent identity data.
import { useEffect, useRef, useState } from 'react'
import { Loading, ErrorNote } from '../ui.jsx'
import { profileRows, prefillWithEdits } from '../../lib/intake.js'

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

const SOURCE_KEY = { mrz: 'passport.source.mrz', ocr: 'passport.source.ocr', derived: 'passport.source.derived' }

export default function PassportIntake({ client, intakeId, t, confirmed, onApply, onManual }) {
  const inputRef = useRef(null)
  const [stage, setStage] = useState('loading') // loading | choice | busy | preview | rejected
  const [busyLabel, setBusyLabel] = useState('')
  const [result, setResult] = useState(null)    // accepted upload response
  const [rejection, setRejection] = useState(null)
  const [edits, setEdits] = useState({})        // profile key -> edited value
  const [error, setError] = useState(null)

  // Refresh-resume: a previously extracted profile reopens in the preview.
  useEffect(() => {
    let stop = false
    async function load() {
      try {
        const res = await client.getIntakePassport(intakeId)
        if (stop) return
        if (res && res.profile) { setResult(res); setStage('preview') }
        else setStage('choice')
      } catch { if (!stop) setStage('choice') }
    }
    if (intakeId) load()
    return () => { stop = true }
  }, [intakeId])

  async function onFiles(fileList) {
    const f = Array.from(fileList || [])[0]
    if (!f) return
    setError(null); setRejection(null)
    if (!ALLOWED[f.type]) { setError({ message: `${t('passport.unsupported')}: ${f.name}` }); return }
    if (f.size > MAX_BYTES) { setError({ message: `${t('passport.tooLarge')}: ${f.name}` }); return }
    setStage('busy'); setBusyLabel(t('passport.reading'))
    try {
      const b64 = await readAsBase64(f)
      setBusyLabel(t('passport.scanning'))
      const res = await client.uploadIntakePassport(intakeId, {
        name: f.name, mime: f.type, size_bytes: f.size, content_b64: b64
      })
      if (res.rejected) {
        setRejection({ name: f.name, message: res.message })
        setStage('rejected')
      } else {
        setResult(res); setEdits({}); setStage('preview')
      }
    } catch (e) {
      setError({ message: e.message }); setStage('choice')
    }
  }

  function apply() {
    const prefill = prefillWithEdits(result?.profile, Object.fromEntries(
      Object.entries(edits).map(([k, v]) => [k, v])))
    onApply(prefill)
  }

  if (stage === 'loading') return <Loading label={t('common.loading')} />

  if (stage === 'busy') {
    return (
      <div className="card card--soft" style={{ padding: 20, marginBottom: 14 }} data-testid="passport-busy">
        <Loading label={busyLabel} />
      </div>
    )
  }

  if (stage === 'rejected' && rejection) {
    return (
      <div className="card" style={{ padding: 16, marginBottom: 14, border: '1px solid #f59e0b', background: '#fff7ed' }}
        data-testid="passport-rejected">
        <strong style={{ color: '#9a3412' }}>{t('passport.rejected')}</strong>
        <div style={{ fontSize: 13, color: '#9a3412', margin: '4px 0 10px' }}>{rejection.message}</div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button className="btn btn--sm" onClick={() => inputRef.current?.click()}>{t('passport.retryCta')}</button>
          <button className="btn btn--sm btn--ghost" onClick={onManual}>{t('start.entry.manual')}</button>
        </div>
        <FileInput inputRef={inputRef} onFiles={onFiles} />
      </div>
    )
  }

  if (stage === 'preview' && result) {
    const rows = profileRows(result.profile)
    const needsAny = rows.some((r) => r.needsConfirm)
    return (
      <div className="card" style={{ padding: 18, marginBottom: 14 }} data-testid="passport-preview">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
          <div>
            <div style={{ fontWeight: 700 }}>{t('passport.previewTitle')}</div>
            <div style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 2 }}>{t('passport.previewSub')}</div>
          </div>
          <span className={'chip' + (result.mrz_valid ? ' chip--ink' : '')}>
            {result.mrz_valid ? t('passport.mrzValid') : t('passport.mrzInvalid')}
          </span>
        </div>
        <div style={{ marginTop: 12 }}>
          {rows.map((r) => (
            <div key={r.key} className="row" style={{ alignItems: 'center', gap: 10, padding: '6px 8px',
              borderRadius: 8, background: r.needsConfirm ? 'rgba(200,120,0,0.08)' : undefined }}>
              <span className={'sevbadge ' + (r.level === 'ok' ? 'sevbadge--ok' : r.level === 'bad' ? 'sevbadge--bad' : 'sevbadge--mid')}>
                {r.level === 'ok' ? '✓' : r.needsConfirm ? '!' : '~'}
              </span>
              <div className="row__main" style={{ minWidth: 0 }}>
                <div className="row__title">{t(r.labelKey)}</div>
                <div className="row__sub">
                  {t(SOURCE_KEY[r.source])} · {Math.round(r.confidence * 100)}%
                  {r.needsConfirm && <strong style={{ color: '#9a3412' }}> · {t('passport.needsConfirm')}</strong>}
                  {r.note && <span> · {r.note}</span>}
                </div>
              </div>
              {r.key === '_age'
                ? <div style={{ fontSize: 14 }}>{edits.birth_date != null
                    ? (prefillWithEdits(result.profile, edits).age ?? '—') : r.value}</div>
                : <input className="input" style={{ maxWidth: 220 }}
                    value={edits[r.key] ?? r.display}
                    onChange={(e) => setEdits((m) => ({ ...m, [r.key]: e.target.value }))} />}
            </div>
          ))}
        </div>
        {error && <ErrorNote error={error} />}
        <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
          <button className="btn" onClick={apply} data-testid="passport-apply">
            {confirmed ? t('passport.reapply') : t('passport.apply')}
          </button>
          <button className="btn btn--sm btn--ghost" onClick={() => inputRef.current?.click()}>
            {t('passport.reupload')}
          </button>
          <button className="btn btn--sm btn--ghost" onClick={onManual}>{t('start.entry.manual')}</button>
        </div>
        {needsAny && (
          <div style={{ fontSize: 12.5, color: '#9a3412', marginTop: 8 }}>{t('passport.confirmHint')}</div>
        )}
        <FileInput inputRef={inputRef} onFiles={onFiles} />
      </div>
    )
  }

  // choice: upload vs manual entry
  return (
    <div style={{ marginBottom: 14 }} data-testid="passport-choice">
      <div style={{ fontSize: 13.5, fontWeight: 600, marginBottom: 8 }}>{t('start.entry.title')}</div>
      <div className="grid grid-2" style={{ gap: 10 }}>
        <div className="card card--soft" role="button" tabIndex={0} data-testid="passport-upload-option"
          style={{ padding: 18, textAlign: 'center', border: '1.5px dashed var(--line-strong)', cursor: 'pointer' }}
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => { e.preventDefault(); onFiles(e.dataTransfer.files) }}>
          <div style={{ fontWeight: 700 }}>{t('start.entry.upload')}</div>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 4 }}>{t('start.entry.uploadSub')}</div>
        </div>
        <div className="card card--soft" role="button" tabIndex={0} data-testid="passport-manual-option"
          style={{ padding: 18, textAlign: 'center', cursor: 'pointer' }}
          onClick={onManual}>
          <div style={{ fontWeight: 700 }}>{t('start.entry.manual')}</div>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 4 }}>{t('start.entry.manualSub')}</div>
        </div>
      </div>
      {error && <ErrorNote error={error} />}
      <FileInput inputRef={inputRef} onFiles={onFiles} />
    </div>
  )
}

function FileInput({ inputRef, onFiles }) {
  return (
    <input ref={inputRef} type="file" hidden accept=".pdf,.jpg,.jpeg,.png,.tiff"
      onChange={(e) => { onFiles(e.target.files); e.target.value = '' }} />
  )
}
