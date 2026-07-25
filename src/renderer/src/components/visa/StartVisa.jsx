// "Start your visa" — the applicant route-intake wizard.
//
// Loads the backend requirements snapshot (info + registries) once, walks the
// applicant through 3 steps + review, autosaves a draft intake on every change
// (PUT /intake/{id}, debounced), then resolves the route (POST resolve) and
// presents ONE honest readiness status with the verification checks behind it.
// The UI never invents requirements: a NOT_READY route shows only that the
// case is saved and queued for administrator review.
import { useEffect, useMemo, useRef, useState } from 'react'
import { useLocale } from '../../lib/locale.jsx'
import { useToast, Loading, ErrorNote } from '../ui.jsx'
import { SUPPORTED, LANGUAGE_NAMES } from '../../lib/i18n.js'
import {
  conditionField, missingRequired, readinessMeta, checksSummary,
  validEmail, datesOrdered, RESIDENCE_STATUS_OPTIONS,
  researchStageMeta, researchTerminal, researchStatusMeta, RESEARCH_STEP_KEYS,
  guidanceDispositionMeta, guidanceStepMeta, guidanceIsUsable, verificationMeta,
  continuationMeta, deriveAge, formatDateUS, localTodayIso, formatAddress
} from '../../lib/intake.js'
import ConnectorBuild from './ConnectorBuild.jsx'
import PassportIntake from './PassportIntake.jsx'

// Readiness statuses that mean no verified LIVE connector exists yet, so the
// applicant may ask Ellis to build one (brief §10).
const NEEDS_CONNECTOR = new Set(['APPLICANT_HANDOFF_READY', 'LIVE_SANDBOX_READY'])

const INVALID_STYLE = { borderColor: 'var(--ink)', boxShadow: '0 0 0 3px rgba(10,10,10,0.14)' }

// ---------------------------------------------------------------------------
// On-demand research-job persistence: the job id is stored per intake so the
// applicant can close the app mid-research and resume the live progress view.
const RESEARCH_STORE_PREFIX = 'ellis.research.'

function storedResearchJobId(intakeId) {
  try { return window.localStorage.getItem(RESEARCH_STORE_PREFIX + intakeId) } catch { return null }
}
function storeResearchJobId(intakeId, jobId) {
  try { window.localStorage.setItem(RESEARCH_STORE_PREFIX + intakeId, jobId) } catch { /* non-fatal */ }
}
function clearResearchJobId(intakeId) {
  try { window.localStorage.removeItem(RESEARCH_STORE_PREFIX + intakeId) } catch { /* non-fatal */ }
}

// Newest progress timestamp of a research job (ISO strings compare lexically).
function newestProgressAt(job) {
  const progress = Array.isArray(job?.progress) ? job.progress : []
  let newest = null
  for (const p of progress) {
    const at = p && typeof p.at === 'string' ? p.at : null
    if (at && (!newest || at > newest)) newest = at
  }
  return newest
}

// Which wizard step each intake field lives on (for the 422 missing-field jump).
const STEP_FIELDS = [
  ['passport_nationality', 'passport_issuing_country', 'travel_document_type',
    'lawful_country_of_residence', 'residence_status',
    'address_line1', 'address_line2', 'address_city', 'address_region',
    'address_postal_code', 'address_country', 'mailing_address_same'],
  // visa_category/visa_subtype are determined automatically by Ellis from the
  // route — the applicant never picks a category before the route is known.
  ['destination_country', 'travel_purpose',
    'arrival_date', 'departure_date', 'transit_countries'],
  ['birth_date', 'age', 'dependants', 'existing_destination_visas', 'existing_residence_permits',
    'prior_refusals', 'existing_portal_account', 'preferred_language', 'email']
]

function stepForField(key) {
  for (let i = 0; i < STEP_FIELDS.length; i++) if (STEP_FIELDS[i].includes(key)) return i
  return 0
}

function openUrl(url) {
  if (typeof window === 'undefined' || !url) return
  if (window.ellis?.openExternal) window.ellis.openExternal(url)
  else window.open(url, '_blank', 'noopener')
}

