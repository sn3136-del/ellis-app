// The appointment cockpit — docs/APPOINTMENTS_DESIGN.md, one surface for both
// routes, honest about the human's remaining act.
//
//   1. Eligibility triage — US waiver / EVUS, and the Schengen 59-month VIS
//      fingerprint question: whether an in-person act is required at all.
//   2. Pre-stage — everything Ellis has prepared, everything still missing.
//      Nothing here has been filed, paid, signed, or booked.
//   3. The group-request roster — the consulate's own channel for 10 or more
//      travelling together. Ellis assembles and checks it; the HUMAN
//      coordinator submits it and books each member.
//   4. Availability — published wait times and the official places to look.
//
// The line this surface exists to hold: Ellis never searches for or books an
// appointment slot. The penalty for automated slot search falls on the
// TRAVELLER — appointments cancelled and visas revoked — so the note is
// rendered unconditionally at the top of the surface, before any payload
// arrives and whether or not one ever does.
import { useState } from 'react'
import { useLocale } from '../../lib/locale.jsx'
import { ErrorNote } from '../ui.jsx'
import {
  appointmentTriageView, appointmentPrestageView, groupRosterView,
  appointmentAvailabilityView, HUMAN_ACT_KEYS, GROUP_MIN_MEMBERS
} from '../../lib/visaBackend.js'

const GROUP_KINDS = ['tour_group', 'company', 'school', 'family']

// The standing compliance note. Its own component so it can be rendered first,
// unconditionally, on every state of this surface.
function NeverBooksNote({ t }) {
  return (
    <div className="card" style={{ padding: 12, marginTop: 8, fontSize: 12.5,
                                   borderColor: '#c77700' }}
         data-testid="appt-never-books">
      {t('appt.neverBooks')}
    </div>
  )
}

