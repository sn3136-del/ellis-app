// Trip.com demo-pipeline PDF generators. Every export here is used by the
// simulated demo portal (TripPortal) only; the shared helpers below render the
// official-form look its application-pack PDFs reuse.
import { ellis } from './api.js'

function esc(s) { return String(s ?? '').replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])) }

/* ---------------- Official-form rendering (shared helpers) ---------------- */
const FORM_CSS = `
  * { box-sizing: border-box; }
  body { font-family: "Times New Roman", Georgia, serif; color: #111; margin: 0; padding: 30px 34px; font-size: 12px; }
  .gov { border: 1.5px solid #111; }
  .govhead { display: flex; justify-content: space-between; align-items: stretch; border-bottom: 1.5px solid #111; }
  .agency { padding: 10px 12px; }
  .agency .dept { font-weight: 700; font-size: 12.5px; }
  .agency .bureau { font-size: 11.5px; }
  .agency .ellis { font-size: 9.5px; color: #555; margin-top: 4px; font-family: Arial, sans-serif; letter-spacing: .5px; }
  .agency { flex: 1; }
  .formmeta { text-align: right; padding: 10px 12px; border-left: 1.5px solid #111; min-width: 230px; }
  .formmeta .fno { font-weight: 800; font-size: 16px; }
  .formmeta .ftitle { font-size: 11px; }
  .formmeta .omb { font-size: 9.5px; color: #444; margin-top: 4px; }
  .usconly { font-family: Arial, sans-serif; font-size: 9.5px; background: #f0f0f0; border-bottom: 1.5px solid #111; padding: 5px 12px; letter-spacing: .3px; }
  .part { border-bottom: 1px solid #111; }
  .part:last-child { border-bottom: none; }
  .parthd { background: #111; color: #fff; font-family: Arial, sans-serif; font-weight: 700; font-size: 11px; padding: 5px 12px; letter-spacing: .3px; }
  .fgrid { display: grid; grid-template-columns: 1fr 1fr; }
  .fbox { border-right: 1px solid #ccc; border-bottom: 1px solid #ccc; padding: 5px 10px 7px; min-height: 42px; }
  .fbox.full { grid-column: 1 / -1; }
  .fbox .cap { display: block; font-family: Arial, sans-serif; font-size: 8.5px; color: #444; margin-bottom: 3px; }
  .fbox .val { display: block; font-size: 13px; font-weight: 600; min-height: 16px; }
  .fbox.miss { background: #f6f6f6; border-left: 3px solid #777; }
  .fbox.miss .val { color: #777; font-weight: 400; font-style: italic; }
  .checks { padding: 6px 12px; }
  .checks .ck { font-size: 11.5px; margin: 3px 0; }
  .cb { font-family: Arial, sans-serif; display: inline-block; width: 13px; }
  .cert { padding: 10px 12px; font-size: 10.5px; color: #222; border-top: 1.5px solid #111; }
  .sigrow { display: flex; gap: 30px; margin-top: 14px; }
  .sigrow .s { flex: 1; border-top: 1px solid #111; padding-top: 3px; font-family: Arial, sans-serif; font-size: 9px; color: #555; }
  .notebar { font-family: Arial, sans-serif; font-size: 9.5px; color: #666; margin: 12px 2px 0; }
  .miss-summary { margin: 12px 2px 0; font-family: Arial, sans-serif; font-size: 10px; }
  .miss-summary b { font-size: 10px; }
  .fbox .cap .nat { display: block; font-size: 10.5px; color: #111; font-family: "Times New Roman", "Hiragino Sans", "Apple SD Gothic Neo", serif; margin-bottom: 1px; }
  .photobox { width: 112px; min-height: 144px; border: 1.5px solid #111; margin: 10px 12px 10px 0; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; font-family: Arial, sans-serif; font-size: 8.5px; color: #555; padding: 6px; flex-shrink: 0; line-height: 1.5; }
  .titleband { text-align: center; border-bottom: 1.5px solid #111; padding: 12px 14px; }
  .titleband .natt { font-size: 20px; font-weight: 700; letter-spacing: 6px; font-family: "Times New Roman", "Hiragino Sans", "Apple SD Gothic Neo", serif; }
  .titleband .entt { font-size: 13.5px; font-weight: 700; letter-spacing: 3px; margin-top: 3px; }
  .titleband .tsub { font-family: Arial, sans-serif; font-size: 8.5px; color: #555; margin-top: 5px; }
  .cert .natc { display: block; font-size: 11px; margin-bottom: 3px; font-family: "Times New Roman", "Hiragino Sans", "Apple SD Gothic Neo", serif; }
`

function field(label, value) {
  const has = value !== undefined && value !== null && String(value).trim() !== '' && String(value).trim().toUpperCase() !== 'MISSING'
  return `<div class="fbox${has ? '' : ' miss'}"><span class="cap">${esc(label)}</span><span class="val">${has ? esc(value) : 'To be completed'}</span></div>`
}
// Bilingual field: native-script caption above the English one, exactly how
// the printed government forms label their boxes.
function bfield(native, label, value) {
  const has = value !== undefined && value !== null && String(value).trim() !== '' && String(value).trim().toUpperCase() !== 'MISSING'
  const cap = `${native ? `<span class="nat">${esc(native)}</span>` : ''}${esc(label)}`
  return `<div class="fbox${has ? '' : ' miss'}"><span class="cap">${cap}</span><span class="val">${has ? esc(value) : 'To be completed'}</span></div>`
}
function bfull(native, label, value) { return bfield(native, label, value).replace('class="fbox', 'class="fbox full') }
function fullField(label, value) { return field(label, value).replace('class="fbox', 'class="fbox full') }
function part(title, fields) { return `<div class="part"><div class="parthd">${esc(title)}</div><div class="fgrid">${fields.join('')}</div></div>` }
function checkPart(title, lines) {
  return `<div class="part"><div class="parthd">${esc(title)}</div><div class="checks">${lines.map((l) => `<div class="ck"><span class="cb">${l.on ? '\u2612' : '\u2610'}</span> ${esc(l.label)}</div>`).join('')}</div></div>`
}

