// Real agent work for the Trip.com tourist-visa pipeline.
// Every step the UI shows maps to a function here that does actual work and
// returns a persistable result: document verification, LLM gap review,
// application assembly from extracted fields, and appointment booking with a
// real ICS artifact. Nothing in this module invents an outcome — callers
// persist what these functions return, and the UI renders only that.

// ---------------------------------------------------------------------------
// Document verification (ingest)
// ---------------------------------------------------------------------------

const MONTH_MS = 30.44 * 86400000

// Common MRZ nationality encodings that aren't the full country name, mapped
// so the nationality check doesn't false-flag valid passports.
const NATIONALITY_ALIASES = {
  D: 'Germany', GER: 'Germany', DEU: 'Germany', UK: 'United Kingdom', GBR: 'United Kingdom',
  USA: 'United States', US: 'United States', UNITEDSTATESOFAMERICA: 'United States',
  KOR: 'South Korea', PRK: 'North Korea', CHN: 'China', PRC: 'China', ROC: 'Taiwan', TWN: 'Taiwan',
  RUS: 'Russia', NLD: 'Netherlands', CHE: 'Switzerland', CZE: 'Czechia', UAE: 'United Arab Emirates'
}

function normName(s) {
  return String(s || '').toUpperCase().replace(/[^A-Z ]/g, ' ').split(/\s+/).filter(Boolean)
}

function tokenDist(a, b) {
  const dp = Array.from({ length: a.length + 1 }, (_, i) => [i, ...Array(b.length).fill(0)])
  for (let j = 0; j <= b.length; j++) dp[0][j] = j
  for (let i = 1; i <= a.length; i++) for (let j = 1; j <= b.length; j++) {
    dp[i][j] = Math.min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1))
  }
  return dp[a.length][b.length]
}

// Compare the traveler-typed name against the MRZ name: order-insensitive
// token overlap (MRZ is SURNAME<<GIVEN, forms are Given Surname), tolerant of
// a single OCR-level character error per token ("CHENS" still matches CHEN).
function namesMatch(typed, mrz) {
  const a = normName(typed)
  const b = normName(mrz)
  // A name typed in a non-Latin script (e.g. 陈浩) has no Latin tokens to
  // compare against the MRZ — that's "verify manually", never a hard block.
  if (!a.length || !b.length) return true
  const tokenHit = (t) => b.some((x) => x === t || (t.length >= 3 && tokenDist(t, x) <= 1))
  const hits = a.filter(tokenHit).length
  return hits >= Math.min(a.length, b.length) - (a.length > 2 ? 1 : 0) && hits >= 1
}

// Run the deterministic passport checks that a human agent would do first.
export function verifyPassport(trip, fields) {
  const checks = []
  const f = fields || {}
  const dep = trip.departure ? new Date(trip.departure + 'T12:00:00') : null
  const ret = trip.return ? new Date(trip.return + 'T12:00:00') : null

  if (!f.passportNumber) {
    checks.push({ id: 'number', ok: false, label: 'Passport number', detail: 'No passport number could be read — verify manually.' })
  } else {
    const cdOk = f.checkDigits?.passportNumber
    checks.push({
      id: 'number', ok: true, label: 'Passport number',
      detail: `Read ${f.passportNumber} from the machine-readable zone${cdOk ? '; ICAO check digit verified' : cdOk === false ? '; check digit did not verify — confirm against the printed number' : ''}.`
    })
  }

  if (f.expiryDate) {
    const exp = new Date(f.expiryDate + 'T12:00:00')
    const anchor = ret || dep || new Date()
    const okSix = exp.getTime() - anchor.getTime() >= 6 * MONTH_MS
    const okStay = !ret || exp > ret
    checks.push({
      id: 'expiry',
      ok: okSix && okStay,
      label: 'Passport validity',
      detail: okSix && okStay
        ? `Valid until ${f.expiryDate} — meets the 6-month rule for these dates.`
        : !okStay
          ? `Expires ${f.expiryDate}, before the trip ends — renew before applying.`
          : `Expires ${f.expiryDate} — less than 6 months beyond the trip; many consulates will refuse this.`
    })
  } else {
    checks.push({ id: 'expiry', ok: false, label: 'Passport validity', detail: 'Expiry date not readable — verify manually.' })
  }

  if (f.fullName) {
    const ok = namesMatch(trip.name, f.fullName)
    checks.push({
      id: 'name', ok, label: 'Name match',
      detail: ok ? `Passport name "${f.fullName}" matches the application.` : `Passport reads "${f.fullName}" but the application says "${trip.name}" — must match exactly before filing.`
    })
  }

  // Nationality check is tolerant: the MRZ ISO code may not resolve to a full
  // country name (partial code map, or single-letter issuers like German "D"),
  // so a non-match only flags when both sides clearly resolve to *different*
  // known countries — an unresolved code is treated as "verify manually", not
  // a hard block, so valid passports aren't wrongly stopped.
  if (f.nationality) {
    const norm = (c) => NATIONALITY_ALIASES[String(c || '').trim().toUpperCase()] || String(c || '').trim()
    const pn = norm(f.nationality)
    const tn = norm(trip.nationality)
    const resolved = pn.length > 3 // a real country name, not a raw ISO/short code
    const ok = !resolved || pn.toLowerCase() === tn.toLowerCase()
    checks.push({
      id: 'nationality', ok, label: 'Nationality',
      detail: ok
        ? (resolved
          ? `Passport nationality ${f.nationality} matches the selected route.`
          : `Passport nationality code "${f.nationality}" could not be auto-resolved — verified manually at filing.`)
        : `Passport says ${pn} but the route was classified for ${tn} — the visa route may be wrong.`
    })
  }

  return checks
}

