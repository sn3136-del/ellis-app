// Machine-readable zone (TD3 passport) parsing, ICAO 9303 check-digit
// validation, and visual-zone cross-correction. Pure functions — shared by the
// Electron main process and the offline test harness.

export const ISO3_COUNTRY = {
  TUN: 'Tunisia', CHN: 'China', IND: 'India', USA: 'United States', CAN: 'Canada',
  GBR: 'United Kingdom', JPN: 'Japan', FRA: 'France', DEU: 'Germany', VNM: 'Vietnam',
  KOR: 'South Korea', BRA: 'Brazil', MEX: 'Mexico', EGY: 'Egypt', TUR: 'Turkey',
  THA: 'Thailand', SGP: 'Singapore', ARE: 'United Arab Emirates', AUS: 'Australia',
  NZL: 'New Zealand', ESP: 'Spain', ITA: 'Italy', NLD: 'Netherlands', CHE: 'Switzerland',
  PRT: 'Portugal', GRC: 'Greece', POL: 'Poland', IDN: 'Indonesia', PHL: 'Philippines',
  PAK: 'Pakistan', NGA: 'Nigeria', RUS: 'Russia', UKR: 'Ukraine', MAR: 'Morocco',
  DZA: 'Algeria', SAU: 'Saudi Arabia', IRL: 'Ireland', SWE: 'Sweden', NOR: 'Norway',
  DNK: 'Denmark', FIN: 'Finland', AUT: 'Austria', BEL: 'Belgium', COL: 'Colombia',
  ARG: 'Argentina', PER: 'Peru', KEN: 'Kenya', ETH: 'Ethiopia', BGD: 'Bangladesh',
  LKA: 'Sri Lanka', NPL: 'Nepal', MYS: 'Malaysia', HKG: 'Hong Kong', TWN: 'Taiwan',
  ZAF: 'South Africa', GHA: 'Ghana', SEN: 'Senegal', CMR: 'Cameroon', ZWE: 'Zimbabwe',
  QAT: 'Qatar', KWT: 'Kuwait', JOR: 'Jordan', LBN: 'Lebanon', IRQ: 'Iraq', IRN: 'Iran',
  ISR: 'Israel', AFG: 'Afghanistan', KAZ: 'Kazakhstan', UZB: 'Uzbekistan', KHM: 'Cambodia',
  HUN: 'Hungary', ROU: 'Romania', BGR: 'Bulgaria', HRV: 'Croatia', CZE: 'Czechia',
  ALB: 'Albania', CUB: 'Cuba', JAM: 'Jamaica', HTI: 'Haiti', HND: 'Honduras',
  GTM: 'Guatemala', SLV: 'El Salvador', DOM: 'Dominican Republic', CRI: 'Costa Rica',
  ECU: 'Ecuador', BOL: 'Bolivia', VEN: 'Venezuela', CHL: 'Chile', URY: 'Uruguay',
  PRY: 'Paraguay', TZA: 'Tanzania', UGA: 'Uganda', SDN: 'Sudan', LBY: 'Libya',
  MMR: 'Myanmar', LAO: 'Laos', MNG: 'Mongolia', ISL: 'Iceland', LUX: 'Luxembourg'
}

export function mrzDate(yymmdd, isExpiry) {
  if (!/^\d{6}$/.test(yymmdd)) return null
  const yy = parseInt(yymmdd.slice(0, 2), 10)
  const century = isExpiry ? (yy <= 60 ? 2000 : 1900) : (yy > (new Date().getFullYear() % 100) ? 1900 : 2000)
  return `${century + yy}-${yymmdd.slice(2, 4)}-${yymmdd.slice(4, 6)}`
}

// OCR confusion pairs, used only when a check digit says the raw read is
// wrong: digits misread as letters and vice versa.
const TO_DIGIT = { O: '0', Q: '0', D: '0', I: '1', L: '1', Z: '2', S: '5', B: '8', G: '6', T: '7', A: '4' }
const TO_ALPHA = { 0: 'O', 1: 'I', 2: 'Z', 5: 'S', 8: 'B', 6: 'G' }

// A name is letters only. When OCR reads a digit in a name field it is ALWAYS a
// misread letter, so map the common confusions back (0→O, 1→I, 5→S, 8→B) and
// drop any residual digit/glyph. Total and idempotent: 'N0EMI ELIAS' → 'NOEMI
// ELIAS'. Used everywhere a passport name is parsed, stored, or compared.
const NAME_DIGIT_TO_ALPHA = { 0: 'O', 1: 'I', 5: 'S', 8: 'B' }

