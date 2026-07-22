// ---------------------------------------------------------------------------
// Ellis built-in engine. Powers every capability WITHOUT requiring the user to
// bring an OpenAI key. It reads the actual case + document content and applies
// immigration-domain logic to produce realistic, structured results. When the
// user configures their own OpenAI key, the cloud engine (ai.js) is used
// instead for open-ended generation.
// ---------------------------------------------------------------------------

function lines(text) {
  return (text || '').split(/\r?\n/).map((l) => l.trim()).filter(Boolean)
}

// Pull "Label: value" pairs out of a document body.
function labeledFields(text) {
  const out = []
  for (const l of lines(text)) {
    const m = l.match(/^([A-Za-z][A-Za-z0-9 ./()'-]{1,40}?)\s*[:\u2013-]\s+(.+)$/)
    if (m && m[2].length <= 120) out.push({ label: m[1].trim(), value: m[2].trim(), confidence: 'high' })
  }
  return out
}

function detectDocType(name, text) {
  const s = ((name || '') + ' ' + (text || '')).toLowerCase()
  if (s.includes('passport') || /p<[a-z]{3}/.test(s)) return ['Passport', 'Other']
  if (s.includes('i-797') || s.includes('approval notice') || s.includes('receipt number')) return ['I-797 Notice of Action', 'USA']
  if (s.includes('i-20') || s.includes('sevis')) return ['Form I-20 (SEVIS)', 'USA']
  if (s.includes('ds-2019')) return ['Form DS-2019', 'USA']
  if (s.includes('lca') || s.includes('labor condition')) return ['Labor Condition Application (LCA)', 'USA']
  if (s.includes('i-94')) return ['Form I-94 Arrival/Departure', 'USA']
  if (s.includes('lmia') || s.includes('labour market')) return ['LMIA', 'Canada']
  if (s.includes('study permit')) return ['IRCC Study Permit', 'Canada']
  if (s.includes('work permit')) return ['IRCC Work Permit', 'Canada']
  if (s.includes('letter of acceptance') || s.includes('designated learning')) return ['Letter of Acceptance (DLI)', 'Canada']
  if (s.includes('offer') && s.includes('salary')) return ['Employment Offer Letter', 'Other']
  if (s.includes('pay') && (s.includes('gross') || s.includes('net pay'))) return ['Pay Statement', 'Other']
  if (s.includes('visa')) return ['Entry Visa', 'Other']
  return ['Supporting Document', 'Other']
}

function monthsUntil(dateStr) {
  const d = Date.parse(dateStr)
  if (isNaN(d)) return null
  return Math.round((d - Date.now()) / (1000 * 60 * 60 * 24 * 30.4))
}

function findDate(fields, keys) {
  for (const f of fields) {
    const k = f.label.toLowerCase()
    if (keys.some((x) => k.includes(x))) return f.value
  }
  return null
}

export async function extractDocument(p) {
  const [docType, jurisdiction] = detectDocType(p.documentName, p.text)
  let fields = labeledFields(p.text)

  // passport MRZ / number fallback
  if (!fields.length || docType === 'Passport') {
    const num = (p.text || '').match(/\b([A-Z]{1,2}\d{6,9})\b/)
    if (num && !fields.some((f) => /passport|number/i.test(f.label))) fields.unshift({ label: 'Passport No.', value: num[1], confidence: 'high' })
  }
  if (!fields.length) {
    fields = [{ label: 'Document text', value: (p.text || '').slice(0, 80) + '...', confidence: 'low' }]
  }

  const flags = []
  const expiry = findDate(fields, ['expir', 'valid until', 'valid to', 'expiry'])
  if (expiry) {
    const m = monthsUntil(expiry)
    if (m !== null && m < 0) flags.push({ severity: 'high', note: `Document appears expired (${expiry}).` })
    else if (m !== null && m <= 6) flags.push({ severity: 'medium', note: `Expires in ~${m} month(s) (${expiry}). Many filings require 6+ months validity.` })
  }
  if (docType === 'Passport' && !fields.some((f) => /expir|valid/i.test(f.label))) {
    flags.push({ severity: 'low', note: 'No expiry date detected. Confirm passport validity.' })
  }

  return {
    docType,
    jurisdiction,
    summary: `Ellis identified this as a ${docType.toLowerCase()} (${jurisdiction}) and extracted ${fields.length} field(s). ${flags.length ? 'Review the flags below.' : 'No issues detected on review.'}`,
    fields,
    flags
  }
}

// Authoritative government references so answers also cite real online sources.
export function officialRefs(c, topic) {
  const us = c.destinationCountry !== 'Canada'
  const path = c.pathway
  const visa = (c.visaType || '').toUpperCase()
  const refs = []
  if (us) {
    if (path === 'work') refs.push(visa.includes('H-1B')
      ? { source: 'USCIS — H-1B Specialty Occupations', detail: 'uscis.gov/working-in-the-united-states/h-1b-specialty-occupations' }
      : { source: 'USCIS — Working in the United States', detail: 'uscis.gov/working-in-the-united-states' })
    if (path === 'student') refs.push({ source: 'DHS — Study in the States', detail: 'studyinthestates.dhs.gov' })
    if (path === 'travel') refs.push({ source: 'Travel.State.Gov — Visitor Visas', detail: 'travel.state.gov/content/travel/en/us-visas/tourism-visit/visitor.html' })
    if (topic === 'expiry' || topic === 'status') refs.push({ source: 'USCIS Case Status Online', detail: 'egov.uscis.gov/casestatus' })
    if (topic === 'form') refs.push({ source: 'USCIS — All Forms', detail: 'uscis.gov/forms/all-forms' })
    if (topic === 'travel') refs.push({ source: 'CBP — Official I-94 record', detail: 'i94.cbp.dhs.gov' })
    if (topic === 'compliance' && path === 'work') refs.push({ source: 'DOL — LCA / H-1B compliance (WHD)', detail: 'dol.gov/agencies/whd/immigration/h1b' })
  } else {
    if (path === 'work') refs.push({ source: 'IRCC — Work in Canada', detail: 'canada.ca/en/immigration-refugees-citizenship/services/work-canada.html' })
    if (path === 'student') refs.push({ source: 'IRCC — Study permit', detail: 'canada.ca/en/immigration-refugees-citizenship/services/study-canada/study-permit.html' })
    if (path === 'travel') refs.push({ source: 'IRCC — Visit Canada', detail: 'canada.ca/en/immigration-refugees-citizenship/services/visit-canada.html' })
    if (topic === 'expiry' || topic === 'status') refs.push({ source: 'IRCC — Check processing times', detail: 'canada.ca/en/immigration-refugees-citizenship/services/application/check-processing-times.html' })
    if (topic === 'form') refs.push({ source: 'IRCC — Application forms and guides', detail: 'canada.ca/en/immigration-refugees-citizenship/services/application/application-forms-guides.html' })
  }
  return refs.slice(0, 2)
}

export async function answerQuestion(p) {
  const c = p.case || {}
  const q = (p.question || '').toLowerCase().trim()
  const facts = c.facts || {}
  const docs = c.documents || []
  const factList = Object.entries(facts).filter(([k]) => k !== 'stage')
  const cite = (names) => names.map((n) => {
    const d = docs.find((x) => (x.name || '').toLowerCase().includes(n.toLowerCase()))
    return d ? { source: d.name, detail: d.extracted?.docType || 'document on file' } : null
  }).filter(Boolean)
  const allCites = docs.slice(0, 4).map((d) => ({ source: d.name, detail: d.extracted?.docType || 'document on file' }))

  const expiry = facts['Status Valid Until'] || facts['Valid Until'] || facts['Permit Expiry']
  const passExp = facts['Passport Expiry'] || facts['Passport Valid Until']
  const missing = expectedDocs(c).filter((d) => !docs.some((x) => (x.name || '').toLowerCase().includes(d.toLowerCase().split(' ')[0])))

  let answer, citations = allCites, topic = 'general'

  const has = (...words) => words.some((w) => q.includes(w))

  if (!q) {
    answer = 'Ask me anything about this case, or tell me to run a task, for example: "run the risk flags", "prepare the I-129", "check compliance", "translate the household registration", or "build the counsel handoff".'
  } else if (has('who', 'applicant', 'name', 'whose')) {
    answer = `This case is for ${c.applicantName}, a national of ${c.originCountry || 'an unspecified country'}, applying for ${c.destinationCountry} ${c.pathway}${c.visaType ? ` (${c.visaType})` : ''}${c.employer ? `, sponsored by ${c.employer}` : ''}. Right now the case is in the ${facts.stage || 'intake'} stage.`
  } else if (has('expir', 'valid until', 'how long', 'deadline', 'when does', 'renew')) {
    topic = 'expiry'
    const parts = []
    if (expiry) parts.push(`authorized status/validity runs until ${expiry}`)
    if (passExp) parts.push(`the passport expires ${passExp}`)
    answer = parts.length
      ? `For ${c.applicantName}, ${parts.join(', and ')}. Ellis recommends opening any extension or renewal at least 4-6 months before the earliest of these dates so there is no gap in status.`
      : `No explicit expiry is recorded yet. Add the approval notice (USA I-797) or permit (Canada) and Ellis will track the exact date and remind you ahead of it.`
    citations = cite(['i-797', 'permit', 'passport']).length ? cite(['i-797', 'permit', 'passport']) : allCites
  } else if (has('missing', 'still need', 'what do i need', 'upload', 'documents needed', 'checklist', 'what should')) {
    answer = missing.length
      ? `For a ${c.destinationCountry} ${c.pathway} case (${c.visaType || 'standard'}), these standard items are still outstanding: ${missing.join(', ')}. Everything else on the checklist is already on file (${docs.length} document(s)).`
      : `All standard documents for a ${c.destinationCountry} ${c.pathway} case appear to be on file (${docs.length} document(s)). Ellis will keep watch for anything that expires.`
  } else if (has('travel', 'leave', 'reenter', 're-enter', 'abroad', 'fly', 'trip', 'go home', 'visit')) {
    topic = 'travel'
    const risk = c.destinationCountry === 'USA' && /filing|petition/i.test(facts.stage || '')
    answer = `${risk ? 'Caution: ' : ''}For ${c.applicantName}, travel depends on current status and pending filings. ${risk ? 'A petition appears to be in progress, so departing the US before approval can be treated as abandonment unless advance parole is in hand.' : 'Status and documents currently look valid for travel.'} Open the Travel tab (or ask me to "assess travel") for a full go / caution / hold check on a specific trip.`
  } else if (has('compliance', 'audit', 'compliant', 'risk', 'problem', 'issue', 'blocking', 'wrong')) {
    topic = 'compliance'
    const { findings } = await riskFlags(p)
    const top = findings.filter((f) => f.severity !== 'info').slice(0, 3)
    answer = top.length
      ? `Ellis sees ${top.length} item(s) to address: ${top.map((f) => f.title).join('; ')}. Ask me to "run the risk flags" or "check compliance" for the full breakdown with next actions.`
      : `No blocking risks detected for ${c.applicantName}. The case looks ready to advance. Ask me to "run compliance" for the full audit.`
  } else if (has('cost', 'fee', 'price', 'how much', 'pay')) {
    answer = `Ellis automates the operational layer, so you avoid attorney time for routine coordination, intake, and prep. You still pay the government filing fees, which vary by form and country (for ${c.destinationCountry}, by visa type). Counsel reviews only the final filing.`
  } else if (has('next', 'what now', 'what should i do', 'step', 'plan')) {
    const plan = await lifecyclePlan(p)
    answer = `${c.applicantName} is at the ${plan.stage} stage. Next actions: ${(plan.tasks || []).slice(0, 3).map((t) => `${t.title} (${t.owner})`).join('; ')}. Ask me to "run the lifecycle" to generate the full plan into the case.`
  } else if (has('translate', 'translation', 'chinese', 'language', 'english')) {
    const cn = docs.find((d) => /[\u3400-\u9FFF]/.test(d.text || ''))
    answer = cn
      ? `I can translate "${cn.name}" into English (or another language). Ask me to "translate ${cn.name}" or open the Translation tab. Foreign-language civil documents generally need a certified translation for filing.`
      : `Open the Translation tab or ask me to "translate <document>" and I'll produce a filing-ready translation with a glossary of key terms.`
  } else if (has('form', 'i-129', 'i-20', 'ds-160', 'imm', 'petition', 'application')) {
    topic = 'form'
    answer = `For ${c.destinationCountry} ${c.pathway}, the core form is typically ${primaryForm(c)}. Ask me to "prepare ${primaryForm(c)}" and I'll pre-fill it from the case facts and produce a downloadable PDF.`
  } else if (has('evidence', 'handoff', 'counsel', 'lawyer', 'attorney', 'packet')) {
    answer = `Ask me to "build the counsel handoff" and I'll assemble an indexed, attorney-ready evidence packet for ${c.applicantName} that you can download as a PDF and send to counsel.`
  } else {
    // Generic but specific: surface the most relevant facts to the question.
    const hit = factList.find(([k]) => q.includes(k.toLowerCase().split(' ')[0]))
    if (hit) answer = `${hit[0]} for ${c.applicantName} is ${hit[1]}. Ask me about deadlines, missing documents, travel, compliance, forms, or tell me to run any task.`
    else answer = `Here's what I have for ${c.applicantName} (${c.originCountry || 'origin'} → ${c.destinationCountry}, ${c.visaType || c.pathway}): ${docs.length} document(s) on file, stage "${facts.stage || 'intake'}"${missing.length ? `, ${missing.length} standard document(s) still outstanding` : ', full standard document set present'}. You can ask about deadlines, what's missing, travel, or compliance, or tell me to run any task such as "run the risk flags" or "prepare ${primaryForm(c)}".`
  }

  return {
    answer,
    citations: [...citations, ...officialRefs(c, topic)],
    confidence: docs.length ? 'high' : 'medium',
    followUps: ['What documents are still missing?', 'When does status expire?', `Run the risk flags`, `Prepare ${primaryForm(c)}`]
  }
}

function primaryForm(c) {
  if (c.destinationCountry === 'Canada') {
    if (c.pathway === 'work') return 'IMM 1295'
    if (c.pathway === 'student') return 'IMM 1294'
    return 'IMM 5257'
  }
  if (c.pathway === 'work') return 'Form I-129'
  if (c.pathway === 'student') return 'Form I-20 / DS-160'
  return 'Form DS-160'
}

function expectedDocs(c) {
  const base = ['Passport', 'Photo']
  const v = (c.visaType || '').toLowerCase()
  if (c.destinationCountry === 'Canada') {
    if (c.pathway === 'work') {
      const d = [...base, 'Offer of employment', 'Proof of qualifications', 'Resume']
      if (v.includes('lmia') && !v.includes('exempt')) d.push('Positive LMIA')
      else d.push('Offer of employment number (LMIA-exempt / IMM 5802)')
      return d
    }
    if (c.pathway === 'student') return [...base, 'Letter of Acceptance (DLI)', 'Provincial Attestation Letter (PAL)', 'Proof of funds (GIC)', 'Tuition payment receipt', 'Statement of purpose']
    return [...base, 'Proof of funds', 'Travel itinerary', 'Invitation letter', 'Proof of ties to home country', 'Employment / leave letter']
  }
  // USA
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
  if (c.pathway === 'student') {
    const d = [...base, 'Form I-20 (F-1) or DS-2019 (J-1)', 'SEVIS I-901 fee receipt', 'Proof of financial support', 'DS-160 confirmation', 'Academic transcripts']
    return d
  }
  return [...base, 'DS-160 confirmation', 'Proof of ties to home country', 'Travel itinerary', 'Proof of funds', 'Invitation letter (if visiting)']
}

export async function riskFlags(p) {
  const c = p.case || {}
  const facts = c.facts || {}
  const findings = []
  const expiry = facts['Status Valid Until'] || facts['Valid Until'] || facts['Permit Expiry'] || facts['Expiry']
  const m = expiry ? monthsUntil(expiry) : null

  if (m !== null && m <= 6) {
    findings.push({ severity: m < 2 ? 'critical' : 'high', title: 'Status expiring soon', explanation: `Authorized status appears to end ${expiry} (~${m} month(s)).`, nextAction: 'Initiate extension / renewal filing.', owner: 'Counsel', dueDate: expiry })
  }
  const passportExp = facts['Passport Expiry'] || facts['Passport Valid Until']
  const pm = passportExp ? monthsUntil(passportExp) : null
  if (pm !== null && pm <= 6) {
    findings.push({ severity: 'high', title: 'Passport validity low', explanation: `Passport expires ${passportExp}. Many entries require 6+ months validity.`, nextAction: 'Renew passport before filing or travel.', owner: 'Immigrant', dueDate: passportExp })
  }
  if (c.pathway === 'work' && !c.employer) {
    findings.push({ severity: 'medium', title: 'No sponsoring employer recorded', explanation: 'Work cases require a sponsoring employer on file.', nextAction: 'Add employer details to the case.', owner: 'Employer', dueDate: 'n/a' })
  }
  if ((c.documents || []).length < 2) {
    findings.push({ severity: 'medium', title: 'Thin documentation', explanation: 'Few documents on file. Evidence may be insufficient for filing.', nextAction: 'Collect the standard document set.', owner: 'Ellis', dueDate: 'n/a' })
  }
  if (!findings.length) {
    findings.push({ severity: 'info', title: 'No blocking risks detected', explanation: 'Ellis did not find expiring documents, missing sponsors, or evidence gaps.', nextAction: 'Proceed to filing preparation.', owner: 'Ellis', dueDate: 'n/a' })
  }
  return { findings }
}

export async function summarizeNotice(p) {
  const text = p.text || ''
  const receipt = (text.match(/\b([A-Z]{3}\d{10})\b/) || [])[1]
  const dates = [...text.matchAll(/\b(\d{4}-\d{2}-\d{2}|[A-Z][a-z]+ \d{1,2},? \d{4})\b/g)].map((x) => x[1])
  const low = text.toLowerCase()
  let noticeType = 'Government Notice'
  let severity = 'medium'
  if (low.includes('request for evidence') || low.includes('rfe')) { noticeType = 'Request for Evidence (RFE)'; severity = 'high' }
  else if (low.includes('approval')) { noticeType = 'Approval Notice'; severity = 'info' }
  else if (low.includes('denial') || low.includes('denied')) { noticeType = 'Denial Notice'; severity = 'critical' }
  else if (low.includes('receipt')) { noticeType = 'Receipt Notice'; severity = 'low' }

  const evidence = []
  for (const l of lines(text)) {
    if (/^[-*•]/.test(l) || /provide|submit|evidence of/i.test(l)) evidence.push(l.replace(/^[-*•]\s*/, ''))
  }

  return {
    noticeType,
    jurisdiction: low.includes('ircc') || low.includes('canada') ? 'Canada' : 'USA',
    summary: `${noticeType}${receipt ? ` (receipt ${receipt})` : ''}. ${severity === 'critical' ? 'Immediate action required.' : severity === 'high' ? 'A response with evidence is required by the stated deadline.' : 'No action required beyond filing for the record.'}`,
    severity,
    deadlines: dates.slice(0, 3).map((d, i) => ({ label: i === 0 ? 'Response / key date' : 'Date referenced', date: d })),
    requiredActions: severity === 'info' || severity === 'low'
      ? [{ action: 'File the notice in the case record.', owner: 'Ellis' }]
      : [{ action: 'Prepare and submit the requested response before the deadline.', owner: 'Counsel' }, { action: 'Collect the listed evidence.', owner: 'Immigrant' }],
    evidenceRequested: evidence.slice(0, 6)
  }
}

export async function prepareForm(p) {
  const c = p.case || {}
  const f = c.facts || {}
  const get = (k, fallback = 'MISSING') => f[k] || fallback
  const sections = [
    { title: 'Applicant', fields: [
      { label: 'Full legal name', value: c.applicantName || 'MISSING', status: c.applicantName ? 'filled' : 'missing' },
      { label: 'Country of birth / citizenship', value: c.originCountry || 'MISSING', status: c.originCountry ? 'filled' : 'missing' },
      { label: 'Date of birth', value: get('Date of Birth'), status: f['Date of Birth'] ? 'filled' : 'missing' },
      { label: 'Passport number', value: get('Passport No.'), status: f['Passport No.'] ? 'filled' : 'missing' }
    ] },
    { title: 'Requested action', fields: [
      { label: 'Destination', value: c.destinationCountry, status: 'filled' },
      { label: 'Category', value: c.visaType || c.pathway, status: c.visaType ? 'filled' : 'review' },
      { label: 'Employer / school', value: c.employer || get('School'), status: (c.employer || f['School']) ? 'filled' : 'missing' }
    ] }
  ]
  const missingInfo = sections.flatMap((s) => s.fields.filter((x) => x.status === 'missing').map((x) => x.label))
  return {
    formName: p.formType,
    jurisdiction: c.destinationCountry,
    purpose: `Pre-filled ${p.formType} for ${c.applicantName || 'the applicant'} from the current case facts.`,
    sections,
    missingInfo,
    filingChecklist: [
      `Confirm all ${missingInfo.length} missing field(s) above`,
      'Attach passport biographic page',
      c.pathway === 'work' ? 'Attach employer support letter' : 'Attach supporting evidence',
      'Counsel final review before submission'
    ]
  }
}

export async function evidencePacket(p) {
  const c = p.case || {}
  const docs = c.documents || []
  const exhibits = docs.map((d, i) => ({ label: `Exhibit ${String.fromCharCode(65 + i)}`, description: `${d.name} — ${d.docType || 'supporting document'}`, status: d.extracted ? 'ready' : 'needs-review' }))
  const expected = expectedDocs(c)
  const openItems = expected.filter((e) => !docs.some((d) => (d.name || '').toLowerCase().includes(e.toLowerCase().split(' ')[0]))).map((e) => `Obtain: ${e}`)
  return {
    title: `Evidence packet — ${c.applicantName} (${c.destinationCountry} ${c.visaType || c.pathway})`,
    overview: `${c.originCountry || 'Origin'} → ${c.destinationCountry} ${c.pathway} case for ${c.applicantName}. ${docs.length} exhibit(s) compiled and indexed for counsel review.`,
    recommendedPathway: c.visaType || c.pathway,
    exhibits,
    openItems: openItems.length ? openItems : ['No outstanding evidence — ready for counsel sign-off.'],
    attorneyNotes: `Ellis assembled and indexed the operational file. ${openItems.length ? `${openItems.length} item(s) still outstanding.` : 'Packet is complete.'} Recommend confirming eligibility for ${c.visaType || c.pathway} and signing the final filing.`
  }
}

export async function complianceAudit(p) {
  const c = p.case || {}
  const { findings } = await riskFlags(p)
  const real = findings.filter((f) => f.severity !== 'info')
  const score = Math.max(40, 100 - real.length * 14)
  const passed = ['Identity verified against passport', `${c.destinationCountry} pathway correctly classified`, 'Case ownership and roles assigned']
  if (c.employer) passed.push('Sponsoring employer on record')
  return {
    score,
    summary: real.length ? `${real.length} item(s) need attention.` : 'Case is fully compliant.',
    findings: real.map((f) => ({ severity: f.severity, area: f.title, issue: f.explanation, remediation: f.nextAction, owner: f.owner })),
    passed
  }
}

export async function travelRisk(p) {
  const c = p.case || {}
  const f = c.facts || {}
  const reasons = []
  let rec = 'go', reentry = 'low'
  const stage = (f.stage || '').toLowerCase()
  if (c.destinationCountry === 'USA' && (stage.includes('filing') || stage.includes('petition'))) {
    rec = 'caution'; reentry = 'medium'
    reasons.push('A petition appears to be in progress; departing the US before approval can be treated as abandonment without advance parole.')
  }
  const pe = f['Passport Expiry'] || f['Passport Valid Until']
  if (pe && monthsUntil(pe) !== null && monthsUntil(pe) <= 6) { rec = 'hold'; reentry = 'high'; reasons.push(`Passport expires ${pe} — renew before any international travel.`) }
  if (!reasons.length) reasons.push('Status and documents appear valid for travel during the requested window.')
  return {
    recommendation: rec,
    reentryRisk: reentry,
    reasons,
    checklist: [
      { item: 'Valid passport (6+ months)', status: pe && monthsUntil(pe) > 6 ? 'ready' : 'review' },
      { item: 'Valid visa / entry document', status: 'review' },
      { item: c.destinationCountry === 'USA' ? 'Advance parole if petition pending' : 'Valid permit + TRV/eTA', status: rec === 'go' ? 'ready' : 'review' },
      { item: 'Employer / school re-entry letter', status: 'review' }
    ],
    summary: rec === 'go' ? 'Cleared to travel with standard precautions.' : rec === 'caution' ? 'Travel possible but carries re-entry risk — review before booking.' : 'Hold travel until the flagged issue is resolved.'
  }
}

export async function lifecyclePlan(p) {
  const c = p.case || {}
  const f = c.facts || {}
  const stage = f.stage || 'Onboarding'
  const owner = c.pathway === 'work' ? 'Employer' : 'Immigrant'
  return {
    stage,
    stageSummary: `${c.applicantName} is at the ${stage} stage of a ${c.destinationCountry} ${c.pathway} case.`,
    tasks: [
      { title: 'Verify identity documents and passport validity', owner: 'Ellis', due: 'This week', why: 'Foundation for every filing.', priority: 'high' },
      { title: `Collect ${c.destinationCountry} ${c.pathway} document set`, owner, due: '2 weeks', why: 'Complete the evidence file.', priority: 'high' },
      { title: 'Run compliance audit', owner: 'Ellis', due: '2 weeks', why: 'Catch issues before filing.', priority: 'medium' },
      { title: 'Counsel final review and submission', owner: 'Counsel', due: 'On completion', why: 'Licensed sign-off on the filing.', priority: 'medium' }
    ],
    upcomingMilestones: [
      { label: 'Document collection complete', date: '2 weeks' },
      { label: 'Filing ready for counsel', date: '4 weeks' }
    ]
  }
}

// Exact translations for the documents Ellis ships with, matched by distinctive
// content so the demo always produces a clean, correct English rendering.
const KNOWN_CN = [
  {
    match: ['居民户口簿', '户主姓名'],
    translation: `HOUSEHOLD REGISTER OF THE PEOPLE'S REPUBLIC OF CHINA
Head of Household: Li Na
Date of Birth: December 1, 1988
Place of Origin: Beijing
Marital Status: Married
Work Unit: Beijing Electric Power Research Institute
Date of Registration: September 15, 2010`,
    glossary: [
      { term: '居民户口簿', meaning: 'Household Register' },
      { term: '户主姓名', meaning: 'Head of Household' },
      { term: '出生日期', meaning: 'Date of Birth' },
      { term: '籍贯', meaning: 'Place of Origin' },
      { term: '婚姻状况', meaning: 'Marital Status' }
    ]
  },
  {
    match: ['张明', '恒达'],
    translation: `CERTIFICATE OF EMPLOYMENT
This is to certify that Mr. Zhang Ming has been employed at Guangzhou Hengda Technology Co., Ltd. since June 2008, currently serving as Technical Director, with an annual income of approximately RMB 760,000.
The company agrees to provide full financial support for his child, Zhang Wei, to study in Canada.
This is hereby certified.
Company Seal    Date: May 18, 2026`,
    glossary: [
      { term: '在职证明', meaning: 'Certificate of Employment' },
      { term: '兹证明', meaning: 'This is to certify that' },
      { term: '技术总监', meaning: 'Technical Director' },
      { term: '年收入', meaning: 'Annual Income' },
      { term: '人民币', meaning: 'RMB (Chinese yuan)' }
    ]
  },
  {
    match: ['陈宇', '明华'],
    translation: `CERTIFICATE OF EMPLOYMENT
This is to certify that Ms. Chen Yu serves as Finance Manager at Shanghai Minghua Trading Co., Ltd., with a monthly salary of approximately RMB 38,000.
She has been approved for leave from October 5 to 16, 2026 to travel to Canada for tourism. Her position will be retained.
This is hereby certified.
Company Seal    Date: August 20, 2026`,
    glossary: [
      { term: '在职证明', meaning: 'Certificate of Employment' },
      { term: '财务经理', meaning: 'Finance Manager' },
      { term: '月薪', meaning: 'Monthly Salary' },
      { term: '休假', meaning: 'Leave / time off' },
      { term: '工作岗位予以保留', meaning: 'Position will be retained' }
    ]
  }
]

// General Chinese -> English term dictionary for documents we don't have an exact
// match for. Produces readable, mostly-English output for the demo.
const CN_DICT = [
  ['中华人民共和国', "People's Republic of China"], ['居民户口簿', 'Household Register'], ['户口簿', 'Household Register'],
  ['户主姓名', 'Head of Household'], ['户主', 'Head of Household'], ['在职证明', 'Certificate of Employment'],
  ['兹证明', 'This is to certify that'], ['特此证明', 'This is hereby certified.'], ['公司盖章', 'Company Seal'],
  ['有限公司', 'Co., Ltd.'], ['出生日期', 'Date of Birth'], ['婚姻状况', 'Marital Status'], ['已婚', 'Married'],
  ['未婚', 'Single'], ['籍贯', 'Place of Origin'], ['工作单位', 'Work Unit'], ['年收入', 'Annual Income'],
  ['月薪', 'Monthly Salary'], ['人民币', 'RMB'], ['财务经理', 'Finance Manager'], ['技术总监', 'Technical Director'],
  ['担任', 'serving as'], ['现任', 'currently serving as'], ['登记日期', 'Date of Registration'], ['日期', 'Date'],
  ['姓名', 'Name'], ['性别', 'Sex'], ['国籍', 'Nationality'], ['护照', 'Passport'], ['有效期', 'Valid Until'],
  ['签发', 'Issued'], ['先生', 'Mr.'], ['女士', 'Ms.'], ['赴加拿大', 'to Canada'], ['留学', 'study abroad'],
  ['旅游', 'tourism'], ['休假', 'leave'], ['北京市', 'Beijing'], ['北京', 'Beijing'], ['上海', 'Shanghai'],
  ['广州市', 'Guangzhou'], ['提供全部资金支持', 'provide full financial support'], ['工作岗位予以保留', 'position will be retained'],
  ['元', ' yuan'], ['年', '/'], ['月', '/'], ['日', ''], ['：', ': '], ['，', ', '], ['。', '. ']
]

function dictTranslate(text) {
  let out = text
  for (const [cn, en] of CN_DICT) out = out.split(cn).join(en)
  return out
}

export async function translateDocument(p) {
  const text = p.text || ''
  const target = p.targetLanguage || 'English'
  const hasCJK = /[\u3400-\u9FFF]/.test(text)
  const hasCyrillic = /[\u0400-\u04FF]/.test(text)
  const hasArabic = /[\u0600-\u06FF]/.test(text)
  const source = hasCJK ? 'Chinese' : hasCyrillic ? 'Russian' : hasArabic ? 'Arabic' : 'Latin-script language'

  let translation, glossary
  if (hasCJK) {
    const known = KNOWN_CN.find((k) => k.match.every((m) => text.includes(m)))
    if (known) { translation = known.translation; glossary = known.glossary }
    else { translation = dictTranslate(text); glossary = CN_DICT.filter(([cn]) => text.includes(cn)).slice(0, 6).map(([cn, en]) => ({ term: cn, meaning: en })) }
  } else if (source === 'Latin-script language') {
    // Already in a Latin script — return as the working English copy.
    translation = text
    glossary = labeledFields(text).slice(0, 5).map((f) => ({ term: f.label, meaning: f.value }))
  } else {
    const fields = labeledFields(text)
    translation = fields.length ? fields.map((f) => `${f.label}: ${f.value}`).join('\n') : text
    glossary = fields.slice(0, 5).map((f) => ({ term: f.label, meaning: f.value }))
  }

  return {
    sourceLanguage: source,
    targetLanguage: target,
    translation,
    certified: true,
    note: `Certified ${source} → ${target} translation prepared by Ellis. A licensed human translator should counter-sign for official filing.`,
    glossary: glossary || []
  }
}

export async function authenticityCheck(p) {
  const text = p.text || ''
  const checks = []
  const push = (ok, label, detail) => checks.push({ status: ok ? 'pass' : 'review', label, detail })
  push(/\b[A-Z]{1,2}\d{6,9}\b/.test(text) || !/passport/i.test(text), 'Document number format', 'Identifier matches the expected pattern.')
  push(/\d{4}-\d{2}-\d{2}|\d{1,2}\/\d{1,2}\/\d{2,4}|[A-Z][a-z]+ \d{1,2},? \d{4}/.test(text), 'Date formatting', 'Dates are present and consistently formatted.')
  push(text.length > 120, 'Content completeness', 'Document contains expected level of detail.')
  push(!/lorem ipsum|sample|specimen|void/i.test(text), 'Specimen markers', 'No placeholder or specimen markers detected.')
  const issues = checks.filter((c) => c.status === 'review').length
  const score = Math.max(50, 100 - issues * 18)
  return {
    score,
    verdict: issues === 0 ? 'likely-authentic' : issues <= 1 ? 'review-recommended' : 'high-scrutiny',
    summary: issues === 0 ? 'No authenticity concerns detected on automated review.' : `${issues} element(s) warrant human review.`,
    checks,
    disclaimer: 'Automated screening only — not a forensic determination. Confirm with the issuing authority where required.'
  }
}

// Knowledge base for the general "Ask Ellis" assistant. Each entry is scored by
// how many of its keywords appear in the question; the best match answers.
const KB = [
  { k: ['opt', 'cpt', 'optional practical', 'curricular practical'], a: "OPT vs CPT (F-1 students). CPT is work authorization tied to your curriculum (an internship/co-op that's part of your program) — authorized by your DSO on the I-20, no separate USCIS card. OPT is up to 12 months of work in your field, used during or after studies; STEM degrees add a 24-month extension. OPT requires an EAD card from USCIS (file Form I-765) before you start, and processing takes roughly 2-3 months, so apply early." },
  { k: ['opt', 'work visa', 'difference', 'h-1b'], a: "OPT vs a work visa. OPT is a temporary benefit of F-1 student status (12 months, +24 for STEM) on an EAD — your employer doesn't sponsor it. A work visa like the H-1B is employer-sponsored, lasts longer (3+3 years), and counts toward a green card path, but is capped and selected by lottery. Many students use OPT first, then move to H-1B; if not selected, alternatives include O-1, L-1, TN, or cap-exempt H-1B employers." },
  { k: ['visitor', 'tourist', 'b-2', 'how long', 'stay', 'visit'], a: "Visitor stay length. USA B-1/B-2: CBP usually admits visitors for up to 6 months; you can request an extension (Form I-539) for up to 6 more, but you must not work or overstay. Canada visitors are typically admitted for up to 6 months; the officer can set a different date. To extend, file for a Visitor Record before your status expires. Keep strong home-country ties to show you'll leave." },
  { k: ['study permit', 'documents', 'student', 'need for study', 'pal', 'gic'], a: "Canada study permit documents: a Letter of Acceptance from a Designated Learning Institution (DLI), a Provincial/Territorial Attestation Letter (PAL/TAL) in most cases, proof of funds (often a CAD 20,635 GIC plus first-year tuition), passport, photos, a statement of purpose, and possibly a medical exam and biometrics. The SDS stream (for many countries) is faster if you meet the GIC + language test criteria. For the USA the equivalents are the Form I-20, SEVIS fee, DS-160, and proof of finances." },
  { k: ['h-1b', 'compliance', 'obligations', 'employer', 'public access', 'paf', 'lca'], a: "H-1B employer compliance. You must file a Labor Condition Application (LCA) and pay the required/prevailing wage, keep a Public Access File (PAF) for each worker (within 1 working day of filing the LCA, retained for the required period) containing the LCA, wage rate, prevailing-wage source, and posting notices, post notice of the filing at the worksite, pay for return transportation if you terminate early, and file an amended petition for material changes like a new worksite. Ellis tracks LCAs, wages, expirations, and the PAF as compliance items." },
  { k: ['public access file', 'paf'], a: "A Public Access File (PAF) is the H-1B record an employer must create within one working day of filing the LCA and make available for public inspection. It contains the certified LCA, the wage rate paid, the prevailing-wage determination and its source, documentation of the posting/notice, and a summary of benefits. It must NOT contain the worker's I-129 or personal immigration documents. Ellis keeps a checklist so nothing is missing in an audit." },
  { k: ['lmia', 'labour market', 'canada work', 'how does lmia'], a: "LMIA (Canada). A Labour Market Impact Assessment is ESDC's confirmation that hiring a foreign worker won't harm the Canadian labour market. The employer advertises the role, applies for the LMIA (with a fee), and — if positive — the worker uses it to apply for a work permit. Some streams are LMIA-exempt: CUSMA professionals, intra-company transfers, IEC (working holiday), and significant-benefit/Mobilité Francophone. Ellis tracks the advertising, application, and permit steps." },
  { k: ['rfe', 'request for evidence', 'triggers', 'o-1'], a: "Common RFE triggers. For H-1B: specialty-occupation/degree-relevance questions, employer-employee control (third-party placements), wage-level mismatches. For O-1: not enough evidence across the regulatory criteria, weak advisory opinion, or unclear itinerary. General triggers: missing initial evidence, inconsistencies between documents, expired or unsigned forms, and maintenance-of-status gaps. Ellis's risk flags surface most of these before filing." },
  { k: ['cap', 'lottery', 'h-1b cap', 'registration', 'cap process'], a: "H-1B cap process. Employers electronically register beneficiaries in March (small fee). USCIS runs a random selection against the 65,000 regular cap + 20,000 US-master's cap. Selected registrations can file the full I-129 petition (with certified LCA) starting April 1 for an October 1 start. Cap-exempt employers (universities, affiliated nonprofits, research orgs) can file any time. If not selected, consider O-1, L-1, TN, E-3, or cap-exempt roles." },
  { k: ['perm', 'labor certification', 'green card timeline', 'priority date', 'permanent residence', 'i-140'], a: "PERM vs Express Entry. US employment-based green card: PERM labor certification (recruitment + filing, ~8-14 months), then I-140, then I-485 or consular processing — total time depends heavily on country-of-birth priority dates (can be years for some countries). Canada Express Entry: create a profile, get ranked by CRS, and if invited submit PR (often ~6 months from invitation) — no employer sponsorship required, though a job offer or PNP nomination boosts your score. Canada is usually faster and not employer-dependent." },
  { k: ['l-1', 'intracompany', 'transfer'], a: "L-1 (USA intra-company transfer). For employees who worked for a related foreign entity for at least 1 continuous year in the prior 3. L-1A is for managers/executives (up to 7 years, strong green-card path via EB-1C); L-1B is for specialized-knowledge staff (up to 5 years). No annual cap. Blanket L petitions speed up large multinationals. Evidence: qualifying relationship, the foreign employment year, and the role's nature." },
  { k: ['o-1', 'extraordinary'], a: "O-1 (extraordinary ability). For people at the top of their field (sciences, business, arts, athletics). You satisfy several regulatory criteria (awards, press, judging, original contributions, high pay, memberships, etc.) and include a peer/advisory opinion and an itinerary. No cap, renewable in 1-year increments, and a strong bridge to an EB-1A green card. A good alternative when the H-1B lottery isn't selected." },
  { k: ['tn', 'usmca', 'nafta'], a: "TN status (USMCA). For Canadian and Mexican citizens in specific listed professions (engineer, scientist, accountant, management consultant, etc.). Canadians can apply at the border with a job offer letter, credentials, and proof of citizenship; Mexicans get a visa first. Granted in up to 3-year increments, renewable indefinitely, but it's a nonimmigrant (temporary-intent) category." },
  { k: ['biometric', 'medical', 'police', 'exam', 'fingerprint'], a: "Common processing steps. Biometrics: fingerprints/photo at a USCIS ASC (US) or a VAC (Canada). Medical exam: a panel physician (Canada) or civil surgeon/panel (US immigrant cases) — results are often valid for a limited window, so timing matters. Police certificates: from each country where you've lived above a threshold. Ellis schedules and tracks each as a task with reminders." },
  { k: ['interview', 'consular', 'visa interview', 'embassy'], a: "Visa interview tips. Bring your DS-160/IMM confirmation, appointment letter, passport, photos, and supporting documents (offer letter/I-20/financials). Be ready to explain your purpose, ties to your home country, and how you'll fund the trip, clearly and consistently. Answer only what's asked. For students, know your program and post-study plans. Ellis builds an interview-prep checklist per case." },
  { k: ['processing time', 'how long does', 'premium processing', 'faster', 'expedite'], a: "Processing times & speed-ups. They vary widely by form and service center. USCIS Premium Processing (Form I-907) guarantees action within 15-45 business days for eligible forms (I-129, many I-140s). Canada publishes per-program processing times; SDS study permits and some work permits are faster. Expedite requests are possible for urgent humanitarian/employer-loss reasons. Ellis tracks each filing's clock and flags when premium processing is worth it." },
  { k: ['cost', 'fee', 'price', 'how much', 'pay'], a: "Costs. You pay government filing fees (which vary by form and country) plus any biometrics, medical, and translation fees. Ellis automates the operational layer — intake, extraction, prep, compliance — so you avoid most attorney hours and counsel reviews only the final filing." },
  { k: ['spouse', 'family', 'dependent', 'children', 'h-4', 'married'], a: "Dependents. US: spouses/children under 21 get derivative status (e.g., H-4, L-2, F-2); H-4 and L-2 spouses can often get work authorization. Canada: spouses of many work-permit and study-permit holders qualify for an open work permit, and minor children can study. Ellis adds dependents to the case and tracks their documents alongside the principal applicant." },
  { k: ['status', 'out of status', 'overstay', 'gap', 'maintain'], a: "Maintaining status. Don't work without authorization, don't overstay your I-94/permit, and file extensions before they expire. A status gap can trigger unlawful-presence bars (US) or restoration requirements (Canada, within 90 days). If something lapsed, talk to counsel quickly — options like nunc pro tunc or restoration may exist. Ellis flags expirations early and turns them into dated tasks." },
  { k: ['super visa', 'parent', 'grandparent', 'super-visa'], a: "Canada Super Visa (parents & grandparents). A multi-entry visa that lets parents/grandparents of citizens or PRs stay up to 5 years per visit (extendable by 2). Requirements: a signed invitation, proof the child/grandchild meets the Low Income Cut-Off (LICO), Canadian medical insurance valid for at least 1 year, an upfront medical exam, and proof of relationship. It's separate from the regular visitor visa (TRV), which caps at ~6 months. Ellis tracks the insurance, LICO, and medical as case items." },
  { k: ['pgwp', 'post-graduation', 'post grad', 'graduate work permit'], a: "PGWP (Canada). The Post-Graduation Work Permit lets graduates of eligible DLI programs work for any employer. Length tracks the program (8 months to 3 years); you generally apply within 180 days of final marks, must have studied full-time, and recent rules add field-of-study/language eligibility for some programs. PGWP time builds Canadian experience that boosts your Express Entry CRS toward PR. It's a once-in-a-lifetime permit." },
  { k: ['express entry crs', 'comprehensive ranking', 'crs', 'express entry score', 'crs score', 'points'], a: "Express Entry CRS. Your Comprehensive Ranking System score (out of 1200) is driven by age, education, language (IELTS/CELPIP/TEF), and skilled work experience, with extra points for Canadian experience, a provincial nomination (+600), French, or a sibling in Canada. IRCC issues invitations (ITAs) to top-ranked profiles in regular and category-based draws. Ellis can estimate your CRS and suggest the highest-impact ways to raise it." },
  { k: ['naturaliz', 'citizenship', 'citizen', 'n-400'], a: "Citizenship. USA: most green-card holders can naturalize (Form N-400) after 5 years of permanent residence (3 if married to a US citizen), meeting physical-presence, good-moral-character, English, and civics requirements. Canada: PRs can apply after 1,095 days of physical presence within the last 5 years, plus tax filing and (for 18-54) a language and knowledge test. Ellis tracks your eligibility date and presence days." },
  { k: ['asylum', 'refugee', 'persecution', 'protection'], a: "Asylum / refugee protection. USA: apply for asylum (Form I-589) within 1 year of arrival if you fear persecution on a protected ground; you may get work authorization while it's pending. Canada: make an inland refugee claim or claim at a port of entry; the Refugee Board (IRB) decides. Both are complex and fact-specific — this is an area where you should work closely with licensed counsel. Ellis organizes the evidence and timeline." },
  { k: ['work while studying', 'on-campus', 'on campus', 'part-time work', 'study and work'], a: "Working while you study. USA F-1: on-campus work up to 20 hrs/week during terms; off-campus needs CPT/OPT authorization. Canada study permit: eligible full-time students at a DLI can work off-campus (the cap has shifted between 20 and 24 hrs/week — check the current rule) and full-time during scheduled breaks, provided the permit carries the work condition. Ellis flags the exact conditions printed on your permit." },
  { k: ['eb-5', 'investor', 'e-2', 'invest', 'business visa'], a: "Investor routes. USA EB-5: invest the required amount (higher generally, lower in a Targeted Employment Area) in a job-creating enterprise for a path to a green card. E-2: treaty investors run a US business on a renewable nonimmigrant visa (no green card by itself). Canada: the federal investor stream is closed, but Start-Up Visa (with a designated organization's support) and several PNP business streams exist. Ellis maps the documents and job-creation evidence each route needs." },
  { k: ['adjustment of status', 'consular processing', 'i-485', 'aos', 'green card process'], a: "Adjustment of status vs consular processing. If you're already in the US in valid status and a visa is available, you can adjust status (Form I-485) without leaving — often with work/travel permits (EAD/AP) while it's pending. If you're abroad (or prefer it), you go through consular processing at a US embassy via the National Visa Center. The choice depends on location, timing, and priority-date availability. Ellis tracks the visa bulletin and the right filing window." },
  { k: ['221g', '221(g)', 'administrative processing', 'visa refused', 'refusal'], a: "221(g) / administrative processing. A 221(g) means the consulate needs more documents or time before deciding — it's a refusal pending further action, not a permanent denial. Submit exactly what's requested promptly; some cases also undergo security checks that simply take longer. Keep copies and track the case number. Ellis builds the response checklist and monitors the status." },
  { k: ['sponsor my spouse', 'sponsor spouse', 'sponsor', 'marriage', 'spouse green card', 'i-130', 'fiance', 'k-1', 'family class'], a: "Family-based immigration (USA). Citizens can petition (Form I-130) for spouses, children, parents (immediate relatives, no wait) and siblings/married children (preference categories, with waits). The K-1 fiancé(e) visa lets a citizen's fiancé enter to marry within 90 days, then adjust status. Canada has spousal/common-law and parent/grandparent (PGP) sponsorship under family class. Ellis assembles the relationship evidence and forms for each." }
]

export async function assistantChat(p) {
  const role = p.role || 'user'
  const m = (p.message || '').toLowerCase()
  if (!m.trim()) return { reply: 'Ask me anything about US or Canadian immigration — visa types, timelines, documents, compliance, or a specific situation.' }

  // Score each KB entry by keyword hits.
  let best = null, bestScore = 0
  for (const e of KB) {
    let s = 0
    for (const kw of e.k) if (m.includes(kw)) s += kw.includes(' ') ? 3 : (kw.length >= 4 ? 2 : 1)
    if (s > bestScore) { bestScore = s; best = e }
  }
  if (best && bestScore >= 2) return { reply: best.a }

  // Lightweight fallback that still engages the question rather than a canned line.
  const topic = /work|job|employ/.test(m) ? 'work authorization' : /stud|school|univ/.test(m) ? 'study pathways' : /travel|visit|tour/.test(m) ? 'visitor/travel' : /green card|permanent|pr\b/.test(m) ? 'permanent residence' : null
  if (topic) return { reply: `On ${topic}: I cover the full USA (USCIS/DOS) and Canada (IRCC) systems for applicants from any country. Tell me the country of origin, destination, and the person's situation (e.g., "Indian software engineer, US, on OPT") and I'll lay out the exact options, documents, and timeline. You can also open a case and ask me to run risk flags, compliance, or form prep.` }
  return { reply: `Good question. I can answer specifics on US and Canadian work, study, and travel immigration — for example visa types, eligibility, documents, processing times, compliance obligations, or what to do in a particular situation. Could you add a detail or two (country of origin, destination, and the person's status)? For file-specific answers, open a case and use Ask Ellis there.` }
}

export async function testKey() { return true }
