// Minimal RFC 5545 calendar event for a booked appointment.
function pad(n) { return String(n).padStart(2, '0') }
function icsStamp(ms) {
  const d = new Date(ms)
  return `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}T${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}00Z`
}
export function appointmentIcs({ uid, startUtc, durationMin = 30, summary, location, description }) {
  const esc = (s) => String(s || '').replace(/[\\;,]/g, (c) => '\\' + c).replace(/\n/g, '\\n')
  return [
    'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Ellis//Visa Appointment//EN', 'BEGIN:VEVENT',
    `UID:${uid}`, `DTSTAMP:${icsStamp(Date.now())}`, `DTSTART:${icsStamp(startUtc)}`, `DTEND:${icsStamp(startUtc + durationMin * 60000)}`,
    `SUMMARY:${esc(summary)}`, `LOCATION:${esc(location)}`, `DESCRIPTION:${esc(description)}`,
    'END:VEVENT', 'END:VCALENDAR'
  ].join('\r\n')
}
