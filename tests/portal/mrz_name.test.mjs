// Passport-name OCR normalization regression (letters-only names): OCR misreads
// a letter as a digit inside a name zone (O→0, I→1, S→5, B→8); names must be
// normalized back and must never trigger a false MRZ/visual mismatch.
import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  normalizeName, namesAgree, parseMrz, refineMrzWithVisualZone
} from '../../src/main/mrz.js'

// Given name NOEMI misread as N0EMI (zero for O); surname ELIAS.
const MRZ_N0EMI = (
  'P<UTOELIAS<<N0EMI<<<<<<<<<<<<<<<<<<<<<<<<<<<\n' +
  'L898902C36UTO7408122F1204159ZE184226B<<<<<10'
)

test('normalizeName maps digit-for-letter OCR confusions to a letters-only name', () => {
  assert.equal(normalizeName('N0EMI ELIAS'), 'NOEMI ELIAS')
  assert.equal(normalizeName('N0EM1'), 'NOEMI')      // 0→O, 1→I
  assert.equal(normalizeName('5TEPHEN'), 'STEPHEN')  // 5→S
  assert.equal(normalizeName('8LAKE'), 'BLAKE')      // 8→B
  // Filler chevrons and stray glyphs collapse away; result is letters only.
  assert.equal(normalizeName('EL<IAS<<'), 'EL IAS')
  assert.match(normalizeName('N0EMI ELIAS'), /^[A-Z '-]+$/)
})

test('parseMrz stores a passport name with letters only, never a digit', () => {
  const f = parseMrz(MRZ_N0EMI)
  assert.ok(f, 'MRZ should parse')
  assert.equal(f.givenNames, 'NOEMI')
  assert.equal(f.surname, 'ELIAS')
  assert.equal(f.fullName, 'NOEMI ELIAS')
  assert.doesNotMatch(f.fullName, /[0-9]/)
})

test('N0EMI ELIAS never triggers a false name mismatch', () => {
  // The exact regression: the digit read and the correct read must agree.
  assert.equal(namesAgree('N0EMI ELIAS', 'NOEMI ELIAS'), true)
  assert.equal(namesAgree('NOEMI ELIAS', 'N0EMI ELIAS'), true)
  // Order-independent and tolerant of a single genuine slip.
  assert.equal(namesAgree('ELIAS NOEMI', 'NOEMI ELIAS'), true)
})

test('visual zone agreeing with the MRZ (after normalization) confirms, no prompt', () => {
  const text = 'Surname: ELIAS\nGiven names: NOEMI\nPassport No: L898902C3'
  const f = refineMrzWithVisualZone(parseMrz(MRZ_N0EMI), text)
  assert.equal(f.fullName, 'NOEMI ELIAS')      // MRZ value preferred, letters only
  assert.equal(f.visualZoneVerified, true)
  assert.ok(!f.nameNeedsConfirmation)
})

test('a genuine visual/MRZ name disagreement flags for user confirmation', () => {
  const text = 'Surname: ELIAS\nGiven names: MARIA\nPassport No: L898902C3'
  const f = refineMrzWithVisualZone(parseMrz(MRZ_N0EMI), text)
  // The authoritative MRZ value is kept; the applicant must confirm.
  assert.equal(f.fullName, 'NOEMI ELIAS')
  assert.equal(f.nameNeedsConfirmation, true)
  assert.equal(f.visualName, 'MARIA ELIAS')
  assert.equal(f.mrzName, 'NOEMI ELIAS')
})

test('a missing MRZ name falls back to the visual zone', () => {
  const f = refineMrzWithVisualZone({ surname: '', givenNames: '', fullName: '' },
    'Surname: ELIAS\nGiven names: NOEMI')
  assert.equal(f.surname, 'ELIAS')
  assert.equal(f.givenNames, 'NOEMI')
  assert.equal(f.visualZoneVerified, true)
})
