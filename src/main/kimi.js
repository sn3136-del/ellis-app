// Kimi K3 (Moonshot AI) — Ellis's primary intelligence engine.
// Kimi K3 is OpenAI-SDK-compatible (https://api.moonshot.ai/v1), 1M-token
// context, released July 2026. Ellis ships an immigration-tailored profile of
// the model: a specialized system prompt, low temperature, and structured
// post-processing so every answer behaves like a purpose-built immigration
// model rather than a general chatbot. If Kimi is unreachable, callers fall
// back to Ollama / Claude / the built-in engine automatically.

import { officialRefs } from './localEngine.js'

const DEFAULT_ENDPOINT = 'https://api.moonshot.ai/v1'
const DEFAULT_MODEL = 'kimi-k3'

// The "modified for immigration" layer: Kimi K3 is steered into a dedicated
// immigration operations profile. Kept stable so Moonshot's automatic context
// caching makes repeat calls ~10x cheaper.
const DOMAIN = `You are Ellis Immigration Intelligence, a specialized configuration of Kimi K3 tuned exclusively for global immigration operations. You cover tourist, work, and student visas for every country pair, with deep expertise in USCIS/DOS (USA), IRCC (Canada), Schengen, UK Home Office, and the e-visa systems of Asia-Pacific, the Gulf, and Latin America.

Rules of the profile:
- Answer only immigration questions; politely decline anything else.
- Be precise, practical, and concise. Prefer checklists, deadlines, and concrete document names over prose.
- Ground every answer in the supplied case/traveler file; never invent facts that were not provided. If something required is missing, say exactly what to provide.
- For tourist visas, always classify the route first: visa-free, eTA/eVisa, visa on arrival, or embassy/consular visa — then give requirements, fees, and processing time.
- Speak in natural, warm sentences. Never recite internal stage labels ("your visa is at Awaiting decision") — say it the way a person would ("your visa is with the embassy and we're monitoring it for you"). No markdown formatting (**, ##, bullets with *) — plain sentences and simple numbered lists only.
- You are not a lawyer; add a one-line reminder to confirm with licensed counsel only for consequential legal strategy.`

function caseContext(c) {
  if (!c) return ''
  const facts = Object.entries(c.facts || {}).map(([k, v]) => `${k}: ${v}`).join('; ')
  const docs = (c.documents || []).map((d) => d.name).join(', ')
  return `\n\nCASE FILE:\nApplicant: ${c.applicantName}\nRoute: ${c.originCountry || '?'} -> ${c.destinationCountry}\nPathway: ${c.pathway}${c.visaType ? ` (${c.visaType})` : ''}\nEmployer: ${c.employer || 'n/a'}\nFacts: ${facts || 'none recorded'}\nDocuments on file: ${docs || 'none'}\nNotes: ${c.notes || 'none'}`
}

// Is the Kimi endpoint reachable with this key?
export async function ping(cfg = {}) {
  const key = (cfg.apiKey || '').trim()
  if (!key) return { available: false, reason: 'NO_KEY' }
  try {
    const res = await fetch((cfg.endpoint || DEFAULT_ENDPOINT) + '/models', {
      headers: { authorization: 'Bearer ' + key }
    })
    if (!res.ok) return { available: false, reason: 'HTTP_' + res.status }
    const data = await res.json()
    return { available: true, models: (data.data || []).map((m) => m.id) }
  } catch (err) {
    return { available: false, reason: err?.message || 'UNREACHABLE' }
  }
}