export function normalizeName(s) {
  if (s == null) return ''
  return String(s).toUpperCase()
    .replace(/[<]+/g, ' ')
    .replace(/[0158]/g, (d) => NAME_DIGIT_TO_ALPHA[d])
    .replace(/[^A-Z '-]+/g, ' ')   // letters-only zone (+ space, apostrophe, hyphen)
    .replace(/\s+/g, ' ')
    .trim()
}

// Token-aware agreement between two names after letters-only normalization, so
// a digit-confusion can never make matching names look different. Tolerates a
// single-character OCR slip per token and one missing token in longer names.
export function namesAgree(a, b) {
  const ta = normalizeName(a).split(' ').filter(Boolean)
  const tb = normalizeName(b).split(' ').filter(Boolean)
  if (!ta.length || !tb.length) return true
  const hit = (t) => tb.some((x) => x === t || (t.length >= 3 && editDistance(t, x) <= 1))
  const hits = ta.filter(hit).length
  return hits >= Math.min(ta.length, tb.length) - (ta.length > 2 ? 1 : 0) && hits >= 1
}

function coerceDigits(s) {
  return s.replace(/[A-Z]/g, (c) => TO_DIGIT[c] || c)
}

// Repair an OCR-read field using its ICAO check digit as the oracle: if the
// raw read fails validation, try bounded substitutions of commonly-confused
// characters and accept the first variant whose check digit passes.
function repairWithCheckDigit(field, check) {
  if (mrzCheckDigit(field) === check) return { value: field, valid: true }
  const ambiguous = []
  for (let i = 0; i < field.length; i++) {
    const c = field[i]
    if (TO_DIGIT[c] || TO_ALPHA[c]) ambiguous.push(i)
  }
  const positions = ambiguous.slice(0, 6) // bounded: at most 64 variants
  for (let mask = 1; mask < 1 << positions.length; mask++) {
    let v = field.split('')
    for (let b = 0; b < positions.length; b++) {
      if (mask & (1 << b)) {
        const i = positions[b]
        v[i] = TO_DIGIT[v[i]] || TO_ALPHA[v[i]] || v[i]
      }
    }
    v = v.join('')
    if (mrzCheckDigit(v) === check) return { value: v, valid: true }
  }
  return { value: field, valid: false }
}

// Parse a TD3 (passport) machine-readable zone out of OCR'd text, repairing
// common OCR confusions using the ICAO 9303 check digits as ground truth.
export function parseMrz(text) {
  const lines = text.split(/\n/).map((l) => l.replace(/[«]/g, '<<').replace(/\s/g, '').toUpperCase()).filter((l) => l.length >= 30)
  const i1 = lines.findIndex((l) => /^P[<A-Z]/.test(l) && l.includes('<<'))
  if (i1 < 0) return null
  const l1 = lines[i1]
  // Line 2: prefer a strict structural match; fall back to any long
  // [A-Z0-9<] line after line 1 whose digit-coerced form matches (OCR often
  // reads digits as letters, which the strict pattern would reject).
  const candidates = lines.slice(i1 + 1).filter((l) => /^[A-Z0-9<]{40,}$/.test(l))
  let l2 = candidates.find((l) => /^[A-Z0-9<]{9}[0-9<][A-Z<]{3}\d{6}/.test(l))
  if (!l2) {
    l2 = candidates.find((l) => {
      const c = l.slice(0, 9) + coerceDigits(l.slice(9, 10)) + l.slice(10, 13) + coerceDigits(l.slice(13, 19))
      return /^[A-Z0-9<]{9}[0-9<][A-Z<]{3}\d{6}/.test(c)
    })
  }
  const fields = {}
  const issuer = l1.slice(2, 5).replace(/</g, '')
  const nameZone = l1.slice(5)
  const [surRaw, givRaw = ''] = nameZone.split('<<')
  // Names are letters only: normalize OCR digit-for-letter misreads at the
  // source so 'N0EMI' is stored as 'NOEMI', never a digit.
  fields.surname = normalizeName(surRaw)
  fields.givenNames = normalizeName(givRaw)
  fields.fullName = `${fields.givenNames} ${fields.surname}`.trim()
  fields.issuingCountry = ISO3_COUNTRY[issuer] || issuer
  if (l2) {
    const numCheck = coerceDigits(l2[9])
    const num = repairWithCheckDigit(l2.slice(0, 9), numCheck)
    fields.passportNumber = num.value.replace(/</g, '')
    const nat = l2.slice(10, 13).replace(/</g, '').replace(/[0-9]/g, (c) => TO_ALPHA[c] || c)
    fields.nationality = ISO3_COUNTRY[nat] || nat
    const dob = repairWithCheckDigit(coerceDigits(l2.slice(13, 19)), coerceDigits(l2[19]))
    fields.birthDate = mrzDate(dob.value, false)
    fields.sex = l2[20] === 'M' ? 'M' : l2[20] === 'F' ? 'F' : null
    const exp = repairWithCheckDigit(coerceDigits(l2.slice(21, 27)), coerceDigits(l2[27]))
    fields.expiryDate = mrzDate(exp.value, true)
    // ICAO 9303 check digits (weights 7-3-1): report whether each field
    // validated (possibly after repair) so downstream steps know what to trust.
    fields.checkDigits = {
      passportNumber: num.valid,
      birthDate: dob.valid,
      expiryDate: exp.valid
    }
  }
  return fields
}

export function mrzCheckDigit(s) {
  const val = (c) => (c === '<' ? 0 : /\d/.test(c) ? Number(c) : c.charCodeAt(0) - 55)
  const w = [7, 3, 1]
  let sum = 0
  for (let i = 0; i < s.length; i++) sum += val(s[i]) * w[i % 3]
  return String(sum % 10)
}

export function editDistance(a, b) {
  a = String(a); b = String(b)
  const dp = Array.from({ length: a.length + 1 }, (_, i) => [i, ...Array(b.length).fill(0)])
  for (let j = 0; j <= b.length; j++) dp[0][j] = j
  for (let i = 1; i <= a.length; i++) for (let j = 1; j <= b.length; j++) {
    dp[i][j] = Math.min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1))
  }
  return dp[a.length][b.length]
}

