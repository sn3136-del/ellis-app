// Thin wrapper around the secure preload bridge (window.ellis), plus shared
// domain metadata used across the role interfaces.

// Graceful degradation: when the Electron preload bridge is unavailable (e.g.
// the renderer is opened in a plain browser for preview/testing), fall back to a
// no-op stub so the app still renders instead of hard-crashing. In the real
// Electron app window.ellis is always present, so this stub is never used —
// production behavior is unchanged. The Visa Platform screen talks to the
// backend over HTTP (visaBackend.js), so it works fully under the stub too.
function makeEllisStub() {
  const recur = () => new Proxy(async () => ({}), {
    get: (_t, p) => (p === 'then' ? undefined : recur()),
    apply: () => Promise.resolve({})
  })
  const overrides = {
    getSettings: async () => ({ onboarded: true }),
    saveSettings: async () => ({}),
    getState: async () => ({}),
    listNotifs: async () => [],
    listCases: async () => [],
    listTrips: async () => [],
    markAllNotifsRead: async () => ({})
  }
  return new Proxy(overrides, { get: (t, p) => (p in t ? t[p] : recur()) })
}

export const ellis = (typeof window !== 'undefined' && window.ellis) ? window.ellis : makeEllisStub()

export const ROLES = {
  immigrant: {
    id: 'immigrant',
    label: 'Immigrant',
    blurb: 'Track your case, know exactly what to upload, and ask Ellis anything, without paying for routine legal time.'
  },
  employer: {
    id: 'employer',
    label: 'Employer',
    blurb: 'Automate onboarding, compliance audits, renewals, and immigration tasks across your whole workforce.'
  },
  counsel: {
    id: 'counsel',
    label: 'Counsel',
    blurb: 'Receive complete, structured case files. Spend your time on legal judgment, not document chasing.'
  },
  tripcom: {
    id: 'tripcom',
    label: 'Trip.com',
    blurb: 'One-click tourist visas for every Trip.com customer, to any destination. Upload documents once — Ellis files, tracks, and emails the visa.'
  }
}

// Comprehensive country list (origin can also be typed freely via datalist).
export const COUNTRIES = [
  'Afghanistan', 'Albania', 'Algeria', 'Argentina', 'Australia', 'Austria', 'Bangladesh', 'Belgium',
  'Bolivia', 'Brazil', 'Bulgaria', 'Cambodia', 'Cameroon', 'Canada', 'Chile', 'China', 'Colombia',
  'Costa Rica', 'Croatia', 'Cuba', 'Czechia', 'Denmark', 'Dominican Republic', 'Ecuador', 'Egypt',
  'El Salvador', 'Ethiopia', 'Finland', 'France', 'Germany', 'Ghana', 'Greece', 'Guatemala', 'Haiti',
  'Honduras', 'Hong Kong', 'Hungary', 'India', 'Indonesia', 'Iran', 'Iraq', 'Ireland', 'Israel',
  'Italy', 'Jamaica', 'Japan', 'Jordan', 'Kazakhstan', 'Kenya', 'South Korea', 'Kuwait', 'Lebanon',
  'Malaysia', 'Mexico', 'Morocco', 'Nepal', 'Netherlands', 'New Zealand', 'Nigeria', 'Norway',
  'Pakistan', 'Peru', 'Philippines', 'Poland', 'Portugal', 'Qatar', 'Romania', 'Russia', 'Saudi Arabia',
  'Senegal', 'Singapore', 'South Africa', 'Spain', 'Sri Lanka', 'Sweden', 'Switzerland', 'Taiwan',
  'Thailand', 'Tunisia', 'Turkey', 'Ukraine', 'United Arab Emirates', 'United Kingdom', 'United States',
  'Uzbekistan', 'Venezuela', 'Vietnam', 'Zimbabwe', 'Other'
]

export const DESTINATIONS = ['USA', 'Canada']

export const PATHWAYS = [
  { id: 'work', label: 'Work visa / permit' },
  { id: 'student', label: 'Student visa / permit' },
  { id: 'travel', label: 'Travel / tourist' }
]