/* ---------------- Trip.com issued visa / entry authorization ---------------- */
/* ---------------- Destination arrival registration pass (e.g. Visit Japan Web) ---------------- */
// Deterministic pseudo-random bits so the same traveler always gets the same
// QR pattern (looks authentic; demo document, not a scannable government code).
function qrGridHtml(seedStr) {
  let seed = 0
  for (const ch of String(seedStr)) seed = (seed * 31 + ch.charCodeAt(0)) >>> 0
  const rand = () => { seed = (seed * 1103515245 + 12345) >>> 0; return (seed >>> 16) & 1 }
  const N = 25
  const cells = []
  const inFinder = (r, c) => (r < 7 && c < 7) || (r < 7 && c >= N - 7) || (r >= N - 7 && c < 7)
  const finderOn = (r, c) => {
    const lr = r < 7 ? r : r - (N - 7)
    const lc = c < 7 ? c : c - (N - 7)
    return lr === 0 || lr === 6 || lc === 0 || lc === 6 || (lr >= 2 && lr <= 4 && lc >= 2 && lc <= 4)
  }
  for (let r = 0; r < N; r++) for (let c = 0; c < N; c++) {
    const on = inFinder(r, c) ? finderOn(r, c) : rand()
    cells.push(`<i${on ? ' class="on"' : ''}></i>`)
  }
  return `<div class="qr" style="grid-template-columns:repeat(${N},1fr)">${cells.join('')}</div>`
}

const PASS_CSS = `
  * { box-sizing: border-box; }
  body { font-family: "Helvetica Neue", Arial, sans-serif; color: #0a0a0a; margin: 0; padding: 44px 50px; }
  .pdoc { border: 2px solid #0a0a0a; border-radius: 10px; overflow: hidden; }
  .phead { background: #0a0a0a; color: #fff; padding: 16px 22px; }
  .phead .t { font-size: 19px; font-weight: 800; letter-spacing: .4px; }
  .phead .s { font-size: 11.5px; opacity: .85; margin-top: 3px; }
  .pbody { display: flex; gap: 26px; padding: 22px; align-items: flex-start; }
  .qr { display: grid; width: 190px; height: 190px; padding: 10px; border: 1.5px solid #0a0a0a; border-radius: 6px; background: #fff; flex-shrink: 0; }
  .qr i { display: block; }
  .qr i.on { background: #0a0a0a; }
  .fields { flex: 1; }
  .frow { padding: 8px 0; border-bottom: 1px solid #e6e6e6; }
  .frow .k { display: block; font-size: 8.5px; letter-spacing: 1.2px; text-transform: uppercase; color: #666; margin-bottom: 3px; }
  .frow .v { font-size: 14px; font-weight: 700; }
  .pfoot { padding: 13px 22px; font-size: 10.5px; color: #444; border-top: 1.5px solid #0a0a0a; line-height: 1.6; }
  .note { margin-top: 14px; font-size: 10.5px; color: #777; }
`
function arrivalPassHtml(trip, sys) {
  const ref = 'TR' + String(trip.id || '').replace(/\D/g, '').slice(-9).padStart(9, '3')
  const issued = new Date().toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }).toUpperCase()
  const row = (k, v) => `<div class="frow"><span class="k">${esc(k)}</span><span class="v">${esc(v)}</span></div>`
  const body = `
    <div class="pdoc">
      <div class="phead"><div class="t">${esc(sys.name).toUpperCase()}</div><div class="s">${esc(trip.destination)} · Arrival pack — prepared by Trip.com visa services</div></div>
      <div class="pbody">
        <div class="fields" style="max-width:46%">
          <div class="frow"><span class="k">Before you fly</span><span class="v" style="font-size:12px;font-weight:600;line-height:1.6">1. Complete the official ${esc(sys.name)} registration online.<br>2. Save the official ${esc(sys.doc)} on your phone.<br>3. Present your passport and the ${esc(sys.doc)} at immigration.</span></div>
        </div>
        <div class="fields">
          ${row('Traveler', trip.name)}
          ${row('Nationality', trip.nationality)}
          ${row('Destination', trip.destination)}
          ${row('Travel dates', `${trip.departure || '—'} to ${trip.return || '—'}`)}
          ${row('Registration reference', ref)}
          ${row('Issued', issued)}
        </div>
      </div>
      <div class="pfoot">Registration data prepared from your machine-read passport. The official ${esc(sys.doc)} is issued by ${esc(sys.name)} once the registration is submitted.</div>
    </div>
    <div class="note">Arrival pack generated by Trip.com visa services — the official ${esc(sys.doc)} comes from the authority.</div>
  `
  return `<!doctype html><html><head><meta charset="utf-8"><style>${PASS_CSS}</style></head><body>${body}</body></html>`
}

