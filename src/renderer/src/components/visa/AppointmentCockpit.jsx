// The appointment cockpit — docs/APPOINTMENTS_DESIGN.md, one surface for both
// routes, honest about the human's remaining act. 2026-08-13 visual redesign:
// four illustrated panels, a big triage verdict, labels instead of prose.
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
import { useEffect, useRef, useState } from 'react'
import { useLocale } from '../../lib/locale.jsx'
import { ErrorNote } from '../ui.jsx'
import {
  appointmentTriageView, appointmentPrestageView, groupRosterView,
  appointmentAvailabilityView, bookingView, HUMAN_ACT_KEYS, GROUP_MIN_MEMBERS
} from '../../lib/visaBackend.js'
import {
  AppointmentIllustration, PassportIllustration, DocumentsIllustration,
  PipelineIllustration
} from './Illustrations.jsx'

const GROUP_KINDS = ['tour_group', 'company', 'school', 'family']
const NAVY = 'var(--trip-navy, #0f294d)'
const GRAY = 'var(--trip-gray, #8592a6)'

// The standing compliance note. Its own component so it can be rendered first,
// unconditionally, on every state of this surface.
function NeverBooksNote({ t }) {
  return (
    <div className="card" style={{ padding: '12px 16px', marginTop: 12, fontSize: 12.5,
                                   borderRadius: 16, border: '1px solid #c77700' }}
         data-testid="appt-never-books">
      {t('appt.neverBooks')}
    </div>
  )
}

// The surface header: one illustration, a few words.
function CockpitHeader({ t }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
      <AppointmentIllustration size={88} />
      <div>
        <div style={{ fontWeight: 800, fontSize: 15, color: NAVY }}>{t('appt.title')}</div>
        <div style={{ fontSize: 12, color: GRAY }}>{t('appt.sub')}</div>
      </div>
    </div>
  )
}

