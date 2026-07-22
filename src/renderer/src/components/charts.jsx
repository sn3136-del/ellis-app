import { useState, useEffect } from 'react'
import { Icon } from './icons.jsx'

/* Shared animated chart primitives (monochrome, subtle, professional). */

// Count a number up from 0 on mount.
export function useCountUp(target, ms = 900) {
  const [n, setN] = useState(0)
  useEffect(() => {
    let raf, t0
    const step = (t) => {
      if (!t0) t0 = t
      const p = Math.min(1, (t - t0) / ms)
      setN(Math.round(target * (1 - Math.pow(1 - p, 3)))) // ease-out cubic
      if (p < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [target, ms])
  return n
}

// Flip to true one frame after mount so CSS transitions animate in.
export function useMounted() {
  const [m, setM] = useState(false)
  useEffect(() => { const id = requestAnimationFrame(() => setM(true)); return () => cancelAnimationFrame(id) }, [])
  return m
}

export function AnimatedDonut({ value, size = 130, sublabel = 'out of 100' }) {
  const mounted = useMounted()
  const shown = useCountUp(value, 1100)
  const r = (size - 18) / 2
  const cx = size / 2, circ = 2 * Math.PI * r
  const dash = (mounted ? value / 100 : 0) * circ
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`score ${value}`}>
      <circle cx={cx} cy={cx} r={r} fill="none" stroke="rgba(10,10,10,0.07)" strokeWidth="11" />
      <circle cx={cx} cy={cx} r={r} fill="none" stroke="#0a0a0a" strokeWidth="11" strokeLinecap="round"
        strokeDasharray={`${dash} ${circ}`} transform={`rotate(-90 ${cx} ${cx})`}
        style={{ transition: 'stroke-dasharray 1.1s cubic-bezier(.22,1,.36,1)' }} />
      <text x={cx} y={cx - 1} textAnchor="middle" fontSize={size / 4.3} fontWeight="800" fill="#0a0a0a">{shown}</text>
      <text x={cx} y={cx + size / 7.2} textAnchor="middle" fontSize="10.5" fill="#9a9a9a">{sublabel}</text>
    </svg>
  )
}

// Simple animated horizontal bar (0-100).
export function AnimatedBar({ value, height = 8, delay = 0, striped = false }) {
  const mounted = useMounted()
  return (
    <div style={{ height, background: 'rgba(10,10,10,0.06)', borderRadius: 999, overflow: 'hidden' }}>
      <div style={{
        width: mounted ? `${Math.max(0, Math.min(100, value))}%` : '0%', height: '100%', borderRadius: 999,
        background: striped ? 'repeating-linear-gradient(45deg,#9a9a9a,#9a9a9a 5px,#c4c4c4 5px,#c4c4c4 10px)' : '#0a0a0a',
        transition: `width 1s cubic-bezier(.22,1,.36,1) ${delay}ms`
      }} />
    </div>
  )
}

/* Case lifecycle stage helpers + animated stage progression bar. */
export const STAGE_STEPS = ['Onboarding', 'Evidence', 'Filing', 'Decision']

export function stageIndex(stage) {
  const s = (stage || '').toLowerCase()
  if (/decision|approved|rfe|review/.test(s)) return 3
  if (/filing|filed|submit/.test(s)) return 2
  if (/evidence|document|prep/.test(s)) return 1
  return 0
}

// Blend lifecycle stage (70%) with task completion (30%) into one percentage.
export function caseProgress(c) {
  const tasks = c.tasks || []
  const done = tasks.filter((t) => t.status === 'done').length
  const idx = stageIndex(c.facts?.stage)
  return Math.min(100, Math.round((idx / (STAGE_STEPS.length - 1)) * 70 + (tasks.length ? (done / tasks.length) * 30 : 0)))
}

export function StageBar({ c }) {
  const mounted = useMounted()
  const idx = stageIndex(c.facts?.stage)
  return (
    <div style={{ display: 'flex', alignItems: 'center' }}>
      {STAGE_STEPS.map((s, i) => (
        <div key={s} style={{ display: 'flex', alignItems: 'center', flex: i < STAGE_STEPS.length - 1 ? 1 : 'none' }}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 5 }}>
            <div style={{ width: 24, height: 24, borderRadius: '50%', display: 'grid', placeItems: 'center', background: i <= idx ? 'var(--ink)' : '#fff', border: i <= idx ? 'none' : '2px solid var(--line-strong)', color: '#fff', transition: `background .5s ${i * 150}ms` }}>
              {i < idx ? <Icon.check style={{ width: 12, height: 12 }} /> : i === idx ? <span style={{ width: 7, height: 7, borderRadius: '50%', background: '#fff' }} /> : null}
            </div>
            <span style={{ fontSize: 11, fontWeight: i === idx ? 700 : 400, color: i <= idx ? 'var(--ink)' : 'var(--muted-2)', whiteSpace: 'nowrap' }}>{s}</span>
          </div>
          {i < STAGE_STEPS.length - 1 && (
            <div style={{ flex: 1, height: 2, background: 'rgba(10,10,10,0.08)', margin: '0 8px 18px', borderRadius: 2, overflow: 'hidden' }}>
              <div style={{ width: mounted && i < idx ? '100%' : '0%', height: '100%', background: 'var(--ink)', transition: `width .6s cubic-bezier(.22,1,.36,1) ${200 + i * 200}ms` }} />
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