export async function downloadArrivalPassPdfToDesktop(trip, sys) {
  return ellis.exportPdfToDesktop({ html: arrivalPassHtml(trip, sys), suggestedName: `${trip.name} - ${sys.name}`.replace(/[^\w .-]/g, '') })
}

/* ---------------- Official appointment confirmation notice ---------------- */
// The consular-styled appointment confirmation the traveler brings to the
// mission: reference, appointment slot, location, and the checklist of
// documents to present. Attached to the appointment email and re-attached to
// the approval email so the full official record travels together.
const APPT_CSS = `
  * { box-sizing: border-box; }
  body { font-family: "Times New Roman", Georgia, serif; color: #101820; margin: 0; padding: 36px 42px; font-size: 12.5px; }
  .frame { border: 3px double #101820; padding: 2px; }
  .inner { border: 1px solid #101820; }
  .crest { text-align: center; padding: 18px 20px 8px; }
  .crest .dept { font-size: 15px; font-weight: 700; letter-spacing: .5px; }
  .crest .bureau { font-size: 11.5px; color: #333; margin-top: 2px; }
  .title { text-align: center; padding: 8px 20px 14px; border-bottom: 2px solid #101820; }
  .title .en { font-size: 15px; font-weight: 700; letter-spacing: 3px; }
  .refrow { display: flex; justify-content: space-between; padding: 9px 20px; border-bottom: 1.5px solid #101820; font-family: Arial, sans-serif; font-size: 11px; background: #f4f1e8; }
  .refrow b { font-size: 13px; letter-spacing: 1px; }
  .slot { display: grid; grid-template-columns: 1fr 1fr; border-bottom: 1.5px solid #101820; }
  .slot .cell { padding: 12px 18px; border-right: 1px solid #ddd; }
  .slot .cell:last-child { border-right: none; }
  .slot .k { display: block; font-family: Arial, sans-serif; font-size: 8.5px; letter-spacing: 1.2px; text-transform: uppercase; color: #666; margin-bottom: 4px; }
  .slot .v { font-size: 15px; font-weight: 700; }
  .sechd { font-family: Arial, sans-serif; font-size: 10px; font-weight: 700; letter-spacing: 1.6px; padding: 8px 20px 4px; color: #444; background: #fafaf7; border-bottom: 1px solid #ccc; }
  .who { display: grid; grid-template-columns: 1fr 1fr 1fr; border-bottom: 1.5px solid #101820; }
  .who .cell { padding: 8px 14px 9px; border-right: 1px solid #ddd; }
  .who .cell:nth-child(3n) { border-right: none; }
  .who .k { display: block; font-family: Arial, sans-serif; font-size: 8px; letter-spacing: 1px; text-transform: uppercase; color: #666; margin-bottom: 3px; }
  .who .v { font-size: 13px; font-weight: 700; }
  .bring { padding: 10px 20px 12px; border-bottom: 1.5px solid #101820; }
  .bring ol { margin: 4px 0 0; padding-left: 20px; }
  .bring li { font-size: 12px; margin: 3px 0; }
  .rules { padding: 10px 20px 14px; font-size: 11px; color: #333; line-height: 1.6; }
  .foot { margin-top: 12px; font-family: Arial, sans-serif; font-size: 9px; color: #888; line-height: 1.5; }
`
function appointmentNoticeHtml(trip, plan, appt, mission) {
  const ref = 'APT' + String(trip.id || '').replace(/\D/g, '').slice(-8).padStart(8, '6')
  const when = appt?.at ? new Date(appt.at) : null
  const dateStr = when ? when.toLocaleDateString('en-GB', { weekday: 'long', day: '2-digit', month: 'long', year: 'numeric' }) : '—'
  const timeStr = when ? when.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '—'
  const where = mission ? `${mission.name}` : (appt?.where || `${trip.destination} consulate`)
  const addr = mission?.address ? `${mission.address}, ${mission.city}` : (appt?.where || '')
  const p = trip.passport || {}
  const covered = (trip.gapAnalysis?.covered || []).map((c) => c.requirement).filter((r) => !/trip\.com|appointment|fee|form/i.test(r)).slice(0, 5)
  const bring = ['Passport (original) — ' + (p.passportNumber || 'as verified'), 'This appointment confirmation (printed or on your phone)', 'The signed visa application (attached to your confirmation email)', ...covered.map((r) => r + ' (original)')]
  const cell = (k, v, cls = 'cell') => `<div class="${cls}"><span class="k">${esc(k)}</span><span class="v">${esc(v || '—')}</span></div>`
  const body = `
  <div class="frame"><div class="inner">
    <div class="crest"><div class="dept">${esc(where)}</div><div class="bureau">Visa Section${addr ? ' · ' + esc(addr) : ''}</div></div>
    <div class="title"><div class="en">APPOINTMENT CONFIRMATION — VISA APPLICATION</div></div>
    <div class="refrow"><span>CONFIRMATION NUMBER&nbsp;&nbsp;<b>${ref}</b></span><span>${esc(plan?.headline || trip.destination + ' tourist visa')}</span></div>
    <div class="slot">
      ${cell('Date of appointment', dateStr)}
      ${cell('Time (arrive 15 minutes early)', timeStr)}
    </div>
    <div class="sechd">APPLICANT</div>
    <div class="who">
      ${cell('Full name', p.fullName || trip.name)}
      ${cell('Nationality', p.nationality || trip.nationality)}
      ${cell('Passport number', p.passportNumber || '—')}
    </div>
    <div class="sechd">DOCUMENTS TO PRESENT</div>
    <div class="bring"><ol>${bring.map((b) => `<li>${esc(b)}</li>`).join('')}</ol></div>
    <div class="rules">Arrive 15 minutes before the appointment with all originals. Biometric data may be collected. Late arrival may require rebooking. If you cannot attend, reschedule through Trip.com visa services — do not miss the slot, as consular calendars fill quickly.</div>
  </div></div>
  <div class="foot">Appointment booked and confirmed by Trip.com visa services with the mission's visa section · Ref ${esc(ref)} · Calendar invitation (.ics) attached to the same email.</div>`
  return `<!doctype html><html><head><meta charset="utf-8"><style>${APPT_CSS}</style></head><body>${body}</body></html>`
}
export async function downloadAppointmentNoticePdfToDesktop(trip, plan, appt, mission) {
  return ellis.exportPdfToDesktop({
    html: appointmentNoticeHtml(trip, plan, appt, mission),
    suggestedName: `${trip.name} - ${trip.destination} Appointment Confirmation`.replace(/[^\w .-]/g, '')
  })
}

