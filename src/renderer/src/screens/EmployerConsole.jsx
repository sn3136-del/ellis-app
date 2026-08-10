// Employer console — the petitioner-side workspace (persona '#employer').
// Follows the SetupWizard visual pattern: sectioned cards of labeled fields.
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
import { createVisaClient } from '../lib/visaBackend.js'
import { newEmployerSession } from '../lib/visaSession.js'
import H1bPipeline from '../components/visa/H1bPipeline.jsx'

const LS_KEY = 'ellis.h1b.employer.cases'
function loadCases() {
  try { return JSON.parse(localStorage.getItem(LS_KEY) || '[]') } catch { return [] }
}
function saveCases(list) {
  try { localStorage.setItem(LS_KEY, JSON.stringify(list)) } catch { /* ignore */ }
}

const CASE_KINDS = ['extension', 'amendment', 'transfer', 'cap_exempt', 'cap_initial']

function Field({ label, value, onChange, placeholder }) {
  return (
    <div className="field">
      <label>{label}</label>
      <input className="input" value={value || ''} placeholder={placeholder}
             onChange={(e) => onChange(e.target.value)} />
    </div>
  )
}

// ---- Employer profile form (SetupWizard pattern) ---------------------------
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
    <section className="card" style={{ padding: 18, marginBottom: 14 }}
             data-testid="employer-profile-form">
      <div className="eyebrow">{t('h1b.employer.profileTitle')}</div>
      <div style={{ fontSize: 12.5, color: 'var(--muted)', margin: '4px 0 10px' }}>
        {t('h1b.employer.profileSub')}
      </div>
      {error && <ErrorNote error={error} />}
      <div className="grid grid-2" style={{ gap: 12 }}>
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
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 10 }}>
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
      const res = await client.h1bCreateCase({ ...f, locale: lang })
      onCreated({ id: res.case_id, case_kind: res.case_kind,
        full_name: f.beneficiary_full_name, createdAt: Date.now() })
    } catch (e) {
      setError({ message: e.message })
    }
    setBusy(false)
  }

  return (
    <section className="card" style={{ padding: 18, marginBottom: 14 }}
             data-testid="employer-new-case">
      <div className="eyebrow">{t('h1b.employer.newCaseTitle')}</div>
      {error && <ErrorNote error={error} />}
      <div className="grid grid-2" style={{ gap: 12 }}>
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
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 10 }}>
        <button className="btn" disabled={busy || !f.beneficiary_full_name || !f.beneficiary_email}
                onClick={create}>
          {busy ? t('h1b.employer.creating') : t('h1b.employer.create')}
        </button>
      </div>
    </section>
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
    <section className="card" style={{ padding: 18, marginTop: 14 }}
             data-testid="employer-job-answers">
      <div className="eyebrow">{t('h1b.job.title')}</div>
      <div style={{ fontSize: 12.5, color: 'var(--muted)', margin: '4px 0 10px' }}>
        {t('h1b.job.sub')}
      </div>
      {error && <ErrorNote error={error} />}
      <div className="grid grid-2" style={{ gap: 12 }}>
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
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 10 }}>
        <button className="btn" disabled={busy} onClick={save}>
          {busy ? t('h1b.employer.saving') : t('h1b.job.save')}
        </button>
      </div>
    </section>
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

  function addCase(c) {
    const next = [c, ...cases.filter((x) => x.id !== c.id)]
    setCases(next); saveCases(next)
  }

  if (openId) {
    const c = cases.find((x) => x.id === openId)
    return (
      <div className="page page--wide">
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, margin: '10px 0' }}>
          <button className="btn btn--sm btn--ghost" onClick={() => setOpenId(null)}>
            {t('h1b.employer.back')}
          </button>
          <div style={{ fontWeight: 700 }}>{c?.full_name || openId.slice(0, 8)}</div>
          {c?.case_kind && <span className="chip">{t(`h1b.kind.${c.case_kind}`)}</span>}
        </div>
        <H1bPipeline client={client} caseId={openId} persona="employer" />
        <JobAnswersForm t={t} client={client} caseId={openId} />
      </div>
    )
  }

  return (
    <div className="page page--wide" data-testid="employer-console">
      <div style={{ margin: '14px 0' }}>
        <div className="eyebrow">{t('h1b.employer.title')}</div>
        <div style={{ fontSize: 13, color: 'var(--muted)' }}>{t('h1b.employer.sub')}</div>
        {/* Attorney disclaimer on the console itself, not only in payloads. */}
        <div style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 6 }}
             data-testid="employer-disclaimer">
          {t('h1b.disclaimer')}
        </div>
      </div>

      <div className="grid grid-2" style={{ gap: 20, alignItems: 'start' }}>
        <div>
          <ProfileForm t={t} client={client} onSaved={loadProfiles} />
          <div className="eyebrow">{t('h1b.employer.savedProfiles')}</div>
          {profiles === null ? <Loading label={t('common.loading')} />
            : profiles.length === 0
            ? <div style={{ fontSize: 12.5, color: 'var(--muted)', margin: '6px 0' }}>
                {t('h1b.employer.noProfiles')}
              </div>
            : profiles.map((p) => (
                <div key={p.id} className="row">
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
          <div style={{ fontSize: 11.5, color: 'var(--muted)', margin: '4px 0 8px' }}>
            {t('h1b.employer.casesNote')}
          </div>
          {cases.length === 0
            ? <Empty title={t('h1b.employer.casesTitle')} sub={t('h1b.employer.noCases')} />
            : cases.map((c) => (
                <div key={c.id} className="row" style={{ cursor: 'pointer' }}
                     onClick={() => setOpenId(c.id)}>
                  <div className="row__main">
                    <div className="row__title">{c.full_name}</div>
                    <div className="row__sub">
                      {c.case_kind ? t(`h1b.kind.${c.case_kind}`) : 'H-1B'} · {c.id.slice(0, 8)}
                    </div>
                  </div>
                </div>
              ))}
        </div>
      </div>
    </div>
  )
}
