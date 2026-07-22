import { ISO3_COUNTRY, mrzCheckDigit } from './mrz.js'

// Realistic visa grant notice, generated main-side at issuance when the
// authority's decision arrives without its own document (manual Approve, or a
// bare decision drop). Modeled on real e-visa grant notices (Australia grant
// notice, Korea Confirmation of Visa Issuance, Vietnam e-visa): the full grant
// record with holder details, validity, conditions and an ICAO MRV zone built
// from the machine-read passport. When the authority's own file exists, that
// file is attached first and this notice accompanies it.

const COUNTRY_ISO3 = Object.fromEntries(Object.entries(ISO3_COUNTRY).map(([k, v]) => [v, k]))

const AUTHORITIES = {
  'South Korea': { native: '사증발급확인서', title: 'CONFIRMATION OF VISA ISSUANCE', dept: 'Ministry of Justice · Republic of Korea', nativeDept: '대한민국 법무부 · 출입국외국인정책본부', bureau: 'Korea Immigration Service' },
  Japan: { native: '査証発給通知', title: 'NOTICE OF VISA ISSUANCE', dept: 'Ministry of Foreign Affairs of Japan', nativeDept: '外務省', bureau: 'Consular Affairs Bureau' },
  Thailand: { native: 'การตรวจลงตราอิเล็กทรอนิกส์', title: 'THAILAND E-VISA', dept: 'Ministry of Foreign Affairs, Kingdom of Thailand', nativeDept: 'กระทรวงการต่างประเทศ', bureau: 'Department of Consular Affairs' },
  Vietnam: { native: 'THỊ THỰC ĐIỆN TỬ', title: 'ELECTRONIC VISA (E-VISA)', dept: 'Socialist Republic of Vietnam', nativeDept: 'Cục Quản lý xuất nhập cảnh', bureau: 'Vietnam Immigration Department' },
  China: { native: '签证颁发通知', title: 'NOTICE OF VISA ISSUANCE', dept: "Embassy / Consulate-General of the People's Republic of China", nativeDept: '中华人民共和国驻外使领馆', bureau: 'Consular Section' },
  India: { native: '', title: 'ELECTRONIC VISA GRANT (e-Visa)', dept: 'Government of India · Ministry of Home Affairs', nativeDept: 'भारत सरकार', bureau: 'Bureau of Immigration' },
  Australia: { native: '', title: 'VISA GRANT NOTICE', dept: 'Australian Government', nativeDept: '', bureau: 'Department of Home Affairs' },
  'New Zealand': { native: '', title: 'ELECTRONIC TRAVEL AUTHORITY — GRANT', dept: 'New Zealand Government', nativeDept: '', bureau: 'Immigration New Zealand' },
  'United Kingdom': { native: '', title: 'VISA DECISION — GRANTED', dept: 'UK Home Office', nativeDept: '', bureau: 'UK Visas and Immigration' },
  'United States': { native: '', title: 'VISA ISSUANCE NOTICE', dept: 'U.S. Department of State', nativeDept: '', bureau: 'Bureau of Consular Affairs' },
  Turkey: { native: 'e-Vize', title: 'ELECTRONIC VISA (e-Visa)', dept: 'Republic of Türkiye · Ministry of Foreign Affairs', nativeDept: 'Türkiye Cumhuriyeti Dışişleri Bakanlığı', bureau: 'e-Visa Application System' },
  'United Arab Emirates': { native: 'تأشيرة إلكترونية', title: 'ELECTRONIC VISA', dept: 'United Arab Emirates', nativeDept: 'الهيئة الاتحادية للهوية والجنسية', bureau: 'Federal Authority for Identity, Citizenship, Customs & Port Security' }
}

function esc(s) { return String(s ?? '').replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])) }

function toYYMMDD(s) {
  if (!s) return null
  const str = String(s).trim()
  let d = null
  if (/^\d{4}-\d{2}-\d{2}/.test(str)) d = new Date(str + 'T12:00:00')
  else { const t = Date.parse(str); if (!Number.isNaN(t)) d = new Date(t) }
  if (!d || Number.isNaN(d.getTime())) return null
  const pad = (n) => String(n).padStart(2, '0')
  return String(d.getFullYear()).slice(-2) + pad(d.getMonth() + 1) + pad(d.getDate())
}

function fmtLong(d) {
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }).toUpperCase()
}