export default function StartVisa({ client, onOpenCase }) {
  const { t, lang } = useLocale()
  const toast = useToast()

  const [phase, setPhase] = useState('loading') // loading | hero | wizard | guidance | research | result
  const [loadError, setLoadError] = useState(null)
  const [info, setInfo] = useState(null)
  const [reg, setReg] = useState(null)
  const [draft, setDraft] = useState(null)      // newest resumable draft intake
  const [converted, setConverted] = useState(null) // newest converted intake -> its case

  const [intakeId, setIntakeId] = useState(null)
  const [answers, setAnswers] = useState({})
  const [step, setStep] = useState(0)
  const [missing, setMissing] = useState([])
  const [saveState, setSaveState] = useState('')
  const [resolving, setResolving] = useState(false)
  const [resolveError, setResolveError] = useState(null)
  const [result, setResult] = useState(null)
  const [researchJob, setResearchJob] = useState(null) // {id, status, stage, ...} while researching
  const [guidance, setGuidance] = useState(null)       // Kimi-primary AI route guidance
  const [guidanceLoading, setGuidanceLoading] = useState(false)
  const [guidanceError, setGuidanceError] = useState(null)
  const [entryMode, setEntryMode] = useState(null)     // null (choice) | 'manual' — Step 1 mode
  const [passportConfirmed, setPassportConfirmed] = useState(false)
  const [continuing, setContinuing] = useState(false)
  const [continueError, setContinueError] = useState(null)

  const answersRef = useRef(answers)
  answersRef.current = answers
  const saveTimer = useRef(null)
  const intakeRef = useRef(null)
  intakeRef.current = intakeId

  async function loadAll() {
    setLoadError(null)
    try {
      const [i, r, list] = await Promise.all([
        client.snapshotInfo(), client.snapshotRegistries(), client.listIntakes()
      ])
      setInfo(i); setReg(r)
      const intakes = list.intakes || []
      const drafts = intakes
        .filter((x) => x.status === 'draft')
        .sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')))
      setDraft(drafts[0] || null)
      // A converted intake means an in-flight case: the applicant resumes at
      // the case's current stage, never back at the start of the wizard.
      const conv = intakes
        .filter((x) => x.status === 'converted' && x.case_id)
        .sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')))
      setConverted(conv[0] || null)
      if (await resumeResearch(intakes)) return
      if (await resumeResolved(intakes)) return
      setPhase('hero')
    } catch (e) {
      setLoadError({ message: e.message })
    }
  }

  // If a resolved intake left a stored research-job id behind and that job is
  // still non-terminal, reopen straight into the live progress view.
  async function resumeResearch(intakes) {
    // A converted intake's stored job id is the background AUDIT of an already
    // created case — never a foreground research flow to trap the applicant in.
    const candidates = intakes
      .filter((x) => x && x.id && x.status !== 'converted' && storedResearchJobId(x.id))
      .sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')))
    for (const it of candidates) {
      const jobId = storedResearchJobId(it.id)
      try {
        const job = await client.getResearchJob(jobId)
        if (!researchTerminal(job.status)) {
          // Load the intake's saved answers too, so "Edit answers" from the
          // research panel never autosaves an empty draft over them.
          const full = await client.getIntake(it.id)
          setIntakeId(it.id)
          setAnswers({ travel_purpose: 'tourism', ...(full.answers || {}) })
          setStep(0); setMissing([])
          setResearchJob(job)
          setPhase('research')
          return true
        }
        clearResearchJobId(it.id) // finished while we were away — start fresh
      } catch (e) {
        // Job/intake gone server-side: drop the stale key. Transient errors keep it.
        if (e && (e.status === 404 || e.status === 410)) clearResearchJobId(it.id)
      }
    }
    return false
  }
  // A resolved-but-not-yet-continued intake resumes AT THE GUIDANCE PAGE
  // (cached guidance loads instantly) — the applicant's answers and the
  // primary continuation are never dropped by a refresh or restart.
  async function resumeResolved(intakes) {
    const cand = intakes
      .filter((x) => x && x.id && x.status === 'resolved' && !x.case_id)
      .sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')))[0]
    if (!cand) return false
    try {
      const full = await client.getIntake(cand.id)
      const g = await client.routeGuidance(cand.id)
      setIntakeId(cand.id)
      setAnswers({ travel_purpose: 'tourism', ...(full.answers || {}) })
      setStep(0); setMissing([]); setGuidanceError(null); setContinueError(null)
      setEntryMode((full.answers || {}).passport_nationality ? 'manual' : null)
      setPassportConfirmed(!!(full.answers || {}).passport_number)
      setGuidance(g)
      setPhase('guidance')
      return true
    } catch {
      return false // guidance unavailable right now -> normal hero
    }
  }
  useEffect(() => { loadAll() }, [])
  useEffect(() => () => clearTimeout(saveTimer.current), [])

  // ---- draft autosave ------------------------------------------------------
  async function flushSave() {
    clearTimeout(saveTimer.current)
    if (!intakeRef.current) return
    setSaveState('saving')
    try {
      await client.updateIntake(intakeRef.current, { answers: answersRef.current })
      setSaveState('saved')
    } catch { setSaveState('') }
  }
  function scheduleSave() {
    clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(flushSave, 600)
  }
  function setAnswer(key, value) {
    setAnswers((prev) => {
      const next = { ...prev }
      if (value === undefined || value === '') delete next[key]
      else next[key] = value
      answersRef.current = next
      return next
    })
    setMissing((m) => m.filter((k) => k !== key))
    scheduleSave()
  }

  // ---- start / resume ------------------------------------------------------
  async function startNew() {
    setLoadError(null)
    try {
      const seed = { travel_purpose: 'tourism', travel_document_type: 'ordinary_passport', preferred_language: lang }
      const res = await client.createIntake({ answers: seed, preferred_language: lang, email: '' })
      setIntakeId(res.id)
      setAnswers({ ...seed, ...(res.answers || {}) })
      setStep(0); setMissing([]); setResult(null); setResolveError(null); setResearchJob(null)
      setEntryMode(null); setPassportConfirmed(false); setContinueError(null)
      setGuidance(null); setGuidanceError(null); setGuidanceLoading(false)
      setPhase('wizard')
    } catch (e) { setLoadError({ message: e.message }) }
  }
  async function resumeDraft() {
    if (!draft) return startNew()
    setLoadError(null)
    try {
      const res = await client.getIntake(draft.id)
      setIntakeId(res.id)
      const merged = { travel_purpose: 'tourism', ...(res.answers || {}) }
      setAnswers(merged)
      setStep(0); setMissing([]); setResult(null); setResolveError(null); setResearchJob(null)
      // A draft that already carries passport data resumes past the chooser.
      setEntryMode(merged.passport_nationality ? 'manual' : null)
      setPassportConfirmed(!!merged.passport_number)
      setContinueError(null)
      setGuidance(null); setGuidanceError(null); setGuidanceLoading(false)
      setPhase('wizard')
    } catch (e) { setLoadError({ message: e.message }) }
  }

  // ---- the bounded single-pass Kimi analysis (retryable) -------------------
  async function fetchGuidance() {
    setGuidanceLoading(true); setGuidanceError(null)
    try {
      const g = await client.routeGuidance(intakeId)
      setGuidance(g)
    } catch (ge) {
      // Provider-specific or deadline message straight from the backend —
      // never a generic spinner and never a silent fallback.
      setGuidanceError({ message: ge.detail?.reason || ge.message })
    }
    setGuidanceLoading(false)
  }

  // ---- continuation after guidance (the primary CTA) -----------------------
  async function continueToCase() {
    setContinuing(true); setContinueError(null)
    try {
      await flushSave()
      const res = await client.continueIntake(intakeId)
      // The stored research-job id now belongs to the case's background audit —
      // clear it so a restart never traps the applicant in the research view.
      clearResearchJobId(intakeId)
      onOpenCase && onOpenCase({
        id: res.case_id,
        full_name: answersRef.current.full_name || '',
        destination_country: answersRef.current.destination_country || '',
        visa_type: 'tourist',
        continuation_kind: res.continuation_kind
      })
    } catch (e) {
      const blockers = e.detail && Array.isArray(e.detail.blockers) ? e.detail.blockers : null
      setContinueError({ message: blockers ? blockers.join(', ') : e.message })
    }
    setContinuing(false)
  }

  // ---- resolve -------------------------------------------------------------
  function localProblems() {
    const fields = info?.intake_fields || []
    const bad = missingRequired(fields, answersRef.current)
    if (!datesOrdered(answersRef.current.arrival_date, answersRef.current.departure_date)) bad.push('departure_date')
    if (answersRef.current.email && !validEmail(answersRef.current.email)) bad.push('email')
    return [...new Set(bad)]
  }
  async function resolve() {
    setResolveError(null); setGuidanceError(null)
    const bad = localProblems()
    if (bad.length) {
      setMissing(bad)
      setStep(stepForField(bad[0]))
      toast(t('start.missing'))
      return
    }
    setResolving(true)
    try {
      await flushSave()
      const res = await client.resolveIntake(intakeId)
      // The single-pass Kimi decision is the route analysis. No official-source
      // research runs — resolve either attaches the cached result or the UI
      // requests the bounded analysis now.
      if (res.kimi_guidance) {
        setGuidance(res.kimi_guidance)      // cached -> instant
        setResult(res); setPhase('guidance')
      } else if (res.kimi_guidance_pending) {
        setResult(res); setPhase('guidance')
        await fetchGuidance()
      } else {
        setResult(res)
        setPhase('result')
      }
    } catch (e) {
      const mf = e.detail && Array.isArray(e.detail.missing_fields) ? e.detail.missing_fields : null
      if (e.status === 422 && mf && mf.length) {
        setMissing(mf)
        setStep(stepForField(mf[0]))
        toast(t('start.missing'))
      } else {
        setResolveError({ message: e.message })
      }
    }
    setResolving(false)
  }

  // ---- registry-derived option lists --------------------------------------
  const countryOpts = useMemo(() => (reg?.countries || []).map((c) => ({
    value: c.alpha_3,
    label: `${c.flag ? c.flag + ' ' : ''}${c.name}`,
    search: `${c.name} ${c.alpha_2} ${c.alpha_3}`.toLowerCase()
  })), [reg])
  const nationalityOpts = useMemo(() => (reg?.nationalities || []).map((n) => ({
    value: n.code, label: n.name, search: `${n.name} ${n.code}`.toLowerCase()
  })), [reg])
  const docTypes = reg?.travel_document_types || []
  const categories = reg?.tourist_visa_categories || []
  const subtypes = (categories.find((c) => c.code === answers.visa_category)?.subtypes) || []

  const countryName = (code) => countryOpts.find((o) => o.value === code)?.label || code || ''
  const nationalityName = (code) => nationalityOpts.find((o) => o.value === code)?.label || code || ''

  const fieldsByKey = useMemo(() => {
    const m = {}
    for (const f of info?.intake_fields || []) m[f.key] = f
    return m
  }, [info])
  const visible = (key) => {
    const f = fieldsByKey[key]
    return f ? conditionField(f, answers) : true
  }
  const isMissing = (key) => missing.includes(key)

  // ---- shared header (permanent snapshot line + disclaimer) ---------------
  const header = (
    <div className="card card--soft" style={{ padding: '11px 15px', marginBottom: 18 }} data-testid="snapshot-header">
      <div style={{ fontSize: 13, fontWeight: 600 }}>{t('snapshot.displayLine')}</div>
      <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 2 }}>{t('snapshot.disclaimer')}</div>
    </div>
  )

  if (phase === 'loading') {
    return loadError
      ? <div><ErrorNote error={loadError} /><button className="btn btn--sm" style={{ marginTop: 10 }} onClick={loadAll}>↻</button></div>
      : <Loading label={t('common.loading')} />
  }

  if (phase === 'hero') {
    return (
      <div>
        {header}
        {loadError && <ErrorNote error={loadError} />}
        <div className="card" style={{ padding: 32, textAlign: 'center' }}>
          <div className="h-serif" style={{ fontSize: 34 }}>{t('start.hero.title')}</div>
          <p style={{ fontSize: 14.5, color: 'var(--muted)', maxWidth: 520, margin: '12px auto 22px', lineHeight: 1.55 }}>
            {t('start.hero.sub')}
          </p>
          <div style={{ display: 'flex', gap: 10, justifyContent: 'center', flexWrap: 'wrap' }}>
            {converted && (
              <button className="btn" data-testid="resume-case"
                onClick={() => onOpenCase && onOpenCase({ id: converted.case_id,
                  destination_country: (converted.answers || {}).destination_country || '',
                  full_name: (converted.answers || {}).full_name || '', visa_type: 'tourist' })}>
                {t('case.resume')}
              </button>
            )}
            {draft && (
              <button className={'btn' + (converted ? ' btn--ghost' : '')} onClick={resumeDraft}>{t('start.resume.title')}</button>
            )}
            <button className={'btn' + (draft || converted ? ' btn--ghost' : '')} onClick={startNew}>
              {draft || converted ? t('start.resume.new') : t('start.hero.cta')}
            </button>
          </div>
          {converted && <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 12 }}>{t('case.resumeSub')}</div>}
          {draft && !converted && <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 12 }}>{t('start.resume.sub')}</div>}
        </div>
      </div>
    )
  }

  if (phase === 'guidance' && intakeId) {
    return (
      <div>
        {header}
        <GuidancePanel t={t} guidance={guidance} loading={guidanceLoading}
          error={guidanceError} onRetry={fetchGuidance}
          answers={answers}
          onAnswerHealth={(countries) => { setAnswer('recent_travel_countries', countries); flushSave() }}
          onContinue={continueToCase} continuing={continuing} continueError={continueError}
          onEdit={() => { setPhase('wizard'); setStep(3) }}
          onNew={startNew} />
      </div>
    )
  }

  if (phase === 'research' && researchJob && intakeId) {
    return (
      <div>
        {header}
        <ResearchPanel client={client} t={t} intakeId={intakeId} initialJob={researchJob}
          onEdit={() => { setPhase('wizard'); setStep(3) }}
          onNew={startNew} />
      </div>
    )
  }

  if (phase === 'result' && result) {
    return (
      <div>
        {header}
        <ResultPanel client={client} t={t} result={result}
          onEdit={() => { setPhase('wizard'); setStep(3) }}
          onNew={startNew} />
      </div>
    )
  }

  // ---- wizard --------------------------------------------------------------
  const stepTitles = [t('start.step.passport'), t('start.step.trip'), t('start.step.about'), t('start.step.review')]

  return (
    <div>
      {header}
      <div className="card" style={{ padding: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 18 }}>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {stepTitles.map((s, i) => (
              <button key={s} className={'chip' + (i === step ? ' chip--ink' : '')}
                style={{ cursor: 'pointer' }} onClick={() => { flushSave(); setStep(i) }}>
                {i + 1}. {s}
              </button>
            ))}
          </div>
          <span style={{ fontSize: 11.5, color: 'var(--muted)' }}>
            {saveState === 'saving' ? t('start.saving') : saveState === 'saved' ? t('start.saved') : ''}
          </span>
        </div>

        {step === 0 && (
          <PassportIntake client={client} intakeId={intakeId} t={t}
            confirmed={passportConfirmed}
            onApply={(prefill) => {
              setAnswers((prev) => {
                const next = { ...prev }
                for (const [k, v] of Object.entries(prefill || {})) {
                  if (v !== undefined && v !== null && v !== '') next[k] = v
                }
                answersRef.current = next
                return next
              })
              setMissing((m) => m.filter((k) => !(prefill || {})[k]))
              setPassportConfirmed(true)
              setEntryMode('manual')   // reveal the (now prefilled) fields for review
              scheduleSave()
              toast(t('passport.applied'))
            }}
            onManual={() => setEntryMode('manual')} />
        )}
        {step === 0 && passportConfirmed && (
          <div style={{ marginBottom: 10 }}>
            <span className="chip chip--ink" data-testid="passport-prefilled-chip">✓ {t('passport.prefilledChip')}</span>
          </div>
        )}
        {step === 0 && entryMode === 'manual' && (
          <div className="grid grid-2" style={{ gap: 12 }}>
            <Field label={t('field.passport_nationality')} invalid={isMissing('passport_nationality')}>
              <SearchSelect t={t} value={answers.passport_nationality} options={nationalityOpts}
                invalid={isMissing('passport_nationality')}
                onChange={(v) => setAnswer('passport_nationality', v)} />
            </Field>
            <Field label={t('field.passport_issuing_country')} invalid={isMissing('passport_issuing_country')}>
              <SearchSelect t={t} value={answers.passport_issuing_country} options={countryOpts}
                invalid={isMissing('passport_issuing_country')}
                onChange={(v) => setAnswer('passport_issuing_country', v)} />
            </Field>
            <Field label={t('field.travel_document_type')} invalid={isMissing('travel_document_type')}>
              <select className="select" value={answers.travel_document_type || 'ordinary_passport'}
                onChange={(e) => setAnswer('travel_document_type', e.target.value)}>
                {docTypes.map((d) => <option key={d.code} value={d.code}>{d.name}</option>)}
              </select>
            </Field>
            <Field label={t('field.lawful_country_of_residence')} invalid={isMissing('lawful_country_of_residence')}>
              <SearchSelect t={t} value={answers.lawful_country_of_residence} options={countryOpts}
                invalid={isMissing('lawful_country_of_residence')}
                onChange={(v) => setAnswer('lawful_country_of_residence', v)} />
            </Field>
            {visible('residence_status') && (
              <Field label={t('field.residence_status')} invalid={isMissing('residence_status')}>
                <select className="select" value={answers.residence_status || ''}
                  onChange={(e) => setAnswer('residence_status', e.target.value)}>
                  <option value="">{t('start.select')}</option>
                  {RESIDENCE_STATUS_OPTIONS.map((o) => <option key={o} value={o}>{t('res.' + o)}</option>)}
                </select>
              </Field>
            )}
          </div>
        )}

        {/* Structured home address — mandatory, entered manually, country-aware
            (many countries have no state/region or postal code, so only
            line 1 / city / country are required; no U.S. format is assumed).
            Renders in BOTH entry modes. */}
        {step === 0 && (
          <div style={{ marginTop: 16 }} data-testid="address-section">
            <div className="eyebrow">{t('address.title')}</div>
            <div style={{ fontSize: 12.5, color: 'var(--muted)', margin: '2px 0 10px' }}>
              {t('address.sub')}
            </div>
            <div className="grid grid-2" style={{ gap: 12 }}>
              <Field label={t('field.address_line1')} invalid={isMissing('address_line1')}>
                <input className="input" value={answers.address_line1 || ''}
                  style={isMissing('address_line1') ? INVALID_STYLE : undefined}
                  onChange={(e) => setAnswer('address_line1', e.target.value)} />
              </Field>
              <Field label={t('field.address_line2')}>
                <input className="input" value={answers.address_line2 || ''}
                  onChange={(e) => setAnswer('address_line2', e.target.value)} />
              </Field>
              <Field label={t('field.address_city')} invalid={isMissing('address_city')}>
                <input className="input" value={answers.address_city || ''}
                  style={isMissing('address_city') ? INVALID_STYLE : undefined}
                  onChange={(e) => setAnswer('address_city', e.target.value)} />
              </Field>
              <Field label={t('field.address_region')}>
                <input className="input" value={answers.address_region || ''}
                  onChange={(e) => setAnswer('address_region', e.target.value)} />
              </Field>
              <Field label={t('field.address_postal_code')}>
                <input className="input" value={answers.address_postal_code || ''}
                  onChange={(e) => setAnswer('address_postal_code', e.target.value)} />
              </Field>
              <Field label={t('field.address_country')} invalid={isMissing('address_country')}>
                <SearchSelect t={t} value={answers.address_country} options={countryOpts}
                  invalid={isMissing('address_country')}
                  onChange={(v) => setAnswer('address_country', v)} />
              </Field>
            </div>
            <label style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 10,
                            fontSize: 13, cursor: 'pointer' }}>
              <input type="checkbox" checked={answers.mailing_address_same !== false}
                onChange={(e) => setAnswer('mailing_address_same', e.target.checked)} />
              {t('field.mailing_address_same')}
            </label>
          </div>
        )}

        {step === 1 && (
          <div className="grid grid-2" style={{ gap: 12 }}>
            <Field label={t('field.destination_country')} invalid={isMissing('destination_country')}>
              <SearchSelect t={t} value={answers.destination_country} options={countryOpts}
                invalid={isMissing('destination_country')}
                onChange={(v) => setAnswer('destination_country', v)} />
            </Field>
            {/* The visa/entry category is determined automatically by Ellis
                once the route is known — the applicant never has to pick
                "Tourist visa (consular)" before Ellis knows whether a visa is
                even required. */}
            <Field label={t('field.visa_category')}>
              <div className="input" style={{ display: 'flex', alignItems: 'center', background: 'var(--bg-soft)',
                fontSize: 13, color: 'var(--muted)' }} data-testid="auto-category">
                {t('start.autoCategory')}
              </div>
            </Field>
            <Field label={t('field.travel_purpose')}>
              <div className="input" style={{ display: 'flex', alignItems: 'center', background: 'var(--bg-soft)' }}>
                {t('purpose.tourism')}
              </div>
            </Field>
            <Field label={t('field.arrival_date')} invalid={isMissing('arrival_date')}>
              <input type="date" className="input" style={isMissing('arrival_date') ? INVALID_STYLE : undefined}
                value={answers.arrival_date || ''} onChange={(e) => setAnswer('arrival_date', e.target.value)} />
            </Field>
            <Field label={t('field.departure_date')} invalid={isMissing('departure_date')}>
              <input type="date" className="input" style={isMissing('departure_date') ? INVALID_STYLE : undefined}
                value={answers.departure_date || ''} onChange={(e) => setAnswer('departure_date', e.target.value)} />
              {!datesOrdered(answers.arrival_date, answers.departure_date) && (
                <div style={{ fontSize: 12, color: 'var(--ink)', fontWeight: 600, marginTop: 4 }}>
                  {t('start.departAfterArrive')}
                </div>
              )}
            </Field>
            <Field label={t('field.transit_countries')} optional t={t}>
              <MultiSelect t={t} values={answers.transit_countries || []} options={countryOpts}
                onChange={(v) => setAnswer('transit_countries', v.length ? v : undefined)} />
            </Field>
          </div>
        )}

        {step === 2 && (
          <div className="grid grid-2" style={{ gap: 12 }}>
            <Field label={t('field.birth_date')} invalid={isMissing('birth_date')}>
              <input type="date" className="input" value={answers.birth_date || ''}
                onChange={(e) => {
                  const dob = e.target.value || undefined
                  setAnswer('birth_date', dob)
                  // Age is ALWAYS derived from the date of birth — never typed
                  // independently once a birth date exists. "Today" is the
                  // applicant's LOCAL calendar day (UTC would flip the age on
                  // birthdays west of Greenwich).
                  const age = dob ? deriveAge(dob, localTodayIso()) : null
                  setAnswer('age', age == null ? undefined : age)
                }} />
            </Field>
            <Field label={t('field.age')} invalid={isMissing('age')}>
              {answers.birth_date
                ? <div className="input" style={{ display: 'flex', alignItems: 'center', background: 'var(--bg-soft)' }}
                    data-testid="derived-age">
                    {answers.age ?? '—'} · {t('passport.source.derived')}
                  </div>
                : <input type="number" min="0" max="130" className="input" style={isMissing('age') ? INVALID_STYLE : undefined}
                    value={answers.age ?? ''}
                    onChange={(e) => setAnswer('age', e.target.value === '' ? undefined : Number(e.target.value))} />}
            </Field>
            <Field label={t('field.dependants')} optional t={t}>
              <input type="number" min="0" max="20" className="input" value={answers.dependants ?? ''}
                onChange={(e) => setAnswer('dependants', e.target.value === '' ? undefined : Number(e.target.value))} />
            </Field>
            <Field label={t('field.existing_destination_visas')} optional t={t}>
              <input className="input" value={answers.existing_destination_visas || ''}
                onChange={(e) => setAnswer('existing_destination_visas', e.target.value)} />
            </Field>
            <Field label={t('field.existing_residence_permits')} optional t={t}>
              <input className="input" value={answers.existing_residence_permits || ''}
                onChange={(e) => setAnswer('existing_residence_permits', e.target.value)} />
            </Field>
            <Field label={t('field.prior_refusals')}>
              <select className="select" value={answers.prior_refusals || ''}
                onChange={(e) => setAnswer('prior_refusals', e.target.value || undefined)}>
                <option value="">{t('start.select')}</option>
                <option value="no">{t('opt.no')}</option>
                <option value="yes">{t('opt.yes')}</option>
              </select>
            </Field>
            {answers.prior_refusals === 'yes' && (
              <Field label={t('field.prior_refusals_detail')}>
                <input className="input" value={answers.prior_refusals_detail || ''}
                  onChange={(e) => setAnswer('prior_refusals_detail', e.target.value)} />
              </Field>
            )}
            <Field label={t('field.existing_portal_account')}>
              <select className="select" value={answers.existing_portal_account || ''}
                onChange={(e) => setAnswer('existing_portal_account', e.target.value || undefined)}>
                <option value="">{t('start.select')}</option>
                <option value="no">{t('opt.no')}</option>
                <option value="yes">{t('opt.yes')}</option>
              </select>
            </Field>
            <Field label={t('field.preferred_language')} invalid={isMissing('preferred_language')}>
              <select className="select" value={answers.preferred_language || lang}
                onChange={(e) => setAnswer('preferred_language', e.target.value)}>
                {SUPPORTED.map((c) => <option key={c} value={c}>{LANGUAGE_NAMES[c]}</option>)}
              </select>
            </Field>
            <Field label={t('field.email')} invalid={isMissing('email')}>
              <input type="email" className="input" style={isMissing('email') ? INVALID_STYLE : undefined}
                value={answers.email || ''} onChange={(e) => setAnswer('email', e.target.value)}
                onBlur={flushSave} />
              {answers.email && !validEmail(answers.email) && (
                <div style={{ fontSize: 12, color: 'var(--ink)', fontWeight: 600, marginTop: 4 }}>
                  {t('start.invalidEmail')}
                </div>
              )}
            </Field>
          </div>
        )}

        {step === 3 && (
          <ReviewStep t={t} answers={answers} info={info} missing={missing}
            display={{
              countryName, nationalityName,
              docName: (c) => docTypes.find((d) => d.code === c)?.name || c,
              catName: (c) => categories.find((x) => x.code === c)?.name || c
            }}
            onJump={(k) => setStep(stepForField(k))} />
        )}

        {resolveError && <ErrorNote error={resolveError} />}

        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 20 }}>
          <button className="btn btn--sm btn--ghost" disabled={step === 0}
            onClick={() => { flushSave(); setStep(step - 1) }}>{t('start.back')}</button>
          {step < 3
            ? <button className="btn btn--sm" onClick={() => { flushSave(); setStep(step + 1) }}>{t('start.next')}</button>
            : <button className="btn" disabled={resolving} onClick={resolve}>
                {resolving ? t('start.resolving') : t('start.resolve')}
              </button>}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
