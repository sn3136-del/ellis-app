// Deterministic tourist-visa routing engine for the Trip.com portal.
// Two layers: curated corridor rules for major destinations (fees, portals,
// filing channels from official government practice — MOFA Japan, France-Visas,
// US DOS/CBP, IRCC, Korean MOFA, etc.), backed by the full Passport Index
// matrix (visaMatrix.js) which is authoritative on WHETHER a visa is needed
// for every one of the ~199×199 country pairs.

import { matrixLookup } from './visaMatrix.js'

const SCHENGEN = ['France', 'Germany', 'Italy', 'Spain', 'Netherlands', 'Switzerland', 'Greece', 'Portugal', 'Austria', 'Belgium', 'Czechia', 'Denmark', 'Finland', 'Norway', 'Sweden', 'Poland', 'Hungary', 'Croatia', 'Iceland']

// Nationalities with broad visa-free / VWP access to developed destinations.
const STRONG_PASSPORTS = ['United States', 'Canada', 'United Kingdom', 'Japan', 'South Korea', 'Singapore', 'Australia', 'New Zealand', ...SCHENGEN, 'Ireland']

// Japan eVISA: some nationalities may file online directly; China/India/etc. must
// use a MOFA-accredited agency (source: mofa.go.jp eVISA page, May 2026).
const JAPAN_AGENCY_EVISA = ['China', 'India', 'Indonesia', 'Philippines', 'Vietnam', 'Mongolia']

function normCountry(c) {
  if (c === 'USA' || c === 'US' || c === 'United States of America') return 'United States'
  if (c === 'UK') return 'United Kingdom'
  if (c === 'Korea') return 'South Korea'
  return c
}

function schengenVac(destination) {
  // Intake centres used for visa-required applicants. The consulate still decides.
  if (destination === 'France') return { vac: 'TLScontact', portal: 'France-Visas + TLScontact' }
  if (destination === 'Netherlands') return { vac: 'TLScontact', portal: 'Netherlands TLScontact' }
  if (destination === 'Germany' || destination === 'Italy' || destination === 'Austria' || destination === 'Belgium') {
    return { vac: 'VFS Global', portal: `${destination} Visa Application Centre (VFS)` }
  }
  if (destination === 'Spain' || destination === 'Portugal') return { vac: 'BLS International', portal: `${destination} Visa Application Centre (BLS)` }
  if (destination === 'Switzerland') return { vac: 'TLScontact', portal: 'Switzerland TLScontact' }
  return { vac: `${destination} consulate`, portal: `${destination} consulate` }
}

