// Appointment preferences — shown ONLY when the route's verified guidance says
// an appointment / in-person submission is required. Captures the applicant's
// scheduling constraints and controls (auto-book / reschedule policy) and saves
// them to the backend, which ranks the earliest qualifying slot honoring every
// constraint. Centers come from the route's real adapter when one is released;
// no fictional center or placeholder ever appears here.
import { useEffect, useState } from 'react'
import { useToast, ErrorNote } from '../ui.jsx'
import { defaultPreferences, dateToMs, msToDate, PREF_DAY_MS } from '../../lib/visaSession.js'

const WEEKDAYS = [['Mon', 1], ['Tue', 2], ['Wed', 3], ['Thu', 4], ['Fri', 5], ['Sat', 6], ['Sun', 0]]

export default function Preferences({ client, caseId, initial, onSaved }) {
  const toast = useToast()
  const [p, setP] = useState(() => ({ ...defaultPreferences(), ...(initial || {}) }))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [centers, setCenters] = useState(null)   // real centers when the case's pending slots name them
  const set = (patch) => setP((prev) => ({ ...prev, ...patch }))

  // Real center names only: the locations the case's actual portal driver has
  // surfaced in pending appointment slots. No slots yet -> free-text input
  // with no suggested value (never a fictional default).
  useEffect(() => {
    client.getCase(caseId).then((c) => {
      const slots = (c.pending && Array.isArray(c.pending.slots)) ? c.pending.slots : []
      const locs = [...new Set(slots.map((s) => s.locationId || s.location_id).filter(Boolean))]
      setCenters(locs.length ? locs : null)
    }).catch(() => {})
  }, [caseId])

  function toggleWeekday(d) {
    const on = p.preferredWeekdays.includes(d)
    set({ preferredWeekdays: on ? p.preferredWeekdays.filter((x) => x !== d) : [...p.preferredWeekdays, d] })
  }

  async function save() {
    setBusy(true); setError(null)
    try {
      await client.setPreferences(caseId, p)
      toast('Preferences saved')
      onSaved && onSaved(p)
    } catch (e) { setError({ message: e.message }); setBusy(false); return }
    setBusy(false)
  }

  return (
    <div className="card" style={{ padding: 22 }}>
      <div className="grid grid-2" style={{ gap: 14 }}>
        <div className="field"><label>Preferred center</label>
          {centers ? (
            <select className="select" value={p.preferredLocation}
                    onChange={(e) => set({ preferredLocation: e.target.value })}>
              <option value="">No preference</option>
              {centers.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          ) : (
            <input className="input" value={p.preferredLocation}
                   onChange={(e) => set({ preferredLocation: e.target.value })}
                   placeholder="Center name (as shown by the official portal)" />
          )}</div>
        <div className="field"><label>Alternative centers (comma-separated)</label>
          <input className="input" value={(p.alternativeLocations || []).join(', ')}
                 onChange={(e) => set({ alternativeLocations: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) })} /></div>
        <div className="field"><label>Earliest acceptable date</label>
          <input className="input" type="date" value={msToDate(p.earliestUtc)}
                 onChange={(e) => set({ earliestUtc: dateToMs(e.target.value) })} /></div>
        <div className="field"><label>Latest acceptable date</label>
          <input className="input" type="date" value={msToDate(p.latestUtc)}
                 onChange={(e) => set({ latestUtc: dateToMs(e.target.value) })} /></div>
        <div className="field"><label>Max travel distance (km)</label>
          <input className="input" type="number" value={p.maxTravelKm ?? ''}
                 onChange={(e) => set({ maxTravelKm: e.target.value === '' ? null : Number(e.target.value) })} /></div>
        <div className="field"><label>Minimum advance notice (days)</label>
          <input className="input" type="number" value={Math.round((p.minAdvanceMs || 0) / PREF_DAY_MS)}
                 onChange={(e) => set({ minAdvanceMs: Number(e.target.value) * PREF_DAY_MS })} /></div>
        <div className="field"><label>Applicant time zone</label>
          <input className="input" value={p.applicantTimeZone}
                 onChange={(e) => set({ applicantTimeZone: e.target.value })} /></div>
        <div className="field"><label>Blackout dates (comma-separated YYYY-MM-DD)</label>
          <input className="input" value={(p.blackoutDates || []).join(', ')}
                 onChange={(e) => set({ blackoutDates: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) })} /></div>
        <div className="field"><label>Accessibility needs</label>
          <input className="input" value={p.accessibility}
                 onChange={(e) => set({ accessibility: e.target.value })} placeholder="e.g. step-free access" /></div>
        <div className="field"><label>Interpreter language</label>
          <input className="input" value={p.interpreterLanguage}
                 onChange={(e) => set({ interpreterLanguage: e.target.value })} placeholder="e.g. Vietnamese" /></div>
      </div>

      <div style={{ marginTop: 12 }}>
        <label style={{ fontSize: 12, color: 'var(--muted)' }}>Preferred weekdays</label>
        <div style={{ display: 'flex', gap: 6, marginTop: 6, flexWrap: 'wrap' }}>
          {WEEKDAYS.map(([label, d]) => (
            <button key={d} className={'chip' + (p.preferredWeekdays.includes(d) ? ' chip--ink' : '')}
                    onClick={() => toggleWeekday(d)} type="button">{label}</button>
          ))}
        </div>
      </div>

      <div className="card card--soft" style={{ padding: 14, marginTop: 14 }}>
        <div className="eyebrow">Automation controls</div>
        <label style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 8, fontSize: 13 }}>
          <input type="checkbox" checked={p.allowAutoBook} onChange={(e) => set({ allowAutoBook: e.target.checked })} />
          Let Ellis book the earliest qualifying slot automatically
        </label>
        <label style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 8, fontSize: 13 }}>
          <input type="checkbox" checked={p.allowAutoReschedule} onChange={(e) => set({ allowAutoReschedule: e.target.checked })} />
          Watch for earlier slots and reschedule
        </label>
        {p.allowAutoReschedule && (
          <div className="grid grid-2" style={{ gap: 12, marginTop: 10 }}>
            <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13 }}>
              <input type="checkbox" checked={p.askBeforeReschedule} onChange={(e) => set({ askBeforeReschedule: e.target.checked })} />
              Ask me before rescheduling
            </label>
            <div className="field"><label>Min improvement (days)</label>
              <input className="input" type="number" value={Math.round((p.minRescheduleImprovementMs || 0) / PREF_DAY_MS)}
                     onChange={(e) => set({ minRescheduleImprovementMs: Number(e.target.value) * PREF_DAY_MS })} /></div>
            <div className="field"><label>Max reschedules</label>
              <input className="input" type="number" value={p.maxAutoReschedules}
                     onChange={(e) => set({ maxAutoReschedules: Number(e.target.value) })} /></div>
            <div className="field"><label>Max reschedule fee (cents)</label>
              <input className="input" type="number" value={p.maxRescheduleFeeCents}
                     onChange={(e) => set({ maxRescheduleFeeCents: Number(e.target.value) })} /></div>
          </div>
        )}
      </div>

      {error && <ErrorNote error={error} />}
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 14 }}>
        <button className="btn" disabled={busy} onClick={save}>{busy ? 'Saving…' : 'Save preferences'}</button>
      </div>
    </div>
  )
}