export async function chat({ apiKey, endpoint, model, system, messages, maxTokens = 1200, reasoningEffort, responseFormat }) {
  const key = (apiKey || '').trim()
  if (!key) { const e = new Error('NO_KIMI_KEY'); e.code = 'NO_KIMI_KEY'; throw e }
  const body = {
    model: model || DEFAULT_MODEL,
    max_tokens: maxTokens,
    messages: [{ role: 'system', content: system }, ...messages]
  }
  if (responseFormat) body.response_format = responseFormat
  // Kimi K3: thinking is always on; reasoning_effort (low/high/max) is the
  // only depth control — K3 rejects non-default temperature outright.
  body.reasoning_effort = reasoningEffort || 'low'
  // Retry transient overload/rate-limit (429) and 5xx a couple of times with
  // backoff before letting the caller fall through to the next engine, so a
  // momentary Kimi overload doesn't bounce every request to the fallback.
  let lastErr = null
  for (let attempt = 0; attempt < 3; attempt++) {
    if (attempt) await new Promise((r) => setTimeout(r, 1500 * attempt))
    let res
    try {
      res = await fetch((endpoint || DEFAULT_ENDPOINT) + '/chat/completions', {
        method: 'POST',
        signal: AbortSignal.timeout(90000),
        headers: { 'content-type': 'application/json', authorization: 'Bearer ' + key },
        body: JSON.stringify(body)
      })
    } catch (err) { lastErr = err; continue }
    if (res.ok) {
      const data = await res.json()
      const text = (data.choices?.[0]?.message?.content || '').trim()
      if (!text) throw new Error('Kimi K3 returned an empty response')
      return text
    }
    const errBody = await res.text().catch(() => '')
    lastErr = new Error(`Kimi K3 API ${res.status}: ${errBody.slice(0, 300)}`)
    // Only retry transient conditions; auth/credit/validation fail fast.
    if (res.status !== 429 && res.status < 500) break
  }
  throw lastErr
}

// Case Q&A — same result shape as the other providers.
export async function answerQuestion(cfg, p) {
  const c = p.case || {}
  const system = DOMAIN + caseContext(c) + `\n\nINSTRUCTIONS: Answer ONLY about this specific applicant and case. Use the recorded facts, dates, and documents on file; reference document names when relevant. Be concrete and under ~180 words.`
  const answer = await chat({ ...cfg, system, messages: [{ role: 'user', content: p.question || '' }] })
  const q = (p.question || '').toLowerCase()
  const topic = /expir|deadline|renew|status/.test(q) ? 'expiry'
    : /travel|trip|re-?enter|abroad/.test(q) ? 'travel'
    : /complian|audit|risk/.test(q) ? 'compliance'
    : /form|petition|application|i-?\d|imm ?\d|ds-?\d/.test(q) ? 'form' : 'general'
  return {
    answer,
    citations: [
      ...(c.documents || []).slice(0, 3).map((d) => ({ source: d.name, detail: d.extracted?.docType || 'document on file' })),
      ...officialRefs(c, topic)
    ],
    confidence: 'high',
    followUps: ['What documents are still missing?', 'When does status expire?', 'Run the risk flags']
  }
}

// General immigration assistant chat.
export async function assistantChat(cfg, p) {
  const history = (p.history || []).slice(-8).map((m) => ({ role: m.role === 'assistant' ? 'assistant' : 'user', content: m.content }))
  const reply = await chat({ ...cfg, system: DOMAIN, messages: [...history, { role: 'user', content: p.message || '' }] })
  return { reply }
}

// Generic single-prompt completion for agent tasks (portal research etc.).
export async function textCompletion(cfg, prompt, maxTokens = 700) {
  return chat({ ...cfg, system: DOMAIN, messages: [{ role: 'user', content: prompt }], maxTokens, reasoningEffort: 'low' })
}