// Each panel is one icon card: illustration + title left, its action right.
// Rest props pass through so each panel carries its own literal data-testid.
function PanelCard({ art, title, sub, action, className = '', children, ...rest }) {
  return (
    <div className={`card ${className}`} {...rest}
         style={{ padding: 20, borderRadius: 18, marginTop: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12,
                    alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          {art}
          <div>
            <div style={{ fontWeight: 800, fontSize: 14, color: NAVY }}>{title}</div>
            {sub && <div style={{ fontSize: 12, color: GRAY, marginTop: 2 }}>{sub}</div>}
          </div>
        </div>
        {action}
      </div>
      {children}
    </div>
  )
}

function HumanActs({ t, acts, testid = 'appt-human-acts' }) {
  if (!acts || acts.length === 0) return null
  return (
    <div style={{ marginTop: 10 }} data-testid={testid}>
      <div style={{ fontSize: 12, fontWeight: 700 }}>{t('appt.acts.title')}</div>
      <ul style={{ margin: '4px 0 0 18px', fontSize: 12.5 }}>
        {acts.map((a, i) => {
          const known = a.key && HUMAN_ACT_KEYS.includes(a.key)
          const label = known ? t(`cockpit.act.${a.key}`) : a.act
          if (!label) return null
          return (
            <li key={a.key || i}>
              {label}
              {a.who && (
                <span style={{ color: GRAY }}> · {t('appt.acts.who', { who: a.who })}</span>
              )}
              {/* Non-delegable is the strongest line on this surface: not even
                  an accredited agent may do it. */}
              {a.nonDelegable === true && (
                <span style={{ color: '#c77700' }}> · {t('cockpit.acts.nonDelegable')}</span>
              )}
              {a.ellisDoes && (
                <div style={{ fontSize: 11.5, color: GRAY }}>
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
    <div style={{ fontSize: 12.5, marginTop: 5,
                  color: value === null ? GRAY : 'inherit' }}
         data-testid={testid}>
      {t(key)}
    </div>
  )
}

// The big triage verdict: one glyph circle + the short status beside it.
// Tri-state like everything here — unknown is its own honest face, never a no.
function BigVerdict({ t, value }) {
  const face = value === true
    ? { bg: '#fdf3e0', fg: '#b97b0a', glyph: '!', key: 'appt.triage.required' }
    : value === false
    ? { bg: '#e8f8ee', fg: '#1d9e55', glyph: '✓', key: 'appt.triage.notRequired' }
    : { bg: '#f0f3f8', fg: GRAY, glyph: '?', key: 'appt.triage.unknown' }
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginTop: 4 }}>
      <div aria-hidden="true"
           style={{ width: 52, height: 52, borderRadius: '50%', background: face.bg,
                    color: face.fg, display: 'grid', placeItems: 'center',
                    fontSize: 26, fontWeight: 800, flexShrink: 0 }}>
        {face.glyph}
      </div>
      <div style={{ fontSize: 14.5, fontWeight: 800, color: value == null ? GRAY : NAVY }}
           data-testid="appt-inperson">
        {t(face.key)}
      </div>
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
    <PanelCard art={<PassportIllustration size={60} />} title={t('appt.triage.title')}
               className="anim-rise" data-testid="appt-triage"
               action={
                 <button className="btn btn--sm btn--ghost" disabled={busy} onClick={run}>
                   {busy ? t('appt.triage.checking') : t('appt.triage.run')}
                 </button>
               }>
      {error && <ErrorNote error={error} />}
      {view && !view.available && (
        <div className="card card--soft anim-rise" style={{ padding: 14, marginTop: 10, borderRadius: 14, fontSize: 12.5 }}>
          <div>{t('appt.triage.unknown')}</div>
          {view.reason && <div style={{ color: GRAY, marginTop: 4 }}>{view.reason}</div>}
        </div>
      )}
      {view && view.available && (
        <div className="anim-rise" style={{ marginTop: 14 }}>
          {/* The big verdict first: one glyph, four words. */}
          <BigVerdict t={t} value={view.inPersonRequired} />
          {/* The server's own localized verdict sentence. */}
          {view.summary && (
            <div style={{ fontSize: 12.5, color: GRAY, marginTop: 8 }} data-testid="appt-summary">
              {view.summary}
            </div>
          )}
          <div style={{ marginTop: 8 }}>
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
          </div>
          {view.reasons.length > 0 && (
            <ul style={{ margin: '8px 0 0 18px', fontSize: 12, color: GRAY }}>
              {view.reasons.map((r, i) => <li key={i}>{r}</li>)}
            </ul>
          )}
          {/* What would settle an unknown. The questions themselves, never a
              guess dressed as an answer. */}
          {view.openQuestions.length > 0 && (
            <div style={{ marginTop: 10 }} data-testid="appt-open-questions">
              <div style={{ fontSize: 12, fontWeight: 700 }}>{t('appt.triage.questions')}</div>
              <ul style={{ margin: '4px 0 0 18px', fontSize: 12.5 }}>
                {view.openQuestions.map((q, i) => (
                  <li key={i}>
                    {q.question}
                    {q.resolveWith && (
                      <div style={{ fontSize: 11.5, color: GRAY }}>
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
            <div style={{ fontSize: 11, color: GRAY, marginTop: 8 }}>{view.disclaimer}</div>
          )}
        </div>
      )}
    </PanelCard>
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
    <PanelCard art={<DocumentsIllustration size={60} />} title={t('appt.prestage.title')}
               className="anim-rise-1" data-testid="appt-prestage"
               action={
                 <button className="btn btn--sm btn--ghost" disabled={busy} onClick={run}>
                   {busy ? t('cockpit.loading') : t('appt.prestage.load')}
                 </button>
               }>
      <div style={{ fontSize: 11.5, color: GRAY, marginTop: 6 }}
           data-testid="appt-nothing-done">
        {t('appt.prestage.nothingDone')}
      </div>
      {error && <ErrorNote error={error} />}
      {view && !view.available && (
        <div className="card card--soft anim-rise" style={{ padding: 14, marginTop: 10, borderRadius: 14, fontSize: 12.5 }}>
          <div>{t('appt.prestage.empty')}</div>
          {view.reason && <div style={{ color: GRAY, marginTop: 4 }}>{view.reason}</div>}
        </div>
      )}
      {view && view.available && (
        <div className="anim-rise" style={{ marginTop: 12 }}>
          {view.filledCount != null && view.totalCount != null && (
            <div style={{ marginBottom: 8 }} data-testid="appt-readiness">
              <span className="chip">
                {t('appt.prestage.readiness', { filled: view.filledCount,
                  total: view.totalCount, missing: view.missingRequiredCount })}
              </span>
            </div>
          )}
          <div style={{ fontSize: 12, fontWeight: 700 }}>{t('appt.prestage.ready')}</div>
          {view.prepared.length === 0 ? (
            <div style={{ fontSize: 12.5, color: GRAY }}>{t('cockpit.prepared.empty')}</div>
          ) : (
            <ul style={{ margin: '4px 0 0 18px', fontSize: 12.5 }}>
              {view.prepared.map((p, i) => (
                <li key={p.key || i}>
                  {p.label}{p.value ? `: ${p.value}` : ''}
                  <span style={{ color: GRAY }}>
                    {' '}· {p.sourceKnown
                      ? t('cockpit.prepared.source', { source: p.source })
                      : t('cockpit.prepared.sourceUnknown')}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <div style={{ fontSize: 12, fontWeight: 700, marginTop: 12 }}>
            {t('appt.prestage.missing')}
          </div>
          {view.missingCount === 0 ? (
            <div style={{ fontSize: 12.5, color: GRAY }}>{t('cockpit.missing.none')}</div>
          ) : (
            <ul style={{ margin: '4px 0 0 18px', fontSize: 12.5 }}>
              {view.missing.map((m, i) => (
                <li key={m.key || i}>
                  {m.label}
                  <div style={{ fontSize: 11.5, color: GRAY }}>
                    {t('cockpit.missing.input', { input: m.input })}
                  </div>
                </li>
              ))}
            </ul>
          )}
          {view.documents.length > 0 && (
            <div style={{ marginTop: 12 }} data-testid="appt-documents">
              <div style={{ fontSize: 12, fontWeight: 700 }}>
                {t('cockpit.prepared.documents')}
              </div>
              <ul style={{ margin: '4px 0 0 18px', fontSize: 12.5, color: GRAY }}>
                {view.documents.map((d, i) => <li key={d.key || i}>{d.label}</li>)}
              </ul>
            </div>
          )}
          {/* The exact fee stack, with the official channel. Ellis computes the
              amount and names where to pay it; it never pays. */}
          {view.fees.length > 0 && (
            <div style={{ marginTop: 12 }} data-testid="appt-fees">
              <div style={{ fontSize: 12, fontWeight: 700 }}>{t('appt.prestage.feeTitle')}</div>
              <ul style={{ margin: '4px 0 0 18px', fontSize: 12.5 }}>
                {view.fees.map((f, i) => (
                  <li key={f.key || i}>
                    {t('appt.prestage.feeLine', {
                      label: f.label || '—',
                      amount: f.amount != null ? f.amount : '—',
                      currency: f.currency || ''
                    })}
                    {f.channels.length > 0 && (
                      <div style={{ fontSize: 11.5, color: GRAY }}>
                        {t('appt.prestage.feeChannels', { channels: f.channels.join(', ') })}
                      </div>
                    )}
                    {/* The server's own "Ellis never pays this" sentence. */}
                    {f.ellisNever && (
                      <div style={{ fontSize: 11.5, color: GRAY }}>{f.ellisNever}</div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <HumanActs t={t} acts={view.humanActs} testid="appt-prestage-acts" />
          {view.note && (
            <div style={{ fontSize: 11.5, color: GRAY, marginTop: 8 }}>{view.note}</div>
          )}
        </div>
      )}
    </PanelCard>
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
    <PanelCard art={<PipelineIllustration size={72} />}
               title={t('appt.group.title', { min: GROUP_MIN_MEMBERS })}
               sub={t('appt.group.sub')}
               className="anim-rise-2" data-testid="appt-group-roster">
      <div className="field" style={{ marginTop: 12 }}>
        <label>{t('appt.group.caseIds')}</label>
        <textarea className="input" rows={3} value={form.caseIds}
                  onChange={(e) => set('caseIds')(e.target.value)} />
      </div>
      <div className="grid grid-2" style={{ gap: '12px 16px' }}>
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
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 4 }}>
        <button className="btn btn--sm" disabled={busy || caseIds.length === 0} onClick={build}>
          {busy ? t('appt.group.building') : t('appt.group.build')}
        </button>
        <button className="btn btn--sm btn--ghost" disabled={busy || caseIds.length === 0}
                onClick={exportRoster}>
          {t('appt.group.export')}
        </button>
      </div>
      {view && (
        <div className="card card--soft anim-rise" style={{ padding: 16, marginTop: 12, borderRadius: 14 }}
             data-testid="appt-roster-result">
          <span className="chip">{t('appt.group.members', { count: view.count })}</span>
          {/* Ellis's OWN pre-check, labeled as such — the consulate's rules are
              the authority, and this is only what Ellis can see from here. */}
          {view.preChecks.length > 0 && (
            <div style={{ marginTop: 10 }} data-testid="appt-roster-prechecks">
              <div style={{ fontSize: 11.5, color: GRAY }}>{t('appt.group.preCheck')}</div>
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
            <ul style={{ margin: '10px 0 0 18px', fontSize: 12.5 }}>
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
          <div style={{ marginTop: 10 }} data-testid="appt-roster-submittable">
            <span className={view.submittable === true ? 'chip-icon chip-icon--ok'
                    : view.submittable === false ? 'chip-icon chip-icon--crit' : 'chip-icon'}
                  style={{ whiteSpace: 'normal' }}>
              {view.submittable === true ? t('appt.group.submittable')
                : view.submittable === false ? t('appt.group.notSubmittable')
                : t('appt.group.submittableUnknown')}
            </span>
          </div>
          <div style={{ fontSize: 11.5, color: GRAY, marginTop: 8 }}>
            {t('appt.group.coordinatorActs')}
          </div>
          <HumanActs t={t} acts={view.humanActs} testid="appt-roster-acts" />
        </div>
      )}
    </PanelCard>
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
    <PanelCard art={<AppointmentIllustration size={60} />} title={t('appt.avail.title')}
               sub={t('appt.avail.sub')}
               className="anim-rise-3" data-testid="appt-availability"
               action={
                 <button className="btn btn--sm btn--ghost" disabled={busy} onClick={load}>
                   {busy ? t('cockpit.loading') : t('appt.avail.load')}
                 </button>
               }>
      <div className="grid grid-2" style={{ gap: '12px 16px', marginTop: 12 }}>
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
      {view && !view.available && (
        <div className="card card--soft anim-rise" style={{ padding: 14, marginTop: 10, borderRadius: 14, fontSize: 12.5 }}
             data-testid="appt-avail-unavailable">
          <div>{t('appt.avail.unavailable')}</div>
          {view.reason && <div style={{ color: GRAY, marginTop: 4 }}>{view.reason}</div>}
        </div>
      )}
      {view && view.available && (
        <div className="card card--soft anim-rise" style={{ padding: 16, marginTop: 10, borderRadius: 14 }}>
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
          <div style={{ fontSize: 11.5, color: GRAY, marginTop: 6 }}>
            {t('appt.avail.estimate')}
          </div>
          {/* The server's own explanation of why there is no live slot feed. */}
          {view.whyNoLiveSlots && (
            <div style={{ fontSize: 11.5, color: GRAY, marginTop: 4 }}>
              {view.whyNoLiveSlots}
            </div>
          )}
          {(view.source || view.asOf) && (
            <div style={{ fontSize: 11, color: GRAY, marginTop: 4 }}>
              {t('cockpit.prepared.source', { source: view.source || '—' })}
              {view.asOf ? ` · ${t('appt.avail.asOf', { asOf: view.asOf })}` : ''}
            </div>
          )}
        </div>
      )}
      {/* Official deep links: https only, and a host Ellis does not recognize
          as a government or an authorized provider is LABELED, not hidden. */}
      {view && view.links.length > 0 && (
        <div style={{ marginTop: 10 }} data-testid="appt-avail-links">
          {view.links.map((l, i) => (
            <div key={i} style={{ fontSize: 12.5, marginTop: 4 }}>
              {l.url ? (
                <a href={l.url} target="_blank" rel="noreferrer">
                  {l.label || t('appt.avail.officialLink')} ({l.host})
                </a>
              ) : (
                // A destination Ellis has not verified is still an answer: it
                // renders as text saying so, never as a link to nowhere.
                <span style={{ color: GRAY }}>
                  {l.label || t('appt.avail.linkNotDetermined')}
                </span>
              )}
              {l.description && (
                <div style={{ fontSize: 11.5, color: GRAY }}>{l.description}</div>
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
    </PanelCard>
  )
}

// ---- Agent-channel booking (app/appt_booking.py) ---------------------------
// The applicant asks and picks HERE, inside Trip.com. A named human operator
// reads the official calendar and executes the booking in their own session;
// this panel polls while the request is active so the operator's slots appear
// within seconds of being recorded. "Booked" renders only behind evidence.
function StatusChip({ t, status }) {
  const tone = status === 'booked' ? ' chip--ink'
    : (status === 'failed' || status === 'cancelled') ? ' chip-icon--warn' : ''
  return <span className={'chip' + tone} data-testid="booking-status">
    {t(`appt.booking.status.${status}`)}
  </span>
}

function BookingPanel({ t, lang, client, caseId }) {
  const [view, setView] = useState(null)   // null = first fetch not resolved
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [form, setForm] = useState({ route: 'us_b1b2', posts: '', from: '', to: '', note: '' })
  const timer = useRef(null)
  // Guards a poll response that lands after the case changed underneath it —
  // a stale case's booking must never render as this case's.
  const caseRef = useRef(caseId)
  const set = (k) => (v) => setForm((p) => ({ ...p, [k]: v }))

  async function refresh() {
    const forCase = caseId
    try {
      const payload = await client.bookingForCase(forCase)
      if (caseRef.current !== forCase) return
      setView(bookingView(payload))
      setError(null)
    } catch (e) {
      if (caseRef.current !== forCase) return
      setError({ message: e.message })
    }
  }
  useEffect(() => { caseRef.current = caseId; setView(null); refresh() }, [caseId])
  // Poll only while a request is ACTIVE — the operator's recorded slots and
  // the final confirmation arrive without a manual reload.
  useEffect(() => {
    clearInterval(timer.current)
    if (view?.active) timer.current = setInterval(refresh, 4000)
    return () => clearInterval(timer.current)
  }, [view?.active, view?.status])

  async function request() {
    setBusy(true); setError(null)
    try {
      const posts = form.posts.split(',').map((s) => s.trim()).filter(Boolean)
      const windows = (form.from || form.to) ? [{ from: form.from, to: form.to }] : []
      setView(bookingView(await client.bookingCreate(caseId, {
        route: form.route, posts, date_windows: windows, note: form.note })))
    } catch (e) {
      setError({ message: e.message })
    }
    setBusy(false)
  }

  async function act(fn) {
    setBusy(true); setError(null)
    try { setView(bookingView(await fn())) } catch (e) { setError({ message: e.message }) }
    setBusy(false)
  }

  // While the first fetch is in flight (view === null) show nothing but the
  // loader — the request form must not flash before we know the case's state.
  const loading = view === null
  const showForm = !!view && (!view.exists || (!view.active && view.status !== 'booked'))
  return (
    <PanelCard art={<AppointmentIllustration size={60} />} title={t('appt.booking.title')}
               sub={t('appt.booking.sub')} className="anim-rise-2"
               data-testid="appt-booking">
      {error && <ErrorNote error={error} />}
      {loading && !error && (
        <div style={{ fontSize: 12.5, color: GRAY, marginTop: 10 }}
             data-testid="booking-loading">{t('cockpit.loading')}</div>
      )}
      {/* A failed FIRST fetch leaves view null; without a way forward the
          whole panel is stuck. Offer a retry rather than blindly showing the
          request form (a request may already exist). */}
      {loading && error && (
        <button className="btn btn--sm btn--ghost" style={{ marginTop: 8 }}
                disabled={busy} onClick={refresh} data-testid="booking-retry">
          {t('common.retry')}
        </button>
      )}
      {/* A finished (failed / cancelled) request stays visible above the
          fresh-request form: the history is honest, not swept away. */}
      {view && view.exists && (view.status === 'failed' || view.status === 'cancelled') && (
        <div className="card card--soft anim-rise" style={{ padding: 14, marginTop: 10, borderRadius: 14, fontSize: 12.5 }}
             data-testid="booking-ended">
          <StatusChip t={t} status={view.status} />
          {view.failureReason && <div style={{ color: GRAY, marginTop: 6 }}>{view.failureReason}</div>}
        </div>
      )}

      {showForm && (
        <div style={{ marginTop: 12 }} data-testid="booking-request-form">
          <div className="grid grid-2" style={{ gap: '12px 16px' }}>
            <div className="field">
              <label>{t('appt.booking.route')}</label>
              <select className="input" value={form.route}
                      onChange={(e) => set('route')(e.target.value)}>
                <option value="us_b1b2">{t('appt.booking.routeUS')}</option>
                <option value="schengen">{t('appt.booking.routeSchengen')}</option>
              </select>
            </div>
            <div className="field">
              <label>{t('appt.booking.posts')}</label>
              <input className="input" value={form.posts} placeholder={t('appt.booking.postsHint')}
                     onChange={(e) => set('posts')(e.target.value)} />
            </div>
            <div className="field">
              <label>{t('appt.booking.windowFrom')}</label>
              <input className="input" type="date" value={form.from}
                     onChange={(e) => set('from')(e.target.value)} />
            </div>
            <div className="field">
              <label>{t('appt.booking.windowTo')}</label>
              <input className="input" type="date" value={form.to}
                     onChange={(e) => set('to')(e.target.value)} />
            </div>
          </div>
          <button className="btn btn--sm" style={{ marginTop: 10 }} disabled={busy || !form.posts.trim()}
                  onClick={request} data-testid="booking-request">
            {t('appt.booking.request')}
          </button>
        </div>
      )}

      {view && view.exists && view.active && (
        <div className="card card--soft anim-rise" style={{ padding: 16, marginTop: 12, borderRadius: 14, fontSize: 12.5 }}
             data-testid="booking-active">
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <StatusChip t={t} status={view.status} />
            <span style={{ color: GRAY }}>
              {view.posts.join(' · ')}
              {view.dateWindows[0] && ` · ${view.dateWindows[0].from || '…'} → ${view.dateWindows[0].to || '…'}`}
            </span>
          </div>
          {view.status === 'requested' && (
            <div style={{ color: GRAY, marginTop: 8 }}>{t('appt.booking.waitingOp')}</div>
          )}
          {view.status === 'slots_offered' && (
            <div style={{ marginTop: 10 }} data-testid="booking-slots">
              <div style={{ fontWeight: 700 }}>{t('appt.booking.pickTitle')}</div>
              {/* Who read the calendar and when — on every slot. */}
              <div style={{ fontSize: 11.5, color: GRAY, marginTop: 2 }}>{view.slotsNotice}</div>
              {view.offeredSlots.map((s, i) => (
                <div key={i} style={{ display: 'flex', justifyContent: 'space-between',
                                      alignItems: 'center', gap: 10, marginTop: 8 }}>
                  <div>
                    <b>{s.post}</b> · {s.when}{s.label ? ` · ${s.label}` : ''}
                    <div style={{ fontSize: 11, color: GRAY }}>
                      {s.source === 'ellis_agent'
                        ? t('appt.booking.readByEllis', { at: s.recordedAt })
                        : t('appt.booking.seenBy', { who: s.recordedBy, at: s.recordedAt })}
                    </div>
                  </div>
                  <button className="btn btn--sm" disabled={busy} data-testid={`booking-pick-${i}`}
                          onClick={() => act(() => client.bookingPick(view.id, i, s))}>
                    {t('appt.booking.pick')}
                  </button>
                </div>
              ))}
            </div>
          )}
          {view.status === 'slot_picked' && view.pickedSlot && (
            <div style={{ marginTop: 8 }} data-testid="booking-picked">
              <b>{view.pickedSlot.post}</b> · {view.pickedSlot.when}
              <div style={{ color: GRAY, marginTop: 4 }}>{t('appt.booking.waitingBook')}</div>
            </div>
          )}
          <button className="btn btn--sm btn--ghost" style={{ marginTop: 10 }} disabled={busy}
                  onClick={() => act(() => client.bookingCancel(view.id))} data-testid="booking-cancel">
            {t('appt.booking.cancel')}
          </button>
        </div>
      )}

      {view && view.exists && view.status === 'booked' && view.confirmation && (
        <div className="card card--soft anim-rise" style={{ padding: 16, marginTop: 12, borderRadius: 14, fontSize: 12.5 }}
             data-testid="booking-booked">
          <StatusChip t={t} status="booked" />
          <div style={{ marginTop: 8 }}>
            <b>{view.pickedSlot?.post}</b> · {view.pickedSlot?.when}
          </div>
          <div style={{ marginTop: 4 }}>
            {t('appt.booking.confNumber', { number: view.confirmation.number })}
          </div>
          <div style={{ fontSize: 11.5, color: GRAY, marginTop: 4 }}>
            {t('appt.booking.bookedBy', { who: view.confirmation.recordedBy })}
          </div>
        </div>
      )}

      {view && view.exists && (
        <>
          {view.legalBasis.basis && (
            <div style={{ fontSize: 11.5, color: GRAY, marginTop: 10 }} data-testid="booking-basis">
              {view.legalBasis.basis}{view.legalBasis.limit ? ` ${view.legalBasis.limit}` : ''}
            </div>
          )}
          {view.neverAutomatedNotice && (
            <div style={{ fontSize: 11.5, color: GRAY, marginTop: 4 }}>{view.neverAutomatedNotice}</div>
          )}
          <HumanActs t={t} acts={view.humanActs} testid="booking-acts" />
        </>
      )}
    </PanelCard>
  )
}

export default function AppointmentCockpit({ client, caseId, showGroupRoster = true,
                                            showBooking = true }) {
  const { t, lang } = useLocale()
  return (
    <div style={{ marginTop: 28 }} data-testid="appointment-cockpit">
      <CockpitHeader t={t} />
      {/* Unconditional, before any payload: Ellis never books. */}
      <NeverBooksNote t={t} />
      {caseId && <TriagePanel t={t} lang={lang} client={client} caseId={caseId} />}
      {caseId && <PrestagePanel t={t} lang={lang} client={client} caseId={caseId} />}
      {/* Booking is the APPLICANT's own act on their own case. It never shows
          on the employer console, and never on the parent petition case (only
          a released child consular case has a real appointment to book). */}
      {caseId && showBooking && <BookingPanel t={t} lang={lang} client={client} caseId={caseId} />}
      {showGroupRoster && <GroupRosterPanel t={t} lang={lang} client={client} />}
      <AvailabilityPanel t={t} lang={lang} client={client} />
    </div>
  )
}
