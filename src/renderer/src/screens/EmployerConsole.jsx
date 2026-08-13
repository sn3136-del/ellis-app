// Employer console — the petitioner-side workspace (persona '#employer').
// 2026-08-13 visual redesign: one accent illustration per section, text cut to
// labels, spacious cards, motion from theme.css's shared system.
// It holds the employer profile (FEIN / NAICS / signatory / parent company),
// starts H-1B petitions, embeds the per-case pipeline walkthrough, and writes
// the petitioner's own job/wage/worksite answers through the party-answers
// endpoint. Party doctrine carried here: only ANSWERED fields are ever written
// (an unanswered field is asked on the filing, never defaulted — these become
// statements on government forms), and every denied server action surfaces its
// honest reason. Cases are listed from localStorage because main.py has no
// org-scoped GET /cases yet (checked 2026-08-10) — same pattern as VisaConsole.
import { useEffect, useState } from 'react'
import { useToast, Loading, ErrorNote, Empty } from '../components/ui.jsx'
import { useLocale } from '../lib/locale.jsx'
import { createVisaClient, wageLevelView, socSuggestionsView } from '../lib/visaBackend.js'
import { newEmployerSession } from '../lib/visaSession.js'
import H1bPipeline from '../components/visa/H1bPipeline.jsx'
import FilingCockpit from '../components/visa/FilingCockpit.jsx'
import AppointmentCockpit from '../components/visa/AppointmentCockpit.jsx'
import {
  ShieldIllustration, FormFillIllustration, PipelineIllustration, WageIllustration
} from '../components/visa/Illustrations.jsx'

const LS_KEY = 'ellis.h1b.employer.cases'
function loadCases() {
  try { return JSON.parse(localStorage.getItem(LS_KEY) || '[]') } catch { return [] }
}
function saveCases(list) {
  try { localStorage.setItem(LS_KEY, JSON.stringify(list)) } catch { /* ignore */ }
}

const CASE_KINDS = ['extension', 'amendment', 'transfer', 'cap_exempt', 'cap_initial']

const NAVY = 'var(--trip-navy, #0f294d)'
const GRAY = 'var(--trip-gray, #8592a6)'

function Field({ label, value, onChange, placeholder }) {
  return (
    <div className="field">
      <label>{label}</label>
      <input className="input" value={value || ''} placeholder={placeholder}
             onChange={(e) => onChange(e.target.value)} />
    </div>
  )
}

// A section header: one illustration, a few words, nothing else.
function IlloHeader({ art, title, sub }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 18, marginBottom: 18 }}>
      {art}
      <div>
        <div style={{ fontSize: 16, fontWeight: 800, color: NAVY }}>{title}</div>
        {sub && <div style={{ fontSize: 12.5, color: GRAY, marginTop: 3 }}>{sub}</div>}
      </div>
    </div>
  )
}

// A quiet three-stop dot trail for case cards: prepare → file → done, drawn
// with plain divs (the artwork itself lives in Illustrations.jsx). Neutral by
// design — it names the road, it never claims where this case stands on it.
function DotTrail() {
  const dot = { width: 8, height: 8, borderRadius: '50%', background: '#c3d6f2' }
  const dash = { flex: 1, height: 2, borderRadius: 2,
                 background: 'repeating-linear-gradient(90deg, #c3d6f2 0 6px, transparent 6px 12px)' }
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, width: 92 }} aria-hidden="true">
      <span style={{ ...dot, background: 'var(--trip-blue, #2681ff)' }} />
      <span style={dash} /><span style={dot} /><span style={dash} /><span style={dot} />
    </div>
  )
}

