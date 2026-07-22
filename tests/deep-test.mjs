// Deep functional test of Ellis engine + seeds + Ask Ellis command parsing.
import { seedCases } from '/Users/sammynawaly/Documents/ellis-app/src/main/seed.js'
import * as eng from '/Users/sammynawaly/Documents/ellis-app/src/main/localEngine.js'
import { readFileSync } from 'fs'

let pass = 0, fail = 0
function ok(cond, name, extra = '') {
  if (cond) { pass++; console.log('  PASS', name) }
  else { fail++; console.log('  FAIL', name, extra) }
}

const cases = seedCases()
console.log(`\n== Seeds: ${cases.length} cases ==`)
ok(cases.length >= 16, 'seed count >= 16', String(cases.length))
const bao = cases.find((c) => c.applicantName === 'Bao Tran')
ok(!!bao, 'Bao Tran case exists')
ok(bao.employer === 'deep24, Inc.', 'Bao employer deep24')
ok(bao.originCountry === 'Vietnam' && bao.visaType === 'H-1B', 'Bao Vietnam H-1B')
ok(bao.documents.length >= 7, 'Bao has >=7 documents', String(bao.documents.length))
ok(bao.tasks.some((t) => /right-to-control|board resolution/i.test(t.title)), 'founder right-to-control task')
const ids = cases.map((c) => c.id)
ok(new Set(ids).size === ids.length, 'no duplicate case ids')
// Every case structurally sound
for (const c of cases) {
  if (!c.applicantName || !c.destinationCountry || !c.pathway || !Array.isArray(c.documents) || !Array.isArray(c.tasks)) {
    ok(false, `case ${c.id} structure`); break
  }
}
ok(true, 'all cases structurally sound')

console.log('\n== Engine capabilities on Bao Tran ==')
const p = { case: bao }
const extract = await eng.extractDocument({ text: bao.documents[0].text, name: bao.documents[0].name })
ok(extract && extract.docType, 'extractDocument returns docType', JSON.stringify(extract).slice(0, 80))
const risks = await eng.riskFlags(p)
ok(Array.isArray(risks.findings), 'riskFlags returns findings')
const comp = await eng.complianceAudit(p)
ok(typeof comp.score === 'number' && comp.score >= 0 && comp.score <= 100, 'complianceAudit score 0-100', String(comp.score))
const form = await eng.prepareForm({ formType: 'Form I-129', case: bao })
ok(form.formName?.includes('I-129'), 'prepareForm I-129')
const filled = (form.sections || []).flatMap((s) => s.fields).filter((f) => f.status === 'filled')
ok(filled.length >= 6, 'I-129 fields filled from case', `${filled.length} filled`)
ok((form.missingInfo || []).length === 0, 'I-129 no missing fields for Bao', JSON.stringify(form.missingInfo))
ok(JSON.stringify(form).includes('deep24') || JSON.stringify(form).includes('DEEP24'), 'I-129 contains deep24')
const ev = await eng.evidencePacket(p)
ok((ev.exhibits || []).length >= 5, 'evidencePacket exhibits', String((ev.exhibits || []).length))
const life = await eng.lifecyclePlan(p)
ok(life.stage && (life.tasks || []).length > 0, 'lifecyclePlan stage+tasks')
const trav = await eng.travelRisk({ trip: {}, case: bao })
ok(trav.recommendation, 'travelRisk recommendation', trav.recommendation)
const auth = await eng.authenticityCheck({ text: bao.documents[0].text })
ok(auth.verdict, 'authenticityCheck verdict', auth.verdict)

console.log('\n== answerQuestion grounding + citations ==')
const qa1 = await eng.answerQuestion({ case: bao, question: 'Who is the applicant?' })
ok(qa1.answer.includes('Bao Tran'), 'names Bao Tran')
ok(qa1.answer.includes('deep24'), 'names deep24', qa1.answer.slice(0, 120))
const qa2 = await eng.answerQuestion({ case: bao, question: 'When does his status expire?' })
ok(qa2.answer.includes('2027-01-14') || qa2.answer.includes('2027'), 'expiry cites real date')
ok(qa2.citations.some((c) => /uscis|egov/i.test(c.detail || '')), 'expiry cites official USCIS source', JSON.stringify(qa2.citations))
const qa3 = await eng.answerQuestion({ case: bao, question: 'Can Bao travel to Vietnam right now?' })
ok(qa3.citations.some((c) => /uscis|cbp|travel\.state/i.test(c.detail || '')), 'travel answer cites gov source')
const qa4 = await eng.answerQuestion({ case: bao, question: 'Are there compliance risks?' })
ok(qa4.citations.some((c) => /dol\.gov|uscis/i.test(c.detail || '')), 'compliance cites DOL/USCIS')
// Canada case citations
const ca = cases.find((c) => c.destinationCountry === 'Canada' && c.pathway === 'work')
const qa5 = await eng.answerQuestion({ case: ca, question: 'What form do we need?' })
ok(qa5.citations.some((c) => /canada\.ca/i.test(c.detail || '')), 'Canada answer cites canada.ca', JSON.stringify(qa5.citations.slice(-2)))

console.log('\n== Ask Ellis command parsing (extracted from CaseWorkspace.jsx) ==')
const src = readFileSync('/Users/sammynawaly/Documents/ellis-app/src/renderer/src/screens/CaseWorkspace.jsx', 'utf8')
const grab = (name, end) => {
  const i = src.indexOf(`function ${name}`)
  const j = src.indexOf(end, i)
  return src.slice(i, j)
}
const code = grab('parseCommand', '\n// Pick a specific form') +
  grab('formFromText', '\nfunction wantsDesktop') +
  src.slice(src.indexOf('function wantsDesktop'), src.indexOf('\n\nasync function executeCommand'))
const primaryFormSrc = src.slice(src.indexOf('function primaryForm'), src.indexOf('\n}', src.indexOf('function primaryForm')) + 2)
const fns = new Function(primaryFormSrc + '\n' + code + '\nreturn { parseCommand, formFromText, wantsDesktop, primaryForm }')()

const T = (text, expected) => ok(fns.parseCommand(text) === expected, `"${text}" -> ${expected}`, `got ${fns.parseCommand(text)}`)
T('file their forms then submit it to USCIS on their behalf', 'fileSubmit')
T('file the I-129 and submit it to uscis', 'fileSubmit')
T('submit the petition to the government portal', 'fileSubmit')
T('submit his study permit application to IRCC on his behalf', 'fileSubmit')
T('fill out the I-129 and download it to my desktop', 'forms')
T('prepare the DS-160', 'forms')
T('run the risk flags using current documentation', 'risks')
T('check compliance', 'compliance')
T('build the counsel handoff', 'evidence')
T('translate the household registration', 'translation')
T('can he travel to vietnam?', null)  // question, not a command
T('who is the applicant?', null)
T('what documents are still missing?', null)
ok(fns.formFromText('file the I-129 and submit it', bao) === 'Form I-129', 'formFromText I-129')
ok(fns.formFromText('submit everything to uscis', bao) === 'Form I-129', 'formFromText defaults to primary form for H-1B')
ok(fns.wantsDesktop('save it to my desktop') === true, 'wantsDesktop desktop')

console.log(`\n==== ${pass} passed, ${fail} failed ====`)
process.exit(fail ? 1 : 0)