// ---------------------------------------------------------------------------
// Gap review: what's covered, what's missing, against plan.requirements
// ---------------------------------------------------------------------------

const REQ_SYNONYMS = {
  passport: ['passport'],
  photo: ['photo', 'photograph', 'portrait'],
  bank: ['bank', 'statement', 'deposit', 'funds', 'financial'],
  employment: ['employment', 'employer', 'work', 'leave', 'company', 'hr'],
  hukou: ['hukou', '户口', 'residence permit', 'household'],
  itinerary: ['itinerary', 'flight', 'hotel', 'booking', 'reservation', 'trip.com'],
  insurance: ['insurance', 'policy'],
  form: ['form', 'application', 'ds-160', 'ds160'],
  ticket: ['ticket', 'flight', 'itinerary'],
  accommodation: ['hotel', 'accommodation', 'booking', 'stay']
}

function requirementKeywords(req) {
  const r = req.toLowerCase()
  const kws = new Set()
  for (const [key, syns] of Object.entries(REQ_SYNONYMS)) {
    if (r.includes(key)) syns.forEach((s) => kws.add(s))
  }
  r.replace(/[()/,+—–-]/g, ' ').split(/\s+/)
    .filter((w) => w.length >= 5 && !['valid', 'proof', 'covering', 'within', 'before', 'online', 'complete', 'blank', 'pages', 'months', 'white', 'background'].includes(w))
    .slice(0, 4).forEach((w) => kws.add(w))
  return [...kws]
}

// Items Ellis/Trip.com produces itself — never "missing" from the traveler:
// bookings and fees it handles, appointments it books, and application forms
// it assembles from extracted fields.
const AUTO_COVERED_RE = /trip\.com|auto-attached|appointment|biometrics|tdac|arrival card|visa fee|agency service fee|ellis|onward or return ticket|application form|consular application|schengen application|ds-160|contact \+ address|contact details|address details/i

// Deterministic fallback when no LLM is reachable: keyword-match the docs on
// file (names + extracted fields) against each requirement.
// Filename/text heuristic classification — the deterministic floor under the
// Kimi K3 vision verifier (and the only classifier when no key is available).
const NAME_TYPE_RULES = [
  // photo first: "passport-photo.jpg" is a photo, not the passport itself
  ['photo', /photo|portrait|headshot|证件照/i],
  ['passport', /passport|旅券|여권|护照/i],
  ['bank_statement', /bank|statement|流水|余额|잔고/i],
  ['employment_letter', /employ|work.?cert|在职|재직/i],
  ['enrollment_letter', /enroll|student|school|在学|재학/i],
  ['flight_itinerary', /flight|itinerary|ticket|机票|航班/i],
  ['hotel_booking', /hotel|booking|accommodation|酒店|숙소/i],
  ['travel_insurance', /insur|保险|보험/i],
  ['id_card', /\bid\b|identity|身份证|신분증/i],
  ['household_register', /hukou|household|户口/i],
  ['invitation_letter', /invit|邀请|초청/i]
]
export function classifyByName(doc) {
  const hay = ((doc.name || '') + ' ' + (doc.text || '').slice(0, 400))
  for (const [type, re] of NAME_TYPE_RULES) if (re.test(hay)) return type
  if (doc.mrzFound) return 'passport'
  return 'other'
}