// Comprehensive visa/permit type suggestions by destination + pathway.
export const VISA_TYPES = {
  USA: {
    work: ['H-1B', 'H-1B1 (Chile/Singapore)', 'H-2A / H-2B', 'L-1A / L-1B', 'O-1', 'P-1', 'TN (USMCA)', 'E-1 / E-2', 'E-3 (Australia)', 'R-1', 'EB-1', 'EB-2 / NIW', 'EB-3', 'PERM'],
    student: ['F-1', 'F-1 OPT', 'F-1 STEM OPT', 'F-1 CPT', 'J-1 Exchange', 'M-1 Vocational'],
    travel: ['B-1 (business)', 'B-2 (tourism)', 'B-1/B-2', 'ESTA / Visa Waiver', 'C-1 Transit']
  },
  Canada: {
    work: ['Work permit (LMIA)', 'Work permit (LMIA-exempt)', 'CUSMA Professional', 'Intra-Company Transfer', 'IEC (Working Holiday)', 'Open Work Permit', 'PGWP', 'Express Entry (PR)', 'Provincial Nominee'],
    student: ['Study permit', 'Study permit (SDS)', 'PGWP', 'Co-op work permit'],
    travel: ['Visitor visa (TRV)', 'eTA', 'Super Visa (parents/grandparents)', 'Transit visa']
  }
}

export const INTEGRATIONS = [
  { id: 'slack', label: 'Slack', desc: 'Pull case context from channels and DMs.' },
  { id: 'email', label: 'Email', desc: 'Ingest notices and threads; send updates.' },
  { id: 'hris', label: 'HRIS (Workday / Rippling)', desc: 'Sync employee records and start dates.' },
  { id: 'calendar', label: 'Calendar', desc: 'Track deadlines, biometrics, and interviews.' },
  { id: 'drive', label: 'Cloud storage', desc: 'Import documents from Drive / SharePoint.' }
]

// Capability registry: each maps to an AI handler and renders its own panel.
export const CAPABILITIES = {
  documents: { label: 'Document review', desc: 'Extract every field and flag what is missing or expiring.' },
  qa: { label: 'Ask Ellis', desc: 'Answer any question, grounded only in this case file.' },
  risks: { label: 'Risk flags', desc: 'Scan the case for compliance and travel risk.' },
  notices: { label: 'Notice summaries', desc: 'Summarize a government notice or RFE into clear actions.' },
  forms: { label: 'Form preparation', desc: 'Pre-fill an immigration form from the case facts.' },
  evidence: { label: 'Evidence packets', desc: 'Build an attorney-ready handoff for counsel.' },
  compliance: { label: 'Compliance', desc: 'One scan for compliance status and every risk flag.' },
  travel: { label: 'Travel risk', desc: 'Assess an upcoming trip before anyone books it.' },
  translation: { label: 'Translation', desc: 'Translate a foreign document for filing, with a glossary.' },
  authenticity: { label: 'Authenticity', desc: 'Screen a document for authenticity concerns.' },
  lifecycle: { label: 'Run the lifecycle', desc: 'Determine the stage and the next set of actions.' }
}

// Which capabilities each role sees in a case workspace, in order.
// Compliance includes risk flags; translation lives inside Documents.
export const ROLE_TABS = {
  immigrant: ['overview', 'documents', 'qa', 'notices', 'travel', 'forms'],
  employer: ['overview', 'documents', 'compliance', 'forms', 'evidence', 'qa'],
  counsel: ['overview', 'documents', 'qa', 'compliance', 'notices', 'forms', 'evidence', 'authenticity', 'travel']
}

