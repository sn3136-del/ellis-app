import OpenAI from 'openai'
import { getState } from './store.js'

// ---------------------------------------------------------------------------
// Ellis AI service. Every immigration capability is a deterministic, structured
// call to the OpenAI API with a domain-grounded system prompt. Supports United
// States (USCIS / DOS) and Canada (IRCC) pathways, including cross-border cases
// such as China to USA or Canada for work, study, and travel.
// ---------------------------------------------------------------------------

function client() {
  const { settings } = getState()
  if (!settings.apiKey) {
    const err = new Error('NO_API_KEY')
    err.code = 'NO_API_KEY'
    throw err
  }
  return new OpenAI({ apiKey: settings.apiKey })
}

function model() {
  return getState().settings.model || 'gpt-4o-mini'
}

const DOMAIN = `You are Ellis, an expert AI immigration operations agent and the action layer of a shared workspace used by employers, immigrants, and immigration counsel.

You handle the full immigration lifecycle: onboarding, filing, renewals, travel, compliance, and notices/RFEs.

Jurisdictions you support:
- United States (USCIS, DOS, CBP). Common items: H-1B, L-1, O-1, TN, E-2, F-1 with OPT/CPT, J-1, B-1/B-2, ESTA, green card / I-485, PERM, I-94, I-797 receipt/approval notices, RFEs, LCA, advance parole.
- Canada (IRCC). Common items: work permit (LMIA-based and LMIA-exempt), study permit, PGWP, visitor visa (TRV) and eTA, Express Entry / PR, biometrics, GCKey, IMM forms, port-of-entry letters.

You routinely handle cross-border origin countries (for example China, India, Nigeria) to the USA or Canada for work, study, and tourism.

Operating principles:
- You automate the operational layer. Licensed counsel still approves final filings; you prepare, organize, flag, and draft.
- Be precise, practical, and grounded in the documents and facts provided. Never invent specific dates, receipt numbers, or facts that are not present; mark them as missing instead.
- This is operational assistance, not a substitute for legal advice. Keep guidance actionable.
- Always return valid JSON exactly matching the requested schema. No prose outside JSON.`

async function callJSON(task, userPayload, { temperature = 0.2 } = {}) {
  const res = await client().chat.completions.create({
    model: model(),
    temperature,
    response_format: { type: 'json_object' },
    messages: [
      { role: 'system', content: DOMAIN },
      { role: 'system', content: task },
      { role: 'user', content: JSON.stringify(userPayload) }
    ]
  })
  const text = res.choices?.[0]?.message?.content || '{}'
  try {
    return JSON.parse(text)
  } catch {
    const match = text.match(/\{[\s\S]*\}/)
    return match ? JSON.parse(match[0]) : {}
  }
}

function caseBlob(c) {
  if (!c) return {}
  return {
    applicantName: c.applicantName,
    originCountry: c.originCountry,
    destinationCountry: c.destinationCountry,
    pathway: c.pathway,
    visaType: c.visaType,
    employer: c.employer,
    facts: c.facts || {},
    documents: (c.documents || []).map((d) => ({
      name: d.name,
      docType: d.extracted?.docType,
      fields: d.extracted?.fields,
      excerpt: (d.text || '').slice(0, 4000)
    })),
    tasks: c.tasks || [],
    notes: c.notes || ''
  }
}

// --- Capabilities ----------------------------------------------------------

export async function extractDocument(payload) {
  const task = `TASK: Document review and field extraction.
Read the immigration document text and return JSON:
{
  "docType": "best guess of the document type, e.g. Passport, I-797 Approval Notice, IRCC Work Permit, LCA, Pay Stub, Study Permit, I-94",
  "jurisdiction": "USA | Canada | Other",
  "summary": "2-3 sentence plain-language summary",
  "fields": [{ "label": "string", "value": "string", "confidence": "high|medium|low" }],
  "flags": [{ "severity": "high|medium|low", "note": "anything missing, expiring, or inconsistent" }]
}`
  return callJSON(task, payload)
}

export async function answerQuestion(payload) {
  const task = `TASK: Case file question answering. Answer ONLY from the case context provided.
If the answer is not in the case, say so and suggest what document would answer it.
Return JSON:
{
  "answer": "clear, professional answer in plain language",
  "citations": [{ "source": "document name or fact", "detail": "what it says" }],
  "confidence": "high|medium|low",
  "followUps": ["suggested next question", "..."]
}`
  return callJSON({ task }, { instruction: task, case: caseBlob(payload.case), question: payload.question })
}

export async function riskFlags(payload) {
  const task = `TASK: Compliance and travel risk scan across the whole case.
Look for expirations (passport, I-94, status, permits), worksite/LCA mismatches, wage issues, missing dependents or evidence, filing-window thresholds, travel risk while petitions or advance parole are pending, and status-maintenance gaps.
Return JSON:
{
  "findings": [{
    "severity": "critical|high|medium|low|info",
    "title": "short title",
    "explanation": "why it matters, grounded in the facts",
    "nextAction": "the single next action",
    "owner": "Immigrant|Employer|Counsel|Ellis",
    "dueDate": "relative or specific, or 'n/a'"
  }]
}`
  return callJSON(task, caseBlob(payload.case))
}

