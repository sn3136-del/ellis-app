import { useState, useEffect } from 'react'

export default function Tour({ steps, onClose }) {
  const [i, setI] = useState(0)
  const [rect, setRect] = useState(null)
  const step = steps[i]
  const PAD = 8

  // Run the step action, then locate the spotlight target.
  useEffect(() => {
    let cancelled = false
    setRect(null)
    ;(async () => {
      if (step.before) { try { await step.before() } catch { /* noop */ } }
      if (!step.target) return
      const start = Date.now()
      const tick = () => {
        if (cancelled) return
        const el = document.querySelector(`[data-tour="${step.target}"]`)
        if (el) {
          try { el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' }) } catch { /* noop */ }
          setTimeout(() => {
            if (cancelled) return
            const r = el.getBoundingClientRect()
            setRect({ top: r.top, left: r.left, width: r.width, height: r.height })
          }, 220)
        } else if (Date.now() - start < 3000) {
          setTimeout(tick, 90)
        }
      }
      tick()
    })()
    return () => { cancelled = true }
  }, [i])

  // Keep the spotlight aligned if the layout shifts.
  useEffect(() => {
    if (!step?.target) return
    const upd = () => {
      const el = document.querySelector(`[data-tour="${step.target}"]`)
      if (el) { const r = el.getBoundingClientRect(); setRect({ top: r.top, left: r.left, width: r.width, height: r.height }) }
    }
    const iv = setInterval(upd, 400)
    window.addEventListener('resize', upd)
    return () => { clearInterval(iv); window.removeEventListener('resize', upd) }
  }, [i])

  const last = i === steps.length - 1
  const next = () => (last ? onClose() : setI(i + 1))
  const back = () => i > 0 && setI(i - 1)

  // Keyboard: space / enter / → advance, ← / backspace go back, esc closes.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === ' ' || e.key === 'Spacebar' || e.key === 'Enter' || e.key === 'ArrowRight') {
        e.preventDefault(); next()
      } else if (e.key === 'ArrowLeft' || e.key === 'Backspace') {
        e.preventDefault(); back()
      } else if (e.key === 'Escape') {
        e.preventDefault(); onClose()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [i, last])

  const vw = window.innerWidth, vh = window.innerHeight
  const BW = 320

  // Bubble + arrow placement.
  let bubble, arrow = null
  if (!rect) {
    bubble = { left: vw / 2 - BW / 2, top: vh / 2 - 90 }
  } else {
    const place = step.placement || (rect.top > vh / 2 ? 'top' : 'bottom')
    if (place === 'bottom') {
      bubble = { left: clamp(rect.left + rect.width / 2 - BW / 2, 12, vw - BW - 12), top: rect.top + rect.height + 18 }
      arrow = { cls: 'up', left: rect.left + rect.width / 2 - 9, top: rect.top + rect.height + 7 }
    } else if (place === 'top') {
      bubble = { left: clamp(rect.left + rect.width / 2 - BW / 2, 12, vw - BW - 12), top: rect.top - 18, transform: 'translateY(-100%)' }
      arrow = { cls: 'down', left: rect.left + rect.width / 2 - 9, top: rect.top - 18 }
    } else if (place === 'right') {
      bubble = { left: rect.left + rect.width + 18, top: clamp(rect.top + rect.height / 2 - 70, 12, vh - 180) }
      arrow = { cls: 'left', left: rect.left + rect.width + 7, top: rect.top + rect.height / 2 - 9 }
    } else {
      bubble = { left: rect.left - BW - 18, top: clamp(rect.top + rect.height / 2 - 70, 12, vh - 180) }
      arrow = { cls: 'right', left: rect.left - 18 - 2, top: rect.top + rect.height / 2 - 9 }
    }
  }

  return (
    <>
      <div className="tour-block" onClick={(e) => e.stopPropagation()} />
      <svg className="tour-svg" width={vw} height={vh}>
        <defs>
          <mask id="tourmask">
            <rect x="0" y="0" width={vw} height={vh} fill="white" />
            {rect && <rect x={rect.left - PAD} y={rect.top - PAD} width={rect.width + PAD * 2} height={rect.height + PAD * 2} rx="12" fill="black" />}
          </mask>
        </defs>
        <rect x="0" y="0" width={vw} height={vh} fill="rgba(10,10,10,0.66)" mask="url(#tourmask)" />
      </svg>

      {rect && <div className="tour-ring" style={{ top: rect.top - PAD, left: rect.left - PAD, width: rect.width + PAD * 2, height: rect.height + PAD * 2 }} />}
      {arrow && <div className={`tour-arrow tour-arrow--${arrow.cls}`} style={{ left: arrow.left, top: arrow.top }} />}

      <div className="tour-bubble" style={{ left: bubble.left, top: bubble.top, width: BW, transform: bubble.transform }}>
        <div className="tour-bubble__step">Step {i + 1} of {steps.length}</div>
        <h4>{step.title}</h4>
        <p>{step.body}</p>
        <div className="tour-bubble__foot">
          <div className="tour-bubble__dots">{steps.map((_, k) => <i key={k} className={k === i ? 'on' : ''} />)}</div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <span style={{ fontSize: 11, color: 'var(--muted-2)' }}>press space</span>
            {i > 0 && <button className="btn btn--ghost btn--sm" onClick={back}>Back</button>}
            <button className="btn btn--sm" onClick={next}>{last ? 'Finish' : 'Next'}</button>
          </div>
        </div>
        <button onClick={onClose} style={{ position: 'absolute', top: 12, right: 14, background: 'none', border: 0, color: 'var(--muted-2)', fontSize: 13 }}>Skip</button>
      </div>
    </>
  )
}

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)) }