/* ---------------- Trip.com milestone receipt (submission / appointment) ---------------- */
const RECEIPT_CSS = `
  * { box-sizing: border-box; }
  body { font-family: "Helvetica Neue", Arial, sans-serif; color: #0f294d; margin: 0; padding: 44px 50px; }
  .rdoc { border: 1.5px solid #e3ebf5; border-radius: 14px; overflow: hidden; }
  .rhead { background: #287dfa; color: #fff; padding: 18px 22px; display: flex; justify-content: space-between; align-items: center; }
  .rhead .t { font-size: 18px; font-weight: 800; letter-spacing: -0.2px; }
  .rhead .s { font-size: 12px; opacity: .9; text-align: right; }
  .rband { background: #eef4ff; padding: 10px 22px; font-size: 12px; font-weight: 700; color: #1c66d9; }
  .rgrid { display: grid; grid-template-columns: 1fr 1fr; }
  .rcell { padding: 12px 18px; border-bottom: 1px solid #eef2f8; }
  .rcell .k { display: block; font-size: 9px; letter-spacing: 1px; text-transform: uppercase; color: #8592a6; margin-bottom: 4px; }
  .rcell .v { font-size: 14px; font-weight: 700; color: #0f294d; }
  .rfoot { padding: 14px 22px; font-size: 11px; color: #5a6b85; line-height: 1.5; }
  .note { margin-top: 14px; font-size: 10.5px; color: #8592a6; }
`
function receiptHtml(trip, plan, kind, extra = {}) {
  const ref = 'TR' + String(trip.id || '').replace(/\D/g, '').slice(-9).padStart(9, '2')
  const issued = new Date().toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
  const titles = {
    submitted: 'Application receipt',
    appointment: 'Appointment receipt'
  }
  const title = titles[kind] || 'Trip.com receipt'
  const cell = (k, v) => `<div class="rcell"><span class="k">${esc(k)}</span><span class="v">${esc(v || '—')}</span></div>`
  const body = `
    <div class="rdoc">
      <div class="rhead"><div class="t">Trip.com</div><div class="s">${esc(title)}<br><b>${ref}</b></div></div>
      <div class="rband">${esc(plan?.headline || `${trip.destination} tourist visa`)}</div>
      <div class="rgrid">
        ${cell('Traveler', trip.name)}
        ${cell('Route', `${trip.nationality} → ${trip.destination}`)}
        ${cell('Travel dates', `${trip.departure || '—'} to ${trip.return || '—'}`)}
        ${cell('Fee', plan?.fee || 'None')}
        ${cell('Channel', plan?.portal || 'Trip.com')}
        ${cell('Issued', issued)}
        ${extra.when ? cell('Appointment', extra.when) : ''}
        ${extra.where ? cell('Location', extra.where) : ''}
      </div>
      <div class="rfoot">Keep this receipt for your records. Trip.com visa services will email your next update automatically.</div>
    </div>
    <div class="note">Service record — generated by Trip.com visa services.</div>
  `
  return `<!doctype html><html><head><meta charset="utf-8"><style>${RECEIPT_CSS}</style></head><body>${body}</body></html>`
}

export async function downloadTripReceiptPdfToDesktop(trip, plan, kind = 'submitted', extra = {}) {
  const label = kind === 'appointment' ? 'Appointment Receipt' : 'Application Receipt'
  return ellis.exportPdfToDesktop({
    html: receiptHtml(trip, plan, kind, extra),
    suggestedName: `${trip.name} - ${trip.destination} ${label}`.replace(/[^\w .-]/g, '')
  })
}