// Standard document checklist by destination + pathway (mirrors the engine's
// expectations) so the Documents tab can show what's still required.
export function requiredDocs(c) {
  const base = ['Passport', 'Photo']
  const v = (c.visaType || '').toLowerCase()
  if (c.destinationCountry === 'Canada') {
    if (c.pathway === 'work') {
      const d = [...base, 'Offer of employment', 'Proof of qualifications', 'Resume']
      if (v.includes('lmia') && !v.includes('exempt')) d.push('Positive LMIA')
      else d.push('Offer of employment number (LMIA-exempt)')
      return d
    }
    if (c.pathway === 'student') return [...base, 'Letter of Acceptance (DLI)', 'Provincial Attestation Letter (PAL)', 'Proof of funds (GIC)', 'Tuition payment receipt', 'Statement of purpose']
    return [...base, 'Proof of funds', 'Travel itinerary', 'Invitation letter', 'Proof of ties to home country', 'Employment / leave letter']
  }
  if (c.pathway === 'work') {
    const d = [...base, 'Offer letter', 'Resume / credentials', 'Degree + evaluation']
    if (v.includes('h-1b') || v.includes('h1b')) d.push('Certified LCA', 'Form I-129', 'Support letter')
    else if (v.includes('l-1') || v.includes('l1')) d.push('Foreign employment proof (1 yr)', 'Org charts', 'Form I-129')
    else if (v.includes('o-1') || v.includes('o1')) d.push('Evidence of extraordinary ability', 'Advisory opinion', 'Form I-129')
    else if (v.includes('tn')) d.push('USMCA profession proof', 'Detailed support letter')
    else if (v.includes('e-2') || v.includes('e-3')) d.push('Investment / treaty evidence', 'Form DS-160')
    else d.push('Form I-129')
    return d
  }
  if (c.pathway === 'student') return [...base, 'Form I-20 (F-1) or DS-2019 (J-1)', 'SEVIS I-901 fee receipt', 'Proof of financial support', 'DS-160 confirmation', 'Academic transcripts']
  return [...base, 'DS-160 confirmation', 'Proof of ties to home country', 'Travel itinerary', 'Proof of funds', 'Invitation letter (if visiting)']
}

// True when a document on file plausibly satisfies a required item.
const DOC_SYNONYMS = {
  offer: ['employment agreement', 'offer letter', 'job offer'],
  qualifications: ['resume', 'degree', 'credential', 'transcript'],
  credentials: ['resume', 'cv', 'degree'],
  funds: ['bank', 'gic', 'financial'],
  photo: ['photograph']
}
export function docSatisfies(docs, requirement) {
  const words = requirement.toLowerCase().replace(/[()/+]/g, ' ').split(/\s+/).filter((w) => w.length >= 3 && !['for', 'the', 'proof', 'and', 'form'].includes(w))
  const key = words[0] || requirement.toLowerCase()
  const syns = words.flatMap((w) => DOC_SYNONYMS[w] || [])
  return (docs || []).some((d) => {
    const n = (d.name || '').toLowerCase()
    return n.includes(key) || words.slice(0, 3).some((w) => n.includes(w)) || syns.some((s) => n.includes(s))
  })
}

// Dashboard quick-capability tiles per role.
export const ROLE_TILES = {
  immigrant: ['qa', 'documents', 'translation', 'travel'],
  employer: ['compliance', 'documents', 'risks', 'evidence'],
  counsel: ['evidence', 'authenticity', 'risks', 'forms']
}

export function fmtDate(ts) {
  if (!ts) return ''
  return new Date(ts).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

export const ROLE_LABEL = { immigrant: 'Immigrant', employer: 'Employer', counsel: 'Counsel' }

// Scope the visible cases to what the user selected during onboarding:
// employers/counsel see only their country's (destination) cases; immigrants
// see only cases from their origin country.
export function filterCasesForProfile(cases, role, profile) {
  if (!profile || !Array.isArray(cases)) return cases || []
  if (role === 'immigrant' && profile.originCountry) {
    return cases.filter((c) => !c.originCountry || c.originCountry === profile.originCountry)
  }
  if ((role === 'employer' || role === 'counsel') && profile.destinationCountry) {
    return cases.filter((c) => c.destinationCountry === profile.destinationCountry)
  }
  return cases
}

// Notify the other stakeholders on a case when something happens.
export async function notifyOthers(fromRole, c, title, targets) {
  const others = (targets || ['immigrant', 'employer', 'counsel']).filter((r) => r !== fromRole)
  for (const r of others) {
    await ellis.addNotif({ forRole: r, fromRole, caseId: c.id, caseName: c.applicantName, title })
  }
}
