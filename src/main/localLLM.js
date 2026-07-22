// Free, private, no-API-key AI for Ellis via a local LLM (Ollama).
// The user installs Ollama (one download) and pulls a model once; Ellis then
// runs a real LLM entirely on-device — no key, no cost, no data leaving the
// laptop. If Ollama isn't running, callers fall back to the built-in engine.

import { officialRefs } from './localEngine.js'

const DEFAULT_ENDPOINT = 'http://127.0.0.1:11434'

const DOMAIN = `You are Ellis, an expert AI immigration operations agent covering the full United States (USCIS/DOS) and Canadian (IRCC) systems — work, study, and travel — for applicants from any country. You are precise, practical, and concise. Explain options, documents, timelines, and compliance clearly. You are not a lawyer; add a one-line reminder to confirm with licensed counsel only for consequential legal strategy. Never invent specific case facts that were not provided.`

function caseContext(c) {
  if (!c) return ''
  const facts = Object.entries(c.facts || {}).map(([k, v]) => `${k}: ${v}`).join('; ')
  const docs = (c.documents || []).map((d) => d.name).join(', ')
  return `\n\nCASE FILE:\nApplicant: ${c.applicantName}\nRoute: ${c.originCountry || '?'} -> ${c.destinationCountry}\nPathway: ${c.pathway}${c.visaType ? ` (${c.visaType})` : ''}\nEmployer: ${c.employer || 'n/a'}\nFacts: ${facts || 'none recorded'}\nDocuments on file: ${docs || 'none'}\nNotes: ${c.notes || 'none'}`
}

// Check whether Ollama is up and which models are installed.
export async function ping(endpoint) {
  try {
    const res = await fetch((endpoint || DEFAULT_ENDPOINT) + '/api/tags', { method: 'GET' })
    if (!res.ok) return { available: false }
    const data = await res.json()
    return { available: true, models: (data.models || []).map((m) => m.name) }
  } catch {
    return { available: false }
  }
}

async function chat({ endpoint, model, system, messages }) {
  const res = await fetch((endpoint || DEFAULT_ENDPOINT) + '/api/chat', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ model: model || 'llama3.1:8b', stream: false, keep_alive: '30m', options: { temperature: 0.3 }, messages: [{ role: 'system', content: system }, ...messages] })
  })
  if (!res.ok) throw new Error(`Local AI (Ollama) error ${res.status}`)
  const data = await res.json()
  const text = (data.message?.content || '').trim()
  if (!text) throw new Error('Local AI returned an empty response')
  return text
}

export async function answerQuestion(cfg, p) {
  const c = p.case || {}
  const system = DOMAIN + caseContext(c) + `\n\nINSTRUCTIONS: Answer ONLY about this specific applicant and case. Use the applicant's name, route (${c.originCountry || '?'} -> ${c.destinationCountry || '?'}), pathway/visa, the recorded facts/dates, and the documents on file. If the question is general, still apply it to this case. Reference document names when relevant. Be concrete and concise (under ~180 words). Do not invent facts that are not in the case file; if something needed is missing, say it's missing and what to provide.`
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

export async function assistantChat(cfg, p) {
  const history = (p.history || []).slice(-8).map((m) => ({ role: m.role === 'assistant' ? 'assistant' : 'user', content: m.content }))
  const messages = [...history, { role: 'user', content: p.message || '' }]
  const reply = await chat({ ...cfg, system: DOMAIN, messages })
  return { reply }
}