/* ---------------- Trip.com application package ---------------- */
// The real work-product of the agent: the application form filled from
// extracted passport data (every value traced to its source), the requirement
// checklist from the gap review, and the documents on file. This is what gets
// submitted to the agency/consulate and what the traveler receives by email.
const PACK_CSS = `
  * { box-sizing: border-box; }
  body { font-family: "Helvetica Neue", Arial, sans-serif; color: #0f294d; margin: 0; padding: 40px 46px; font-size: 12.5px; }
  .phead { display: flex; justify-content: space-between; align-items: flex-end; border-bottom: 2.5px solid #287dfa; padding-bottom: 12px; margin-bottom: 6px; }
  .phead .brand { font-size: 22px; font-weight: 800; color: #287dfa; letter-spacing: -0.3px; }
  .phead .meta { font-size: 11px; color: #5a6b85; text-align: right; }
  h1 { font-size: 17px; margin: 14px 0 2px; }
  .authority { color: #5a6b85; font-size: 12px; margin-bottom: 16px; }
  .sec { font-size: 10.5px; letter-spacing: 1.4px; text-transform: uppercase; color: #1c66d9; margin: 20px 0 8px; font-weight: 700; }
  .fgrid { display: grid; grid-template-columns: 1fr 1fr; border: 1px solid #d7e3f4; border-radius: 8px; overflow: hidden; }
  .fb { padding: 8px 12px; border-bottom: 1px solid #e8eef8; border-right: 1px solid #e8eef8; min-height: 44px; }
  .fb .cap { display: block; font-size: 8.5px; letter-spacing: 1px; text-transform: uppercase; color: #8592a6; margin-bottom: 2px; }
  .fb .val { font-size: 13px; font-weight: 700; }
  .fb .src { display: block; font-size: 9px; color: #9aa8bd; margin-top: 2px; }
  .fb.miss { background: #fbf7ee; }
  .fb.miss .val { color: #a15c00; font-weight: 500; font-style: italic; font-size: 12px; }
  .ck { display: flex; gap: 8px; padding: 6px 2px; border-bottom: 1px solid #eef2f8; align-items: baseline; }
  .ck .mark { font-weight: 800; width: 14px; flex-shrink: 0; }
  .ck.ok .mark { color: #1a7f37; }
  .ck.bad .mark { color: #a15c00; }
  .ck .why { color: #5a6b85; font-size: 11.5px; }
  .docrow { padding: 5px 2px; border-bottom: 1px solid #eef2f8; font-size: 12px; }
  .docrow .ex { color: #8592a6; font-size: 10.5px; }
  .foot { margin-top: 28px; padding-top: 12px; border-top: 1px solid #d7e3f4; font-size: 10px; color: #8592a6; line-height: 1.5; }
`
function applicationPackHtml(trip, plan, form, gap) {
  const ref = 'AP' + String(trip.id || '').replace(/\D/g, '').slice(-9).padStart(9, '5')
  const date = new Date().toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })
  const fieldBox = (f) => f.value
    ? `<div class="fb"><span class="cap">${esc(f.label)}</span><span class="val">${esc(f.value)}</span><span class="src">from ${esc(f.source)}</span></div>`
    : `<div class="fb miss"><span class="cap">${esc(f.label)}</span><span class="val">To be provided</span></div>`
  const checks = trip.docChecks || []
  const checkRow = (c) => `<div class="ck ${c.ok ? 'ok' : 'bad'}"><span class="mark">${c.ok ? '✓' : '!'}</span><div><b>${esc(c.label)}</b> — <span class="why">${esc(c.detail)}</span></div></div>`
  const covRow = (c) => `<div class="ck ok"><span class="mark">✓</span><div>${esc(c.requirement)} <span class="why">— ${esc(c.satisfiedBy || 'covered')}</span></div></div>`
  const misRow = (m) => `<div class="ck bad"><span class="mark">!</span><div>${esc(m.requirement)} <span class="why">— ${esc(m.why || 'still needed')}</span></div></div>`
  const docs = (trip.documents || [])
  const body = `
    <div class="phead">
      <div class="brand">Trip.com <span style="color:#0f294d;font-weight:600;font-size:14px">· Visa application package</span></div>
      <div class="meta">Reference ${ref}<br>Prepared ${esc(date)}</div>
    </div>
    <h1>${esc(form.form)}</h1>
    <div class="authority">${esc(form.authority)} · ${esc(trip.nationality)} → ${esc(trip.destination)} · ${esc(plan.headline)}</div>
    <div class="sec">Application — ${form.completeness}% completed from extracted data</div>
    <div class="fgrid">${(form.fields || []).map(fieldBox).join('')}</div>
    ${checks.length ? `<div class="sec">Passport verification</div>${checks.map(checkRow).join('')}` : ''}
    ${gap ? `<div class="sec">Requirements covered (${(gap.covered || []).length})</div>${(gap.covered || []).map(covRow).join('') || '<div class="ck"><div class="why">None yet.</div></div>'}` : ''}
    ${gap && (gap.missing || []).length ? `<div class="sec">Still needed from the traveler (${gap.missing.length})</div>${gap.missing.map(misRow).join('')}` : ''}
    ${docs.length ? `<div class="sec">Documents on file (${docs.length})</div>${docs.map((d) => `<div class="docrow">${esc(d.name)}${d.extracted?.passportNumber ? ` <span class="ex">· machine-read: ${esc(d.extracted.passportNumber)}</span>` : ''}</div>`).join('')}` : ''}
    <div class="foot">Assembled automatically by Trip.com visa services from the traveler's uploaded documents${gap?.engine && gap.engine !== 'builtin' ? `, reviewed by ${gap.engine === 'kimi' ? 'Kimi K3' : 'on-device AI'}` : ''}. Values marked "from passport MRZ" were machine-read on-device from the uploaded passport. This package accompanies the filing through ${esc(plan.portal || 'the official channel')}.</div>
  `
  return `<!doctype html><html><head><meta charset="utf-8"><style>${PACK_CSS}</style></head><body>${body}</body></html>`
}

export async function downloadTripApplicationPackPdfToDesktop(trip, plan, form, gap) {
  return ellis.exportPdfToDesktop({
    html: applicationPackHtml(trip, plan, form, gap),
    suggestedName: `${trip.name} - ${trip.destination} Application Package`.replace(/[^\w .-]/g, '')
  })
}

