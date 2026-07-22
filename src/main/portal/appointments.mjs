// Appointment preferences, filtering, and earliest-qualifying ranking.
// All slot times are portal-local instants normalized to UTC epoch ms; weekday
// / time-of-day preferences are evaluated in the APPLICANT's timezone so
// "prefer mornings" means the applicant's morning, not the portal's.

/**
 * @typedef {Object} AppointmentPreferences
 * @property {string} preferredLocation
 * @property {string[]} alternativeLocations
 * @property {number} [maxTravelKm]
 * @property {number} earliestUtc        earliest acceptable start (epoch ms)
 * @property {number} latestUtc          latest acceptable start (epoch ms)
 * @property {number[]} preferredWeekdays   0..6 in applicant tz (empty = any)
 * @property {number[]} excludedWeekdays
 * @property {[string,string]|null} preferredTimeRange  ['09:00','12:00'] applicant local
 * @property {string[]} blackoutDates    'YYYY-MM-DD' in applicant tz
 * @property {number} minAdvanceMs       minimum notice before the appointment
 * @property {boolean} anyQualifyingTimeOk
 * @property {boolean} allowAutoBook
 * @property {boolean} allowAutoReschedule
 * @property {number} minRescheduleImprovementMs
 * @property {number} maxRescheduleFeeCents
 * @property {number} maxAutoReschedules
 * @property {string} applicantTimeZone  IANA tz, e.g. 'America/New_York'
 */

export function defaultPreferences(overrides = {}) {
  return {
    preferredLocation: '',
    alternativeLocations: [],
    maxTravelKm: null,
    earliestUtc: Date.now(),
    latestUtc: Date.now() + 120 * 86400000,
    preferredWeekdays: [],
    excludedWeekdays: [],
    preferredTimeRange: null,
    blackoutDates: [],
    minAdvanceMs: 2 * 86400000,
    anyQualifyingTimeOk: true,
    allowAutoBook: false,
    allowAutoReschedule: false,
    minRescheduleImprovementMs: 3 * 86400000,
    maxRescheduleFeeCents: 0,
    maxAutoReschedules: 2,
    applicantTimeZone: 'UTC',
    ...overrides
  }
}

// Validate preferences for internal contradictions before they drive any
// booking. Returns an array of human-readable problems (empty = valid).
export function validatePreferences(p) {
  const problems = []
  if (p.earliestUtc >= p.latestUtc) problems.push('earliest date is not before latest date')
  if (p.minAdvanceMs < 0) problems.push('minimum advance notice cannot be negative')
  const pref = new Set(p.preferredWeekdays || [])
  for (const d of p.excludedWeekdays || []) if (pref.has(d)) problems.push(`weekday ${d} is both preferred and excluded`)
  if (p.preferredWeekdays?.length && p.excludedWeekdays?.length &&
      p.preferredWeekdays.every((d) => p.excludedWeekdays.includes(d))) problems.push('all preferred weekdays are excluded')
  if (p.allowAutoReschedule && p.maxAutoReschedules <= 0) problems.push('auto-reschedule enabled but max count is 0')
  if (p.preferredTimeRange) {
    const [a, b] = p.preferredTimeRange
    if (!/^\d{2}:\d{2}$/.test(a) || !/^\d{2}:\d{2}$/.test(b) || a >= b) problems.push('invalid preferred time range')
  }
  return problems
}

// Compute applicant-local weekday + HH:mm + YYYY-MM-DD for a UTC instant.
function localParts(utcMs, tz) {
  const dtf = new Intl.DateTimeFormat('en-CA', {
    timeZone: tz, weekday: 'short', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false
  })
  const parts = Object.fromEntries(dtf.formatToParts(new Date(utcMs)).map((p) => [p.type, p.value]))
  const wdMap = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 }
  const hour = parts.hour === '24' ? '00' : parts.hour
  return {
    weekday: wdMap[parts.weekday],
    date: `${parts.year}-${parts.month}-${parts.day}`,
    time: `${hour}:${parts.minute}`
  }
}

/**
 * Filter portal slots to those that satisfy ALL mandatory constraints, ranked
 * chronologically (earliest first). `nowMs` lets tests pin the clock.
 */
export function qualifyingSlots(slots, prefs, nowMs = Date.now()) {
  const p = prefs
  const locOk = new Set([p.preferredLocation, ...(p.alternativeLocations || [])].filter(Boolean))
  const excluded = new Set(p.excludedWeekdays || [])
  const preferred = new Set(p.preferredWeekdays || [])
  const blackout = new Set(p.blackoutDates || [])

  const out = []
  for (const s of slots) {
    if (locOk.size && !locOk.has(s.locationId)) continue
    if (s.startUtc < p.earliestUtc || s.startUtc > p.latestUtc) continue
    if (s.startUtc - nowMs < p.minAdvanceMs) continue
    const lp = localParts(s.startUtc, p.applicantTimeZone)
    if (blackout.has(lp.date)) continue
    if (excluded.has(lp.weekday)) continue
    if (preferred.size && !preferred.has(lp.weekday)) continue
    if (p.preferredTimeRange) {
      const [a, b] = p.preferredTimeRange
      if (lp.time < a || lp.time >= b) continue
    }
    out.push({ ...s, local: lp })
  }
  out.sort((a, b) => a.startUtc - b.startUtc)
  return out
}

/** The single earliest slot satisfying all mandatory constraints, or null. */
export function earliestQualifying(slots, prefs, nowMs = Date.now()) {
  return qualifyingSlots(slots, prefs, nowMs)[0] || null
}

/**
 * Is a candidate slot a meaningful improvement over the current appointment,
 * per the applicant's minimum-improvement threshold? Used before any automatic
 * reschedule so we never move someone for a trivial gain.
 */
export function isImprovement(currentStartUtc, candidateStartUtc, prefs) {
  return currentStartUtc - candidateStartUtc >= (prefs.minRescheduleImprovementMs || 0)
}

// A tiny per-case booking lock so two concurrent booking attempts for the same
// case can't both proceed. Portal-side, MockPortal also enforces slot atomicity.
const LOCKS = new Set()
export function acquireBookingLock(caseId) {
  if (LOCKS.has(caseId)) return false
  LOCKS.add(caseId)
  return true
}
export function releaseBookingLock(caseId) { LOCKS.delete(caseId) }