// ---- Employer profile form (one spacious card, no per-field prose) ----------
function ProfileForm({ t, client, onSaved }) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [f, setF] = useState({
    legal_name: '', trade_name: '', fein: '', naics_code: '',
    address_line1: '', address_line2: '', city: '', state: '', postal_code: '',
    phone: '', signatory_name: '', signatory_title: '', signatory_email: '',
    signatory_phone: '', parent_company_name: '', parent_company_country: ''
  })
  const set = (k) => (v) => setF((p) => ({ ...p, [k]: v }))

  async function save() {
    setBusy(true); setError(null)
    try {
      const res = await client.h1bCreateEmployerProfile(f)
      toast(t('h1b.employer.profileSaved'))
      onSaved(res.employer_profile_id)
    } catch (e) {
      setError({ message: e.message })
    }
    setBusy(false)
  }

  return (
    <section className="card anim-rise-1" style={{ padding: 28, borderRadius: 20, marginBottom: 24 }}
             data-testid="employer-profile-form">
      <IlloHeader art={<FormFillIllustration size={92} />}
                  title={t('h1b.employer.profileTitle')}
                  sub={t('h1b.employer.profileSub')} />
      {error && <ErrorNote error={error} />}
      <div className="grid grid-2" style={{ gap: '14px 18px' }}>
        <Field label={t('h1b.employer.legalName')} value={f.legal_name} onChange={set('legal_name')} />
        <Field label={t('h1b.employer.tradeName')} value={f.trade_name} onChange={set('trade_name')} />
        <Field label={t('h1b.employer.fein')} value={f.fein} onChange={set('fein')}
               placeholder="12-3456789" />
        <Field label={t('h1b.employer.naics')} value={f.naics_code} onChange={set('naics_code')} />
        <Field label={t('h1b.employer.address1')} value={f.address_line1} onChange={set('address_line1')} />
        <Field label={t('h1b.employer.address2')} value={f.address_line2} onChange={set('address_line2')} />
        <Field label={t('h1b.employer.city')} value={f.city} onChange={set('city')} />
        <Field label={t('h1b.employer.state')} value={f.state} onChange={set('state')} />
        <Field label={t('h1b.employer.postal')} value={f.postal_code} onChange={set('postal_code')} />
        <Field label={t('h1b.employer.phone')} value={f.phone} onChange={set('phone')} />
        <Field label={t('h1b.employer.signatoryName')} value={f.signatory_name} onChange={set('signatory_name')} />
        <Field label={t('h1b.employer.signatoryTitle')} value={f.signatory_title} onChange={set('signatory_title')} />
        <Field label={t('h1b.employer.signatoryEmail')} value={f.signatory_email} onChange={set('signatory_email')} />
        <Field label={t('h1b.employer.signatoryPhone')} value={f.signatory_phone} onChange={set('signatory_phone')} />
        <Field label={t('h1b.employer.parentCompany')} value={f.parent_company_name} onChange={set('parent_company_name')} />
        <Field label={t('h1b.employer.parentCountry')} value={f.parent_company_country} onChange={set('parent_company_country')} />
      </div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 18 }}>
        <button className="btn" disabled={busy} onClick={save}>
          {busy ? t('h1b.employer.saving') : t('h1b.employer.saveProfile')}
        </button>
      </div>
    </section>
  )
}