/* ---------------- Official application forms, filled from extracted data ---------------- */
// Faithful renderings of the actual forms each route files — Korea's
// Application for Visa, Japan's MOFA visa application, Thailand's TDAC — with
// every field populated from the machine-read passport and trip data. These
// are the documents an agency/consulate actually receives.
function fx(form, label) {
  const f = (form?.fields || []).find((x) => x.label === label)
  return f?.value || ''
}

function tripOfficialFormSpec(trip, plan, form) {
  const p = trip.passport || {}
  const surname = p.surname || (trip.name || '').trim().split(/\s+/).slice(-1)[0] || ''
  const given = p.givenNames || (trip.name || '').trim().split(/\s+/).slice(0, -1).join(' ') || ''
  const purpose = fx(form, 'Purpose of visit') || 'Tourism'
  const homeAddr = fx(form, 'Home address')
  const occupation = fx(form, 'Occupation')
  const funding = fx(form, 'Funding of stay') || 'Self-funded'
  const stayAddr = fx(form, 'Accommodation') || 'Hotel per attached Trip.com booking'
  const email = fx(form, 'Contact email')

  if (trip.destination === 'South Korea') {
    return {
      dept: 'Ministry of Justice, Republic of Korea', nativeDept: '대한민국 법무부', bureau: 'Korea Immigration Service · 출입국·외국인정책본부',
      nativeTitle: '사증발급신청서', title: 'APPLICATION FOR VISA',
      tsub: '별지 제17호 서식 · Form No. 17, Enforcement Rules of the Immigration Act · 신청서는 사실에 근거하여 정확하게 작성하여야 합니다 (Fill out in block letters, true and accurate)',
      photo: '사진 PHOTO\n3.5cm × 4.5cm\n흰색 바탕에 모자를 쓰지 않은 정면 사진\nTaken within last 6 months, full face, no hat, white background',
      parts: [
        part('1. 인적사항 / PERSONAL DETAILS', [
          bfield('여권과 동일한 영문 성명 — 성', 'Surname (as shown in passport)', surname),
          bfield('명', 'Given names', given),
          bfield('성별', 'Sex  ☐ Male ☐ Female', fx(form, 'Sex')),
          bfield('생년월일', 'Date of Birth (yyyy/mm/dd)', fx(form, 'Date of birth')),
          bfield('국적', 'Nationality', fx(form, 'Nationality')),
          bfield('출생국가', 'Country of Birth', fx(form, 'Nationality'))
        ]),
        part('2. 여권정보 / PASSPORT INFORMATION', [
          bfield('여권종류', 'Passport Type', 'Regular · 일반'),
          bfield('여권번호', 'Passport No.', fx(form, 'Passport number')),
          bfield('발급국가', 'Country of Issue', p.issuingCountry || fx(form, 'Issuing country')),
          bfield('기간만료일', 'Date of Expiry', fx(form, 'Passport expiry'))
        ]),
        part('3. 연락처 / CONTACT INFORMATION', [
          bfull('본국 주소', 'Address in Home Country', homeAddr),
          bfield('이메일', 'E-mail', email),
          bfield('휴대전화', 'Cell Phone No.', fx(form, 'Phone'))
        ]),
        part('4. 직업 / EMPLOYMENT', [
          bfield('직업', 'Occupation', occupation),
          bfield('경비지불자', 'Person paying travel costs', `${funding} · 본인 Self`)
        ]),
        checkPart('5. 사증 종류 / TYPE OF VISA APPLIED FOR', [
          { label: 'C-3-1 단기일반 Short-term general (tourism, visiting)', on: true },
          { label: 'C-3-9 일반관광 General tourist', on: false },
          { label: '단수사증 Single entry', on: true },
          { label: '복수사증 Multiple entry', on: false }
        ]),
        part('6. 방문정보 / DETAILS OF VISIT', [
          bfield('입국목적', 'Purpose of Visit to Korea', purpose),
          bfield('입국예정일', 'Intended Date of Entry', fx(form, 'Intended arrival')),
          bfield('출국예정일', 'Intended Date of Departure', fx(form, 'Intended departure')),
          bfield('체류예정기간', 'Intended Period of Stay', fx(form, 'Length of stay')),
          bfull('체류예정지 (호텔 포함)', 'Address in Korea (incl. hotel)', stayAddr),
          bfield('과거 5년간 한국 방문', 'Visits to Korea in last 5 years', 'None declared'),
          bfield('동반가족', 'Accompanying family members', 'None declared')
        ])
      ],
      nativeCert: '본인은 이 신청서에 기재된 내용이 거짓 없이 정확하게 작성되었음을 확인합니다.',
      cert: 'I declare that the statements made in this application are true and correct to the best of my knowledge and belief, and that I will comply with the Immigration Act of the Republic of Korea.',
      sigLabel: '신청인 서명 SIGNATURE OF APPLICANT', dateLabel: '작성일 DATE OF APPLICATION'
    }
  }
  if (trip.destination === 'Japan') {
    return {
      dept: 'Ministry of Foreign Affairs of Japan', nativeDept: '外務省', bureau: plan.channel === 'agency' ? 'Filed via MOFA-accredited travel agency · 指定旅行会社経由' : 'Embassy / Consulate-General of Japan',
      nativeTitle: '査証申請書', title: 'VISA APPLICATION FORM TO ENTER JAPAN',
      tsub: 'Please print in block letters · 楷書で記入してください',
      photo: '写真 PHOTO\n4.5cm × 4.5cm\nTaken within last 6 months, full face, plain background',
      parts: [
        part('PERSONAL DETAILS · 身分事項', [
          bfield('姓', 'Surname (as shown in passport)', surname),
          bfield('名・ミドルネーム', 'Given and middle names', given),
          bfield('生年月日', 'Date of Birth (dd/mm/yyyy)', fx(form, 'Date of birth')),
          bfield('性別', 'Sex  ☐ Male ☐ Female', fx(form, 'Sex')),
          bfield('出生地', 'Place of Birth', fx(form, 'Nationality')),
          bfield('国籍', 'Current Nationality', fx(form, 'Nationality'))
        ]),
        part('PASSPORT · 旅券', [
          bfield('種類', 'Type', 'Regular · 一般'),
          bfield('番号', 'Passport No.', fx(form, 'Passport number')),
          bfield('発行国', 'Issuing Country', p.issuingCountry || fx(form, 'Issuing country')),
          bfield('有効期限', 'Date of Expiry', fx(form, 'Passport expiry'))
        ]),
        part('CONTACT · 連絡先', [
          bfull('現住所', 'Current Residential Address', homeAddr),
          bfield('Eメール', 'E-mail Address', email),
          bfield('職業', 'Occupation', occupation)
        ]),
        part('DETAILS OF VISIT · 渡航内容', [
          bfield('渡航目的', 'Purpose of Visit to Japan', purpose),
          bfield('入国予定日', 'Intended Date of Arrival in Japan', fx(form, 'Intended arrival')),
          bfield('滞在予定期間', 'Intended Length of Stay in Japan', fx(form, 'Length of stay')),
          bfield('入国港', 'Port of Entry into Japan', 'Per attached flight itinerary'),
          bfull('滞在先 (ホテル名・住所)', 'Names and Addresses of Hotels', stayAddr),
          bfield('渡航費支弁者', 'Guarantor or Person Paying for Expenses', `${funding} · 本人 Self`),
          bfield('日本国内の招へい人', 'Inviter / Reference in Japan', 'None — package tour via Trip.com')
        ])
      ],
      nativeCert: '私は、この申請書の記載事項が真実かつ正確であることを申告します。',
      cert: 'I hereby declare that the statement given above is true and correct. I understand that immigration status and period of stay to be granted are decided by the Japanese immigration authorities upon my arrival.',
      sigLabel: '申請人署名 SIGNATURE OF APPLICANT', dateLabel: '申請年月日 DATE OF APPLICATION'
    }
  }
  if (trip.destination === 'Thailand' && plan.kind === 'free') {
    return {
      dept: 'Immigration Bureau, Kingdom of Thailand', nativeDept: 'สำนักงานตรวจคนเข้าเมือง', bureau: 'tdac.immigration.go.th',
      nativeTitle: 'บัตรขาเข้าดิจิทัล', title: 'THAILAND DIGITAL ARRIVAL CARD (TDAC)',
      tsub: 'Submit online within 72 hours before arrival · ยื่นออนไลน์ภายใน 72 ชั่วโมงก่อนเดินทางถึง',
      photo: null,
      parts: [
        part('PERSONAL INFORMATION · ข้อมูลส่วนบุคคล', [
          bfield('นามสกุล', 'Family Name', surname),
          bfield('ชื่อ', 'First / Middle Name', given),
          bfield('สัญชาติ', 'Nationality / Citizenship', fx(form, 'Nationality')),
          bfield('วันเกิด', 'Date of Birth', fx(form, 'Date of birth')),
          bfield('เพศ', 'Gender', fx(form, 'Sex')),
          bfield('หนังสือเดินทางเลขที่', 'Passport No.', fx(form, 'Passport number')),
          bfield('อาชีพ', 'Occupation', occupation),
          bfield('อีเมล', 'E-mail', email)
        ]),
        part('TRIP INFORMATION · ข้อมูลการเดินทาง', [
          bfield('วันที่เดินทางถึง', 'Date of Arrival', fx(form, 'Intended arrival')),
          bfield('วันที่เดินทางออก', 'Date of Departure', fx(form, 'Intended departure')),
          bfield('วัตถุประสงค์', 'Purpose of Travel', 'Holiday · ' + purpose),
          bfield('เที่ยวบิน', 'Flight No. / Vehicle', 'Per attached Trip.com itinerary'),
          bfield('ประเทศต้นทาง', 'Country of Departure', fx(form, 'Nationality')),
          bfull('ที่พักในประเทศไทย', 'Accommodation in Thailand', stayAddr)
        ])
      ],
      nativeCert: 'ข้าพเจ้าขอรับรองว่าข้อมูลข้างต้นเป็นความจริงทุกประการ',
      cert: 'I certify that the above information is true and complete. This record contains the data submitted to the official TDAC portal; the confirmation QR is delivered by the Immigration Bureau once issued.',
      sigLabel: 'SIGNATURE OF TRAVELER', dateLabel: 'DATE'
    }
  }
  return {
    dept: `${trip.destination} Immigration Authority`, nativeDept: '', bureau: form.authority || plan.portal || 'Consular section',
    nativeTitle: '', title: `APPLICATION FOR VISA — ${String(trip.destination).toUpperCase()}`,
    tsub: `${form.form || `${trip.destination} tourist visa application`} · ${plan.headline || ''} · Fill out in block letters`,
    photo: 'PHOTO\n3.5cm × 4.5cm\nTaken within last 6 months, full face, plain background',
    parts: [
      part('1. PERSONAL DETAILS', [
        field('Surname (as shown in passport)', surname),
        field('Given names', given),
        field('Date of birth', fx(form, 'Date of birth')),
        field('Sex', fx(form, 'Sex')),
        field('Nationality', fx(form, 'Nationality')),
        field('Country of birth', fx(form, 'Nationality'))
      ]),
      part('2. TRAVEL DOCUMENT', [
        field('Passport type', 'Regular'),
        field('Passport number', fx(form, 'Passport number')),
        field('Country of issue', p.issuingCountry || fx(form, 'Issuing country')),
        field('Date of expiry', fx(form, 'Passport expiry'))
      ]),
      part('3. CONTACT AND EMPLOYMENT', [
        fullField('Address in home country', homeAddr),
        field('E-mail address', email),
        field('Occupation', occupation),
        field('Funding of stay', funding)
      ]),
      part('4. DETAILS OF VISIT', [
        field('Purpose of visit', purpose),
        field('Intended date of entry', fx(form, 'Intended arrival')),
        field('Intended date of departure', fx(form, 'Intended departure')),
        field('Intended length of stay', fx(form, 'Length of stay')),
        fullField('Accommodation at destination', stayAddr),
        field('Previous visits', 'None declared')
      ])
    ],
    nativeCert: '',
    cert: 'I declare that the statements made in this application are true and correct to the best of my knowledge and belief.',
    sigLabel: 'SIGNATURE OF APPLICANT', dateLabel: 'DATE OF APPLICATION'
  }
}