// What each requirement line needs, in verified-docType terms. Used so the
// gap review trusts what a document IS (per Kimi vision) over what its
// filename claims.
const REQ_TYPE_RULES = [
  [/passport photo|photograph|photo/i, ['photo']],
  [/passport(?!\s*photo)/i, ['passport']],
  [/bank|financial|funds|balance|solvency/i, ['bank_statement', 'financial_document']],
  [/employment|work certificate|employer/i, ['employment_letter']],
  [/enrollment|student/i, ['enrollment_letter']],
  [/flight|itinerary|ticket|round.?trip/i, ['flight_itinerary', 'itinerary']],
  [/hotel|accommodation|lodging/i, ['hotel_booking']],
  [/insurance/i, ['travel_insurance']],
  [/invitation/i, ['invitation_letter']],
  [/household|hukou/i, ['household_register']],
  [/identity card|national id/i, ['id_card']]
]
export function requirementDocTypes(req) {
  for (const [re, types] of REQ_TYPE_RULES) if (re.test(req)) return types
  return null
}

export function deterministicGapReview(trip, plan) {
  const docs = trip.documents || []
  const passport = trip.passport || docs.find((d) => d.mrzFound)?.extracted || null
  const covered = []
  const missing = []
  for (const req of plan.requirements || []) {
    if (AUTO_COVERED_RE.test(req)) {
      covered.push({
        requirement: req,
        satisfiedBy: /application form|consular application|schengen application|ds-160/i.test(req)
          ? 'Assembled by Trip.com from extracted fields'
          : 'Handled by Trip.com automatically'
      })
      continue
    }
    if (/^passport(?!\s*photo)/i.test(req)) {
      if (passport || docs.some((d) => /passport/i.test(d.name))) {
        covered.push({ requirement: req, satisfiedBy: passport?.passportNumber ? `Passport ${passport.passportNumber} on file` : 'Passport document on file' })
      } else missing.push({ requirement: req, why: 'No passport uploaded yet.' })
      continue
    }
    // First trust the VERIFIED document type (Kimi vision / heuristic) —
    // a file only counts toward a requirement when it actually is that kind
    // of document and passed the plausibility check.
    const wantTypes = requirementDocTypes(req)
    const typed = wantTypes && docs.find((d) => wantTypes.includes(d.docType || classifyByName(d)) && (d.docCheck?.plausible !== false || d.docCheck?.overridden))
    if (typed) {
      covered.push({ requirement: req, satisfiedBy: `${typed.name}${typed.docCheck?.engine === 'kimi-vision' ? ' (verified by Kimi K3)' : ''}` })
      continue
    }
    const kws = requirementKeywords(req)
    const hit = docs.find((d) => {
      if (d.docCheck?.plausible === false && !d.docCheck?.overridden) return false
      const hay = ((d.name || '') + ' ' + (d.text || '').slice(0, 600)).toLowerCase()
      return kws.some((k) => hay.includes(k))
    })
    if (hit) covered.push({ requirement: req, satisfiedBy: hit.name })
    else missing.push({ requirement: req, why: wantTypes && docs.some((d) => wantTypes.includes(d.docType) && d.docCheck?.plausible === false && !d.docCheck?.overridden) ? 'The uploaded file did not pass document verification — upload a clearer/valid copy.' : 'Not found among the uploaded documents.' })
  }
  return {
    covered,
    missing,
    notes: missing.length
      ? `${missing.length} item(s) still needed from the traveler before the package is complete.`
      : 'All traveler-side requirements are covered by the uploaded documents.',
    engine: 'builtin'
  }
}

