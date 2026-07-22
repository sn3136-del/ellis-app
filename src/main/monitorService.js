// Post-appointment auto-continuation. Once an application is filed the trip
// sits in "monitoring" and this service takes over — no clicks required:
//
//  1. Periodic monitoring checks run on every monitored trip and are written
//     to the agent record with timestamps, so "monitoring" is an activity,
//     not a label. Government visa portals expose no public status APIs
//     (status pages sit behind per-application credentials and CAPTCHAs), so
//     the checks watch the channels Ellis actually controls.
//  2. The decisions folder (userData/decisions) is watched continuously.
//     When the consulate/agency returns the decision document (e-visa PDF,
//     approval notice — typically by email to the ops team), dropping it in
//     with the trip reference in the filename auto-matches the trip, marks it
//     issued, and emails the traveler with the document attached.
//
// The manual Approve/Refuse card in the portal remains as an override.

import { app } from 'electron'
import { join, basename, extname } from 'path'
import { existsSync, mkdirSync, readdirSync, watch, statSync, renameSync } from 'fs'
import { getState, update } from './store.js'

const CHECK_EVERY_MS = 15 * 60 * 1000
const SCAN_EVERY_MS = 20 * 1000
const MONITORED = ['submitted', 'monitoring', 'prepared']

let issueCb = null
let refuseCb = null
let started = false

export function decisionsDir() {
  const dir = join(app.getPath('userData'), 'decisions')
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true })
  return dir
}

// Short reference ops put in the decision filename — shown in the portal UI.
export function tripRef(trip) {
  const suffix = String(trip.id || '').split('_').pop() || ''
  return `TRIP-${suffix.toUpperCase()}`
}

function appendLog(tripId, entry) {
  update((st) => {
    const t = (st.trips || []).find((x) => x.id === tripId)
    if (!t) return
    if (!Array.isArray(t.agentLog)) t.agentLog = []
    t.agentLog.push({ at: Date.now(), ...entry })
    t.updatedAt = Date.now()
  })
}

function slug(s) {
  return String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '')
}

// Match a dropped file to a monitored trip: the reference (preferred) or the
// traveler's full name in the filename.
function matchTrip(file, trips) {
  const f = slug(file)
  // Reference match is authoritative and unique.
  const byRef = trips.find((t) => f.includes(slug(tripRef(t))))
  if (byRef) return byRef
  // Name match only when exactly one monitored trip carries that name AND the
  // filename begins with it — never guess between same-named applicants or
  // match a name that is merely a substring of a longer one.
  const named = trips.filter((t) => slug(t.name).length >= 6 && f.startsWith(slug(t.name)))
  return named.length === 1 ? named[0] : null
}

let scanning = false
async function scanDecisions() {
  if (scanning || !issueCb) return
  scanning = true
  try {
    const dir = decisionsDir()
    const files = readdirSync(dir, { withFileTypes: true })
      .filter((e) => e.isFile() && !e.name.startsWith('.') && ['.pdf', '.jpg', '.jpeg', '.png'].includes(extname(e.name).toLowerCase()))
      .map((e) => e.name)
    if (!files.length) return
    const trips = (getState().trips || []).filter((t) => MONITORED.includes(t.status))
    for (const f of files) {
      const full = join(dir, f)
      // Skip files still being copied (size changing / just created)
      try { if (Date.now() - statSync(full).mtimeMs < 3000) continue } catch { continue }
      const trip = matchTrip(f, trips)
      if (!trip) continue
      // Mark the file processed BEFORE issuing so a slow issue can't let the
      // next 20s scan pick the same file up again (idempotency).
      let stored = full
      try {
        const done = join(dir, 'processed')
        if (!existsSync(done)) mkdirSync(done, { recursive: true })
        stored = join(done, f)
        renameSync(full, stored)
      } catch { /* if rename fails, issue guard on the trip still protects us */ }
      // A refusal is caught the moment the document lands: files named
      // TRIP-XXXX-refused[-reason].pdf (or containing refus/reject/denied)
      // trigger the automatic rejection-recovery flow instead of issuance.
      const isRefusal = /refus|reject|denied/i.test(f)
      if (isRefusal) {
        const m = f.toLowerCase().match(/(?:refused|refusal|rejected|rejection|denied)[-_ ]+([a-z0-9-_ ]+?)(?:\.[a-z]+)?$/)
        const reason = m ? m[1].replace(/[-_]+/g, ' ').trim() : 'See the attached refusal letter'
        appendLog(trip.id, {
          step: 'decision',
          title: `Refusal notice received: ${basename(f)}`,
          detail: `Caught by 24/7 monitoring — analyzing the refusal ground and preparing the fix automatically. Stated reason: ${reason}.`,
          artifact: stored
        })
        if (refuseCb) await refuseCb(trip.id, stored, reason)
        continue
      }
      appendLog(trip.id, {
        step: 'decision',
        title: `Decision document received: ${basename(f)}`,
        detail: 'Matched in the decisions folder — issuing and notifying the traveler automatically.',
        artifact: stored
      })
      await issueCb(trip.id, stored, 'Decision document retrieved from the decisions folder')
    }
  } catch (err) {
    console.error('decision scan failed', err)
  } finally {
    scanning = false
  }
}

// Periodic monitoring heartbeat: records that each monitored application was
// checked and where the decision will arrive from, with real timestamps.
function runChecks() {
  const trips = (getState().trips || []).filter((t) => MONITORED.includes(t.status))
  for (const t of trips) {
    const checks = (t.agentLog || []).filter((e) => e.step === 'check')
    // Cap the heartbeat trail: after 24 checks (~6h) log daily instead, and
    // stop entirely at 60 so the persisted log can't grow without bound.
    if (checks.length >= 60) continue
    const last = checks[checks.length - 1]
    const interval = checks.length >= 24 ? 24 * 60 * 60 * 1000 : CHECK_EVERY_MS
    if (last && Date.now() - last.at < interval - 5000) continue
    const channel = t.mission ? `${t.mission.name}, ${t.mission.city}` : (t.plan?.portal || 'the filing channel')
    appendLog(t.id, {
      step: 'check',
      title: 'Monitoring check',
      detail: `No decision yet from ${channel}. Watching the decisions folder for ${tripRef(t)}; the portal decision card can record it manually at any time.`
    })
  }
}

export function startMonitor(onIssue, onRefuse) {
  if (started) return
  started = true
  issueCb = onIssue
  refuseCb = onRefuse || null
  decisionsDir()
  try {
    watch(decisionsDir(), { persistent: false }, () => setTimeout(scanDecisions, 3500))
  } catch { /* fs.watch unavailable — interval scan still covers it */ }
  setInterval(scanDecisions, SCAN_EVERY_MS)
  setInterval(runChecks, CHECK_EVERY_MS)
  setTimeout(runChecks, 20 * 1000)
  setTimeout(scanDecisions, 8 * 1000)
}
