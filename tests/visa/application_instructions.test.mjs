import test from 'node:test'
import assert from 'node:assert/strict'
import { applicationInstructions } from '../../src/renderer/src/lib/applicationInstructions.js'
const guidance={disposition:'VISA_REQUIRED',application_channel:'online_portal'}

test('available application prose remains readable without inventing an ordered procedure',()=>{
 const summary='Chinese ordinary passport holders must apply through a designated agency. Use the online issuance notice on a mobile device; a screenshot is not accepted.'
 for(const application_steps_status of ['unknown','not_applicable',undefined]){
  const r=applicationInstructions({guidance:{...guidance,application_channel_detail:summary},application_steps_status,apply_steps:[]})
  assert.equal(r.summary,summary);assert.equal(r.status,'available');assert.deepEqual(r.steps,[])
 }
 assert.equal(applicationInstructions({held:true,guidance:{...guidance,application_channel_detail:summary}}).summary,undefined)
})

test('published string and array instructions survive with complete qualifiers and no invented sequencing',()=>{
 const submit='For applicants in Indonesia, submit through KVAC Jakarta.\nAttend personally unless the published representation conditions apply.'
 const g={...guidance,account_registration_steps:'Create an account only if applying online.',
  submission_process:[submit,'Unknown',null,{text:'Unrecognized object must not become a step'}],
  payment_process:['Pay USD 40 plus the separate service fee.',submit]}
 const before=structuredClone(g)
 const r=applicationInstructions({guidance:g,application_steps_status:'unknown',apply_steps:[]})
 assert.equal(r.status,'available')
 assert.deepEqual(r.steps,[g.account_registration_steps,submit,g.payment_process[0]])
 assert.deepEqual(g,before)
})

test('unmarked apply_steps do not supply an ordered procedure',()=>{
 assert.deepEqual(applicationInstructions({guidance,apply_steps:['Pay fee','Submit']}),{status:'unknown',steps:[],sourceUrl:null})
})

test('reviewed full order wins and mandatory qualifiers survive without limits',()=>{
 const steps=Array.from({length:7},(_,i)=>`${i+1}. ${'Complete full mandatory condition. '.repeat(20)}If asked, retain this exact qualification.`)
 const r={guidance:{...guidance,submission_process:['An older saved procedure']},application_steps_status:'source_ordered',apply_steps:steps,application_steps_source_url:'https://www.gov.uk/standard-visitor/apply-standard-visitor-visa'}
 assert.equal(applicationInstructions(r).status,'source_ordered')
 assert.deepEqual(applicationInstructions(r).steps,steps)
 assert.equal(applicationInstructions(r).sourceUrl,r.application_steps_source_url)
})

test('placeholder-only values and references never manufacture instructions',()=>{
 for(const value of ['',null,'Unknown','Not publicly available.','not applicable','N/A','Not yet confirmed.',
  'Application steps are not yet confirmed.','https://www.mofa.go.jp/','[Official source](https://www.mofa.go.jp/)']){
  const r=applicationInstructions({guidance:{...guidance,application_channel_detail:value,submission_process:[value],payment_process:value},
   application_steps_status:'source_ordered',apply_steps:[value]})
  assert.deepEqual(r,{status:'unknown',steps:[],sourceUrl:null})
 }
 for(const apply_steps of [[],null,['ok',null],[''],{},'Pay',[['Apply']]])
  assert.equal(applicationInstructions({guidance,application_steps_status:'source_ordered',apply_steps}).status,'unknown')
})

test('held and no-filing exempt answers do not revive raw application fields',()=>{
 const raw={...guidance,submission_process:['Pay and submit']}
 assert.deepEqual(applicationInstructions({guidance:raw,held:true,apply_steps:['Apply'],application_steps_status:'source_ordered'}),{status:'not_applicable',steps:[],sourceUrl:null})
 assert.deepEqual(applicationInstructions({guidance:{...raw,disposition:'VISA_EXEMPT',application_channel:'none'},apply_steps:['Pay'],application_steps_status:'source_ordered'}),{status:'not_applicable',steps:[],sourceUrl:null})
})

test('product-specific procedures stay named and never fill a sibling or route',()=>{
 const g={...guidance,visa_products:[
  {type:'eVisa',submission_process:['Submit online only for this eVisa.']},
  {type:'Embassy visa',submission_process:'Apply at the responsible embassy.',payment_process:['Pay at the counter if requested.']},
  {type:'Unavailable',submission_process:['Not publicly available']},
  {submission_process:['Unnamed sibling instructions']},
 ]}
 const r=applicationInstructions({guidance:g})
 assert.equal(r.status,'available');assert.deepEqual(r.steps,[])
 assert.deepEqual(r.products.map(p=>[p.name,p.steps]),[
  ['eVisa',['Submit online only for this eVisa.']],
  ['Embassy visa',['Apply at the responsible embassy.','Pay at the counter if requested.']],
 ])
 assert.equal(applicationInstructions({guidance:{...guidance,submission_process:['Apply online.'],visa_products:[{type:'Same visa',submission_process:['Apply online.']}]}}).products,undefined)
})

test('an optional visa keeps its own procedure on a no-filing default route',()=>{
 const r=applicationInstructions({guidance:{disposition:'VISA_EXEMPT',application_channel:'none',submission_process:['Stale default application'],
  visa_products:[{type:'Optional long-stay visa',disposition:'VISA_REQUIRED',submission_process:'Apply for this visa before travel.'}]}})
 assert.equal(r.status,'available');assert.deepEqual(r.steps,[])
 assert.deepEqual(r.products[0].steps,['Apply for this visa before travel.'])
})

test('source links are reference metadata only and require HTTPS',()=>{
 for(const url of ['javascript:alert(1)','http://example.com','https://user:secret@example.com/'])
  assert.equal(applicationInstructions({guidance,application_steps_source_url:url}).sourceUrl,null)
 const r=applicationInstructions({guidance,application_steps_source_url:'https://www.mofa.go.jp/'})
 assert.equal(r.status,'unknown');assert.deepEqual(r.steps,[]);assert.equal(r.sourceUrl,'https://www.mofa.go.jp/')
})

for (const application_channel of ['none', 'not_required', 'no_application_required', 'none_or_port_of_entry', ' NONE_OR_PORT_OF_ENTRY ']) {
 test('no-filing channel does not revive stale route steps: '+application_channel,()=>{
  const g={disposition:'VISA_EXEMPT',application_channel,submission_process:['Pay and submit'],application_channel_detail:'Submit a visa application.'}
  assert.deepEqual(applicationInstructions({guidance:g,application_steps_status:'source_ordered',apply_steps:['Pay and submit']}),{status:'not_applicable',steps:[],sourceUrl:null})
  const withOptional=applicationInstructions({guidance:{...g,visa_products:[{type:'Optional visa',disposition:'VISA_REQUIRED',submission_process:['Apply only for the optional visa.']}]}})
  assert.deepEqual(withOptional.steps,[])
  assert.equal(withOptional.summary,undefined)
  assert.deepEqual(withOptional.products[0].steps,['Apply only for the optional visa.'])
 })
}