export async function summarizeNotice(payload) {
  const task = `TASK: Government notice / RFE summary (USCIS I-797, RFE, NOID, IRCC letters, etc.).
Return JSON:
{
  "noticeType": "string",
  "jurisdiction": "USA | Canada | Other",
  "summary": "plain-language summary",
  "severity": "critical|high|medium|low|info",
  "deadlines": [{ "label": "string", "date": "string or 'not stated'" }],
  "requiredActions": [{ "action": "string", "owner": "Immigrant|Employer|Counsel|Ellis" }],
  "evidenceRequested": ["string"]
}`
  return callJSON(task, { noticeText: payload.text, case: caseBlob(payload.case) })
}

export async function prepareForm(payload) {
  const task = `TASK: Form preparation. Pre-fill the requested immigration form from case facts. Mark unknown values as "MISSING" so counsel knows what to collect. Do not invent values.
Return JSON:
{
  "formName": "official form name and number if applicable",
  "jurisdiction": "USA | Canada | Other",
  "purpose": "one line",
  "sections": [{ "title": "string", "fields": [{ "label": "string", "value": "string or MISSING", "status": "filled|missing|review" }] }],
  "missingInfo": ["string"],
  "filingChecklist": ["string"]
}`
  return callJSON(task, { formType: payload.formType, case: caseBlob(payload.case) })
}

export async function evidencePacket(payload) {
  const task = `TASK: Build an attorney-ready evidence packet / handoff for counsel review.
Return JSON:
{
  "title": "string",
  "overview": "case overview for the reviewing attorney",
  "recommendedPathway": "string",
  "exhibits": [{ "label": "Exhibit A...", "description": "string", "status": "ready|missing|needs-review" }],
  "openItems": ["what counsel must still decide or collect"],
  "attorneyNotes": "concise notes and flags for the attorney"
}`
  return callJSON(task, caseBlob(payload.case))
}

export async function complianceAudit(payload) {
  const task = `TASK: Run a daily compliance audit for the employer and the immigrant.
Cover status validity, work authorization, worksite vs LCA/permit, I-9 / right-to-work, wage parity, public access file (US) or LMIA conditions (Canada), and upcoming obligations.
Return JSON:
{
  "score": 0-100,
  "summary": "one line",
  "findings": [{ "severity": "critical|high|medium|low", "area": "string", "issue": "string", "remediation": "string", "owner": "Immigrant|Employer|Counsel|Ellis" }],
  "passed": ["checks that passed"]
}`
  return callJSON(task, caseBlob(payload.case))
}

export async function travelRisk(payload) {
  const task = `TASK: Pre-travel risk assessment for an upcoming trip.
Weigh passport validity, visa/permit status, pending petitions or advance parole, re-entry documents, visa stamping needs, and country-specific risk for the origin nationality.
Return JSON:
{
  "recommendation": "go|caution|hold",
  "reentryRisk": "low|medium|high",
  "reasons": ["string"],
  "checklist": [{ "item": "string", "status": "ready|missing|review" }],
  "summary": "one line recommendation"
}`
  return callJSON(task, { trip: payload.trip, case: caseBlob(payload.case) })
}

export async function lifecyclePlan(payload) {
  const task = `TASK: Run the immigration lifecycle. Given the case, determine the current stage and produce the plan to keep it moving.
Stages: Onboarding, Filing, Renewals, Travel, Compliance, Notices/RFEs.
Return JSON:
{
  "stage": "current stage",
  "stageSummary": "one line on where the case stands",
  "tasks": [{ "title": "string", "owner": "Immigrant|Employer|Counsel|Ellis", "due": "string", "why": "string", "priority": "high|medium|low" }],
  "upcomingMilestones": [{ "label": "string", "date": "string" }]
}`
  return callJSON(task, caseBlob(payload.case))
}

export async function translateDocument(payload) {
  const task = `TASK: Certified-style document translation for immigration filing.
Detect the source language and translate the document into the target language, preserving labels and structure.
Return JSON:
{
  "sourceLanguage": "string",
  "targetLanguage": "string",
  "translation": "full translated text, preserving line structure",
  "certified": true,
  "note": "one line on certification / human counter-signature",
  "glossary": [{ "term": "string", "meaning": "string" }]
}`
  return callJSON(task, { text: payload.text, targetLanguage: payload.targetLanguage || 'English' })
}

export async function authenticityCheck(payload) {
  const task = `TASK: Document authenticity screening (automated, non-forensic).
Check for internal consistency, expected identifiers, date formatting, and specimen/placeholder markers.
Return JSON:
{
  "score": 0-100,
  "verdict": "likely-authentic|review-recommended|high-scrutiny",
  "summary": "one line",
  "checks": [{ "status": "pass|review", "label": "string", "detail": "string" }],
  "disclaimer": "string"
}`
  return callJSON(task, { text: payload.text })
}

export async function assistantChat(payload) {
  // Free-form role-aware assistant grounded in the optional case context.
  const role = payload.role || 'user'
  const res = await client().chat.completions.create({
    model: model(),
    temperature: 0.3,
    messages: [
      { role: 'system', content: DOMAIN },
      {
        role: 'system',
        content: `The current user is the ${role}. Tailor tone and detail to them. Be concise and practical. If a case is provided, ground answers in it. Plain text answer, no JSON.`
      },
      ...(payload.case ? [{ role: 'system', content: 'CASE CONTEXT: ' + JSON.stringify(caseBlob(payload.case)) }] : []),
      ...(payload.history || []),
      { role: 'user', content: payload.message }
    ]
  })
  return { reply: res.choices?.[0]?.message?.content || '' }
}

export async function testKey(apiKey) {
  const c = new OpenAI({ apiKey })
  await c.models.list()
  return true
}