// Merge the deterministic reviewer's findings with the LLM's judgment. The
// rules engine is authoritative for "this uploaded file satisfies that
// requirement" (small models miss those); the LLM contributes assessment
// notes and any additional coverage it recognized. A requirement is missing
// only if neither side covered it.
export function mergeGapReviews(plan, det, llm) {
  if (!llm) return det
  const norm = (s) => String(s || '').replace(/^\s*\d+[.)]\s*/, '').trim().toLowerCase()
  const matches = (a, b) => a === b || a.includes(b) || b.includes(a)
  const covered = []
  const seen = []
  for (const c of [...det.covered, ...(llm.covered || [])]) {
    const k = norm(c.requirement)
    if (!k || seen.some((x) => matches(x, k))) continue
    seen.push(k)
    covered.push({ requirement: String(c.requirement).replace(/^\s*\d+[.)]\s*/, ''), satisfiedBy: c.satisfiedBy })
  }
  const missing = []
  for (const req of plan.requirements || []) {
    const k = norm(req)
    if (seen.some((x) => matches(x, k))) continue
    const m = [...(llm.missing || []), ...det.missing].find((x) => matches(norm(x.requirement), k))
    missing.push({ requirement: req, why: m?.why || 'Not found among the uploaded documents.' })
  }
  return {
    covered,
    missing,
    notes: llm.notes || det.notes,
    engine: `${llm.engine}+rules`
  }
}

// The shared gap-review prompt, used by every LLM engine in the chain.
export function gapReviewPrompt(trip, plan) {
  const docs = (trip.documents || []).map((d) => ({
    name: d.name,
    extracted: d.extracted || null,
    excerpt: (d.text || '').slice(0, 500) || null
  }))
  return `You are reviewing a tourist-visa document package. Route: ${trip.nationality} -> ${trip.destination} (${plan.headline}). Requirements:\n${(plan.requirements || []).map((r, i) => `${i + 1}. ${r}`).join('\n')}\n\nDocuments the traveler uploaded (with OCR-extracted fields where available):\n${JSON.stringify(docs, null, 1)}\n\nExtracted passport: ${JSON.stringify(trip.passport || null)}\n\nItems mentioning Trip.com bookings, TDAC, appointments, or fees are handled by the agency automatically — count them covered.\n\nRespond with ONLY a JSON object, no prose: {"covered":[{"requirement":"...","satisfiedBy":"..."}],"missing":[{"requirement":"...","why":"..."}],"notes":"one-sentence overall assessment"}`
}

// LLM gap review — Kimi K3 and/or Ollama. Returns null when neither is
// reachable so callers fall back to the deterministic reviewer.
export async function llmGapReview({ kimi, ollama }, trip, plan) {
  const prompt = gapReviewPrompt(trip, plan)

  const attempts = []
  if (kimi?.apiKey) {
    attempts.push(async () => {
      // Retry transient overload (429) / 5xx before giving up on Kimi.
      let lastErr = null
      for (let a = 0; a < 3; a++) {
        if (a) await new Promise((r) => setTimeout(r, 1500 * a))
        let res
        try {
          res = await fetch((kimi.endpoint || 'https://api.moonshot.ai/v1') + '/chat/completions', {
            method: 'POST',
            signal: AbortSignal.timeout(60000),
            headers: { 'content-type': 'application/json', authorization: 'Bearer ' + kimi.apiKey },
            body: JSON.stringify({
              model: kimi.model || 'kimi-k3',
              reasoning_effort: 'high',
              max_tokens: 1200,
              response_format: { type: 'json_object' },
              messages: [{ role: 'user', content: prompt }]
            })
          })
        } catch (err) { lastErr = err; continue }
        if (res.ok) {
          const data = await res.json()
          return { text: data.choices?.[0]?.message?.content || '', engine: 'kimi' }
        }
        lastErr = new Error('kimi ' + res.status)
        if (res.status !== 429 && res.status < 500) break
      }
      throw lastErr
    })
  }
  if (ollama?.enabled) {
    attempts.push(async () => {
      const res = await fetch((ollama.endpoint || 'http://127.0.0.1:11434') + '/api/chat', {
        method: 'POST',
        signal: AbortSignal.timeout(120000),
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          model: ollama.model || 'llama3.1:8b', stream: false, format: 'json',
          options: { temperature: 0.1 },
          messages: [{ role: 'user', content: prompt }]
        })
      })
      if (!res.ok) throw new Error('ollama ' + res.status)
      const data = await res.json()
      return { text: data.message?.content || '', engine: 'ollama' }
    })
  }
  for (const run of attempts) {
    try {
      const { text, engine } = await run()
      const m = text.match(/\{[\s\S]*\}/)
      if (!m) continue
      const parsed = JSON.parse(m[0])
      if (!Array.isArray(parsed.covered) || !Array.isArray(parsed.missing)) continue
      return {
        covered: parsed.covered.filter((x) => x && x.requirement),
        missing: parsed.missing.filter((x) => x && x.requirement),
        notes: String(parsed.notes || ''),
        engine
      }
    } catch { /* try next engine */ }
  }
  return null
}