// China-outbound corrections, verified against official sources July 2026.
// The 2024–2026 wave of mutual visa-exemption agreements outran the base
// dataset, so Chinese ordinary passports get an authoritative override table
// (nia.gov.cn, embassy announcements). Time-boxed schemes carry their window
// in the label so travelers see the condition.
const CN_2026 = {
  Turkey: { kind: 'free', label: 'Visa-free (90 days in 180 — since Jan 2026)', processing: 'None', validity: '90 days' },
  Russia: { kind: 'free', label: 'Visa-free (30 days — trial through Sep 14, 2026)', processing: 'None', validity: '30 days' },
  Brazil: { kind: 'free', label: 'Visa-free (30 days — reciprocal window May 11–Dec 31, 2026)', processing: 'None', validity: '30 days' },
  Philippines: { kind: 'free', label: 'Visa-free (14 days — trial through Jan 15, 2027)', processing: 'None', validity: '14 days' },
  Cambodia: { kind: 'free', label: 'Visa-free (14 days — trial through Oct 15, 2026; e-arrival card required)', processing: 'None', validity: '14 days' },
  Thailand: { kind: 'free', label: 'Visa-free (30 days, bilateral agreement)', portal: 'Thailand Digital Arrival Card (TDAC)', processing: 'None', validity: '30 days' },
  Georgia: { kind: 'free', label: 'Visa-free (30 days per entry, 90 in 180)', processing: 'None', validity: '30 days' },
  Armenia: { kind: 'free', label: 'Visa-free (90 days in 180)', processing: 'None', validity: '90 days' },
  Azerbaijan: { kind: 'free', label: 'Visa-free (30 days per entry — since Jul 2025)', processing: 'None', validity: '30 days' },
  Uzbekistan: { kind: 'free', label: 'Visa-free (30 days — since Jun 2025)', processing: 'None', validity: '30 days' },
  Serbia: { kind: 'free', label: 'Visa-free (30 days)', processing: 'None', validity: '30 days' },
  Belarus: { kind: 'free', label: 'Visa-free (30 days per entry, 90 per year)', processing: 'None', validity: '30 days' },
  Mauritius: { kind: 'free', label: 'Visa-free (90 days)', processing: 'None', validity: '90 days' },
  Maldives: { kind: 'free', label: 'Visa-free (30-day entry permit on arrival)', processing: 'None', validity: '30 days' },
  Oman: { kind: 'free', label: 'Visa-free (14 days — hotel, insurance and return ticket required)', processing: 'None', validity: '14 days' },
  'Sri Lanka': { kind: 'eta', label: 'Free ETA (30 days, double entry — fee waived)', portal: 'eta.gov.lk', fee: 'None (fee waived)', processing: '1–2 days', validity: '30 days' },
  Nepal: { kind: 'voa', label: 'Visa on arrival — fee waived for Chinese nationals', fee: 'None (gratis)', processing: '0 days', validity: '15/30/90 days' },
  Laos: { kind: 'voa', label: 'Visa on arrival (30 days)', fee: '~US$40 (eVisa US$50)', portal: 'laoevisa.gov.la', processing: '0 days', validity: '30 days' },
  Bahrain: { kind: 'voa', label: 'Visa on arrival / eVisa (14 days)', portal: 'evisa.gov.bh', fee: '~BHD 5–29', processing: '0–2 days', validity: '14 days' },
  Jordan: { kind: 'voa', label: 'Visa on arrival (30 days)', fee: 'JOD 40 (free with Jordan Pass)', processing: '0 days', validity: '30 days' },
  'Saudi Arabia': { kind: 'evisa', label: 'Saudi tourist eVisa (1-year multiple, 90-day stays)', portal: 'visa.visitsaudi.com', fee: '~US$101 incl. insurance', processing: '1–3 days', validity: '90 days per stay' },
  Myanmar: { kind: 'evisa', label: 'Myanmar tourist eVisa (28 days)', portal: 'evisa.moip.gov.mm', fee: 'US$50', processing: '3 days', validity: '28 days' },
  Mongolia: { kind: 'evisa', label: 'Mongolia K2 tourist eVisa (30 days)', portal: 'evisa.mn', fee: '~US$25', processing: '3 days', validity: '30 days' },
  'South Africa': { kind: 'eta', label: 'South Africa digital ETA (up to 90 days — 2026 rollout)', portal: 'Home Affairs ETA', processing: '3–7 days', validity: '90 days' },
  India: { kind: 'embassy', label: 'India regular tourist visa (e-visa not available to Chinese nationals)', portal: 'Indian Visa Application Centre', fee: '~US$77', processing: '5–15 days', validity: 'up to 1 year', appointment: 'consulate' },
  Ecuador: { kind: 'embassy', label: 'Ecuador consular tourist visa (exemption suspended since Jul 2024)', portal: 'Ecuadorian consulate', fee: 'Varies', processing: '10–30 days', validity: '90 days', appointment: 'consulate' }
}