// ---- New petition form -----------------------------------------------------
function NewCaseForm({ t, client, profiles, onCreated }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [f, setF] = useState({
    case_kind: 'extension', beneficiary_full_name: '', beneficiary_email: '',
    employer_profile_id: ''
  })
  const set = (k, v) => setF((p) => ({ ...p, [k]: v }))
  const { lang } = useLocale()

  async function create() {
    setBusy(true); setError(null)
    try {
      // The console creates AS THE PETITIONER: the employer operates the
      // filing side; the worker's seat stays open until they join.
      const res = await client.h1bCreateCase({ ...f, locale: lang, acting_as: 'petitioner' })
      onCreated({ id: res.case_id, case_kind: res.case_kind,
        full_name: f.beneficiary_full_name, createdAt: Date.now() })
    } catch (e) {
      setError({ message: e.message })
    }
    setBusy(false)
  }

  return (
    <section className="card anim-rise-1" style={{ padding: 28, borderRadius: 20, marginBottom: 24 }}
             data-testid="employer-new-case">
      <IlloHeader art={<PipelineIllustration size={110} />}
                  title={t('h1b.employer.newCaseTitle')} />
      {error && <ErrorNote error={error} />}
      <div className="grid grid-2" style={{ gap: '14px 18px' }}>
        <div className="field">
          <label>{t('h1b.employer.caseKind')}</label>
          <select className="select" value={f.case_kind}
                  onChange={(e) => set('case_kind', e.target.value)}>
            {CASE_KINDS.map((k) => <option key={k} value={k}>{t(`h1b.kind.${k}`)}</option>)}
          </select>
        </div>
        <div className="field">
          <label>{t('h1b.employer.profilePick')}</label>
          <select className="select" value={f.employer_profile_id}
                  onChange={(e) => set('employer_profile_id', e.target.value)}>
            <option value="">—</option>
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.legal_name}{p.fein_last4 ? ` · …${p.fein_last4}` : ''}
              </option>
            ))}
          </select>
        </div>
        <Field label={t('h1b.employer.beneficiaryName')} value={f.beneficiary_full_name}
               onChange={(v) => set('beneficiary_full_name', v)} />
        <Field label={t('h1b.employer.beneficiaryEmail')} value={f.beneficiary_email}
               onChange={(v) => set('beneficiary_email', v)} />
      </div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 18 }}>
        <button className="btn" disabled={busy || !f.beneficiary_full_name || !f.beneficiary_email}
                onClick={create}>
          {busy ? t('h1b.employer.creating') : t('h1b.employer.create')}
        </button>
      </div>
    </section>
  )
}

// Fill {placeholders} in an already-localized template (mirrors H1bPipeline's
// manual .replace pattern; t()'s own vars arg would also work).
function fill(str, vars) {
  let out = String(str == null ? '' : str)
  for (const [k, v] of Object.entries(vars)) out = out.split(`{${k}}`).join(String(v))
  return out
}
// US dollars, no cents — an OEWS yearly wage never carries meaningful cents.
function money(n) {
  return typeof n === 'number' && Number.isFinite(n)
    ? '$' + Math.round(n).toLocaleString('en-US') : ''
}
// Known structured caveat codes localize; anything else renders verbatim.
const WAGE_CAVEAT_CODES = ['geo_broadened', 'geo_statewide', 'geo_national',
                           'label_high', 'label_annual']

