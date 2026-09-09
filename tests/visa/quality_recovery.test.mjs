import test from 'node:test'
import assert from 'node:assert/strict'
import { indexRecoveredRecords, recoveredForRecord } from '../../src/renderer/src/lib/qualityRecovery.js'
const base = {travel_document_country:'HKG', destination_country:'VNM', travel_document_type:'ordinary_passport',travel_purpose:'tourism'}
test('recovery keeps current fixes and deliberately cleared fields untouched', () => {
  const old = {...base,visa_type_name:'Tourist',visa_requirement:'Visa-free',source_url:'https://old.example/',validity_duration:30}
  const current = {...base,visa_type_name:'Tourist',visa_requirement:'Visa Required in Advance',validity_duration:null,held:true}
  const before = structuredClone(current)
  assert.deepEqual(recoveredForRecord(current,indexRecoveredRecords([old])),[old])
  assert.deepEqual(current,before)
})
test('recovery never mixes passports, destinations, documents or purposes', () => {
  const old = {...base,visa_type_name:'Tourist'}
  const index=indexRecoveredRecords([old])
  for (const field of ['travel_document_country','destination_country','travel_document_type','travel_purpose']) {
    assert.deepEqual(recoveredForRecord({...old,[field]:'different'},index),[])
  }
})
test('exact product preferred; renamed products get route history without changing current product', () => {
  const first={...base,visa_type_name:'Single'},second={...base,visa_type_name:'Multiple'}
  const index=indexRecoveredRecords([first,second])
  assert.deepEqual(recoveredForRecord(second,index),[second])
  assert.deepEqual(recoveredForRecord({...base,visa_type_name:'Renamed'},index),[first,second])
})
