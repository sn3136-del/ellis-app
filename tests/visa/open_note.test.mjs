import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { closeOpenNote, hasOpenNote, registerOpenNote } from '../../src/renderer/src/lib/openNote.js'

// The registry behind NoteCell: one note is open at a time, and a closed
// note leaves nothing behind. The cell registers on open and runs the
// returned unregister on every close path (own button, click elsewhere,
// Escape, scroll, resize, unmount).

function cell() {
  const c = { closed: 0, unregister: null }
  c.open = () => { c.unregister = registerOpenNote(() => { c.closed += 1; c.unregister?.(); c.unregister = null }) }
  c.dismiss = () => { c.unregister?.(); c.unregister = null }  // any close path that is not the registry
  return c
}

test('a cell dismissed by another path reopens without closing itself', () => {
  // The finding: a panel dismissed by click-away, Escape, scroll or resize
  // left a stale closer behind, and the next click on the same button ran
  // that closer and never reopened. With registration the dismissal
  // unregisters, so reopening calls nothing.
  const a = cell()
  for (const path of ['click-away', 'Escape', 'scroll', 'resize']) {
    a.open()
    assert.ok(hasOpenNote(), path)
    a.dismiss()
    assert.ok(!hasOpenNote(), path)
    a.open()
    assert.equal(a.closed, 0, `reopening after ${path} must not close the cell`)
    assert.ok(hasOpenNote(), path)
    a.dismiss()
  }
})

test('opening a second cell closes the first, once', () => {
  const a = cell(), b = cell()
  a.open(); b.open()
  assert.equal(a.closed, 1)
  assert.equal(b.closed, 0)
  assert.ok(hasOpenNote())
  closeOpenNote()
  assert.equal(b.closed, 1)
  assert.equal(a.closed, 1)
  assert.ok(!hasOpenNote())
})

test('a replaced cell that unregisters late cannot clear the newer registration', () => {
  const a = cell(), b = cell()
  a.open()
  const lateUnregisterA = a.unregister
  b.open()
  lateUnregisterA()
  assert.ok(hasOpenNote(), 'b is still the open note')
  closeOpenNote()
  assert.equal(b.closed, 1)
})

test('closing with nothing open is a no-op, and a close runs a closer once', () => {
  closeOpenNote()
  assert.ok(!hasOpenNote())
  const a = cell()
  a.open()
  closeOpenNote(); closeOpenNote()
  assert.equal(a.closed, 1)
})

test('NoteCell registers through the shared registry, not a module-level pointer', () => {
  const source = readFileSync(new URL('../../src/renderer/src/screens/QualityConsole.jsx', import.meta.url), 'utf8')
  assert.ok(source.includes("import { registerOpenNote } from '../lib/openNote.js'"))
  assert.ok(source.includes('return registerOpenNote(() => setOpen(false))'))
  assert.ok(!/let closeOpenNote/.test(source))
  // The button is a measured property; no character count stands in for it.
  assert.ok(!/value\.length > 30/.test(source))
})