// Destination rules: how each destination treats major traveler groups.
// kind: free | evisa | eta | voa | embassy
function routeFor(nationality, destination) {
  nationality = normCountry(nationality)
  destination = normCountry(destination) === 'United States' ? 'USA' : normCountry(destination)
  const strong = STRONG_PASSPORTS.includes(nationality) || nationality === 'USA'
  const d = destination
  if (normCountry(d) === nationality || (d === 'USA' && nationality === 'United States')) {
    return { kind: 'free', label: 'No visa needed (home country)' }
  }
  if (nationality === 'China' && CN_2026[d]) return { ...CN_2026[d] }

  // Matrix verdict for this exact pair — authoritative on visa-exemption.
  const m = matrixLookup(nationality, d === 'USA' ? 'United States' : d)
  if (m?.type === 'home') return { kind: 'free', label: 'No visa needed (home country)' }
  const exempt = m ? m.type === 'free' || m.type === 'eta' : strong

  if (d === 'USA') {
    // CBP ESTA for VWP; others file DS-160 at CEAC then consular interview (travel.state.gov).
    // ESTA fee is $40.27 as of Jan 2026 (raised from $21 under HR1, Sep 2025).
    if (exempt && nationality !== 'United States') {
      return { kind: 'eta', label: 'ESTA (Visa Waiver Program)', portal: 'ESTA (esta.cbp.dhs.gov)', fee: 'US$40.27', processing: '3 days', validity: '90 days' }
    }
    // MRV $185 + mandatory $250 Visa Integrity Fee at issuance (OBBBA, Jul 2025).
    return { kind: 'embassy', label: 'B-2 visitor visa', portal: 'CEAC (DS-160)', fee: 'US$185 (US$250 integrity fee enacted; collection expected by Sep 2026)', processing: '14–84 days', validity: '90 days', appointment: 'consulate' }
  }

  if (d === 'Canada') {
    // IRCC: eTA for visa-exempt; TRV online for visa-required (canada.ca).
    if (exempt) return { kind: 'eta', label: 'eTA (Electronic Travel Authorization)', portal: 'IRCC eTA', fee: 'CA$7', processing: '3 days', validity: '180 days' }
    return { kind: 'evisa', label: 'Visitor visa (TRV)', portal: 'IRCC secure account', fee: 'CA$100', processing: '14–56 days', validity: '180 days' }
  }

  if (d === 'United Kingdom') {
    // UK ETA for eligible; Standard Visitor on gov.uk for visa nationals.
    if (exempt) return { kind: 'eta', label: 'UK ETA', portal: 'gov.uk (UK ETA)', fee: '£20', processing: '3 days', validity: '180 days' }
    return { kind: 'evisa', label: 'Standard Visitor visa', portal: 'gov.uk', fee: '£135', processing: '21 days', validity: '180 days' }
  }

  if (SCHENGEN.includes(d)) {
    // Visa-exempt short-stay; others Type C via France-Visas / national VAC.
    // ETIAS is NOT operational yet (target Q4 2026, mandatory ~Apr 2027), so
    // visa-exempt nationals currently enter with no pre-authorization.
    if (m ? m.type === 'free' : strong) {
      return { kind: 'free', label: 'Visa-free (90 days in any 180)', portal: 'No pre-authorization (ETIAS from late 2026, €20)', processing: 'None', validity: '90 days' }
    }
    const vac = schengenVac(d)
    // France-Visas portal name for France; other states use their national form + VAC.
    const portal = d === 'France' ? 'France-Visas + TLScontact' : vac.portal
    return {
      kind: 'embassy',
      label: 'Schengen short-stay visa (Type C)',
      portal,
      fee: '€90',
      processing: '15 days',
      validity: '90 days',
      appointment: 'vac',
      vac: vac.vac
    }
  }

  if (d === 'Japan') {
    // Visa-exempt → Visit Japan Web only.
    // China etc. → MOFA-accredited travel agency collects docs; embassy/consulate decides
    // (mofa.go.jp visa topics/china + eVISA accredited-agency tier).
    if (m ? m.type === 'free' : strong) {
      return { kind: 'free', label: `Visa-free (${m?.days || 90} days)`, portal: 'Visit Japan Web', processing: 'None', validity: `${m?.days || 90} days` }
    }
    if (nationality === 'China') {
      return {
        kind: 'evisa',
        label: 'Japan short-stay tourist visa',
        portal: 'MOFA-accredited Chinese travel agency',
        fee: '¥3,000 + agency fee',
        processing: '5–7 days',
        validity: '15–30 days',
        channel: 'agency',
        appointment: null,
        summary: 'Visa required; submit passport, application, itinerary and financial documents through a Japan-approved travel agency, which forwards the application to the consulate; pay visa and agency fees.'
      }
    }
    if (JAPAN_AGENCY_EVISA.includes(nationality)) {
      return {
        kind: 'evisa',
        label: 'Japan eVISA (accredited agency)',
        portal: 'MOFA-accredited travel agency',
        fee: '¥3,000 + agency fee',
        processing: '5–7 days',
        validity: nationality === 'Vietnam' || nationality === 'Philippines' ? '15 days' : '90 days',
        channel: 'agency',
        appointment: null
      }
    }
    return {
      kind: 'evisa',
      label: 'Japan eVISA',
      portal: 'JAPAN eVISA (evisa.mofa.go.jp)',
      fee: '¥3,000',
      processing: '5–10 days',
      validity: '90 days'
    }
  }

  if (d === 'Thailand') {
    // Thai MFA: visa exemption for many nationalities incl. China (60 days under
    // the 2024 mutual exemption) and India (tourism).
    if (nationality === 'China') {
      const days = m?.days || 60
      return {
        kind: 'free',
        label: `Visa-free (${days} days, tourism)`,
        portal: 'Thailand Digital Arrival Card (TDAC)',
        processing: 'None',
        validity: `${days} days`,
        summary: `No visa for stays up to ${days} days—book travel, complete the free TDAC online within three days before arrival, and present your passport at immigration.`
      }
    }
    if ((m ? m.type === 'free' : strong) || nationality === 'India') {
      return { kind: 'free', label: `Visa-free (${m?.days || 60} days, tourism)`, portal: 'Thailand Digital Arrival Card (TDAC)', processing: 'None', validity: `${m?.days || 60} days` }
    }
    if (m?.type === 'voa') {
      return { kind: 'voa', label: 'Visa on arrival (15 days)', portal: 'Thailand VOA counter / TDAC', fee: 'THB 2,000', processing: '0 days', validity: '15 days' }
    }
    return { kind: 'evisa', label: 'Thailand eVisa (TR)', portal: 'thaievisa.go.th', fee: 'US$40', processing: '3–7 days', validity: '60 days' }
  }

  if (d === 'United Arab Emirates') {
    if (m ? m.type === 'free' || m.type === 'voa' : strong) return { kind: 'free', label: `Visa-free / on arrival (${m?.days || '30–90'} days)`, processing: '0 days', validity: `${m?.days || '30–90'} days` }
    return { kind: 'evisa', label: 'UAE tourist eVisa (30 days)', portal: 'UAE ICP official portal', fee: '~US$95', processing: '2–5 days', validity: '30 days' }
  }

  if (d === 'Singapore') {
    // ICA: visa-free for many; others need ICA entry visa; all complete SG Arrival Card.
    if (m ? m.type === 'free' : strong || nationality === 'United States') {
      return { kind: 'free', label: `Visa-free (${m?.days || 90} days) + SG Arrival Card`, portal: 'SG Arrival Card (ICA)', processing: '1 day', validity: `${m?.days || 90} days` }
    }
    return { kind: 'evisa', label: 'Singapore entry visa', portal: 'ICA Singapore', fee: 'S$30', processing: '3–5 days', validity: '90 days' }
  }

  if (d === 'Turkey') {
    if (nationality === 'China' || nationality === 'India') {
      return { kind: 'evisa', label: 'Turkey e-Visa', portal: 'evisa.gov.tr', fee: 'US$43–60', processing: '1 day', validity: '30 days' }
    }
    if (m ? m.type === 'free' : strong) return { kind: 'free', label: `Visa-free (up to ${m?.days || 90} days)`, processing: 'None', validity: `${m?.days || 90} days` }
    return { kind: 'evisa', label: 'Turkey e-Visa', portal: 'evisa.gov.tr', fee: 'US$50', processing: '1 day', validity: '30 days' }
  }

  if (d === 'Vietnam') {
    // 45-day visa exemption for a fixed list (UK, France, Germany, Italy, Spain,
    // Japan, South Korea, the Nordics, Russia, Belarus) through Aug 2028.
    const VN_EXEMPT = ['United Kingdom', 'France', 'Germany', 'Italy', 'Spain', 'Japan', 'South Korea', 'Denmark', 'Sweden', 'Norway', 'Finland', 'Russia', 'Belarus']
    if (m?.type === 'free' || VN_EXEMPT.includes(nationality)) {
      return { kind: 'free', label: `Visa-free (${m?.days || 45} days)`, portal: 'No visa required', processing: 'None', validity: `${m?.days || 45} days` }
    }
    return { kind: 'evisa', label: 'Vietnam E-visa (90 days)', portal: 'evisa.gov.vn', fee: 'US$25 (single) / US$50 (multiple)', processing: '3 days', validity: '90 days' }
  }

  if (d === 'Australia') {
    if (exempt) return { kind: 'eta', label: 'ETA (subclass 601)', portal: 'AustralianETA app', fee: 'A$20', processing: '1 day', validity: '90 days' }
    return { kind: 'evisa', label: 'Visitor visa (subclass 600)', portal: 'ImmiAccount', fee: 'A$200', processing: '14–28 days', validity: '365 days' }
  }

  if (d === 'South Korea') {
    // Visa-exempt nationalities: K-ETA. China is not visa-exempt — C-3 via Korean consulate / KVAC.
    if (nationality !== 'China' && (m ? m.type === 'free' || m.type === 'eta' : strong)) return { kind: 'eta', label: 'K-ETA', portal: 'k-eta.go.kr', fee: '₩10,000', processing: '3 days', validity: '90 days' }
    return {
      kind: 'embassy',
      label: 'C-3 short-term visitor visa',
      portal: nationality === 'China' ? 'Korean Consulate' : 'Korean Consulate / KVAC',
      fee: nationality === 'China' ? '~US$15 (group visa fee waived through Dec 31, 2026)' : 'US$40',
      processing: '7–14 days',
      validity: '90 days',
      appointment: 'consulate',
      summary: nationality === 'China'
        ? 'Normally obtain a C-3 tourist visa through a Korean consulate.'
        : 'C-3 short-term visitor visa filed at the Korean consulate before travel.'
    }
  }

  if (d === 'India') {
    return { kind: 'evisa', label: 'India e-Tourist Visa', portal: 'indianvisaonline.gov.in', fee: 'US$25–80 by duration', processing: '3 days', validity: '30 days' }
  }

  if (d === 'China') {
    // China unilateral 30-day visa-free list (expanded through Feb 2026: adds
    // Canada, UK, + Schengen, Australia, NZ, Japan, Korea, Singapore).
    // The United States is NOT on this list — US nationals need an L visa
    // (or the separate 240-hour transit exemption).
    if (m?.type === 'free' || SCHENGEN.includes(nationality) || ['Australia', 'New Zealand', 'Canada', 'United Kingdom', 'Japan', 'South Korea', 'Singapore'].includes(nationality)) {
      return { kind: 'free', label: `Visa-free (${m?.days || 30} days — unilateral policy)`, processing: 'None', validity: `${m?.days || 30} days` }
    }
    return {
      kind: 'embassy',
      label: 'L (tourist) visa',
      portal: 'Chinese consulate (COVA)',
      fee: 'US$140 / varies',
      processing: '4 days',
      validity: '90 days',
      appointment: 'consulate'
    }
  }

  if (d === 'Mexico' || d === 'Brazil' || d === 'Argentina') {
    if (m ? m.type === 'free' : strong) return { kind: 'free', label: `Visa-free (${m?.days || '90–180'} days)`, processing: 'None', validity: `${m?.days || '90–180'} days` }
    if (m?.type === 'evisa') return { kind: 'evisa', label: `${d} tourist eVisa`, portal: `${d} official e-visa portal`, fee: '~US$50', processing: '5–15 days', validity: '90 days' }
    return { kind: 'embassy', label: `${d} tourist visa`, portal: `${d} consulate`, fee: '~US$50', processing: '7–21 days', validity: '90 days', appointment: 'consulate' }
  }

  if (d === 'Indonesia') {
    // Fellow-ASEAN nationals enter visa-free for 30 days; others use VOA/e-VOA.
    const ASEAN = ['Malaysia', 'Singapore', 'Thailand', 'Philippines', 'Vietnam', 'Brunei', 'Cambodia', 'Laos', 'Myanmar', 'Timor-Leste']
    if (m?.type === 'free' || ASEAN.includes(nationality)) {
      return { kind: 'free', label: 'Visa-free (30 days, ASEAN)', portal: 'No visa required', processing: 'None', validity: '30 days' }
    }
    // Nationalities the matrix says need a visa are NOT VOA-eligible.
    if (m?.type === 'evisa' || m?.type === 'required') {
      return { kind: 'evisa', label: 'Indonesia e-Visa (B1 tourist)', portal: 'evisa.imigrasi.go.id', fee: '~US$50', processing: '5–10 days', validity: '60 days' }
    }
    return { kind: 'voa', label: 'Visa on Arrival / e-VOA (30 days)', portal: 'molina.imigrasi.go.id (e-VOA)', fee: 'IDR 500,000 (~US$32)', processing: '0 days', validity: '30 days' }
  }

  if (d === 'Egypt') {
    return { kind: 'evisa', label: 'Egypt e-Visa', portal: 'visa2egypt.gov.eg', fee: 'US$25', processing: '5–7 days', validity: '30 days' }
  }

  if (d === 'New Zealand') {
    if (exempt) return { kind: 'eta', label: 'NZeTA', portal: 'NZ ETA / Immigration NZ', fee: 'NZ$17', processing: '3 days', validity: '90 days' }
    return { kind: 'embassy', label: 'Visitor visa', portal: 'Immigration New Zealand', fee: 'NZ$246 + NZ$100 IVL', processing: '14–28 days', validity: '90 days', appointment: 'consulate' }
  }

  // Everything else: the matrix decides for the exact pair.
  if (m) {
    if (m.type === 'free') {
      return { kind: 'free', label: `Visa-free (${m.days ? m.days + ' days' : 'tourism'})`, processing: 'None', validity: m.days ? `${m.days} days` : '—' }
    }
    if (m.type === 'voa') {
      return { kind: 'voa', label: 'Visa on arrival', portal: `${d} border/airport VOA counter`, fee: 'Varies (paid at entry)', processing: '0 days', validity: m.days ? `${m.days} days` : '30 days' }
    }
    if (m.type === 'eta') {
      return { kind: 'eta', label: `${d} electronic travel authorization`, portal: `${d} official eTA portal`, fee: 'Varies', processing: '1–3 days', validity: '90 days' }
    }
    if (m.type === 'evisa') {
      return { kind: 'evisa', label: `${d} tourist eVisa`, portal: `${d} official e-visa portal`, fee: 'Varies', processing: '3–10 days', validity: m.days ? `${m.days} days` : '30–90 days' }
    }
    return { kind: 'embassy', label: `${d} tourist visa`, portal: `${d} consulate`, fee: 'Varies', processing: '7–21 days', validity: '90 days', appointment: 'consulate' }
  }
  if (strong) return { kind: 'free', label: 'Likely visa-free or visa on arrival', processing: 'None', validity: '30–90 days' }
  return { kind: 'embassy', label: 'Tourist visa', portal: `${d} consulate`, fee: 'Varies', processing: '3–21 days', validity: '90 days', appointment: 'consulate' }
}

