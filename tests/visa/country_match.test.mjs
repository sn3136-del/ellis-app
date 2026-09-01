// The class of bug Trip.com's 2026-08-31 evaluation caught: a country
// filter that takes the FIRST substring match resolves "KOR" to North Korea
// (whose formal name contains it and sorts first) and "IND" to the British
// Indian Ocean Territory. These tests pin the tiered resolver to the real
// registry so no code or name can ever resolve to a different country.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { matchCountry, matchCountryStrict } from '../../src/renderer/src/lib/countryMatch.js'
import { countryRows } from '../../src/renderer/src/lib/countryNames.js'

const reg = JSON.parse(readFileSync(new URL('../../data/reference/countries.json', import.meta.url)))
const rows = countryRows(reg.entries)
const value = (q) => matchCountry(rows, q)?.country?.value

test('the two Koreas resolve by exactness, not list order', () => {
  assert.equal(value('KOR'), 'KOR')
  assert.equal(value('kor'), 'KOR')
  assert.equal(value('KR'), 'KOR')
  assert.equal(value('South Korea'), 'KOR')
  assert.equal(value('korea'), 'KOR')
  assert.equal(value('韩国'), 'KOR')
  assert.equal(value('KP'), 'PRK')
  assert.equal(value('PRK'), 'PRK')
  assert.equal(value('north korea'), 'PRK')
  assert.equal(value('北朝鲜'), 'PRK')
})

test('India beats the British Indian Ocean Territory', () => {
  assert.equal(value('IND'), 'IND')
  assert.equal(value('India'), 'IND')
  assert.equal(value('IN'), 'IND')
  assert.equal(value('印度'), 'IND')
})

test('every alpha-3 code round-trips to its own country', () => {
  for (const c of reg.entries) {
    assert.equal(value(c.alpha_3), c.alpha_3,
      `alpha-3 ${c.alpha_3} resolved to ${value(c.alpha_3)}`)
  }
})

test('every alpha-2 code round-trips to its own country', () => {
  for (const c of reg.entries) {
    if (!c.alpha_2) continue
    assert.equal(value(c.alpha_2), c.alpha_3,
      `alpha-2 ${c.alpha_2} resolved to ${value(c.alpha_2)}`)
  }
})

test('every registry name round-trips to its own country', () => {
  for (const c of reg.entries) {
    assert.equal(value(c.name), c.alpha_3,
      `name ${c.name} resolved to ${value(c.name)}`)
  }
})

test('strict matcher refuses ambiguous fragments but commits exact ones', () => {
  assert.equal(matchCountryStrict(rows, 'KOR')?.value, 'KOR')
  assert.equal(matchCountryStrict(rows, 'KR')?.value, 'KOR')
  assert.equal(matchCountryStrict(rows, 'India')?.value, 'IND')
  assert.equal(matchCountryStrict(rows, 'korea'), null)
  assert.equal(matchCountryStrict(rows, 'united'), null)
})