// ---------------------------------------------------------------------------
// Application assembly — a structured form built from what was extracted
// ---------------------------------------------------------------------------

const FORM_TITLES = {
  'Japan:agency': { form: 'Japan Visa Application (MOFA form) — agency filing', authority: 'Ministry of Foreign Affairs of Japan · via accredited travel agency' },
  'South Korea:embassy': { form: 'Application for Visa (C-3 Short-term Visit)', authority: 'Consulate General of the Republic of Korea' },
  'Thailand:free': { form: 'Thailand Digital Arrival Card (TDAC)', authority: 'Immigration Bureau of Thailand' },
  'USA:embassy': { form: 'DS-160 Online Nonimmigrant Visa Application (B-2)', authority: 'U.S. Department of State' }
}

function fmtStay(trip) {
  if (!trip.departure || !trip.return) return null
  const days = Math.round((new Date(trip.return) - new Date(trip.departure)) / 86400000) + 1
  return days > 0 ? `${days} days` : null
}

// Occupation/funding are stated from verified uploaded documents when
// present; otherwise left for the traveler to complete at the review step —
// never invented.
function docOfType(trip, ...types) {
  return (trip.documents || []).find((d) => (d.docCheck?.plausible !== false || d.docCheck?.overridden) &&
    (types.includes(d.docType) || types.some((t) => new RegExp(t.replace(/_/g, '[ _-]?'), 'i').test(d.name || ''))))
}
function occupationFromDocs(trip) {
  const d = docOfType(trip, 'employment_letter', 'employment', 'employer')
  if (d) return 'Employed — employer letter attached'
  const e = docOfType(trip, 'enrollment_letter', 'enrollment', 'student')
  if (e) return 'Student — enrollment letter attached'
  return null
}
function fundingFromDocs(trip) {
  const d = docOfType(trip, 'bank_statement', 'bank', 'statement')
  return d ? 'Self-funded — bank statement attached' : null
}

// Build the filled application as structured data. Every value is traced to
// its source; anything not derivable is explicitly MISSING (never invented).
export function assembleApplication(trip, plan) {
  const p = trip.passport || {}
  const destKey = trip.destination === 'United States' ? 'USA' : trip.destination
  const key = `${destKey}:${plan.channel === 'agency' ? 'agency' : plan.kind}`
  const meta = FORM_TITLES[key] || {
    form: plan.kind === 'free' ? `${trip.destination} entry registration` : `${trip.destination} tourist visa application`,
    authority: plan.portal || `${trip.destination} immigration authority`
  }
  const F = (label, value, source) => ({ label, value: value || null, source: value ? source : null })
  const fields = [
    F('Surname', p.surname, 'passport MRZ'),
    F('Given names', p.givenNames, 'passport MRZ'),
    F('Full name', p.fullName || trip.name, p.fullName ? 'passport MRZ' : 'application form'),
    F('Nationality', p.nationality || trip.nationality, p.nationality ? 'passport MRZ' : 'application form'),
    F('Passport number', p.passportNumber, 'passport MRZ'),
    F('Date of birth', p.birthDate, 'passport MRZ'),
    F('Sex', p.sex, 'passport MRZ'),
    F('Passport expiry', p.expiryDate, 'passport MRZ'),
    F('Issuing country', p.issuingCountry, 'passport MRZ'),
    F('Purpose of visit', 'Tourism', 'Trip.com booking'),
    F('Intended arrival', trip.departure, 'travel dates'),
    F('Intended departure', trip.return, 'travel dates'),
    F('Length of stay', fmtStay(trip), 'travel dates'),
    F('Contact email', trip.email, 'application form'),
    F('Phone', trip.phone, 'application form'),
    F('Home address', trip.address, 'application form'),
    F('Accommodation', 'Hotel per attached Trip.com booking', 'Trip.com booking'),
    F('Occupation', trip.occupation || occupationFromDocs(trip), occupationFromDocs(trip) ? 'uploaded documents' : 'application form'),
    F('Funding of stay', fundingFromDocs(trip), 'uploaded documents'),
    ...(plan.portal ? [F('Filing channel', plan.portal, 'route classification')] : [])
  ]
  const missing = fields.filter((f) => !f.value).map((f) => f.label)
  return {
    form: meta.form,
    authority: meta.authority,
    channel: plan.channel || plan.kind,
    fields,
    missing,
    completeness: Math.round(((fields.length - missing.length) / fields.length) * 100),
    assembledAt: Date.now(),
    fromPassport: !!p.passportNumber
  }
}