function HumanActs({ t, acts, testid = 'appt-human-acts' }) {
  if (!acts || acts.length === 0) return null
  return (
    <div style={{ marginTop: 8 }} data-testid={testid}>
      <div style={{ fontSize: 12, fontWeight: 600 }}>{t('appt.acts.title')}</div>
      <ul style={{ margin: '4px 0 0 18px', fontSize: 12.5 }}>
        {acts.map((a, i) => {
          const known = a.key && HUMAN_ACT_KEYS.includes(a.key)
          const label = known ? t(`cockpit.act.${a.key}`) : a.act
          if (!label) return null
          return (
            <li key={a.key || i}>
              {label}
              {a.who && (
                <span style={{ color: 'var(--muted)' }}> · {t('appt.acts.who', { who: a.who })}</span>
              )}
              {/* Non-delegable is the strongest line on this surface: not even
                  an accredited agent may do it. */}
              {a.nonDelegable === true && (
                <span style={{ color: '#c77700' }}> · {t('cockpit.acts.nonDelegable')}</span>
              )}
              {a.ellisDoes && (
                <div style={{ fontSize: 11.5, color: 'var(--muted)' }}>
                  {t('appt.acts.ellis', { what: a.ellisDoes })}
                </div>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}

// A tri-state verdict line: true / false / "Ellis cannot tell". The three are
// different answers and are never collapsed into two.
function Verdict({ t, value, yesKey, noKey, unknownKey, testid }) {
  const key = value === true ? yesKey : value === false ? noKey : unknownKey
  return (
    <div style={{ fontSize: 12.5, marginTop: 4,
                  color: value === null ? 'var(--muted)' : 'inherit' }}
         data-testid={testid}>
      {t(key)}
    </div>
  )
}

// ---- 1. Eligibility triage -------------------------------------------------
function TriagePanel({ t, lang, client, caseId }) {
  const [view, setView] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function run() {
    setBusy(true); setError(null)
    try {
      setView(appointmentTriageView(await client.appointmentTriage(caseId, lang)))
    } catch (e) {
      setError({ message: e.message })
    }
    setBusy(false)
  }

  return (
    <div style={{ marginTop: 14 }} data-testid="appt-triage">
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10,
                    alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ fontWeight: 600, fontSize: 13 }}>{t('appt.triage.title')}</div>
        <button className="btn btn--sm btn--ghost" disabled={busy} onClick={run}>
          {busy ? t('appt.triage.checking') : t('appt.triage.run')}
        </button>
      </div>
      {error && <ErrorNote error={error} />}
      {view && !view.available && (
        <div className="card" style={{ padding: 12, marginTop: 8, fontSize: 12.5 }}>
          <div>{t('appt.triage.unknown')}</div>
          {view.reason && <div style={{ color: 'var(--muted)', marginTop: 4 }}>{view.reason}</div>}
        </div>
      )}
      {view && view.available && (
        <div className="card" style={{ padding: 14, marginTop: 8 }}>
          {/* The server's own localized verdict sentence, first. */}
          {view.summary && (
            <div style={{ fontSize: 13, fontWeight: 600 }} data-testid="appt-summary">
              {view.summary}
            </div>
          )}
          <Verdict t={t} value={view.inPersonRequired} testid="appt-inperson"
                   yesKey="appt.triage.required" noKey="appt.triage.notRequired"
                   unknownKey="appt.triage.unknown" />
          {/* Schengen: the fingerprint gate itself, then the 59-month VIS
              question that decides whether the journey can sit inside the
              accredited-agent envelope. Two separate answers, kept separate. */}
          {view.route === 'schengen' && (
            <Verdict t={t} value={view.biometricsRequired} testid="appt-bio"
                     yesKey="appt.triage.bio.required" noKey="appt.triage.bio.notRequired"
                     unknownKey="appt.triage.bio.unknown" />
          )}
          <Verdict t={t} value={view.visBiometricsReusable} testid="appt-vis59"
                   yesKey="appt.triage.vis59.reuse" noKey="appt.triage.vis59.required"
                   unknownKey="appt.triage.vis59.unknown" />
          {/* US: interview waiver, and EVUS before travel on a 10-year visa. */}
          <Verdict t={t} value={view.interviewWaiverEligible} testid="appt-waiver"
                   yesKey="appt.triage.waiver.eligible" noKey="appt.triage.waiver.notEligible"
                   unknownKey="appt.triage.waiver.unknown" />
          <Verdict t={t} value={view.evusRequired} testid="appt-evus"
                   yesKey="appt.triage.evus.required" noKey="appt.triage.evus.notRequired"
                   unknownKey="appt.triage.evus.unknown" />
          {view.reasons.length > 0 && (
            <ul style={{ margin: '8px 0 0 18px', fontSize: 12, color: 'var(--muted)' }}>
              {view.reasons.map((r, i) => <li key={i}>{r}</li>)}
            </ul>
          )}
          {/* What would settle an unknown. The questions themselves, never a
              guess dressed as an answer. */}
          {view.openQuestions.length > 0 && (
            <div style={{ marginTop: 8 }} data-testid="appt-open-questions">
              <div style={{ fontSize: 12, fontWeight: 600 }}>{t('appt.triage.questions')}</div>
              <ul style={{ margin: '4px 0 0 18px', fontSize: 12.5 }}>
                {view.openQuestions.map((q, i) => (
                  <li key={i}>
                    {q.question}
                    {q.resolveWith && (
                      <div style={{ fontSize: 11.5, color: 'var(--muted)' }}>
                        {t('appt.triage.resolveWith', { how: q.resolveWith })}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <HumanActs t={t} acts={view.humanActs} testid="appt-triage-acts" />
          {view.disclaimer && (
            <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 8 }}>{view.disclaimer}</div>
          )}
        </div>
      )}
    </div>
  )
}

// ---- 2. Pre-stage readiness ------------------------------------------------
function PrestagePanel({ t, lang, client, caseId }) {
  const [view, setView] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function run() {
    setBusy(true); setError(null)
    try {
      setView(appointmentPrestageView(await client.appointmentPrestage(caseId, lang)))
    } catch (e) {
      setError({ message: e.message })
    }
    setBusy(false)
  }

  return (
    <div style={{ marginTop: 18 }} data-testid="appt-prestage">
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10,
                    alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ fontWeight: 600, fontSize: 13 }}>{t('appt.prestage.title')}</div>
        <button className="btn btn--sm btn--ghost" disabled={busy} onClick={run}>
          {busy ? t('cockpit.loading') : t('appt.prestage.load')}
        </button>
      </div>
      <div style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 4 }}
           data-testid="appt-nothing-done">
        {t('appt.prestage.nothingDone')}
      </div>
      {error && <ErrorNote error={error} />}
      {view && !view.available && (
        <div className="card" style={{ padding: 12, marginTop: 8, fontSize: 12.5 }}>
          <div>{t('appt.prestage.empty')}</div>
          {view.reason && <div style={{ color: 'var(--muted)', marginTop: 4 }}>{view.reason}</div>}
        </div>
      )}
      {view && view.available && (
        <div className="card" style={{ padding: 14, marginTop: 8 }}>
          {view.filledCount != null && view.totalCount != null && (
            <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 6 }}
                 data-testid="appt-readiness">
              {t('appt.prestage.readiness', { filled: view.filledCount,
                total: view.totalCount, missing: view.missingRequiredCount })}
            </div>
          )}
          <div style={{ fontSize: 12, fontWeight: 600 }}>{t('appt.prestage.ready')}</div>
          {view.prepared.length === 0 ? (
            <div style={{ fontSize: 12.5, color: 'var(--muted)' }}>{t('cockpit.prepared.empty')}</div>
          ) : (
            <ul style={{ margin: '4px 0 0 18px', fontSize: 12.5 }}>
              {view.prepared.map((p, i) => (
                <li key={p.key || i}>
                  {p.label}{p.value ? `: ${p.value}` : ''}
                  <span style={{ color: 'var(--muted)' }}>
                    {' '}· {p.sourceKnown
                      ? t('cockpit.prepared.source', { source: p.source })
                      : t('cockpit.prepared.sourceUnknown')}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <div style={{ fontSize: 12, fontWeight: 600, marginTop: 10 }}>
            {t('appt.prestage.missing')}
          </div>
          {view.missingCount === 0 ? (
            <div style={{ fontSize: 12.5, color: 'var(--muted)' }}>{t('cockpit.missing.none')}</div>
          ) : (
            <ul style={{ margin: '4px 0 0 18px', fontSize: 12.5 }}>
              {view.missing.map((m, i) => (
                <li key={m.key || i}>
                  {m.label}
                  <div style={{ fontSize: 11.5, color: 'var(--muted)' }}>
                    {t('cockpit.missing.input', { input: m.input })}
                  </div>
                </li>
              ))}
            </ul>
          )}
          {view.documents.length > 0 && (
            <div style={{ marginTop: 10 }} data-testid="appt-documents">
              <div style={{ fontSize: 12, fontWeight: 600 }}>
                {t('cockpit.prepared.documents')}
              </div>
              <ul style={{ margin: '4px 0 0 18px', fontSize: 12.5, color: 'var(--muted)' }}>
                {view.documents.map((d, i) => <li key={d.key || i}>{d.label}</li>)}
              </ul>
            </div>
          )}
          {/* The exact fee stack, with the official channel. Ellis computes the
              amount and names where to pay it; it never pays. */}
          {view.fees.length > 0 && (
            <div style={{ marginTop: 10 }} data-testid="appt-fees">
              <div style={{ fontSize: 12, fontWeight: 600 }}>{t('appt.prestage.feeTitle')}</div>
              <ul style={{ margin: '4px 0 0 18px', fontSize: 12.5 }}>
                {view.fees.map((f, i) => (
                  <li key={f.key || i}>
                    {t('appt.prestage.feeLine', {
                      label: f.label || '—',
                      amount: f.amount != null ? f.amount : '—',
                      currency: f.currency || ''
                    })}
                    {f.channels.length > 0 && (
                      <div style={{ fontSize: 11.5, color: 'var(--muted)' }}>
                        {t('appt.prestage.feeChannels', { channels: f.channels.join(', ') })}
                      </div>
                    )}
                    {/* The server's own "Ellis never pays this" sentence. */}
                    {f.ellisNever && (
                      <div style={{ fontSize: 11.5, color: 'var(--muted)' }}>{f.ellisNever}</div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <HumanActs t={t} acts={view.humanActs} testid="appt-prestage-acts" />
          {view.note && (
            <div style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 8 }}>{view.note}</div>
          )}
        </div>
      )}
    </div>
  )
}

// ---- 3. Group-request roster (10+ travelling together) ---------------------
function GroupRosterPanel({ t, lang, client }) {
  const [form, setForm] = useState({ caseIds: '', group_name: '', group_kind: 'tour_group',
                                     post: '', country: '', coordinator_name: '',
                                     coordinator_email: '' })
  const [view, setView] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const set = (k) => (v) => setForm((p) => ({ ...p, [k]: v }))
  const caseIds = form.caseIds.split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean)

  async function build() {
    setBusy(true); setError(null)
    try {
      const res = await client.appointmentGroupRoster({
        case_ids: caseIds, group_name: form.group_name, group_kind: form.group_kind,
        post: form.post, country: form.country, coordinator_name: form.coordinator_name,
        coordinator_email: form.coordinator_email, locale: lang
      })
      setView(groupRosterView(res, { groupKind: form.group_kind }))
    } catch (e) {
      setError({ message: e.message })
    }
    setBusy(false)
  }

  async function exportRoster() {
    setBusy(true); setError(null)
    try {
      const blob = await client.downloadGroupRoster({
        caseIds, format: 'csv', group_name: form.group_name, group_kind: form.group_kind,
        post: form.post, country: form.country, coordinator_name: form.coordinator_name,
        coordinator_email: form.coordinator_email, locale: lang
      })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'ellis-group-appointment-roster.csv'
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError({ message: e.message })
    }
    setBusy(false)
  }

  return (
    <div style={{ marginTop: 18 }} data-testid="appt-group-roster">
      <div style={{ fontWeight: 600, fontSize: 13 }}>
        {t('appt.group.title', { min: GROUP_MIN_MEMBERS })}
      </div>
      <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 2 }}>{t('appt.group.sub')}</div>
      <div className="field" style={{ marginTop: 8 }}>
        <label>{t('appt.group.caseIds')}</label>
        <textarea className="input" rows={3} value={form.caseIds}
                  onChange={(e) => set('caseIds')(e.target.value)} />
      </div>
      <div className="grid grid-2" style={{ gap: 12 }}>
        <div className="field">
          <label>{t('appt.group.name')}</label>
          <input className="input" value={form.group_name}
                 onChange={(e) => set('group_name')(e.target.value)} />
        </div>
        <div className="field">
          <label>{t('appt.group.kind')}</label>
          <select className="select" value={form.group_kind}
                  onChange={(e) => set('group_kind')(e.target.value)}>
            {GROUP_KINDS.map((k) => <option key={k} value={k}>{t(`appt.group.kind.${k}`)}</option>)}
          </select>
        </div>
        <div className="field">
          <label>{t('appt.group.post')}</label>
          <input className="input" value={form.post}
                 onChange={(e) => set('post')(e.target.value)} />
        </div>
        <div className="field">
          <label>{t('appt.group.coordinator')}</label>
          <input className="input" value={form.coordinator_name}
                 onChange={(e) => set('coordinator_name')(e.target.value)} />
        </div>
      </div>
      {error && <ErrorNote error={error} />}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <button className="btn btn--sm" disabled={busy || caseIds.length === 0} onClick={build}>
          {busy ? t('appt.group.building') : t('appt.group.build')}
        </button>
        <button className="btn btn--sm btn--ghost" disabled={busy || caseIds.length === 0}
                onClick={exportRoster}>
          {t('appt.group.export')}
        </button>
      </div>
      {view && (
        <div className="card" style={{ padding: 14, marginTop: 10 }} data-testid="appt-roster-result">
          <div style={{ fontSize: 12.5 }}>{t('appt.group.members', { count: view.count })}</div>
          {/* Ellis's OWN pre-check, labeled as such — the consulate's rules are
              the authority, and this is only what Ellis can see from here. */}
          {view.preChecks.length > 0 && (
            <div style={{ marginTop: 8 }} data-testid="appt-roster-prechecks">
              <div style={{ fontSize: 11.5, color: 'var(--muted)' }}>{t('appt.group.preCheck')}</div>
              <ul style={{ margin: '4px 0 0 18px', fontSize: 12.5, color: '#c77700' }}>
                {view.preChecks.map((c, i) => (
                  <li key={i}>
                    {c.code === 'too_few'
                      ? t('appt.group.tooFew', { count: c.count, min: GROUP_MIN_MEMBERS })
                      : c.code === 'family_excluded' ? t('appt.group.familyExcluded')
                      : t('appt.group.incomplete', { count: c.count })}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {view.members.length > 0 && (
            <ul style={{ margin: '8px 0 0 18px', fontSize: 12.5 }}>
              {view.members.map((m, i) => (
                <li key={m.caseId || i}>
                  {m.name || m.caseId || `#${m.index}`}
                  {m.missing.length > 0 && (
                    <span style={{ color: '#c77700' }}> · {m.missing.join(', ')}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
          {/* Only the backend may call a roster submittable; absent stays a
              "cannot confirm", never a green light. */}
          <div style={{ fontSize: 12.5, marginTop: 8, fontWeight: 600,
                        color: view.submittable === true ? '#1a7f37'
                          : view.submittable === false ? '#d33' : 'var(--muted)' }}
               data-testid="appt-roster-submittable">
            {view.submittable === true ? t('appt.group.submittable')
              : view.submittable === false ? t('appt.group.notSubmittable')
              : t('appt.group.submittableUnknown')}
          </div>
          <div style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 6 }}>
            {t('appt.group.coordinatorActs')}
          </div>
          <HumanActs t={t} acts={view.humanActs} testid="appt-roster-acts" />
        </div>
      )}
    </div>
  )
}

// ---- 4. Availability -------------------------------------------------------
function AvailabilityPanel({ t, lang, client }) {
  const [form, setForm] = useState({ post: '', country: '', route: 'us', category: 'visitor' })
  const [view, setView] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const set = (k) => (v) => setForm((p) => ({ ...p, [k]: v }))

  async function load() {
    setBusy(true); setError(null)
    try {
      setView(appointmentAvailabilityView(
        await client.appointmentAvailability({ ...form, locale: lang })))
    } catch (e) {
      setError({ message: e.message })
    }
    setBusy(false)
  }

  return (
    <div style={{ marginTop: 18 }} data-testid="appt-availability">
      <div style={{ fontWeight: 600, fontSize: 13 }}>{t('appt.avail.title')}</div>
      <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 2 }}>{t('appt.avail.sub')}</div>
      <div className="grid grid-2" style={{ gap: 12, marginTop: 8 }}>
        <div className="field">
          <label>{t('appt.avail.post')}</label>
          <input className="input" value={form.post} onChange={(e) => set('post')(e.target.value)} />
        </div>
        <div className="field">
          <label>{t('appt.avail.country')}</label>
          <input className="input" value={form.country}
                 onChange={(e) => set('country')(e.target.value)} />
        </div>
      </div>
      {error && <ErrorNote error={error} />}
      <button className="btn btn--sm btn--ghost" disabled={busy} onClick={load}>
        {busy ? t('cockpit.loading') : t('appt.avail.load')}
      </button>
      {view && !view.available && (
        <div className="card" style={{ padding: 12, marginTop: 8, fontSize: 12.5 }}
             data-testid="appt-avail-unavailable">
          <div>{t('appt.avail.unavailable')}</div>
          {view.reason && <div style={{ color: 'var(--muted)', marginTop: 4 }}>{view.reason}</div>}
        </div>
      )}
      {view && view.available && (
        <div className="card" style={{ padding: 14, marginTop: 8 }}>
          <ul style={{ margin: '0 0 0 18px', fontSize: 12.5 }}>
            {view.entries.map((e, i) => (
              <li key={i}>
                {[e.post, e.category].filter(Boolean).join(' · ')}
                {e.waitDays != null
                  ? ` — ${t('appt.avail.waitDays', { days: e.waitDays })}`
                  : ` — ${t('appt.avail.waitUnknown')}`}
                {e.asOf ? ` (${t('appt.avail.asOf', { asOf: e.asOf })})` : ''}
              </li>
            ))}
          </ul>
          <div style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 6 }}>
            {t('appt.avail.estimate')}
          </div>
          {/* The server's own explanation of why there is no live slot feed. */}
          {view.whyNoLiveSlots && (
            <div style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 4 }}>
              {view.whyNoLiveSlots}
            </div>
          )}
          {(view.source || view.asOf) && (
            <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 4 }}>
              {t('cockpit.prepared.source', { source: view.source || '—' })}
              {view.asOf ? ` · ${t('appt.avail.asOf', { asOf: view.asOf })}` : ''}
            </div>
          )}
        </div>
      )}
      {/* Official deep links: https only, and a host Ellis does not recognize
          as a government or an authorized provider is LABELED, not hidden. */}
      {view && view.links.length > 0 && (
        <div style={{ marginTop: 8 }} data-testid="appt-avail-links">
          {view.links.map((l, i) => (
            <div key={i} style={{ fontSize: 12.5, marginTop: 4 }}>
              {l.url ? (
                <a href={l.url} target="_blank" rel="noreferrer">
                  {l.label || t('appt.avail.officialLink')} ({l.host})
                </a>
              ) : (
                // A destination Ellis has not verified is still an answer: it
                // renders as text saying so, never as a link to nowhere.
                <span style={{ color: 'var(--muted)' }}>
                  {l.label || t('appt.avail.linkNotDetermined')}
                </span>
              )}
              {l.description && (
                <div style={{ fontSize: 11.5, color: 'var(--muted)' }}>{l.description}</div>
              )}
              {l.kind === 'insecure' && (
                <div style={{ fontSize: 11.5, color: '#c77700' }}>
                  {t('appt.avail.insecureLink')}
                </div>
              )}
              {l.url && !l.official && (
                <div style={{ fontSize: 11.5, color: '#c77700' }}>
                  {t('appt.avail.unofficialLink')}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {view && <HumanActs t={t} acts={view.humanActs} testid="appt-avail-acts" />}
    </div>
  )
}

export default function AppointmentCockpit({ client, caseId, showGroupRoster = true }) {
  const { t, lang } = useLocale()
  return (
    <div style={{ marginTop: 16 }} data-testid="appointment-cockpit">
      <div style={{ fontWeight: 600, fontSize: 13.5 }}>{t('appt.title')}</div>
      <div style={{ fontSize: 12, color: 'var(--muted)' }}>{t('appt.sub')}</div>
      {/* Unconditional, before any payload: Ellis never books. */}
      <NeverBooksNote t={t} />
      {caseId && <TriagePanel t={t} lang={lang} client={client} caseId={caseId} />}
      {caseId && <PrestagePanel t={t} lang={lang} client={client} caseId={caseId} />}
      {showGroupRoster && <GroupRosterPanel t={t} lang={lang} client={client} />}
      <AvailabilityPanel t={t} lang={lang} client={client} />
    </div>
  )
}
