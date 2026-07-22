// Claude (Anthropic) engine for Ellis — the strongest option in the chain when
// the user provides an API key in Settings. Defaults to Claude Fable 5
// (Anthropic's most capable widely released model) with an automatic
// server-side fallback to Claude Opus 4.8 on safety-classifier refusals, per
// current API guidance. Chain position: Kimi K3 → Claude → Ollama → built-in.

import Anthropic from '@anthropic-ai/sdk'
import { existsSync } from 'fs'
import { homedir } from 'os'
import { join } from 'path'

const DEFAULT_MODEL = 'claude-fable-5'

// A Settings key wins; otherwise the zero-arg client resolves ambient
// credentials (ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN env vars, or an
// `ant auth login` profile on disk) — so logging in once activates Fable 5
// without touching the app.
export function ambientCredsAvailable() {
  if (process.env.ANTHROPIC_API_KEY || process.env.ANTHROPIC_AUTH_TOKEN) return true
  try { return existsSync(join(homedir(), '.config', 'anthropic', 'credentials')) } catch { return false }
}

function clientFor(key) {
  // Subscription-login first: when an `ant auth login` profile exists on this
  // machine, the zero-arg client resolves it (user's explicit preference).
  // A Settings API key is the fallback when no profile is present.
  if (ambientCredsAvailable() && !process.env.ANTHROPIC_API_KEY) return new Anthropic()
  const k = String(key || '').trim()
  return k ? new Anthropic({ apiKey: k }) : new Anthropic()
}

// Fable 5: thinking is always on (omit the param); include the server-side
// refusal fallback to Opus 4.8 by default. Other models use the plain API.
async function callClaude({ key, model, system, messages, maxTokens = 2048 }) {
  const client = clientFor(key)
  const m = model || DEFAULT_MODEL
  let response
  if (m === 'claude-fable-5') {
    response = await client.beta.messages.create({
      model: m,
      max_tokens: maxTokens,
      betas: ['server-side-fallback-2026-06-01'],
      fallbacks: [{ model: 'claude-opus-4-8' }],
      system,
      messages
    })
  } else {
    response = await client.messages.create({
      model: m,
      max_tokens: maxTokens,
      system,
      messages
    })
  }
  if (response.stop_reason === 'refusal') {
    throw new Error('Claude declined this request (safety classifiers).')
  }
  const text = (response.content || [])
    .filter((b) => b.type === 'text')
    .map((b) => b.text)
    .join('')
    .trim()
  if (!text) throw new Error('Claude returned an empty response')
  return text
}

const DOMAIN = `You are Ellis, an expert AI immigration operations agent covering the full United States (USCIS/DOS) and Canadian (IRCC) systems — work, study, and travel — as well as tourist-visa routes for every country pair worldwide. You are precise, practical, and concise. You explain options, documents, timelines, and compliance clearly. You are not a lawyer and add a brief reminder to confirm with licensed counsel only when giving consequential legal strategy. Never invent specific case facts that were not provided.`

function caseContext(c) {
  if (!c) return ''
  const facts = Object.entries(c.facts || {}).map(([k, v]) => `${k}: ${v}`).join('; ')
  const docs = (c.documents || []).map((d) => d.name).join(', ')
  return `\n\nCASE FILE:\nApplicant: ${c.applicantName}\nRoute: ${c.originCountry || '?'} -> ${c.destinationCountry}\nPathway: ${c.pathway}${c.visaType ? ` (${c.visaType})` : ''}\nEmployer: ${c.employer || 'n/a'}\nFacts: ${facts || 'none recorded'}\nDocuments on file: ${docs || 'none'}\nNotes: ${c.notes || 'none'}`
}

// Conversational case Q&A. Returns { answer, citations, followUps } shape.
export async function answerQuestion(key, model, p) {
  const c = p.case || {}
  const system = DOMAIN + caseContext(c) + '\n\nAnswer the user question using the case file above. Cite document names where relevant.'
  const answer = await callClaude({ key, model, system, messages: [{ role: 'user', content: p.question || '' }] })
  return {
    answer,
    citations: (c.documents || []).slice(0, 3).map((d) => ({ source: d.name, detail: d.extracted?.docType || 'document on file' })),
    confidence: 'high',
    followUps: ['What documents are still missing?', 'When does status expire?', 'Run the risk flags']
  }
}

// General immigration assistant. Returns { reply }.
export async function assistantChat(key, model, p) {
  const history = (p.history || []).slice(-8).map((m) => ({ role: m.role === 'assistant' ? 'assistant' : 'user', content: m.content }))
  const messages = [...history, { role: 'user', content: p.message || '' }]
  const reply = await callClaude({ key, model, system: DOMAIN, messages })
  return { reply }
}

// Generic single-prompt completion for agent tasks (portal research etc.).
export async function textCompletion(key, model, prompt, maxTokens = 700) {
  return callClaude({ key, model, system: DOMAIN, messages: [{ role: 'user', content: prompt }], maxTokens })
}

// Trip.com destination brief — short structured summary for the traveler.
export async function tripBrief(key, model, p) {
  const t = p.traveler || {}
  const system = DOMAIN + `\n\nINSTRUCTIONS: Produce a tourist-visa brief for the traveler below in under 140 words. Structure: (1) route classification (visa-free / eVisa / visa on arrival / embassy visa), (2) the 3-5 key requirements, (3) typical processing time and validity. No preamble.`
  const user = `Traveler: ${t.name || 'traveler'}\nNationality: ${t.nationality}\nDestination: ${t.destination}\nTravel dates: ${t.departure || '?'} to ${t.return || '?'}\nPurpose: tourism (booked via Trip.com)\nDocuments provided: ${(t.documents || []).join(', ') || 'passport'}`
  const brief = await callClaude({ key, model, system, messages: [{ role: 'user', content: user }], maxTokens: 600 })
  return { brief }
}

// Trip.com document gap review — structured JSON via output_config.format so
// the result validates without prompt-parsing tricks.
export async function tripGapReview(key, model, prompt) {
  const client = clientFor(key)
  const m = model || DEFAULT_MODEL
  const schema = {
    type: 'object',
    additionalProperties: false,
    required: ['covered', 'missing', 'notes'],
    properties: {
      covered: {
        type: 'array',
        items: {
          type: 'object',
          additionalProperties: false,
          required: ['requirement', 'satisfiedBy'],
          properties: { requirement: { type: 'string' }, satisfiedBy: { type: 'string' } }
        }
      },
      missing: {
        type: 'array',
        items: {
          type: 'object',
          additionalProperties: false,
          required: ['requirement', 'why'],
          properties: { requirement: { type: 'string' }, why: { type: 'string' } }
        }
      },
      notes: { type: 'string' }
    }
  }
  const params = {
    model: m,
    max_tokens: 1500,
    output_config: { format: { type: 'json_schema', schema } },
    messages: [{ role: 'user', content: prompt }]
  }
  const response = m === 'claude-fable-5'
    ? await client.beta.messages.create({ ...params, betas: ['server-side-fallback-2026-06-01'], fallbacks: [{ model: 'claude-opus-4-8' }] })
    : await client.messages.create(params)
  if (response.stop_reason === 'refusal') throw new Error('Claude declined this request.')
  const text = (response.content || []).filter((b) => b.type === 'text').map((b) => b.text).join('')
  return JSON.parse(text)
}