// ICAO 9303 MRV-A machine-readable zone (2 × 44) from real passport data with
// valid check digits — the same math the passport reader validates.
function visaMrz(trip, p, enterBefore) {
  const iso = (c) => COUNTRY_ISO3[c] || (c || '').slice(0, 3).toUpperCase().padEnd(3, '<')
  const clean = (s) => String(s || '').toUpperCase().replace(/[^A-Z ]/g, '').trim()
  const surname = clean(p.surname) || clean((trip.name || '').split(/\s+/).slice(-1)[0])
  const given = clean(p.givenNames) || clean((trip.name || '').split(/\s+/).slice(0, -1).join(' '))
  let l1 = ('V<' + iso(trip.destination) + surname.replace(/ /g, '<') + '<<' + given.replace(/ /g, '<')).slice(0, 44)
  l1 = l1.padEnd(44, '<')
  const num = String(p.passportNumber || '').replace(/[^A-Z0-9]/gi, '').toUpperCase().slice(0, 9).padEnd(9, '<')
  const dob = toYYMMDD(p.birthDate) || '<<<<<<'
  const exp = toYYMMDD(enterBefore) || '<<<<<<'
  const sex = /^f/i.test(p.sex || '') ? 'F' : /^m/i.test(p.sex || '') ? 'M' : '<'
  let l2 = num + mrzCheckDigit(num) + iso(p.nationality || trip.nationality) + dob + mrzCheckDigit(dob) + sex + exp + mrzCheckDigit(exp)
  l2 = l2.padEnd(44, '<').slice(0, 44)
  return [l1, l2]
}

const VISA_CSS = `
  * { box-sizing: border-box; }
  body { font-family: "Times New Roman", Georgia, serif; color: #101820; margin: 0; padding: 34px 40px; font-size: 12.5px; }
  .frame { border: 3px double #101820; padding: 2px; }
  .inner { border: 1px solid #101820; }
  .crest { text-align: center; padding: 18px 20px 6px; }
  .crest .dept { font-size: 15px; font-weight: 700; letter-spacing: .6px; }
  .crest .ndept { font-size: 12px; margin-top: 2px; }
  .crest .bureau { font-size: 11.5px; color: #333; margin-top: 2px; }
  .title { text-align: center; padding: 10px 20px 14px; border-bottom: 2px solid #101820; }
  .title .nat { font-size: 17px; font-weight: 700; letter-spacing: 1px; }
  .title .en { font-size: 15px; font-weight: 700; letter-spacing: 3px; margin-top: 2px; }
  .grantno { display: flex; justify-content: space-between; padding: 9px 20px; border-bottom: 1.5px solid #101820; font-family: Arial, sans-serif; font-size: 11px; background: #f4f1e8; }
  .grantno b { font-size: 13px; letter-spacing: 1px; }
  .sechd { font-family: Arial, sans-serif; font-size: 10px; font-weight: 700; letter-spacing: 1.6px; padding: 7px 20px 4px; color: #444; border-bottom: 1px solid #ccc; background: #fafaf7; }
  .grid { display: grid; grid-template-columns: 1fr 1fr 1fr; }
  .cell { padding: 8px 14px 9px; border-right: 1px solid #ddd; border-bottom: 1px solid #ddd; }
  .cell:nth-child(3n) { border-right: none; }
  .cell .k { display: block; font-family: Arial, sans-serif; font-size: 8px; letter-spacing: 1px; text-transform: uppercase; color: #666; margin-bottom: 3px; }
  .cell .v { display: block; font-size: 13.5px; font-weight: 700; letter-spacing: .3px; }
  .cond { padding: 10px 20px; font-size: 11.5px; line-height: 1.65; border-bottom: 1.5px solid #101820; }
  .cond b { font-family: Arial, sans-serif; font-size: 10px; letter-spacing: 1.4px; }
  .officer { display: flex; justify-content: space-between; align-items: flex-end; padding: 12px 20px 14px; }
  .officer .box { font-family: Arial, sans-serif; font-size: 9px; color: #555; }
  .officer .box .line { border-top: 1px solid #101820; margin-top: 26px; padding-top: 3px; width: 190px; }
  .stamp { border: 2.5px solid #8b1a1a; color: #8b1a1a; border-radius: 6px; padding: 6px 16px; font-family: Arial, sans-serif; font-weight: 800; font-size: 13px; letter-spacing: 2px; transform: rotate(-6deg); opacity: .85; }
  .mrz { font-family: "Courier New", monospace; font-size: 14.5px; letter-spacing: 1.5px; line-height: 1.5; background: #f4f1e8; border-top: 2px solid #101820; padding: 12px 20px; white-space: pre; }
  .foot { margin-top: 12px; font-family: Arial, sans-serif; font-size: 9px; color: #888; line-height: 1.5; }
`