function Field({ label, invalid, optional, t, children }) {
  return (
    <div className="field" style={{ marginBottom: 4 }}>
      <label style={invalid ? { color: 'var(--ink)' } : undefined}>
        {label}{optional && t ? ` — ${t('start.optional')}` : ''}{invalid ? ' •' : ''}
      </label>
      {children}
    </div>
  )
}

// Searchable single-select over a large option list (countries/nationalities).
function SearchSelect({ value, options, onChange, invalid, t }) {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const current = options.find((o) => o.value === value)
  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase()
    const list = s ? options.filter((o) => o.search.includes(s)) : options
    return list.slice(0, 60)
  }, [q, options])
  return (
    <div style={{ position: 'relative' }}>
      <input className="input" style={invalid ? INVALID_STYLE : undefined}
        value={open ? q : (current ? current.label : '')}
        placeholder={current ? current.label : t('start.search')}
        onFocus={() => { setOpen(true); setQ('') }}
        onChange={(e) => setQ(e.target.value)}
        onBlur={() => setTimeout(() => setOpen(false), 150)} />
      {open && (
        <div className="card" style={{ position: 'absolute', zIndex: 40, top: '100%', left: 0, right: 0,
          maxHeight: 236, overflowY: 'auto', marginTop: 4, background: 'var(--bg)', boxShadow: '0 8px 24px rgba(0,0,0,0.12)' }}>
          {filtered.length === 0
            ? <div style={{ padding: 10, fontSize: 13, color: 'var(--muted)' }}>{t('start.search')}</div>
            : filtered.map((o) => (
                <div key={o.value}
                  onMouseDown={(e) => { e.preventDefault(); onChange(o.value); setOpen(false) }}
                  style={{ padding: '8px 12px', cursor: 'pointer', fontSize: 13.5,
                    background: o.value === value ? 'var(--bg-soft)' : undefined }}>
                  {o.label}
                </div>
              ))}
        </div>
      )}
    </div>
  )
}