// The MRZ is dense and OCR sometimes misreads filler chevrons (`<<` → `S<`),
// corrupting the name. Passport bio pages print the same data in a labeled
// visual zone — cross-validate and prefer the visual value when the two
// roughly agree, exactly as a human agent would double-check.
export function refineMrzWithVisualZone(fields, text) {
  if (!fields) return fields
  const grab = (re) => {
    const m = text.match(re)
    return m ? m[1].trim() : null
  }
  // Visual-zone reads are letters-only too — normalize them the same way so a
  // digit-confusion on either side never registers as a disagreement.
  const vSurname = normalizeName(grab(/surname[^A-Za-z0-9\n]{0,4}([A-Za-z0-9][A-Za-z0-9 '-]{0,30})/i))
  const vGiven = normalizeName(grab(/given\s*names?[^A-Za-z0-9\n]{0,4}([A-Za-z0-9][A-Za-z0-9 '-]{0,30})/i))
  const vNumber = grab(/passport\s*(?:no|number)\.?[^A-Z0-9\n]{0,4}([A-Z0-9]{6,10})/i)
  const vFull = `${vGiven} ${vSurname}`.trim()

  // The MRZ machine-encoded name is the authoritative source; the visual zone
  // is a cross-check (brief: "prefer the valid MRZ value"). Only fall back to
  // the visual name when the MRZ name is missing.
  if (!fields.surname && vSurname) {
    fields.surname = vSurname
    fields.givenNames = vGiven
    fields.fullName = `${fields.givenNames || ''} ${fields.surname}`.trim()
    fields.visualZoneVerified = true
  } else if (vSurname || vGiven) {
    // Both present: agreement (after normalization) confirms; a real
    // disagreement is flagged so the applicant confirms before it is used.
    if (namesAgree(vFull, fields.fullName)) {
      fields.visualZoneVerified = true
    } else {
      fields.nameNeedsConfirmation = true
      fields.visualName = vFull
      fields.mrzName = fields.fullName
    }
  }
  if (vNumber && fields.passportNumber && vNumber !== fields.passportNumber) {
    // Trust whichever side validates: the MRZ number wins when its check
    // digit passes; otherwise the printed number corrects the misread.
    if (fields.checkDigits && !fields.checkDigits.passportNumber && editDistance(vNumber, fields.passportNumber) <= 2) {
      fields.passportNumber = vNumber
    }
  } else if (vNumber && !fields.passportNumber) {
    fields.passportNumber = vNumber
  }
  return fields
}

