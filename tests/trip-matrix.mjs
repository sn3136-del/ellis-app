// Scenario matrix test: runs the Trip.com routing engine across every
// nationality x destination pair (800+ scenarios, incl. hard consular cases)
// and validates the structured plan invariants the agentic pipeline relies on.
import { tripPlan } from '../src/main/tripEngine.js'

const NATIONALITIES = ['China', 'India', 'Vietnam', 'United States', 'Canada', 'United Kingdom', 'Japan', 'South Korea', 'Singapore', 'Australia', 'New Zealand', 'France', 'Germany', 'Italy', 'Spain', 'Brazil', 'Mexico', 'Argentina', 'Nigeria', 'Egypt', 'Pakistan', 'Bangladesh', 'Philippines', 'Indonesia', 'Thailand', 'Turkey', 'Russia', 'Ukraine', 'Iran', 'Ethiopia']
const DESTS = ['USA', 'Canada', 'United Kingdom', 'France', 'Germany', 'Italy', 'Spain', 'Netherlands', 'Switzerland', 'Greece', 'Portugal', 'Japan', 'South Korea', 'Thailand', 'Vietnam', 'Singapore', 'Indonesia', 'United Arab Emirates', 'Turkey', 'Australia', 'New Zealand', 'India', 'China', 'Mexico', 'Brazil', 'Argentina', 'Egypt']

const KINDS = ['free', 'eta', 'evisa', 'voa', 'embassy']
let pass = 0, fail = 0
const bad = []

for (const nat of NATIONALITIES) {
  for (const dest of DESTS) {
    const p = await tripPlan({ traveler: { name: 'Test Traveler', nationality: nat, destination: dest, departure: '2026-10-01', return: '2026-10-15' } })
    const errs = []
    if (!KINDS.includes(p.kind)) errs.push('bad kind ' + p.kind)
    if (!p.classification || !p.headline) errs.push('missing classification/headline')
    if (!Array.isArray(p.requirements) || p.requirements.length < 3) errs.push('requirements too thin')
    const expectedSteps = !p.needsFiling ? 4 : p.kind === 'embassy' ? 8 : 7
    if (!Array.isArray(p.steps) || p.steps.length !== expectedSteps) errs.push('bad steps')
    if (p.kind === 'embassy' && !p.steps.some((s) => /appointment/i.test(s))) errs.push('embassy missing appointment step')
    if (p.needsFiling !== (p.kind !== 'free')) errs.push('needsFiling inconsistent')
    if (!p.brief || !p.brief.includes(nat) || !p.brief.includes(dest)) errs.push('brief missing route')
    if (typeof p.fee !== 'string' || typeof p.processing !== 'string') errs.push('fee/processing missing')
    if (errs.length) { fail++; bad.push(`${nat} -> ${dest}: ${errs.join('; ')}`) } else pass++
  }
}

// Hard-case spot checks: the classifications the demo depends on.
const expect = async (nat, dest, kind, label) => {
  const p = await tripPlan({ traveler: { nationality: nat, destination: dest } })
  const okc = p.kind === kind
  okc ? pass++ : (fail++, bad.push(`${label}: expected ${kind}, got ${p.kind} (${p.headline})`))
  console.log(okc ? '  PASS' : '  FAIL', label, '->', p.classification)
}
await expect('China', 'USA', 'embassy', 'China->USA consular B-2 (hard case)')
await expect('Nigeria', 'France', 'embassy', 'Nigeria->Schengen consular (hard case)')
await expect('Iran', 'USA', 'embassy', 'Iran->USA consular (hard case)')
await expect('India', 'Canada', 'evisa', 'India->Canada TRV online')
await expect('China', 'Thailand', 'free', 'China->Thailand visa-free')
await expect('United States', 'France', 'free', 'US->Schengen visa-free + ETIAS')
await expect('United Kingdom', 'USA', 'eta', 'UK->USA ESTA')
await expect('Vietnam', 'United Arab Emirates', 'evisa', 'Vietnam->UAE eVisa')
await expect('Pakistan', 'Indonesia', 'voa', 'Pakistan->Indonesia VOA')
await expect('Russia', 'Vietnam', 'evisa', 'Russia->Vietnam eVisa')

console.log(`\nMatrix: ${NATIONALITIES.length * DESTS.length} route scenarios + 10 hard-case checks`)
console.log(`${pass} passed, ${fail} failed`)
if (bad.length) console.log(bad.slice(0, 20).join('\n'))
process.exit(fail ? 1 : 0)