export function visaGrantHtml(trip, plan) {
  const p = trip.passport || {}
  const a = AUTHORITIES[trip.destination] || { native: '', title: 'VISA GRANT NOTICE', dept: `${trip.destination} Immigration Authority`, nativeDept: '', bureau: plan?.portal || 'Consular Section' }
  const iso = COUNTRY_ISO3[trip.destination] || 'XXX'
  const digits = String(trip.id || '').replace(/\D/g, '').slice(-8).padStart(8, '4')
  const grantNo = `${iso}${new Date().getFullYear()}${digits}`
  const granted = new Date()
  const validDays = parseInt(String(plan?.validity || '').match(/\d+/)?.[0] || '90', 10)
  const enterBefore = new Date(granted.getTime() + Math.max(validDays, 90) * 86400000)
  const stayDays = plan?.validity || `${validDays} days`
  const mission = trip.mission?.name || a.bureau
  const cell = (k, v) => `<div class="cell"><span class="k">${esc(k)}</span><span class="v">${esc(v || '—')}</span></div>`
  const [m1, m2] = visaMrz(trip, p, enterBefore.toISOString().slice(0, 10))
  const body = `
  <div class="frame"><div class="inner">
    <div class="crest">
      <div class="dept">${esc(a.dept)}</div>
      ${a.nativeDept ? `<div class="ndept">${esc(a.nativeDept)}</div>` : ''}
      <div class="bureau">${esc(a.bureau)}</div>
    </div>
    <div class="title">${a.native ? `<div class="nat">${esc(a.native)}</div>` : ''}<div class="en">${esc(a.title)}</div></div>
    <div class="grantno"><span>GRANT / CONFIRMATION NUMBER&nbsp;&nbsp;<b>${grantNo}</b></span><span>DATE OF GRANT&nbsp;&nbsp;<b>${fmtLong(granted)}</b></span></div>
    <div class="sechd">VISA HOLDER</div>
    <div class="grid">
      ${cell('Surname', p.surname || (trip.name || '').split(/\s+/).slice(-1)[0])}
      ${cell('Given names', p.givenNames || (trip.name || '').split(/\s+/).slice(0, -1).join(' '))}
      ${cell('Sex', p.sex || '—')}
      ${cell('Date of birth', p.birthDate || '—')}
      ${cell('Nationality', p.nationality || trip.nationality)}
      ${cell('Passport number', p.passportNumber || '—')}
    </div>
    <div class="sechd">VISA DETAILS</div>
    <div class="grid">
      ${cell('Visa class / type', plan?.headline || plan?.classification || 'Tourist visa')}
      ${cell('Number of entries', /mult/i.test(plan?.validity || '') ? 'Multiple' : 'Single')}
      ${cell('Period of stay', stayDays)}
      ${cell('Valid from', fmtLong(granted))}
      ${cell('Enter before', fmtLong(enterBefore))}
      ${cell('Issuing office', mission)}
    </div>
    <div class="cond"><b>CONDITIONS</b><br>
      Granted for tourism. Employment, paid activity and study are not permitted. The holder must present the passport shown above at the port of entry; final admission and length of stay are determined by the border officer. This document must be carried together with the passport for the duration of travel.</div>
    <div class="officer">
      <div class="box">Authorized officer<div class="line">${esc(mission)}</div></div>
      <div class="stamp">GRANTED · ${fmtLong(granted)}</div>
    </div>
    <div class="mrz">${esc(m1)}
${esc(m2)}</div>
  </div></div>
  <div class="foot">Grant record delivered through Trip.com visa services from the issuing authority's decision · Ref ${esc(grantNo)}. Where the authority issues its own visa document or vignette, that document governs.</div>`
  return `<!doctype html><html><head><meta charset="utf-8"><style>${VISA_CSS}</style></head><body>${body}</body></html>`
}