const KIND_LABEL = {
  free: 'Visa-free',
  eta: 'Travel authorization (eTA/ESTA)',
  evisa: 'eVisa',
  voa: 'Visa on arrival',
  embassy: 'Embassy / consular visa'
}

const PORTAL_URLS = {
  'USA:eta': 'https://esta.cbp.dhs.gov',
  'USA:embassy': 'https://ceac.state.gov/genniv/',
  'Canada:eta': 'https://www.canada.ca/en/immigration-refugees-citizenship/services/visit-canada/eta/apply.html',
  'Canada:evisa': 'https://www.canada.ca/en/immigration-refugees-citizenship/services/visit-canada.html',
  'United Kingdom:eta': 'https://www.gov.uk/guidance/apply-for-an-electronic-travel-authorisation-eta',
  'United Kingdom:evisa': 'https://www.gov.uk/standard-visitor-visa',
  'Japan:evisa': 'https://www.evisa.mofa.go.jp',
  'Japan:free': 'https://www.vjw.digital.go.jp',
  'Thailand:evisa': 'https://www.thaievisa.go.th',
  'Vietnam:evisa': 'https://evisa.gov.vn',
  'Turkey:evisa': 'https://www.evisa.gov.tr',
  'South Korea:eta': 'https://www.k-eta.go.kr',
  'Australia:eta': 'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601',
  'Australia:evisa': 'https://online.immi.gov.au',
  'India:evisa': 'https://indianvisaonline.gov.in/evisa/tvoa.html',
  'China:embassy': 'https://cova.mfa.gov.cn',
  'Indonesia:voa': 'https://molina.imigrasi.go.id',
  'Egypt:evisa': 'https://visa2egypt.gov.eg',
  'Singapore:free': 'https://eservices.ica.gov.sg/sgarrivalcard',
  'Singapore:evisa': 'https://www.ica.gov.sg/enter-transit-depart/entering-singapore/visa_requirements',
  'France:embassy': 'https://france-visas.gouv.fr'
}

