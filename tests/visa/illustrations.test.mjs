// Showcase test for the illustration library (2026-08-13 redesign).
//
// No node test in this repo renders JSX (plain `node --test`, no transpiler),
// so this follows the source-assertion pattern: every advertised component is
// exported, draws its own <svg>, honors the { size, className } contract, and
// leans on the theme.css motion system — which itself must define the shared
// keyframes and exactly one prefers-reduced-motion block.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const illoSrc = await readFile(
  new URL('../../src/renderer/src/components/visa/Illustrations.jsx', import.meta.url), 'utf8')
const themeSrc = await readFile(
  new URL('../../src/renderer/src/theme.css', import.meta.url), 'utf8')

const COMPONENTS = [
  'DocumentsIllustration',
  'PassportIllustration',
  'FormFillIllustration',
  'WageIllustration',
  'AppointmentIllustration',
  'PipelineIllustration',
  'EnvelopeIllustration',
  'ShieldIllustration'
]

// Slice each component's body: from its export to the next export (or EOF).
function componentBody(name) {
  const m = illoSrc.match(new RegExp(`export function ${name}\\s*\\(`))
  assert.ok(m, `${name} is not exported from Illustrations.jsx`)
  const start = illoSrc.indexOf(m[0])
  const next = illoSrc.indexOf('export function', start + 1)
  return illoSrc.slice(start, next === -1 ? illoSrc.length : next)
}

for (const name of COMPONENTS) {
  test(`${name} exists, renders an <svg, and honors { size, className }`, () => {
    const body = componentBody(name)
    assert.match(body, /<svg/, `${name} must draw its own <svg>`)
    assert.match(body, /size\s*=\s*\d+/, `${name} must default its size prop`)
    assert.match(body, /className\s*=\s*''/, `${name} must accept className`)
    // Theming: no illustration may hardcode its palette without the vars.
    assert.match(body, /NAVY|BLUE|GRAY|ORANGE|GREEN/, `${name} must use the shared palette`)
  })
}

test('the palette reads CSS variables so a themed parent re-tints artwork', () => {
  assert.match(illoSrc, /var\(--trip-blue/)
  assert.match(illoSrc, /var\(--trip-navy/)
})

test('theme.css defines the motion system the illustrations depend on', () => {
  for (const kf of ['fadeRise', 'floatSoft', 'drawLine', 'stampDrop', 'pulseSoft', 'nodeLight']) {
    assert.match(themeSrc, new RegExp(`@keyframes ${kf}\\b`), `missing @keyframes ${kf}`)
  }
  for (const cls of ['.anim-rise', '.anim-float', '.card-hover', '.chip-icon', '.section-gap',
    '.illo-rise', '.illo-draw', '.illo-stamp', '.illo-pulse', '.illo-node']) {
    assert.ok(themeSrc.includes(cls), `missing utility class ${cls}`)
  }
})

test('theme.css has exactly ONE prefers-reduced-motion block, and it kills the motion', () => {
  const blocks = themeSrc.match(/@media\s*\(prefers-reduced-motion: reduce\)/g) || []
  assert.equal(blocks.length, 1, 'the reduced-motion story must live in one block')
  const at = themeSrc.indexOf('@media (prefers-reduced-motion: reduce)')
  const block = themeSrc.slice(at, themeSrc.indexOf('}', themeSrc.lastIndexOf('animation: none', themeSrc.length)))
  assert.match(themeSrc.slice(at), /animation: none !important/)
  assert.match(themeSrc.slice(at), /stroke-dashoffset: 0/)
})

test('drawn strokes normalize with pathLength so .illo-draw fits every shape', () => {
  assert.match(illoSrc, /pathLength="100"/)
  assert.match(themeSrc, /stroke-dasharray: 100; stroke-dashoffset: 100/)
})
