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
  const [editingKey, setEditingKey] = useState(null)  // per-field inline edit
  const [error, setError] = useState(null)
  const autoApplied = useRef(false)

  // The extracted details apply THEMSELVES — no "Use these details" ceremony.
  // The applicant gets an Edit button instead; edits re-apply on save.
  // Keyed by the document id so a REPLACEMENT upload also applies (the old
  // guard latched after the first profile and silently kept the first
  // passport's values while displaying the new one's).
  useEffect(() => {
    if (stage !== 'preview' || !result?.profile) return
    const key = result.document_id || result.profile_id ||
      JSON.stringify(result.profile.prefill || {})
    if (autoApplied.current === key) return
    autoApplied.current = key
    setEdits({})
    onApply(prefillWithEdits(result.profile, {}))
  }, [stage, result])  // eslint-disable-line react-hooks/exhaustive-deps

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
    const commit = (key, value) => {
      const next = { ...edits, [key]: value }
      setEdits(next)
      setEditingKey(null)
      onApply(prefillWithEdits(result.profile, next))
    }
    return (
      <div className="card fadeup" style={{ padding: 22, marginBottom: 16 }} data-testid="passport-preview">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span className="uptick" style={{ width: 34, height: 34 }}>
              <svg viewBox="0 0 24 24" style={{ width: 18, height: 18 }}><path d="M5 12.5l4.6 4.6L19 7.6" /></svg>
            </span>
            <div style={{ fontWeight: 700 }}>{t('passport.previewTitle')}</div>
          </div>
          <button className="btn btn--sm btn--ghost" onClick={() => inputRef.current?.click()}>
            {t('passport.reupload')}
          </button>
        </div>
        {/* Every field carries its OWN pencil — the applicant edits exactly
            the value that looks wrong, in place, instead of flipping the whole
            card into a form. Age stays derived from the date of birth. */}
        <div className="ppf-grid">
          {rows.map((r) => {
            const editable = r.key !== '_age'
            const shown = r.key === '_age'
              ? (edits.birth_date != null
                  ? (prefillWithEdits(result.profile, edits).age ?? '—') : r.value)
              : (edits[r.key] ?? r.display)
            const isEditing = editingKey === r.key
            return (
              <div key={r.key} className="ppf" data-testid={'ppf-' + r.key}>
                <div className="ppf__label">{t(r.labelKey)}</div>
                {isEditing ? (
                  <input className="input" autoFocus defaultValue={String(shown)}
                    data-testid={'ppf-input-' + r.key}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') commit(r.key, e.currentTarget.value)
                      if (e.key === 'Escape') setEditingKey(null)
                    }}
                    onBlur={(e) => commit(r.key, e.currentTarget.value)} />
                ) : (
                  <div className="ppf__row">
                    <span className="ppf__value" title={String(shown)}>{shown}</span>
                    {editable && (
                      <button type="button" className="ppf__pencil"
                        data-testid={'ppf-edit-' + r.key}
                        aria-label={`${t('passport.edit')} — ${t(r.labelKey)}`}
                        title={t('passport.edit')}
                        onClick={() => setEditingKey(r.key)}>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
                          strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M12 20h9" />
                          <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z" />
                        </svg>
                      </button>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
        {error && <ErrorNote error={error} />}
        <FileInput inputRef={inputRef} onFiles={onFiles} />
      </div>
    )
  }

  // choice: upload vs manual entry — two illustrated cards. The passport
  // booklet wears the Trip.com livery; the manual card is a form-and-pen.
  return (
    <div style={{ marginBottom: 18 }} data-testid="passport-choice">
      <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 14 }}>{t('start.entry.title')}</div>
      <div className="grid grid-2 choicegrid">
        <div className="choicecard" role="button" tabIndex={0} data-testid="passport-upload-option"
          onClick={() => inputRef.current?.click()}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') inputRef.current?.click() }}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => { e.preventDefault(); onFiles(e.dataTransfer.files) }}>
          <PassportArt />
          <div className="choicecard__name">{t('start.entry.upload')}</div>
        </div>
        <div className="choicecard" role="button" tabIndex={0} data-testid="passport-manual-option"
          onClick={onManual}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onManual() }}>
          <ManualArt />
          <div className="choicecard__name">{t('start.entry.manual')}</div>
        </div>
      </div>
      {error && <ErrorNote error={error} />}
      <FileInput inputRef={inputRef} onFiles={onFiles} />
    </div>
  )
}

// A Trip.com-liveried passport booklet.
function PassportArt() {
  return (
    <svg className="choicecard__art" viewBox="0 0 150 108" fill="none" aria-hidden="true">
      {/* booklet */}
      <rect x="37" y="8" width="76" height="94" rx="8" fill="#0f294d" />
      <rect x="37" y="8" width="76" height="94" rx="8" stroke="#0a1f3c" strokeWidth="2" />
      <rect x="41" y="8" width="3" height="94" fill="rgba(255,255,255,0.12)" />
      {/* globe emblem */}
      <circle cx="75" cy="42" r="15" stroke="#fcaf17" strokeWidth="2.2" />
      <ellipse cx="75" cy="42" rx="6.5" ry="15" stroke="#fcaf17" strokeWidth="1.6" />
      <path d="M60 42h30M62.5 34.5h25M62.5 49.5h25" stroke="#fcaf17" strokeWidth="1.6" />
      {/* livery */}
      <text x="75" y="72" textAnchor="middle" fontFamily="Inter, system-ui, sans-serif"
        fontSize="12.5" fontWeight="800" fill="#ffffff">Trip<tspan fill="#fcaf17">.</tspan>com</text>
      <text x="75" y="88" textAnchor="middle" fontFamily="Inter, system-ui, sans-serif"
        fontSize="7" letterSpacing="2.6" fill="rgba(255,255,255,0.75)">PASSPORT</text>
    </svg>
  )
}

// A form-and-pen card for typing the details yourself.
function ManualArt() {
  return (
    <svg className="choicecard__art" viewBox="0 0 150 108" fill="none" aria-hidden="true">
      {/* sheet */}
      <rect x="41" y="10" width="68" height="88" rx="7" fill="#ffffff" stroke="#c9d7ec" strokeWidth="2" />
      <rect x="50" y="22" width="34" height="7" rx="3.5" fill="#287dfa" opacity="0.85" />
      <rect x="50" y="38" width="50" height="5" rx="2.5" fill="#dbe6f5" />
      <rect x="50" y="50" width="50" height="5" rx="2.5" fill="#dbe6f5" />
      <rect x="50" y="62" width="36" height="5" rx="2.5" fill="#dbe6f5" />
      {/* checkbox tick */}
      <rect x="50" y="74" width="10" height="10" rx="2.5" stroke="#287dfa" strokeWidth="2" />
      <path d="M52.5 79l2.6 2.6 4.6-5" stroke="#287dfa" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      {/* pen */}
      <g transform="rotate(38 104 66)">
        <rect x="98" y="40" width="11" height="42" rx="3" fill="#287dfa" />
        <rect x="98" y="40" width="11" height="9" rx="3" fill="#1c66d9" />
        <path d="M98 82l5.5 12 5.5-12z" fill="#fcaf17" />
      </g>
    </svg>
  )
}

function FileInput({ inputRef, onFiles }) {
  return (
    <input ref={inputRef} type="file" hidden accept=".pdf,.jpg,.jpeg,.png,.tiff"
      onChange={(e) => { onFiles(e.target.files); e.target.value = '' }} />
  )
}