// Multi-select (transit countries): selected chips + a search box to add more.
function MultiSelect({ values, options, onChange, t }) {
  return (
    <div>
      {values.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 6 }}>
          {values.map((v) => (
            <span key={v} className="chip chip--ink" style={{ cursor: 'pointer' }}
              onClick={() => onChange(values.filter((x) => x !== v))}>
              {options.find((o) => o.value === v)?.label || v} ✕
            </span>
          ))}
        </div>
      )}
      <SearchSelect t={t} value={undefined}
        options={options.filter((o) => !values.includes(o.value))}
        onChange={(v) => onChange([...values, v])} />
    </div>
  )
}

// Review step: every answer with its display value; missing ones jump back.
function ReviewStep({ t, answers, info, missing, display, onJump }) {
  const rows = []
  const push = (key, value, labelKey) => rows.push({ key, value, labelKey })
  push('passport_nationality', display.nationalityName(answers.passport_nationality))
  push('passport_issuing_country', display.countryName(answers.passport_issuing_country))
  push('travel_document_type', display.docName(answers.travel_document_type || 'ordinary_passport'))
  push('lawful_country_of_residence', display.countryName(answers.lawful_country_of_residence))
  if (answers.residence_status) push('residence_status', t('res.' + answers.residence_status))
  // Structured home address — the jump key lands on the address section.
  push('address_line1', formatAddress({ ...answers,
    address_country: display.countryName(answers.address_country) }), 'address.title')
  push('mailing_address_same',
    answers.mailing_address_same === false ? t('opt.no') : t('opt.yes'))
  push('destination_country', display.countryName(answers.destination_country))
  // The category is determined automatically by Ellis, never picked upfront.
  push('visa_category', t('start.autoCategory'))
  push('travel_purpose', t('purpose.tourism'))
  // Applicant-facing dates are always U.S. MM/DD/YYYY (values stay ISO).
  push('arrival_date', formatDateUS(answers.arrival_date))
  push('departure_date', formatDateUS(answers.departure_date))
  if (Array.isArray(answers.transit_countries) && answers.transit_countries.length)
    push('transit_countries', answers.transit_countries.map(display.countryName).join(', '))
  if (answers.birth_date) push('birth_date', formatDateUS(answers.birth_date))
  push('age', answers.age)
  if (answers.dependants != null) push('dependants', answers.dependants)
  if (answers.existing_destination_visas) push('existing_destination_visas', answers.existing_destination_visas)
  if (answers.existing_residence_permits) push('existing_residence_permits', answers.existing_residence_permits)
  if (answers.prior_refusals) push('prior_refusals', t('opt.' + answers.prior_refusals) + (answers.prior_refusals === 'yes' && answers.prior_refusals_detail ? ` — ${answers.prior_refusals_detail}` : ''))
  if (answers.existing_portal_account) push('existing_portal_account', t('opt.' + answers.existing_portal_account))
  push('preferred_language', LANGUAGE_NAMES[answers.preferred_language] || answers.preferred_language)
  push('email', answers.email)

  return (
    <div>
      {rows.map(({ key, value, labelKey }) => {
        const bad = missing.includes(key)
        return (
          <div className="kv" key={key} style={{ cursor: 'pointer' }} onClick={() => onJump(key)}>
            <div className="kv__k" style={bad ? { color: 'var(--ink)', fontWeight: 700 } : undefined}>
              {t(labelKey || 'field.' + key)}{bad ? ' •' : ''}
            </div>
            <div className="kv__v">{value == null || value === '' ? '—' : String(value)}</div>
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// The honest result panel: ONE readiness status, the disposition, the
// verification-check rows, expandable sources, snapshot date + disclaimer.
const TONE_STYLES = {
  ok: { background: 'var(--ink)', color: '#fff', border: '1px solid var(--ink)' },
  warn: { background: 'var(--bg)', border: '2px solid var(--ink)' },
  info: { background: 'var(--bg-soft)', border: '1px solid var(--line)' },
  blocked: { background: 'var(--bg)', border: '2px solid var(--ink)' }
}
const STATUS_NOTE_KEY = {
  NOT_READY: 'result.notReadySaved',
  PREPARATION_ONLY: 'result.prepHonest',
  APPLICANT_HANDOFF_READY: 'result.handoffHonest',
  LIVE_SANDBOX_READY: 'result.sandboxHonest',
  LIVE_PRODUCTION_READY: 'result.productionHonest'
}

// The visible 60-second bounded progress state for the Kimi analysis —
// never an indefinite spinner. Counts down from the backend's hard deadline.
const GUIDANCE_DEADLINE_SECONDS = 60

function GuidanceCountdown({ t }) {
  const [left, setLeft] = useState(GUIDANCE_DEADLINE_SECONDS)
  useEffect(() => {
    const timer = setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000)
    return () => clearInterval(timer)
  }, [])
  const pct = Math.round(100 * (GUIDANCE_DEADLINE_SECONDS - left) / GUIDANCE_DEADLINE_SECONDS)
  return (
    <div data-testid="guidance-countdown" style={{ maxWidth: 360, margin: '10px auto 0' }}>
      <div style={{ height: 6, borderRadius: 3, background: 'var(--bg-soft)', overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${pct}%`, background: 'var(--ink)',
          transition: 'width 1s linear' }} />
      </div>
      <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 6 }}>
        {t('guidance.countdown', { s: left })}
      </div>
    </div>
  )
}

// The conditional health question, asked at the guidance stage ONLY when the
// verified route carries a conditional rule and the applicant has not
// answered the travel-history question yet.
function GuidanceHealthQuestion({ t, guidance, answers, onAnswer }) {
  const [selecting, setSelecting] = useState(false)
  const [picked, setPicked] = useState([])
  const reqs = ((guidance || {}).guidance || {}).health_requirements || []
  const conditional = reqs.filter((r) => r && r.applicability === 'conditional')
  if (conditional.length === 0) return null
  if (answers && Object.prototype.hasOwnProperty.call(answers, 'recent_travel_countries')) return null
  const q = conditional[0]
  return (
    <div className="note" style={{ marginTop: 12 }} data-testid="guidance-health-question">
      <div style={{ fontWeight: 600, fontSize: 13 }}>{t('health.questionTitle')}</div>
      <div style={{ fontSize: 13, marginTop: 4 }}>
        {q.question || `${t('health.questionTitle')}: ${q.name}`}
      </div>
      {q.trigger && <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 2 }}>{q.trigger}</div>}
      {!selecting ? (
        <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
          <button className="btn btn--sm" onClick={() => setSelecting(true)}>{t('health.yes')}</button>
          <button className="btn btn--sm btn--ghost" onClick={() => onAnswer([])}>{t('health.no')}</button>
        </div>
      ) : (
        <CountryAnswer t={t} options={q.trigger_countries || []}
          picked={picked} setPicked={setPicked}
          onSubmit={() => onAnswer(picked)} />
      )}
    </div>
  )
}

// Country selection for a travel-history answer: chips when the rule names its
// risk countries, a free entry when it does not (never a fictional default).
function CountryAnswer({ t, options, picked, setPicked, onSubmit }) {
  const [text, setText] = useState('')
  const useChips = options.length > 0
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ fontSize: 12.5, color: 'var(--muted)', marginBottom: 6 }}>
        {t('health.whichCountries')}
      </div>
      {useChips ? (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
          {options.map((c) => (
            <button key={c} type="button"
              className={'chip' + (picked.includes(c) ? ' chip--ink' : '')}
              onClick={() => setPicked((p) => p.includes(c) ? p.filter((x) => x !== c) : [...p, c])}>
              {c}
            </button>
          ))}
        </div>
      ) : (
        <input className="input" style={{ marginBottom: 8 }} value={text}
          placeholder="e.g. KEN, BRA"
          onChange={(e) => {
            setText(e.target.value)
            setPicked(e.target.value.split(',').map((s) => s.trim().toUpperCase()).filter(Boolean))
          }} />
      )}
      <button className="btn btn--sm" disabled={picked.length === 0} onClick={onSubmit}>OK</button>
    </div>
  )
}

// The Kimi route decision card: one Kimi pass decides, deterministic checks
// validate it, and the decision label replaces every official-source claim.
// Irreversible actions still carry an explicit confirmation requirement.
function GuidancePanel({ t, guidance, loading, error, onEdit, onNew, onRetry,
                         answers, onAnswerHealth,
                         onContinue, continuing, continueError }) {
  if (loading) {
    return (
      <div className="card" style={{ padding: 24, textAlign: 'center' }}>
        <div className="badge badge--ai" style={{ marginBottom: 12 }}>{t('guidance.aiBadge')}</div>
        <Loading label={t('guidance.loading')} />
        <GuidanceCountdown t={t} />
      </div>
    )
  }
  // A stale error must never mask real guidance — the error card renders only
  // when there is no guidance to show. The message is the backend's precise
  // provider/deadline explanation, with an immediate retry.
  if (error && !guidance) {
    return (
      <div className="card" style={{ padding: 24 }}>
        <ErrorNote error={{ message: error.message || t('guidance.unavailable') }} />
        <div style={{ marginTop: 12, display: 'flex', gap: 10 }}>
          <button className="btn btn--sm" onClick={onRetry} data-testid="guidance-retry">
            {t('guidance.timeoutRetry')}
          </button>
          <button className="btn btn--ghost btn--sm" onClick={onEdit}>{t('start.editAnswers')}</button>
          <button className="btn btn--ghost btn--sm" onClick={onNew}>{t('start.resume.new')}</button>
        </div>
      </div>
    )
  }
  if (!guidance) return null
  const g = guidance.guidance || {}
  const disp = guidanceDispositionMeta(g.disposition)
  const usable = guidanceIsUsable(guidance)
  const plan = Array.isArray(guidance.workflow_plan) ? guidance.workflow_plan : []
  const irreversible = plan.map(guidanceStepMeta).filter((s) => s.requiresConfirmation)
  const fee = g.government_fee || {}
  // The decision chip needs no verification verdict — a valid Kimi decision
  // (usable KIMI_PRIMARY result, verification {passes: 1}) is enough.
  const verified = usable || verificationMeta(guidance.verification).verified
  const advisories = Array.isArray(guidance.advisories) ? guidance.advisories : []
  // The primary continuation: no normal route ends at this page.
  const cont = continuationMeta(guidance)
  return (
    <div className="card" style={{ padding: 24 }}>
      {/* AI-generated indicator — always visible with guidance-driven flow. */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
        <span className="badge badge--ai">{t('guidance.aiBadge')}</span>
        {verified && (
          <span className="chip chip--sm" data-testid="guidance-verified-chip">
            {t('guidance.verified')}
          </span>
        )}
        {guidance.cached && <span className="chip chip--sm">{t('guidance.cached')}</span>}
        {guidance.stale && <span className="chip chip--sm">{t('guidance.refreshing')}</span>}
      </div>
      <div style={{ fontSize: 12.5, color: 'var(--muted)', marginBottom: 14 }}>
        {t('guidance.disclaimer')}
      </div>
      {advisories.length > 0 && (
        <div className="note note--warn" style={{ marginBottom: 12 }} data-testid="guidance-advisories">
          {advisories.map((a, i) => <div key={i} style={{ fontSize: 13 }}>• {a}</div>)}
        </div>
      )}

      {usable ? (
        <>
          <div className={'result__disp result__disp--' + disp.tone} style={{ marginBottom: 8 }}>
            {t(disp.i18nKey)}
          </div>
          <div className="grid grid-2" style={{ gap: 8, marginBottom: 12 }}>
            {g.visa_category && <GField label={t('guidance.f.category')} value={g.visa_category} />}
            {g.permitted_stay && <GField label={t('guidance.f.stay')} value={g.permitted_stay} />}
            {g.passport_validity && <GField label={t('guidance.f.passport')} value={g.passport_validity} />}
            {g.processing_time && <GField label={t('guidance.f.processing')} value={g.processing_time} />}
            {(fee.amount != null) && <GField label={t('guidance.f.fee')} value={`${fee.amount} ${fee.currency || ''}`} />}
            {g.application_channel && <GField label={t('guidance.f.channel')} value={String(g.application_channel).replace(/_/g, ' ')} />}
          </div>
          {Array.isArray(g.required_documents) && g.required_documents.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div className="eyebrow">{t('guidance.f.documents')}</div>
              <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                {g.required_documents.map((d, i) => <li key={i} style={{ fontSize: 13.5 }}>{d}</li>)}
              </ul>
            </div>
          )}
          {/* The safety boundary, shown plainly. */}
          {irreversible.length > 0 && (
            <div className="note note--warn" style={{ marginTop: 8 }}>
              {t('guidance.confirmBoundary')}
            </div>
          )}
        </>
      ) : (
        <div className="note note--warn">
          <div style={{ fontWeight: 600, marginBottom: 6 }}>{t('guidance.uncertainTitle')}</div>
          {Array.isArray(guidance.missing_fields) && guidance.missing_fields.length > 0 && (
            <div style={{ fontSize: 13 }}>{t('guidance.uncertainMissing')}: {guidance.missing_fields.join(', ')}</div>
          )}
          {Array.isArray(guidance.contradictions) && guidance.contradictions.map((c, i) => (
            <div key={i} style={{ fontSize: 13 }}>• {c}</div>
          ))}
        </div>
      )}

      {/* Travel-history question, asked ONLY when a conditional health rule
          needs it — never a generic vaccination request. */}
      <GuidanceHealthQuestion t={t} guidance={guidance} answers={answers}
        onAnswer={onAnswerHealth} />

      {/* Continuation. A blocked CONDITIONAL route shows the precise blocker
          instead of a CTA; everything else continues into the journey. */}
      {cont.blocked ? (
        <div className="note note--warn" style={{ marginTop: 12 }} data-testid="guidance-blocked">
          <div style={{ fontWeight: 600 }}>{t('guidance.continue.blockedTitle')}</div>
          {cont.blockers.map((b, i) => (
            <div key={i} style={{ fontSize: 13 }}>• {String(b).replace(/_/g, ' ')}</div>
          ))}
        </div>
      ) : (
        <div style={{ marginTop: 16 }}>
          {cont.partial && cont.blockers.length > 0 && (
            <div style={{ fontSize: 12.5, color: 'var(--muted)', marginBottom: 8 }}>
              {t('guidance.uncertainMissing')}: {cont.blockers.map((b) => String(b).replace(/_/g, ' ')).join(', ')}
            </div>
          )}
          <button className="btn" disabled={continuing} onClick={onContinue}
            data-testid="guidance-continue" data-kind={cont.kind}>
            {continuing ? t('guidance.continuing') : t(cont.ctaKey)}
          </button>
        </div>
      )}
      {continueError && <ErrorNote error={continueError} />}

      <div style={{ marginTop: 14, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <button className="btn btn--ghost btn--sm" onClick={onEdit}>{t('start.editAnswers')}</button>
        <button className="btn btn--ghost btn--sm" onClick={onNew}>{t('start.resume.new')}</button>
      </div>
    </div>
  )
}

function GField({ label, value }) {
  return (
    <div>
      <div className="eyebrow">{label}</div>
      <div style={{ fontSize: 13.5 }}>{value}</div>
    </div>
  )
}

// Renders from either source: the direct resolve response OR the `resolution`
// object of a finished research job (which carries no `checks` — the check
// rows are then omitted rather than shown as invented pendings). An optional
// `dateHonesty` ({label, ...}) from research results is shown under the
// snapshot line.
function ResultPanel({ client, t, result, dateHonesty, onEdit, onNew }) {
  const meta = readinessMeta(result.readiness_status)
  const hasChecks = result.checks && typeof result.checks === 'object'
  const rows = hasChecks ? checksSummary(result.checks) : []
  const noteKey = STATUS_NOTE_KEY[result.readiness_status] || 'result.notReadySaved'
  const [showSources, setShowSources] = useState(false)
  const [evidence, setEvidence] = useState(null)
  const [evError, setEvError] = useState(null)

  async function toggleSources() {
    const next = !showSources
    setShowSources(next)
    if (next && evidence === null) {
      try { setEvidence((await client.routeEvidence(result.resolution_id)).evidence || []) }
      catch (e) { setEvError({ message: e.message }) }
    }
  }

  return (
    <div className="card" style={{ padding: 24 }}>
      <div className="eyebrow">{t('result.title')}</div>

      <div style={{ borderRadius: 12, padding: '18px 20px', marginBottom: 16, ...TONE_STYLES[meta.tone] }}
        data-testid="readiness-status" data-tone={meta.tone}>
        <div style={{ fontSize: 21, fontWeight: 700 }}>{t(meta.i18nKey)}</div>
        <div style={{ fontSize: 12.5, marginTop: 4, opacity: 0.85 }}>
          {t('result.disposition')}: {String(result.disposition || '').replace(/_/g, ' ') || '—'}
        </div>
      </div>

      <p className="prose" style={{ fontSize: 13.5, marginBottom: 18 }}>{t(noteKey)}</p>

      {hasChecks && <div className="eyebrow">{t('result.checksTitle')}</div>}
      <div style={{ marginBottom: 16 }}>
        {rows.map((r) => (
          <div className="kv" key={r.key}>
            <div className="kv__k" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span className={'sevbadge ' + (r.status === 'ok' ? 'sevbadge--ok' : 'sevbadge--mid')}>
                {r.status === 'ok' ? '✓' : r.status === 'warn' ? '!' : '…'}
              </span>
              {t(r.labelKey)}
            </div>
            <div className="kv__v" style={{ fontSize: 13 }}>
              <span className="chip" style={{ fontSize: 10.5, marginRight: 8 }}>{r.status}</span>
              <span style={{ color: 'var(--muted)' }}>{r.detail || '—'}</span>
            </div>
          </div>
        ))}
      </div>

      <button className="btn btn--sm btn--ghost" onClick={toggleSources}>
        {showSources ? t('result.sourcesHide') : t('result.sourcesShow')}
      </button>
      {showSources && (
        <div style={{ marginTop: 12 }}>
          {evError && <ErrorNote error={evError} />}
          {evidence === null && !evError && <Loading label={t('common.loading')} />}
          {Array.isArray(evidence) && evidence.length === 0 && (
            <div style={{ fontSize: 13, color: 'var(--muted)', padding: '10px 2px' }}>{t('result.sourcesNone')}</div>
          )}
          {Array.isArray(evidence) && evidence.map((ev, i) => (
            <div key={i} className="row" style={{ alignItems: 'flex-start' }}>
              <div className="row__main">
                <div className="row__title">
                  <a href={ev.final_url} target="_blank" rel="noreferrer noopener"
                    onClick={(e) => { e.preventDefault(); openUrl(ev.final_url) }}
                    style={{ color: 'inherit' }}>
                    {ev.hostname || ev.final_url}
                  </a>
                  {ev.authority && <span className="chip" style={{ marginLeft: 8, fontSize: 10.5 }}>{ev.authority}</span>}
                </div>
                <div className="row__sub">{ev.retrieved_at}</div>
                {ev.excerpt && <div style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 6, whiteSpace: 'pre-wrap' }}>{ev.excerpt}</div>}
              </div>
            </div>
          ))}
        </div>
      )}

      {NEEDS_CONNECTOR.has(result.readiness_status) && result.route_key && (
        <div style={{ marginTop: 18 }}>
          <ConnectorBuild client={client}
            route={{ route_key: result.route_key,
                     destination: result.normalized_input?.destination || result.destination || '',
                     visa_type: result.normalized_input?.visa_category || 'tourist',
                     portal_evidence: result.portal_evidence || {} }} />
        </div>
      )}

      <div style={{ display: 'flex', gap: 10, marginTop: 20 }}>
        <button className="btn btn--sm btn--ghost" onClick={onEdit}>{t('start.editAnswers')}</button>
        <button className="btn btn--sm btn--ghost" onClick={onNew}>{t('start.resume.new')}</button>
      </div>

      <div style={{ marginTop: 18, paddingTop: 12, borderTop: '1px solid var(--line)' }}>
        <div style={{ fontSize: 11.5, color: 'var(--muted-2)' }}>
          {(result.snapshot?.date || '')} · {t('snapshot.disclaimer')}
        </div>
        {dateHonesty?.label && (
          <div style={{ fontSize: 12.5, fontWeight: 700, marginTop: 6 }} data-testid="date-honesty">
            {dateHonesty.label}
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// On-demand research progress panel. Shown when resolve auto-started a focused
// research job for a missing/incomplete route. Polls the job every 2.5s until
// a terminal status, then renders the honest outcome — the normal readiness
// result panel when a resolution exists, an honest stopped/review state
// otherwise. Requirements are NEVER invented in any state.
const RESEARCH_POLL_MS = 2500
const RESEARCH_STALL_MS = 60000

function ResearchPanel({ client, t, intakeId, initialJob, onEdit, onNew }) {
  const [job, setJob] = useState(initialJob)
  const [pollError, setPollError] = useState(null)
  const resumedOnce = useRef(false)

  useEffect(() => {
    let stopped = false
    let timer = null
    async function tick() {
      try {
        const j = await client.getResearchJob(initialJob.id)
        if (stopped) return
        setJob(j); setPollError(null)
        if (researchTerminal(j.status)) {
          clearResearchJobId(intakeId) // done — nothing left to resume
          return
        }
        maybeResume(j)
      } catch (e) {
        if (stopped) return
        setPollError({ message: e.message }) // transient: keep polling
      }
      timer = setTimeout(tick, RESEARCH_POLL_MS)
    }
    // Re-drive a stalled queued/running job at most once per panel mount:
    // only when the newest progress entry is older than 60s.
    function maybeResume(j) {
      if (resumedOnce.current) return
      const at = newestProgressAt(j)
      if (!at) return
      const age = Date.now() - Date.parse(at)
      if (Number.isFinite(age) && age > RESEARCH_STALL_MS) {
        resumedOnce.current = true
        client.resumeResearchJob(initialJob.id).catch(() => {})
      }
    }
    tick()
    return () => { stopped = true; clearTimeout(timer) }
  }, [initialJob.id])

  const status = job?.status
  const statusMeta = researchStatusMeta(status)
  const terminal = researchTerminal(status)
  const result = job?.result && typeof job.result === 'object' ? job.result : null
  const resolution = result?.resolution && typeof result.resolution === 'object' ? result.resolution : null
  const dateHonesty = result?.date_honesty && typeof result.date_honesty === 'object' ? result.date_honesty : null

  // Honest terminal notes: review-task note for incomplete/timed-out,
  // disagreement note for conflicted, generic stopped note otherwise.
  const terminalNoteKey =
    status === 'research_incomplete' || status === 'timed_out' ? 'research.reviewNote'
      : status === 'conflicted' ? 'research.conflictNote'
        : status === 'complete' ? null : 'research.stoppedNote'

  if (terminal && resolution) {
    return (
      <div>
        {terminalNoteKey && (
          <div className="card card--soft" style={{ padding: '12px 16px', marginBottom: 14 }} data-testid="research-terminal-note">
            <div style={{ fontSize: 13, fontWeight: 600 }}>{t(statusMeta.i18nKey)}</div>
            <div style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 3 }}>{t(terminalNoteKey)}</div>
          </div>
        )}
        <ResultPanel client={client} t={t} result={resolution} dateHonesty={dateHonesty}
          onEdit={onEdit} onNew={onNew} />
      </div>
    )
  }

  if (terminal) {
    return (
      <div className="card" style={{ padding: 24 }} data-testid="research-terminal">
        <div className="eyebrow">{t('research.title')}</div>
        <div style={{ borderRadius: 12, padding: '18px 20px', marginBottom: 16, ...TONE_STYLES[statusMeta.tone] }}
          data-tone={statusMeta.tone}>
          <div style={{ fontSize: 21, fontWeight: 700 }}>{t(statusMeta.i18nKey)}</div>
        </div>
        <p className="prose" style={{ fontSize: 13.5, marginBottom: 18 }}>
          {t(terminalNoteKey || 'research.stoppedNote')}
        </p>
        <div style={{ display: 'flex', gap: 10 }}>
          <button className="btn btn--sm btn--ghost" onClick={onEdit}>{t('start.editAnswers')}</button>
          <button className="btn btn--sm btn--ghost" onClick={onNew}>{t('start.resume.new')}</button>
        </div>
      </div>
    )
  }

  // ---- live progress -------------------------------------------------------
  const stageMeta = researchStageMeta(job?.stage)
  const progress = Array.isArray(job?.progress) ? job.progress : []
  const newest = progress.length ? progress[progress.length - 1] : null
  const counters = job?.counters && typeof job.counters === 'object' ? job.counters : {}
  const hasCounters = counters.pages_fetched != null || counters.gov_candidates != null

  return (
    <div className="card" style={{ padding: 24 }} data-testid="research-progress">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
        <div>
          <div className="eyebrow">{t('result.title')}</div>
          <div style={{ fontSize: 21, fontWeight: 700, marginTop: 2 }}>{t('research.title')}</div>
        </div>
        <span className="chip">{t(statusMeta.i18nKey)}</span>
      </div>
      <p className="prose" style={{ fontSize: 13.5, margin: '10px 0 16px' }}>{t('research.sub')}</p>

      <div style={{ marginBottom: 14 }} data-testid="research-steps">
        {RESEARCH_STEP_KEYS.map((key, i) => {
          const done = i < stageMeta.order
          const current = i === stageMeta.order
          return (
            <div key={key} data-current={current || undefined}
              style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0',
                opacity: done || current ? 1 : 0.45 }}>
              <span className={'sevbadge ' + (done ? 'sevbadge--ok' : 'sevbadge--mid')}>
                {done ? '✓' : current ? '…' : i + 1}
              </span>
              <span style={{ fontSize: 13.5, fontWeight: current ? 700 : 400 }}>{t(key)}</span>
            </div>
          )
        })}
      </div>

      {newest?.note && (
        <div style={{ fontSize: 12.5, color: 'var(--muted)', marginBottom: 6 }} data-testid="research-note">
          {newest.note}
        </div>
      )}
      {hasCounters && (
        <div style={{ fontSize: 12, color: 'var(--muted-2)' }} data-testid="research-counters">
          {t('research.counters', {
            pages: counters.pages_fetched ?? 0,
            sources: counters.gov_candidates ?? 0
          })}
        </div>
      )}
      {pollError && <ErrorNote error={pollError} />}
    </div>
  )
}
