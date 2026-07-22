// End-to-end test: Ask Ellis question answered by the local LLM (Ollama),
// grounded in the Bao Tran case, with citations.
import { seedCases } from '../src/main/seed.js'
import * as llm from '../src/main/localLLM.js'

const bao = seedCases().find((c) => c.applicantName === 'Bao Tran')
const ping = await llm.ping()
console.log('Ollama available:', ping.available, '· models:', ping.models)
if (!ping.available) process.exit(1)

const cfg = { model: ping.models.find((m) => m.startsWith('llama3.1')) || ping.models[0] }
const t0 = Date.now()
const res = await llm.answerQuestion(cfg, { case: bao, question: 'What is the current state of this H-1B petition and what should the employer do next?' })
console.log(`\n[${((Date.now() - t0) / 1000).toFixed(1)}s] ANSWER:\n${res.answer}\n`)
console.log('CITATIONS:', JSON.stringify(res.citations, null, 1))

const mentions = /bao|tran|deep24|wac2690038214|premium|california service center/i.test(res.answer)
const hasGov = res.citations.some((c) => /uscis|dol\.gov|travel\.state|canada\.ca|cbp/i.test(c.detail || ''))
console.log('\ngrounded in case:', mentions, '· cites gov source:', hasGov)
process.exit(mentions && hasGov ? 0 : 1)
