import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { applicationInstructions } from '../../src/renderer/src/lib/applicationInstructions.js'
const guidance={disposition:'VISA_REQUIRED',application_channel:'online_portal',account_registration_steps:['Create UKVI account'],payment_process:['Pay an agent'],submission_process:['Attend VAC','Submit online']}
test('published application guidance remains readable without inventing an ordered procedure',()=>{
 const summary='Chinese ordinary passport holders must apply through a designated agency. Use the online issuance notice on a mobile device; a screenshot is not accepted.'
 const r=applicationInstructions({guidance:{...guidance,application_channel_detail:summary},application_steps_status:'unknown',apply_steps:[]})
 assert.equal(r.summary,summary);assert.equal(r.status,'unknown');assert.deepEqual(r.steps,[])
 for(const detail of ['',null,'Not publicly available.','not applicable','Unknown'])assert.equal(applicationInstructions({guidance:{...guidance,application_channel_detail:detail}}).summary,undefined)
 assert.equal(applicationInstructions({held:true,guidance:{...guidance,application_channel_detail:summary}}).summary,undefined)
 for(const disposition of ['VISA_REQUIRED','ELECTRONIC_AUTHORIZATION_REQUIRED','CONDITIONAL']){
  const result=applicationInstructions({application_steps_status:'not_applicable',guidance:{...guidance,disposition,application_channel_detail:summary}})
  assert.equal(result.status,'unknown');assert.equal(result.summary,summary)
 }
})
test('old cached lists and unmarked apply_steps stay unknown',()=>{
 for(const r of [{},{apply_steps:['Pay fee','Submit']}])assert.deepEqual(applicationInstructions({...r,guidance}),{status:'unknown',steps:[],sourceUrl:null})
})
test('reviewed full order and all mandatory qualifiers survive without hard limits',()=>{
 const steps=Array.from({length:7},(_,i)=>`${i+1}. ${'Complete full mandatory condition. '.repeat(20)}If asked, retain this exact qualification.`)
 const r={guidance,application_steps_status:'source_ordered',apply_steps:steps,application_steps_source_url:'https://www.gov.uk/standard-visitor/apply-standard-visitor-visa'}
 assert.deepEqual(applicationInstructions(r).steps,steps)
 assert.equal(applicationInstructions(r).sourceUrl,r.application_steps_source_url)
})
test('explicit empty malformed and held responses do not rebuild arrays',()=>{
 for(const apply_steps of [[],null,['ok',null],[''],{},'Pay'])assert.equal(applicationInstructions({guidance,application_steps_status:'source_ordered',apply_steps}).status,'unknown')
 assert.deepEqual(applicationInstructions({guidance,held:true,application_steps_status:'source_ordered',apply_steps:['Apply']}),{status:'not_applicable',steps:[],sourceUrl:null})
})
test('no-filing exemption has no procedure despite old raw fields',()=>assert.equal(applicationInstructions({guidance:{...guidance,disposition:'VISA_EXEMPT',application_channel:'none'},application_steps_status:'source_ordered',apply_steps:['Pay']}).status,'not_applicable'))
test('unknown source link is reference only and requires HTTPS',()=>{
 for(const url of ['javascript:alert(1)','http://example.com','https://user:secret@example.com/'])assert.equal(applicationInstructions({guidance,application_steps_source_url:url}).sourceUrl,null)
 const r=applicationInstructions({guidance,application_steps_source_url:'https://www.mofa.go.jp/'})
 assert.equal(r.status,'unknown');assert.deepEqual(r.steps,[]);assert.equal(r.sourceUrl,'https://www.mofa.go.jp/')
})
test('actual screen uses the contract and never raw list fallback or rewriting',()=>{
 const s=readFileSync(new URL('../../src/renderer/src/screens/TravelDatabase.jsx',import.meta.url),'utf8')
 assert.match(s,/const instructions = applicationInstructions\(result, g\)/)
 assert.match(s,/const applySteps = instructions.steps/)
 assert.doesNotMatch(s,/itemsOf\(g\.(account_registration_steps|payment_process|submission_process)\)/)
 assert.doesNotMatch(s,/apply_steps.map\(\(x\) => sentence/)
 assert.match(s,/instructions.status === 'unknown'/)
})