function tripOfficialFormHtml(trip, plan, form) {
  const spec = tripOfficialFormSpec(trip, plan, form)
  const date = new Date().toLocaleDateString('en-US', { year: 'numeric', month: '2-digit', day: '2-digit' })
  const missing = form?.missing || []
  const sig = trip.signature || null
  const sigDate = sig ? new Date(sig.at).toLocaleDateString('en-US', { year: 'numeric', month: '2-digit', day: '2-digit' }) : ''
  const sigLabel = spec.sigLabel || 'SIGNATURE OF APPLICANT'
  const dateLabel = spec.dateLabel || 'DATE'
  const sigBlock = sig
    ? `<div class="sigrow">
        <div class="s" style="border-top:none">
          ${sig.image
            ? `<img src="${sig.image}" alt="signature" style="height:44px;display:block;margin-bottom:2px" /><span style="border-top:1px solid #111;display:block;padding-top:3px">${esc(sigLabel)}</span>`
            : `<span style="font-family:'Snell Roundhand','Brush Script MT',cursive;font-size:22px;display:block;margin-bottom:2px">${esc(sig.name)}</span><span style="border-top:1px solid #111;display:block;padding-top:3px">${esc(sigLabel)}</span>`}
        </div>
        <div class="s" style="border-top:none"><span style="font-size:13px;display:block;margin-bottom:${sig.image ? '33px' : '11px'}">${esc(sigDate)}</span><span style="border-top:1px solid #111;display:block;padding-top:3px">${esc(dateLabel)}</span></div>
      </div>`
    : `<div class="sigrow"><div class="s">${esc(sigLabel)}</div><div class="s">${esc(dateLabel)}</div></div>`
  const photoBox = spec.photo
    ? `<div class="photobox">${esc(spec.photo).replace(/\n/g, '<br>')}</div>`
    : ''
  const body = `
    <div class="gov">
      <div class="govhead">
        <div class="agency">
          <div class="dept">${esc(spec.dept)}</div>
          ${spec.nativeDept ? `<div class="bureau">${esc(spec.nativeDept)}</div>` : ''}
          <div class="bureau">${esc(spec.bureau)}</div>
          <div class="ellis">Completed by Trip.com visa services · ${date}${sig ? ` · Signed electronically by ${esc(sig.name)} on ${esc(sigDate)}` : ''}</div>
        </div>
        ${photoBox}
      </div>
      <div class="titleband">
        ${spec.nativeTitle ? `<div class="natt">${esc(spec.nativeTitle)}</div>` : ''}
        <div class="entt">${esc(spec.title)}</div>
        ${spec.tsub ? `<div class="tsub">${esc(spec.tsub)}</div>` : ''}
      </div>
      ${spec.parts.join('')}
      <div class="cert">
        ${spec.nativeCert ? `<span class="natc">${esc(spec.nativeCert)}</span>` : ''}
        ${esc(spec.cert)}
        ${sigBlock}
      </div>
    </div>
    ${missing.length ? `<div class="miss-summary"><b>${missing.length} field(s) to confirm before filing:</b> ${esc(missing.join(', '))}</div>` : '<div class="miss-summary"><b>All fields completed</b> from the extracted documents.</div>'}
    <div class="notebar">Application completed by Trip.com visa services from the traveler's verified documents and reviewed by the applicant before filing.</div>
  `
  return `<!doctype html><html><head><meta charset="utf-8"><style>${FORM_CSS}</style></head><body>${body}</body></html>`
}

export async function downloadTripOfficialFormPdfToDesktop(trip, plan, form) {
  const label = trip.destination === 'Thailand' && plan.kind === 'free' ? 'TDAC' : 'Visa Application Form'
  return ellis.exportPdfToDesktop({
    html: tripOfficialFormHtml(trip, plan, form),
    suggestedName: `${trip.name} - ${trip.destination} ${label}`.replace(/[^\w .-]/g, '')
  })
}