function portalUrlFor(destination, kind, nationality) {
  if (destination === 'Japan' && nationality === 'China') {
    return 'https://www.cn.emb-japan.go.jp/itpr_zh/visa_ippan.html'
  }
  if (destination === 'France' && kind === 'embassy') return PORTAL_URLS['France:embassy']
  if (PORTAL_URLS[`${destination}:${kind}`]) return PORTAL_URLS[`${destination}:${kind}`]
  if (SCHENGEN.includes(destination)) return kind === 'free' ? 'https://travel-europe.europa.eu/etias_en' : 'https://france-visas.gouv.fr'
  return null
}

function requirementsFor(route, destination, nationality) {
  const photo = 'Passport photo (white background, agency/consulate size rules)'
  const base = ['Passport valid 6+ months with blank pages', photo]
  if (route.kind === 'free') {
    if (destination === 'Thailand') {
      return [
        'Passport valid for the full stay',
        'Onward or return ticket',
        'Proof of accommodation (Trip.com booking auto-attached)',
        'Thailand Digital Arrival Card (TDAC) — free, complete online within 3 days before arrival'
      ]
    }
    return ['Passport valid for the full stay', 'Onward or return ticket', 'Proof of accommodation (Trip.com booking auto-attached)']
  }
  if (route.channel === 'agency' && destination === 'Japan' && nationality === 'China') {
    return [
      ...base,
      'MOFA visa application form + photo (45×45 mm)',
      'Hukou copy (户口本) or residence permit',
      'Trip.com flight + hotel itinerary covering the stay',
      'Bank statements (3–6 months) or deposit certificate',
      'Employment / leave letter with company stamp',
      'Day-by-day Japan itinerary',
      'Official visa fee (¥3,000) + agency service fee (varies; some require refundable deposit)'
    ]
  }
  if (route.kind === 'eta') {
    return [...base, 'Trip.com flight itinerary (auto-attached)', 'Contact + address details for the online form']
  }
  if (route.kind === 'voa') {
    return [...base, 'Return ticket', 'Proof of funds or credit card', 'Trip.com hotel confirmation (auto-attached)']
  }
  if (route.kind === 'evisa') {
    return [...base, 'Trip.com flight + hotel confirmations (auto-attached)', 'Bank statement or proof of funds (last 3 months)', 'Travel insurance (Trip.com policy accepted)']
  }
  // Embassy / Schengen Type C
  const appForm = destination === 'USA'
    ? 'Form DS-160 confirmation'
    : destination === 'China'
      ? 'Form V.2013'
      : SCHENGEN.includes(destination)
        ? 'Schengen application (France-Visas / national form)'
        : 'Completed consular application form'
  const biometrics = route.appointment === 'vac'
    ? `Biometrics appointment at ${route.vac || 'the visa application centre'}`
    : `Appointment at the ${destination} consulate`
  return [
    ...base,
    appForm,
    'Trip.com flight + hotel confirmations (auto-attached)',
    'Bank statements (3–6 months)',
    'Employment / leave letter or proof of ties',
    SCHENGEN.includes(destination) ? 'Travel insurance ≥ €30,000 medical cover' : 'Travel insurance covering the full stay',
    biometrics
  ]
}