// Quick multimodal language ID: returns the ISO code of the document's
// dominant language (cheap — capped output).
export async function visionDetectLang(cfg, imageDataUrl) {
  const key = (cfg.apiKey || '').trim()
  if (!key) return null
  const res = await fetch((cfg.endpoint || DEFAULT_ENDPOINT) + '/chat/completions', {
    method: 'POST',
    signal: AbortSignal.timeout(45000),
    headers: { 'content-type': 'application/json', authorization: 'Bearer ' + key },
    body: JSON.stringify({
      model: cfg.model || DEFAULT_MODEL,
      max_tokens: 300,
      reasoning_effort: 'low',
      messages: [{
        role: 'user',
        content: [
          { type: 'image_url', image_url: { url: imageDataUrl } },
          { type: 'text', text: 'What is the dominant written language of this document? Reply with ONLY the ISO 639-1 code (e.g. en, zh, ja, ko, ar, ru, th, fr, es, de).' }
        ]
      }]
    })
  })
  if (!res.ok) return null
  const data = await res.json()
  const t = (data.choices?.[0]?.message?.content || '').trim().toLowerCase()
  // Prefer an exact 2-letter reply, then a parenthesized code, then a
  // language-name map — a bare first-2-letters match turns "the language" into "th".
  if (/^[a-z]{2}(-[a-z]+)?$/.test(t)) return t.split('-')[0]
  const paren = t.match(/\(([a-z]{2})(?:-[a-z]+)?\)/)
  if (paren) return paren[1]
  const NAMES = { chinese: 'zh', mandarin: 'zh', japanese: 'ja', korean: 'ko', arabic: 'ar', russian: 'ru', thai: 'th', french: 'fr', spanish: 'es', german: 'de', portuguese: 'pt', italian: 'it', english: 'en', vietnamese: 'vi', indonesian: 'id', hindi: 'hi', turkish: 'tr' }
  for (const [name, code] of Object.entries(NAMES)) if (t.includes(name)) return code
  const bare = t.match(/\b([a-z]{2})\b/)
  return bare ? bare[1] : null
}

// Multimodal document verification: Kimi K3 looks at the uploaded file and
// determines what it actually IS (not what its filename says) and whether it
// is a plausible, complete instance of that document type — the gate that
// stops a selfie uploaded as a "bank statement" from reaching the consulate.
export const DOC_TYPES = ['passport', 'photo', 'bank_statement', 'employment_letter', 'enrollment_letter', 'flight_itinerary', 'hotel_booking', 'travel_insurance', 'id_card', 'household_register', 'invitation_letter', 'financial_document', 'itinerary', 'other']
export async function visionClassifyDoc(cfg, imageDataUrl) {
  const key = (cfg.apiKey || '').trim()
  if (!key) { const e = new Error('NO_KIMI_KEY'); e.code = 'NO_KIMI_KEY'; throw e }
  const prompt = `You are verifying a document uploaded for a tourist-visa application. Look at the image and decide what it actually is.
Reply with ONLY a JSON object:
{"type": one of ${JSON.stringify(DOC_TYPES)},
 "label": short human name (e.g. "Bank statement — ICBC", "Passport photo page"),
 "plausible": true if this is a genuine, usable instance of that type for a visa filing (readable, complete, has the hallmarks: letterhead/stamps/account rows/MRZ/face photo as appropriate), else false,
 "issues": array of short strings — anything that would make a consular officer reject it (cropped, unreadable, screenshot of a screen, expired, missing signature/stamp, wrong person, blank page), empty if none,
 "summary": one sentence describing what the document shows}`
  const res = await fetch((cfg.endpoint || DEFAULT_ENDPOINT) + '/chat/completions', {
    method: 'POST',
    signal: AbortSignal.timeout(60000),
    headers: { 'content-type': 'application/json', authorization: 'Bearer ' + key },
    body: JSON.stringify({
      model: cfg.model || DEFAULT_MODEL,
      max_tokens: 700,
      reasoning_effort: 'low',
      response_format: { type: 'json_object' },
      messages: [{
        role: 'user',
        content: [
          { type: 'image_url', image_url: { url: imageDataUrl } },
          { type: 'text', text: prompt }
        ]
      }]
    })
  })
  if (!res.ok) throw new Error('Kimi vision ' + res.status + ': ' + (await res.text().catch(() => '')).slice(0, 200))
  const data = await res.json()
  const text = data.choices?.[0]?.message?.content || ''
  const m = text.match(/\{[\s\S]*\}/)
  if (!m) throw new Error('Kimi vision returned no classification')
  const o = JSON.parse(m[0])
  return {
    type: DOC_TYPES.includes(o.type) ? o.type : 'other',
    label: String(o.label || o.type || 'Document').slice(0, 120),
    plausible: o.plausible !== false,
    issues: Array.isArray(o.issues) ? o.issues.map((x) => String(x).slice(0, 160)).slice(0, 6) : [],
    summary: String(o.summary || '').slice(0, 300)
  }
}

