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
  fields.surname = surRaw.replace(/</g, ' ').trim()
  fields.givenNames = givRaw.replace(/</g, ' ').trim()
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
    return m ? m[1].trim().toUpperCase().replace(/\s{2,}/g, ' ') : null
  }
  const vSurname = grab(/surname[^A-Za-z\n]{0,4}([A-Za-z][A-Za-z '-]{0,30})/i)
  const vGiven = grab(/given\s*names?[^A-Za-z\n]{0,4}([A-Za-z][A-Za-z '-]{0,30})/i)
  const vNumber = grab(/passport\s*(?:no|number)\.?[^A-Z0-9\n]{0,4}([A-Z0-9]{6,10})/i)
  const roughly = (a, b) => {
    if (!a || !b) return false
    const ta = a.split(/\s+/), tb = b.split(/\s+/)
    return ta.some((x) => tb.some((y) => x === y || editDistance(x, y) <= Math.max(1, Math.floor(Math.max(x.length, y.length) / 4))))
  }
  if (vSurname && (roughly(vSurname, fields.surname) || roughly(vSurname, fields.givenNames) || !fields.surname)) {
    fields.surname = vSurname
    if (vGiven) fields.givenNames = vGiven
    fields.fullName = `${fields.givenNames || ''} ${fields.surname}`.trim()
    fields.visualZoneVerified = true
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