function stepsFor(route, destination) {
  if (route.kind === 'free') {
    if (destination === 'Thailand') {
      return [
        'Verify passport validity for the stay',
        'Confirm visa-free entry (up to 60 days for eligible nationalities)',
        'Complete Thailand Digital Arrival Card (TDAC) within 3 days of arrival',
        'Attach Trip.com bookings for border control',
        'Deliver the TDAC / entry brief by email'
      ]
    }
    return [
      'Verify passport validity for the stay',
      'Confirm visa-free entry conditions',
      'Attach Trip.com bookings for border control',
      'Deliver the entry brief by email'
    ]
  }
  if (route.kind === 'voa') {
    // Visa on arrival: obtained at the border — the agent prepares, nothing
    // is filed in advance.
    return [
      'Verify passport validity for the stay',
      'Confirm visa-on-arrival eligibility and fee',
      'Attach Trip.com bookings and the arrival checklist',
      'Deliver the arrival pack by email'
    ]
  }
  if (route.channel === 'agency') {
    return [
      'Verify and extract every uploaded document',
      'Match jurisdiction to the correct accredited agency',
      'Assemble the agency document pack from extracted fields',
      'Attach Trip.com bookings, insurance, and photo',
      `Submit via ${route.portal}`,
      'Monitor embassy/consulate decision via the agency',
      'Deliver the issued visa notice by email'
    ]
  }
  const needsAppt = route.kind === 'embassy' || route.appointment === 'vac' || route.appointment === 'consulate'
  return [
    'Verify and extract every uploaded document',
    'Classify the route against official sources',
    'Auto-fill the application from extracted fields',
    'Attach Trip.com bookings, insurance, and photo',
    `Submit via ${route.portal || 'the official channel'}`,
    ...(needsAppt ? [`Book the earliest ${route.appointment === 'vac' ? (route.vac || 'visa centre') : 'consular'} appointment`] : []),
    'Monitor status until decision',
    'Deliver the issued visa by email'
  ]
}