// Multimodal document translation: Kimi K3 reads the document image directly
// (any language/script) and returns each line with its translation and layout
// position. This is more accurate for non-Latin scripts than on-device OCR.
// Returns { lines: [{orig, translated, x, y, w, h}], aspect } or throws.
export async function visionTranslate(cfg, { imageDataUrl, targetName, aspect }) {
  const key = (cfg.apiKey || '').trim()
  if (!key) { const e = new Error('NO_KIMI_KEY'); e.code = 'NO_KIMI_KEY'; throw e }
  const prompt = `This is a scanned official/identity/travel document. Extract EVERY text line and translate each into ${targetName}. Transliterate personal names and place names into the ${targetName} script (e.g. Chinese 陈浩 → "Chen Hao"). Convert dates to a plain numeric form in ${targetName} (e.g. 1988年04月12日 → "12 April 1988"). Keep passport/ID/document numbers exactly as-is. Return ONLY a JSON array of objects, top-to-bottom in reading order: {"orig":"original text","t":"${targetName} translation","x":<left as 0-1 fraction>,"y":<top as 0-1 fraction>,"w":<width 0-1>,"h":<height 0-1>}. Estimate x/y/w/h from the visual position on the page.`
  const res = await fetch((cfg.endpoint || DEFAULT_ENDPOINT) + '/chat/completions', {
    method: 'POST',
    signal: AbortSignal.timeout(90000),
    headers: { 'content-type': 'application/json', authorization: 'Bearer ' + key },
    body: JSON.stringify({
      model: cfg.model || DEFAULT_MODEL,
      max_tokens: 4000,
      reasoning_effort: 'low',
      messages: [{
        role: 'user',
        content: [
          { type: 'image_url', image_url: { url: imageDataUrl } },
          { type: 'text', text: prompt }
        ]
      }]
    })
  })
  if (!res.ok) throw new Error('Kimi vision ' + res.status + ': ' + (await res.text().catch(() => '')).slice(0, 200))
  const data = await res.json()
  const text = data.choices?.[0]?.message?.content || ''
  const m = text.match(/\[[\s\S]*\]/)
  if (!m) throw new Error('Kimi vision returned no structured translation')
  const arr = JSON.parse(m[0])
  const lines = arr.filter((o) => o && (o.orig || o.t)).map((o, i) => ({
    text: String(o.orig || ''),
    translated: String(o.t != null ? o.t : (o.en != null ? o.en : o.orig || '')),
    x: Number.isFinite(o.x) ? o.x : 0.08,
    y: Number.isFinite(o.y) ? o.y : (i + 1) / (arr.length + 2),
    w: Number.isFinite(o.w) ? o.w : 0.6,
    h: Number.isFinite(o.h) ? o.h : 0.04
  }))
  return { lines, aspect: aspect || 1.4 }
}

// Destination brief for the Trip.com one-click tourist-visa agent: a short,
// structured summary of the route's visa requirements for this traveler.
export async function tripBrief(cfg, p) {
  const t = p.traveler || {}
  const system = DOMAIN + `\n\nINSTRUCTIONS: Produce a tourist-visa brief for the traveler below in under 140 words. Structure: (1) route classification (visa-free / eVisa / visa on arrival / embassy visa), (2) the 3-5 key requirements, (3) typical processing time and validity. No preamble.`
  const user = `Traveler: ${t.name || 'traveler'}\nNationality: ${t.nationality}\nDestination: ${t.destination}\nTravel dates: ${t.departure || '?'} to ${t.return || '?'}\nPurpose: tourism (booked via Trip.com)\nDocuments provided: ${(t.documents || []).join(', ') || 'passport'}`
  const brief = await chat({ ...cfg, system, messages: [{ role: 'user', content: user }], maxTokens: 600, reasoningEffort: 'low' })
  return { brief }
}
