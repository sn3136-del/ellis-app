// The one note that is open at a time, kept by registration rather than by
// a pointer that outlives the panel it named.
//
// A note cell registers its closer when it opens and gets back the
// unregister for that registration. The cell runs that unregister on
// every close path it has (its own button, a click elsewhere, Escape,
// scroll, resize, unmount), so nothing here can point at a closed cell.
// The earlier design kept a module-level closer that only the button's own
// click cleared: a panel dismissed any other way left its closer behind,
// and the next click on the same button ran that closer on itself and
// never reopened.
let current = null

export function registerOpenNote(close) {
  const previous = current
  const entry = { close }
  // The new entry is in place before the old note is told to close, so the
  // old cell's unregister (which runs from its close) cannot clear it.
  current = entry
  if (previous && previous.close !== close) previous.close()
  return () => { if (current === entry) current = null }
}

export function closeOpenNote() {
  const entry = current
  current = null
  if (entry) entry.close()
}

export function hasOpenNote() {
  return current !== null
}