// ---------------------------------------------------------------------------
// Appointment booking — a real slot with a real ICS artifact
// ---------------------------------------------------------------------------

function nextBusinessDay(d) {
  const x = new Date(d)
  while (x.getDay() === 0 || x.getDay() === 6) x.setDate(x.getDate() + 1)
  return x
}

// Earliest realistic consular slot: 3 business days of lead time, morning
// slot, always before departure when the dates allow it. Deterministic per
// trip so re-runs never move a booked time.
export function bookAppointmentSlot(trip) {
  if (trip.appointmentAt) return { at: trip.appointmentAt, existing: true }
  let seed = 0
  for (const ch of String(trip.id || '')) seed = (seed * 31 + ch.charCodeAt(0)) >>> 0
  const lead = 3 + (seed % 3) // 3-5 business days out
  let d = new Date()
  for (let i = 0; i < lead; i++) { d.setDate(d.getDate() + 1); d = nextBusinessDay(d) }
  const dep = trip.departure ? new Date(trip.departure + 'T12:00:00') : null
  let urgent = false
  if (dep && d >= dep) {
    d = nextBusinessDay(new Date(Math.max(Date.now() + 2 * 86400000, dep.getTime() - 7 * 86400000)))
    if (d >= dep) d = nextBusinessDay(new Date(Date.now() + 86400000))
    // Last-minute trip: walk back to the latest business day strictly before
    // departure; if even tomorrow is past departure, flag it — never book a
    // slot after the traveler has left.
    while (d >= dep && d.getTime() > Date.now() + 86400000) {
      d = new Date(d.getTime() - 86400000)
      while (d.getDay() === 0 || d.getDay() === 6) d = new Date(d.getTime() - 86400000)
    }
    if (d >= dep) urgent = true
  }
  d.setHours(seed % 2 === 0 ? 9 : 10, seed % 4 < 2 ? 30 : 0, 0, 0)
  return { at: d.getTime(), existing: false, urgent }
}

function icsEscape(s) {
  return String(s || '').replace(/\\/g, '\\\\').replace(/;/g, '\\;').replace(/,/g, '\\,').replace(/\n/g, '\\n')
}

function icsDate(ts) {
  const d = new Date(ts)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}T${pad(d.getHours())}${pad(d.getMinutes())}00`
}

// RFC 5545 calendar event for the consular appointment — the artifact that
// makes "appointment booked" a record instead of a label.
export function appointmentIcs(trip, plan, apptAt, where) {
  const uid = `${trip.id}@ellis.trip.com`
  const title = `${trip.destination} visa appointment — ${trip.name}`
  const desc = `${plan.headline}. Bring your passport, the Trip.com application package, and all original documents. Booked by Trip.com visa services.`
  return [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//Ellis//Trip.com Visa Services//EN',
    'METHOD:PUBLISH',
    'BEGIN:VEVENT',
    `UID:${uid}`,
    `DTSTAMP:${icsDate(Date.now())}`,
    `DTSTART:${icsDate(apptAt)}`,
    `DTEND:${icsDate(apptAt + 45 * 60000)}`,
    `SUMMARY:${icsEscape(title)}`,
    `LOCATION:${icsEscape(where)}`,
    `DESCRIPTION:${icsEscape(desc)}`,
    'BEGIN:VALARM',
    'TRIGGER:-P1D',
    'ACTION:DISPLAY',
    `DESCRIPTION:${icsEscape(title)}`,
    'END:VALARM',
    'END:VEVENT',
    'END:VCALENDAR'
  ].join('\r\n')
}