// Full structured plan for a trip — the agentic pipeline renders these steps.
export async function tripPlan(p) {
  const t = p.traveler || {}
  const nat = normCountry(t.nationality)
  const dest = normCountry(t.destination) === 'United States' ? 'USA' : normCountry(t.destination)
  const route = routeFor(t.nationality, t.destination)
  const reqs = requirementsFor(route, dest, nat)
  // VOA is obtained at the border — nothing is filed in advance.
  const needsFiling = route.kind !== 'free' && route.kind !== 'voa'
  return {
    classification: KIND_LABEL[route.kind],
    kind: route.kind,
    headline: route.label,
    portal: route.portal || null,
    portalUrl: portalUrlFor(dest, route.kind, nat),
    fee: route.fee || 'None',
    processing: route.processing || 'None',
    validity: route.validity || '—',
    requirements: reqs,
    needsFiling,
    channel: route.channel || null,
    appointment: route.appointment || null,
    vac: route.vac || null,
    summary: route.summary || null,
    steps: stepsFor(route, dest),
    brief: route.summary
      || `${t.nationality} → ${t.destination}: ${KIND_LABEL[route.kind]}. ${route.label}. Fee: ${route.fee || 'none'}. Processing: ${route.processing || 'none'}. Validity: ${route.validity || '—'}. Filed via ${route.portal || 'official channel'}.`
  }
}