// ---- Check wage level (deterministic OEWS/OFLC computation) -----------------
// Reads the case's saved petitioner facts server-side and renders the computed
// Level I–IV, the four level wages, whether the offer meets prevailing, and the
// DOL caveats — prominently. Every number is a SUGGESTION: the wage level is a
// legal representation on the LCA/I-129, so the petitioner confirms and enters
// it; this panel never writes a value back into the filed answers.
function WageLevelCheck({ t, client, caseId }) {
  const [view, setView] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function check() {
    setBusy(true); setError(null)
    try {
      setView(wageLevelView(await client.h1bWageAnalysis(caseId)))
    } catch (e) {
      setError({ message: e.message })
    }
    setBusy(false)
  }

  return (
    <div style={{ marginTop: 28 }} data-testid="h1b-wage-check">
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12,
                    alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <WageIllustration size={64} />
          <div>
            <div style={{ fontWeight: 700, fontSize: 14, color: NAVY }}>{t('h1b.wage.title')}</div>
            <div style={{ fontSize: 12, color: GRAY }}>{t('h1b.wage.sub')}</div>
          </div>
        </div>
        <button className="btn btn--sm btn--ghost" disabled={busy} onClick={check}>
          {busy ? t('h1b.wage.checking') : t('h1b.wage.check')}
        </button>
      </div>
      {error && <ErrorNote error={error} />}
      {view && !view.available && (
        <div className="card anim-rise" style={{ padding: 14, marginTop: 12, borderRadius: 16, fontSize: 12.5 }}>
          <div>{t('h1b.wage.unavailable')}</div>
          {view.reason && (
            <div style={{ color: GRAY, marginTop: 4 }}>{view.reason}</div>
          )}
        </div>
      )}
      {view && view.available && (
        <div className="card anim-rise" style={{ padding: 18, marginTop: 12, borderRadius: 16 }}>
          {(view.socCode || view.socTitle) && (
            <div style={{ fontSize: 12.5 }}>
              <span style={{ color: GRAY }}>{t('h1b.wage.socLabel')}: </span>
              {[view.socCode, view.socTitle].filter(Boolean).join(' · ')}
            </div>
          )}
          {view.areaName && (
            <div style={{ fontSize: 12.5, marginTop: 2 }}>
              <span style={{ color: GRAY }}>{t('h1b.wage.areaLabel')}: </span>
              {view.areaName}
            </div>
          )}
          {/* The four prevailing-wage tiers. */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10,
                        margin: '14px 0' }} data-testid="h1b-wage-levels">
            {view.levels.map((lv) => {
              const isComputed = view.computedLevel === lv.roman
              return (
                <div key={lv.level} className="card"
                     style={{ padding: '12px 10px', textAlign: 'center', borderRadius: 14,
                              border: isComputed ? '2px solid var(--trip-blue, #2681ff)' : '2px solid transparent',
                              background: isComputed ? '#eef4ff' : '#f8fafd' }}>
                  <div style={{ fontSize: 11, color: GRAY }}>
                    {t('h1b.wage.levelWord')} {lv.roman}
                  </div>
                  <div style={{ fontWeight: 800, fontSize: 14, color: NAVY }}>{money(lv.wage)}</div>
                  <div style={{ fontSize: 10.5, color: GRAY }}>{t('h1b.wage.perYear')}</div>
                </div>
              )
            })}
          </div>
          {/* Offer vs prevailing. */}
          {view.offeredWage != null && (
            <div style={{ fontSize: 12.5 }}>
              <span style={{ color: GRAY }}>{t('h1b.wage.offered')}: </span>
              {money(view.offeredWage)}
              {view.computedLevel && (
                <span style={{ marginLeft: 8 }}>
                  · {fill(t('h1b.wage.clears'), { level: view.computedLevel })}
                </span>
              )}
            </div>
          )}
          <div style={{ marginTop: 8 }} data-testid="h1b-wage-meets">
            <span className={view.meetsPrevailing === true ? 'chip-icon chip-icon--ok'
                    : view.meetsPrevailing === false ? 'chip-icon chip-icon--crit' : 'chip-icon'}
                  style={{ whiteSpace: 'normal' }}>
              {view.meetsPrevailing === true ? t('h1b.wage.meets')
                : view.meetsPrevailing === false ? t('h1b.wage.belowPrevailing')
                : t('h1b.wage.meetsUnknown')}
            </span>
          </div>
          {/* Caveats — prominent, warning-colored. */}
          {view.caveats.length > 0 && (
            <div style={{ marginTop: 12 }} data-testid="h1b-wage-caveats">
              <div style={{ fontSize: 12, fontWeight: 700, color: '#c77700' }}>
                {t('h1b.wage.caveatsTitle')}
              </div>
              <ul style={{ margin: '4px 0 0 18px', fontSize: 12, color: '#c77700' }}>
                {view.caveats.map((c, i) => (
                  <li key={i}>
                    {c.code && WAGE_CAVEAT_CODES.includes(c.code)
                      ? t(`h1b.wage.caveat.${c.code}`) : (c.text || '')}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {/* Standing honesty note + provenance. */}
          <div style={{ fontSize: 11.5, color: GRAY, marginTop: 12 }}
               data-testid="h1b-wage-confirm-note">
            {t('h1b.wage.confirmNote')}
          </div>
          {(view.source || view.asOf) && (
            <div style={{ fontSize: 11, color: GRAY, marginTop: 4 }}>
              {fill(t('h1b.wage.sourceLine'),
                    { source: view.source || '—', asOf: view.asOf || '—' })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ---- Suggest SOC / NAICS codes (CDC NIOCCS classifier) ---------------------
// occupation_text is the job title above; industry_text is a company/industry
// description the petitioner types here. Ranked SOC + NAICS suggestions render
// with their probabilities, and EVERY suggestion carries the confirm-required
// note — the SOC code is a legal representation Ellis only suggests, so the
// petitioner types the confirmed value into the field above themselves. Nothing
// here auto-fills a filed answer.
function SocSuggest({ t, client, caseId, jobTitle }) {
  const [industryText, setIndustryText] = useState('')
  const [view, setView] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const hasJobTitle = String(jobTitle || '').trim().length > 0

  async function suggest() {
    setBusy(true); setError(null)
    try {
      const res = await client.h1bClassifyOccupation(caseId, {
        occupation_text: String(jobTitle || '').trim(),
        industry_text: industryText.trim()
      })
      setView(socSuggestionsView(res))
    } catch (e) {
      setError({ message: e.message })
    }
    setBusy(false)
  }

  function Suggestion({ s }) {
    const pct = s.probability != null ? Math.round(s.probability * 100) : null
    return (
      <div className="row">
        <div className="row__main">
          <div className="row__title" style={{ fontSize: 13 }}>
            {[s.code, s.title].filter(Boolean).join(' · ')}
          </div>
          <div className="row__sub">
            {t(`h1b.soc.confidence.${s.confidence}`)}
            {pct != null ? ` · ${fill(t('h1b.soc.probability'), { pct })}` : ''}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div style={{ marginTop: 28 }} data-testid="h1b-soc-suggest">
      <div style={{ fontWeight: 700, fontSize: 14, color: NAVY }}>{t('h1b.soc.title')}</div>
      <div style={{ fontSize: 12, color: GRAY }}>{t('h1b.soc.sub')}</div>
      <div className="field" style={{ marginTop: 10 }}>
        <label>{t('h1b.soc.industryInput')}</label>
        <input className="input" value={industryText}
               placeholder={t('h1b.soc.industryPlaceholder')}
               onChange={(e) => setIndustryText(e.target.value)} />
      </div>
      {!hasJobTitle && (
        <div style={{ fontSize: 12, color: GRAY, marginBottom: 6 }}>
          {t('h1b.soc.needJobTitle')}
        </div>
      )}
      {error && <ErrorNote error={error} />}
      <button className="btn btn--sm btn--ghost" disabled={busy || !hasJobTitle}
              onClick={suggest}>
        {busy ? t('h1b.soc.suggesting') : t('h1b.soc.suggest')}
      </button>
      {view && !view.available && (
        <div className="card anim-rise" style={{ padding: 14, marginTop: 12, borderRadius: 16, fontSize: 12.5 }}>
          {view.reason || t('h1b.soc.unavailable')}
        </div>
      )}
      {view && view.available && (
        <div style={{ marginTop: 12 }} className="anim-rise">
          {/* Confirm-required note FIRST — it is the point of the whole panel. */}
          <div className="card" style={{ padding: 14, borderRadius: 16, border: '1px solid #c77700' }}
               data-testid="h1b-soc-confirm-note">
            <div style={{ fontSize: 12.5, color: '#c77700', fontWeight: 700 }}>
              {t('h1b.soc.confirmNote')}
            </div>
            {view.lowConfidence && (
              <div style={{ fontSize: 12, color: '#d33', marginTop: 6 }}
                   data-testid="h1b-soc-lowconf">
                {t('h1b.soc.lowConfidenceNote')}
              </div>
            )}
          </div>
          {view.occupation.length > 0 && (
            <div className="card" style={{ padding: 14, marginTop: 10, borderRadius: 16 }}>
              <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 4 }}>
                {t('h1b.soc.occupationTitle')}
              </div>
              {view.occupation.map((s, i) => <Suggestion key={i} s={s} />)}
            </div>
          )}
          {view.industry.length > 0 && (
            <div className="card" style={{ padding: 14, marginTop: 10, borderRadius: 16 }}>
              <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 4 }}>
                {t('h1b.soc.industryTitle')}
              </div>
              {view.industry.map((s, i) => <Suggestion key={i} s={s} />)}
            </div>
          )}
          <div style={{ fontSize: 11.5, color: GRAY, marginTop: 10 }}>
            {t('h1b.soc.sourceCaveat')}
          </div>
          {(view.source || view.asOf) && (
            <div style={{ fontSize: 11, color: GRAY, marginTop: 4 }}>
              {fill(t('h1b.soc.sourceLine'),
                    { source: view.source || '—', asOf: view.asOf || '—' })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ---- Petitioner job/wage/worksite answers ----------------------------------
// Writes through POST /h1b/cases/{id}/party/petitioner/answers. ONLY answered
// fields are sent: these values become statements on the ETA-9035/I-129, and
// the doctrine forbids defaulting an answer that becomes a government-form
// statement. full_time_position is three-state for exactly that reason.
function JobAnswersForm({ t, client, caseId }) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [f, setF] = useState({
    job_title: '', soc_code: '', soc_title: '', wage_offer: '', wage_offer_unit: '',
    prevailing_wage: '', wage_level: '', employment_start_date: '',
    employment_end_date: '', full_time_position: '', worksite_address_line1: '',
    worksite_city: '', worksite_state: '', worksite_postal_code: ''
  })
  const set = (k) => (v) => setF((p) => ({ ...p, [k]: v }))

  async function save() {
    setBusy(true); setError(null)
    try {
      const answers = {}
      for (const [k, v] of Object.entries(f)) {
        const val = String(v == null ? '' : v).trim()
        if (!val) continue                    // unanswered stays unasked — never defaulted
        answers[k] = k === 'full_time_position' ? (val === 'yes') : val
      }
      await client.h1bPartyAnswers(caseId, 'petitioner', answers)
      toast(t('h1b.job.saved'))
    } catch (e) {
      setError({ message: e.message })
    }
    setBusy(false)
  }

  return (
    <section className="card anim-rise-2" style={{ padding: 28, borderRadius: 20, marginTop: 24 }}
             data-testid="employer-job-answers">
      <IlloHeader art={<FormFillIllustration size={80} />}
                  title={t('h1b.job.title')} sub={t('h1b.job.sub')} />
      {error && <ErrorNote error={error} />}
      <div className="grid grid-2" style={{ gap: '14px 18px' }}>
        <Field label={t('h1b.job.jobTitle')} value={f.job_title} onChange={set('job_title')} />
        <Field label={t('h1b.job.socCode')} value={f.soc_code} onChange={set('soc_code')}
               placeholder="15-1252" />
        <Field label={t('h1b.job.socTitle')} value={f.soc_title} onChange={set('soc_title')} />
        <Field label={t('h1b.job.wageOffer')} value={f.wage_offer} onChange={set('wage_offer')} />
        <div className="field">
          <label>{t('h1b.job.wageUnit')}</label>
          <select className="select" value={f.wage_offer_unit}
                  onChange={(e) => set('wage_offer_unit')(e.target.value)}>
            <option value="">{t('h1b.job.unanswered')}</option>
            <option value="year">{t('h1b.job.unit.year')}</option>
            <option value="hour">{t('h1b.job.unit.hour')}</option>
          </select>
        </div>
        <Field label={t('h1b.job.prevailingWage')} value={f.prevailing_wage}
               onChange={set('prevailing_wage')} />
        <div className="field">
          <label>{t('h1b.job.wageLevel')}</label>
          <select className="select" value={f.wage_level}
                  onChange={(e) => set('wage_level')(e.target.value)}>
            <option value="">{t('h1b.job.unanswered')}</option>
            <option value="I">I</option><option value="II">II</option>
            <option value="III">III</option><option value="IV">IV</option>
          </select>
        </div>
        <div className="field">
          <label>{t('h1b.job.fullTime')}</label>
          <select className="select" value={f.full_time_position}
                  onChange={(e) => set('full_time_position')(e.target.value)}
                  data-testid="fulltime-select">
            <option value="">{t('h1b.job.unanswered')}</option>
            <option value="yes">{t('h1b.job.yes')}</option>
            <option value="no">{t('h1b.job.no')}</option>
          </select>
        </div>
        <Field label={t('h1b.job.startDate')} value={f.employment_start_date}
               onChange={set('employment_start_date')} placeholder="MM/DD/YYYY" />
        <Field label={t('h1b.job.endDate')} value={f.employment_end_date}
               onChange={set('employment_end_date')} placeholder="MM/DD/YYYY" />
        <Field label={t('h1b.job.worksite1')} value={f.worksite_address_line1}
               onChange={set('worksite_address_line1')} />
        <Field label={t('h1b.job.worksiteCity')} value={f.worksite_city}
               onChange={set('worksite_city')} />
        <Field label={t('h1b.job.worksiteState')} value={f.worksite_state}
               onChange={set('worksite_state')} />
        <Field label={t('h1b.job.worksitePostal')} value={f.worksite_postal_code}
               onChange={set('worksite_postal_code')} />
      </div>
      {/* Official-data helpers. Both are suggestion surfaces: they read/compute
          from authoritative sources but write nothing back into the answers
          above — the petitioner confirms and types every filed value. Wage
          analysis runs over the SAVED facts, so it comes after Save. */}
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 18 }}>
        <button className="btn" disabled={busy} onClick={save}>
          {busy ? t('h1b.employer.saving') : t('h1b.job.save')}
        </button>
      </div>
      <SocSuggest t={t} client={client} caseId={caseId} jobTitle={f.job_title} />
      <WageLevelCheck t={t} client={client} caseId={caseId} />
    </section>
  )
}

// ---- Petitioner cockpits ---------------------------------------------------
// The two cockpit surfaces the EMPLOYER owns on an open case:
//   * the public access file (20 CFR 655.760) — the employer's own file, which
//     is never filed with anyone but must be complete and available;
//   * the worker's consular appointment — triage and pre-stage, so the employer
//     can see what remains without ever being able to book it.
// Per-form filing cockpits live on their own step in the pipeline above; this
// section is what has no step of its own.
function PetitionerCockpits({ t, client, caseId }) {
  return (
    <section className="card anim-rise-3" style={{ padding: 28, borderRadius: 20, marginTop: 24 }}
             data-testid="employer-cockpits">
      <div style={{ fontSize: 16, fontWeight: 800, color: NAVY }}>{t('cockpit.employer.title')}</div>
      <div style={{ fontSize: 12.5, color: GRAY, marginTop: 3 }}>
        {t('cockpit.employer.sub')}
      </div>
      <FilingCockpit client={client} caseId={caseId} kind="paf" routeKey="paf"
                     title={t('cockpit.paf.title')} />
      <AppointmentCockpit client={client} caseId={caseId} />
    </section>
  )
}

// The attorney disclaimer, always visible: one compact line with a shield,
// the full text one tap away. Never deleted, never buried (owner decision).
function DisclaimerStrip({ t }) {
  return (
    <details className="card anim-rise-1" data-testid="employer-disclaimer"
             style={{ padding: '10px 18px', borderRadius: 18, marginTop: 14,
                      display: 'inline-block' }}>
      <summary style={{ display: 'inline-flex', alignItems: 'center', gap: 10,
                        cursor: 'pointer', listStyle: 'none' }}>
        <ShieldIllustration size={30} />
        <span style={{ fontSize: 12.5, fontWeight: 600, color: NAVY }}>
          {t('h1b.disclaimerShort')}
        </span>
      </summary>
      <div style={{ fontSize: 12, color: GRAY, marginTop: 8, maxWidth: 640,
                    whiteSpace: 'normal' }}>
        {t('h1b.disclaimer')}
      </div>
    </details>
  )
}

export default function EmployerConsole() {
  const { t } = useLocale()
  const [session] = useState(() => newEmployerSession())
  const [client] = useState(() => createVisaClient(session))
  const [profiles, setProfiles] = useState(null)
  const [cases, setCases] = useState(loadCases)
  const [openId, setOpenId] = useState(null)

  async function loadProfiles() {
    try {
      const res = await client.h1bEmployerProfiles()
      setProfiles(res.profiles || [])
    } catch {
      setProfiles([])
    }
  }
  useEffect(() => { loadProfiles() }, [])

  // The open case registers itself with Ask Ellis via H1bPipeline's own
  // setActiveH1bCase effect — no double bookkeeping here.

  // Self-heal older cases: before the acting_as fix, the console's own cases
  // left the petitioner seat unbound (and bound the creator as the WORKER),
  // so the employer was addressed as the employee. Claiming is idempotent,
  // refuses seats owned by someone else, and simply logs on failure.
  useEffect(() => {
    if (!openId) return
    client.h1bClaimParty(openId, 'petitioner').catch(() => {})
  }, [openId])

  function addCase(c) {
    const next = [c, ...cases.filter((x) => x.id !== c.id)]
    setCases(next); saveCases(next)
  }

  if (openId) {
    const c = cases.find((x) => x.id === openId)
    return (
      <div className="page page--wide">
        <div className="anim-rise"
             style={{ display: 'flex', alignItems: 'center', gap: 14, margin: '22px 0 18px' }}>
          <button className="btn btn--sm btn--ghost" onClick={() => setOpenId(null)}>
            {t('h1b.employer.back')}
          </button>
          <div style={{ fontWeight: 800, fontSize: 20, color: NAVY }}>
            {c?.full_name || openId.slice(0, 8)}
          </div>
          {c?.case_kind && <span className="chip">{t(`h1b.kind.${c.case_kind}`)}</span>}
        </div>
        <H1bPipeline client={client} caseId={openId} persona="employer" />
        <JobAnswersForm t={t} client={client} caseId={openId} />
        <PetitionerCockpits t={t} client={client} caseId={openId} />
      </div>
    )
  }

  return (
    <div className="page page--wide" data-testid="employer-console">
      <div className="anim-rise" style={{ margin: '30px 0 34px' }}>
        <div style={{ fontSize: 27, fontWeight: 800, color: NAVY, letterSpacing: '-0.3px' }}>
          {t('h1b.employer.title')}
        </div>
        <div style={{ fontSize: 13.5, color: GRAY, marginTop: 4 }}>{t('h1b.employer.sub')}</div>
        {/* Attorney disclaimer on the console itself, not only in payloads. */}
        <DisclaimerStrip t={t} />
      </div>

      <div className="grid grid-2" style={{ gap: 28, alignItems: 'start' }}>
        <div>
          <ProfileForm t={t} client={client} onSaved={loadProfiles} />
          <div className="eyebrow">{t('h1b.employer.savedProfiles')}</div>
          {profiles === null ? <Loading label={t('common.loading')} />
            : profiles.length === 0
            ? <div style={{ fontSize: 12.5, color: GRAY, margin: '6px 0' }}>
                {t('h1b.employer.noProfiles')}
              </div>
            : profiles.map((p) => (
                <div key={p.id} className="row card-hover">
                  <div className="row__main">
                    <div className="row__title">{p.legal_name}</div>
                    <div className="row__sub">
                      {p.fein_last4 ? `FEIN …${p.fein_last4}` : '—'}
                      {p.signatory_name ? ` · ${p.signatory_name}` : ''}
                    </div>
                  </div>
                </div>
              ))}
        </div>

        <div>
          <NewCaseForm t={t} client={client} profiles={profiles || []}
            onCreated={(c) => { addCase(c); setOpenId(c.id) }} />
          <div className="eyebrow">{t('h1b.employer.casesTitle')}</div>
          <div style={{ fontSize: 11.5, color: GRAY, margin: '4px 0 10px' }}>
            {t('h1b.employer.casesNote')}
          </div>
          {cases.length === 0
            ? <Empty title={t('h1b.employer.casesTitle')} sub={t('h1b.employer.noCases')} />
            : cases.map((c, i) => (
                <div key={c.id} className={`card card-hover anim-rise-${Math.min(i + 1, 3)}`}
                     style={{ padding: '16px 20px', borderRadius: 18, marginBottom: 12,
                              cursor: 'pointer', display: 'flex', alignItems: 'center',
                              justifyContent: 'space-between', gap: 14 }}
                     onClick={() => setOpenId(c.id)}>
                  <div>
                    <div style={{ fontWeight: 700, fontSize: 14.5, color: NAVY }}>{c.full_name}</div>
                    <div style={{ fontSize: 12, color: GRAY, marginTop: 3 }}>
                      {c.case_kind ? t(`h1b.kind.${c.case_kind}`) : 'H-1B'} · {c.id.slice(0, 8)}
                    </div>
                  </div>
                  <DotTrail />
                </div>
              ))}
        </div>
      </div>
    </div>
  )
}
