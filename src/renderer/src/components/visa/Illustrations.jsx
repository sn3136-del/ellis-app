// Illustrations.jsx — the Ellis illustration library (2026-08-13 redesign).
//
// One accent illustration per section instead of paragraphs. Every drawing
// follows the house style set by the TripPlane loader in components/ui.jsx:
// white fills, navy outlines (~2.2–2.4 stroke), Trip.com blue accents, one
// orange detail, flat and friendly. All motion comes from theme.css classes
// (illo-rise, illo-float, illo-draw, illo-stamp, illo-pulse, illo-node) so
// the single prefers-reduced-motion block there silences everything at once.
//
// Contract: every component takes { size, className }; `size` is the CSS
// width — height follows the viewBox ratio. Colors read the palette vars
// (with hex fallbacks) plus currentColor, so a tinted parent re-themes the
// artwork. Drawn strokes carry pathLength="100" to match .illo-draw's
// dash length. Nobody outside this file draws their own artwork.

import { tripcomLogo } from '../../assets/logos.js'

const NAVY = 'var(--trip-navy, #0f294d)'
const BLUE = 'var(--trip-blue, #2681ff)'
const BLUE_SOFT = '#eef4ff'
const BLUE_LINE = '#c3d6f2'
const GRAY = 'var(--trip-gray, #8592a6)'
const ORANGE = 'var(--trip-orange, #fcaf17)'
const GREEN = '#1d9e55'
const GREEN_SOFT = '#e8f8ee'

// ---------------------------------------------------------------------------
// DocumentsIllustration — papers stack up one by one, then a check appears.
export function DocumentsIllustration({ size = 120, className = '' }) {
  return (
    <svg width={size} viewBox="0 0 160 120" fill="none" className={`illo ${className}`} aria-hidden="true">
      {/* back sheet, tilted left */}
      <g className="illo-part illo-rise">
        <rect x="34" y="24" width="62" height="82" rx="7" fill={BLUE_SOFT} stroke={NAVY} strokeWidth="2.2"
              transform="rotate(-7 65 65)" />
      </g>
      {/* middle sheet, tilted right */}
      <g className="illo-part illo-rise illo-rise--1">
        <rect x="44" y="20" width="62" height="84" rx="7" fill="#ffffff" stroke={NAVY} strokeWidth="2.2"
              transform="rotate(5 75 62)" />
      </g>
      {/* front sheet with its text lines — the real Trip.com wordmark is the
          letterhead, exactly where a branded document carries it */}
      <g className="illo-part illo-rise illo-rise--2">
        <rect x="50" y="16" width="64" height="88" rx="7" fill="#ffffff" stroke={NAVY} strokeWidth="2.4" />
        <image href={tripcomLogo} x="55" y="22" width="54" height="13" />
        <rect x="60" y="44" width="44" height="4" rx="2" fill={GRAY} opacity="0.5" />
        <rect x="60" y="53" width="44" height="4" rx="2" fill={GRAY} opacity="0.5" />
        <rect x="60" y="64" width="32" height="4" rx="2" fill={GRAY} opacity="0.5" />
        <rect x="60" y="82" width="18" height="5" rx="2.5" fill={ORANGE} />
      </g>
      {/* the check appears once the stack settles */}
      <g className="illo-part illo-rise illo-rise--3">
        <circle cx="122" cy="90" r="17" fill={GREEN_SOFT} stroke={GREEN} strokeWidth="2.4" />
        <path d="M114 90.5 l5.5 5.5 L131 84" pathLength="100" className="illo-draw illo-draw--3"
              stroke={GREEN} strokeWidth="3.4" strokeLinecap="round" strokeLinejoin="round" />
      </g>
    </svg>
  )
}

// ---------------------------------------------------------------------------
// PassportIllustration — a passport cover; the entry stamp thunks down onto
// its corner every four seconds.
export function PassportIllustration({ size = 120, className = '' }) {
  return (
    <svg width={size} viewBox="0 0 160 120" fill="none" className={`illo ${className}`} aria-hidden="true">
      {/* cover */}
      <g className="illo-part illo-rise">
        <rect x="42" y="16" width="68" height="92" rx="9" fill={BLUE} stroke={NAVY} strokeWidth="2.4" />
        {/* globe emblem */}
        <circle cx="76" cy="52" r="15" stroke="#ffffff" strokeWidth="2" />
        <ellipse cx="76" cy="52" rx="7" ry="15" stroke="#ffffff" strokeWidth="1.6" />
        <path d="M61 52 h30 M63.5 44.5 h25 M63.5 59.5 h25" stroke="#ffffff" strokeWidth="1.6" strokeLinecap="round" />
        {/* the Trip.com brand plate (the blue logo needs a white ground on the
            blue cover) + biometric chip mark */}
        <rect x="48" y="19" width="56" height="16" rx="4.5" fill="#ffffff" opacity="0.97" />
        <image href={tripcomLogo} x="51" y="21.5" width="50" height="12.1" />
        <rect x="68" y="86" width="16" height="9" rx="2.5" stroke="#ffffff" strokeWidth="1.6" />
        <path d="M72 86 v9 M80 86 v9" stroke="#ffffff" strokeWidth="1.2" opacity="0.8" />
      </g>
      {/* the stamp — drops in, squashes, settles, re-stamps on loop */}
      <g className="illo-part illo-stamp">
        <g transform="rotate(-14 118 40)">
          <circle cx="118" cy="40" r="19" fill="#ffffff" stroke={ORANGE} strokeWidth="3" opacity="0.97" />
          <circle cx="118" cy="40" r="13" stroke={ORANGE} strokeWidth="1.6" strokeDasharray="3 3.4" />
          <path d="M112.5 40.5 l4 4 L125 35.5" stroke={ORANGE} strokeWidth="2.6"
                strokeLinecap="round" strokeLinejoin="round" />
        </g>
      </g>
      {/* landing dust */}
      <g fill={GRAY} opacity="0.45">
        <circle cx="94" cy="62" r="1.8" />
        <circle cx="140" cy="58" r="1.8" />
        <circle cx="136" cy="20" r="1.8" />
      </g>
    </svg>
  )
}

// ---------------------------------------------------------------------------
// FormFillIllustration — the form's lines draw themselves while a pen floats
// alongside; the checkbox ticks itself last.
export function FormFillIllustration({ size = 120, className = '' }) {
  return (
    <svg width={size} viewBox="-8 0 160 120" fill="none" className={`illo ${className}`} aria-hidden="true">
      {/* the form — titled by the real Trip.com wordmark */}
      <g className="illo-part illo-rise">
        <rect x="28" y="14" width="82" height="94" rx="8" fill="#ffffff" stroke={NAVY} strokeWidth="2.4" />
        <image href={tripcomLogo} x="38" y="17" width="54" height="13" />
      </g>
      {/* field labels (static) + values (drawing in) */}
      <g stroke={GRAY} strokeWidth="3" strokeLinecap="round" opacity="0.45">
        <path d="M38 44 h14" />
        <path d="M38 62 h14" />
        <path d="M38 80 h14" />
      </g>
      <g stroke={BLUE} strokeWidth="3.4" strokeLinecap="round">
        <path d="M38 51 h58" pathLength="100" className="illo-draw" />
        <path d="M38 69 h44" pathLength="100" className="illo-draw illo-draw--1" />
        <path d="M38 87 h50" pathLength="100" className="illo-draw illo-draw--2" />
      </g>
      {/* checkbox that ticks itself once the lines are in */}
      <rect x="88" y="96" width="13" height="13" rx="3.5" fill={GREEN_SOFT} stroke={GREEN} strokeWidth="2" />
      <path d="M91 102.5 l3 3 L99 99" pathLength="100" className="illo-draw illo-draw--4"
            stroke={GREEN} strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
      {/* the pen, floating gently, nib toward the current line */}
      <g className="illo-part illo-float">
        <g transform="rotate(40 122 62)">
          <rect x="114" y="30" width="15" height="46" rx="6" fill={BLUE} stroke={NAVY} strokeWidth="2.2" />
          <rect x="114" y="30" width="15" height="10" rx="5" fill={ORANGE} stroke={NAVY} strokeWidth="2.2" />
          <path d="M116.5 76 L121.5 90 L126.5 76 Z" fill="#ffffff" stroke={NAVY} strokeWidth="2.2" strokeLinejoin="round" />
          <path d="M121.5 82 v8" stroke={NAVY} strokeWidth="2" strokeLinecap="round" />
        </g>
      </g>
    </svg>
  )
}

// ---------------------------------------------------------------------------
// WageIllustration — the wage meter: bars rise in sequence, a trend arrow
// draws over them, the coin at the top glints.
export function WageIllustration({ size = 120, className = '' }) {
  return (
    <svg width={size} viewBox="0 0 160 120" fill="none" className={`illo ${className}`} aria-hidden="true">
      {/* baseline */}
      <path d="M30 102 h100" stroke={NAVY} strokeWidth="2.4" strokeLinecap="round" />
      {/* bars rise (drawn as fat round strokes, bottom to top) */}
      <g strokeWidth="16" strokeLinecap="round">
        <path d="M52 94 V78" pathLength="100" className="illo-draw" stroke={BLUE_LINE} />
        <path d="M80 94 V60" pathLength="100" className="illo-draw illo-draw--1" stroke={BLUE} opacity="0.55" />
        <path d="M108 94 V42" pathLength="100" className="illo-draw illo-draw--2" stroke={BLUE} />
      </g>
      {/* trend arrow drawing across the top */}
      <path d="M40 76 C 62 70, 78 58, 100 40 L 116 27" pathLength="100"
            className="illo-draw illo-draw--3" stroke={NAVY} strokeWidth="2.6" strokeLinecap="round" />
      <path d="M107 25.5 l9.5 1 -1 9.5" pathLength="100" className="illo-draw illo-draw--4"
            stroke={NAVY} strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
      {/* the coin glints at the summit */}
      <g className="illo-part illo-pulse">
        <circle cx="132" cy="18" r="11" fill={ORANGE} stroke={NAVY} strokeWidth="2.2" />
        <path d="M132 12.5 v11 M128.5 15 h5.5 a2.6 2.6 0 1 1 0 5.4 h-5.4 a2.6 2.6 0 1 0 0 5.2 h6"
              stroke={NAVY} strokeWidth="1.7" strokeLinecap="round" fill="none"
              transform="scale(0.82) translate(29 4)" />
      </g>
    </svg>
  )
}

// ---------------------------------------------------------------------------
// AppointmentIllustration — a calendar month; today's cell glows and the
// location pin drops onto it, again and again.
export function AppointmentIllustration({ size = 120, className = '' }) {
  const dots = []
  for (let r = 0; r < 3; r += 1) {
    for (let c = 0; c < 5; c += 1) {
      if (r === 1 && c === 3) continue // the highlighted day owns this slot
      dots.push(<circle key={`${r}-${c}`} cx={46 + c * 15} cy={62 + r * 14} r="2.4" fill={GRAY} opacity="0.4" />)
    }
  }
  return (
    <svg width={size} viewBox="0 0 160 120" fill="none" className={`illo ${className}`} aria-hidden="true">
      {/* calendar body + header page */}
      <g className="illo-part illo-rise">
        <rect x="32" y="28" width="88" height="76" rx="9" fill="#ffffff" stroke={NAVY} strokeWidth="2.4" />
        <path d="M32 50 h88" stroke={NAVY} strokeWidth="2" />
        <rect x="32" y="28" width="88" height="22" rx="9" fill={BLUE} />
        <rect x="32" y="42" width="88" height="8" fill={BLUE} />
        {/* Trip.com on the calendar's header band: a white plate keeps the
            wordmark legible over the blue, matching the other three doors. */}
        <rect x="42" y="32" width="68" height="14" rx="3.5" fill="#ffffff" />
        <image href={tripcomLogo} x="45" y="33.6" width="62" height="10.8" />
        {/* binder rings */}
        <path d="M52 22 v12 M100 22 v12" stroke={NAVY} strokeWidth="3.4" strokeLinecap="round" />
      </g>
      {/* day grid */}
      <g className="illo-part illo-rise illo-rise--1">{dots}</g>
      {/* the chosen day glows */}
      <rect x="84" y="68" width="16" height="16" rx="5" fill={BLUE_SOFT} stroke={BLUE} strokeWidth="2"
            className="illo-part illo-pulse illo-rise--1" />
      {/* the pin drops onto it */}
      <g className="illo-part illo-stamp">
        <path d="M92 50 c-7.5 0 -13 5.6 -13 12.6 0 8.6 10.6 17.6 12.2 19 a1.2 1.2 0 0 0 1.6 0 c1.6 -1.4 12.2 -10.4 12.2 -19 0 -7 -5.5 -12.6 -13 -12.6 Z"
              fill={ORANGE} stroke={NAVY} strokeWidth="2.4" strokeLinejoin="round" />
        <circle cx="92" cy="62.5" r="4.6" fill="#ffffff" stroke={NAVY} strokeWidth="1.8" />
      </g>
    </svg>
  )
}

// ---------------------------------------------------------------------------
// PipelineIllustration — three stations on a dashed route light up in
// sequence: prepare, submit, done.
export function PipelineIllustration({ size = 140, className = '' }) {
  return (
    <svg width={size} viewBox="0 0 160 90" fill="none" className={`illo ${className}`} aria-hidden="true">
      {/* the route */}
      <path d="M38 48 H122" stroke={BLUE_LINE} strokeWidth="2.6" strokeLinecap="round" strokeDasharray="2 7" />
      {/* station shells (always visible) */}
      <g fill="#ffffff" stroke={NAVY} strokeWidth="2.4">
        <circle cx="34" cy="48" r="15" />
        <circle cx="80" cy="48" r="15" />
        <circle cx="126" cy="48" r="15" />
      </g>
      {/* station 1 — the document */}
      <g className="illo-part illo-node">
        <circle cx="34" cy="48" r="15" fill={BLUE} />
        <rect x="28.5" y="40.5" width="11" height="15" rx="2" stroke="#ffffff" strokeWidth="1.8" fill="none" />
        <path d="M31.5 46 h5 M31.5 50 h5" stroke="#ffffff" strokeWidth="1.6" strokeLinecap="round" />
      </g>
      {/* station 2 — the paper plane */}
      <g className="illo-part illo-node illo-node--2">
        <circle cx="80" cy="48" r="15" fill={BLUE} />
        <path d="M72.5 48.5 L88 41 l-4.5 14.5 -3.5 -5.5 Z M80 50 l8 -9" fill="none"
              stroke="#ffffff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      </g>
      {/* station 3 — the check */}
      <g className="illo-part illo-node illo-node--3">
        <circle cx="126" cy="48" r="15" fill={GREEN} />
        <path d="M119.5 48.5 l4.5 4.5 L133 44" stroke="#ffffff" strokeWidth="2.6"
              strokeLinecap="round" strokeLinejoin="round" />
      </g>
      {/* a hint of sky */}
      <g fill={GRAY} opacity="0.4">
        <circle cx="56" cy="22" r="1.8" />
        <circle cx="104" cy="18" r="1.8" />
        <circle cx="144" cy="26" r="1.8" />
      </g>
    </svg>
  )
}

// ---------------------------------------------------------------------------
// EnvelopeIllustration — the filing packet: the letter settles in, the flap
// closes over it, the seal presses down.
export function EnvelopeIllustration({ size = 120, className = '' }) {
  return (
    <svg width={size} viewBox="0 0 160 120" fill="none" className={`illo ${className}`} aria-hidden="true">
      {/* the letter, sliding down into the envelope */}
      <g className="illo-part illo-rise">
        <rect x="48" y="18" width="64" height="52" rx="6" fill="#ffffff" stroke={NAVY} strokeWidth="2.2" />
        <rect x="57" y="27" width="26" height="4.5" rx="2.25" fill={BLUE} />
        <rect x="57" y="38" width="46" height="4" rx="2" fill={GRAY} opacity="0.5" />
        <rect x="57" y="48" width="38" height="4" rx="2" fill={GRAY} opacity="0.5" />
      </g>
      {/* envelope front */}
      <g className="illo-part illo-rise illo-rise--1">
        <rect x="34" y="52" width="92" height="54" rx="8" fill={BLUE_SOFT} stroke={NAVY} strokeWidth="2.4" />
        <path d="M36 102 L70 76 M124 102 L90 76" stroke={NAVY} strokeWidth="2" strokeLinecap="round" opacity="0.55" />
      </g>
      {/* flap folding shut */}
      <g className="illo-part illo-rise illo-rise--2">
        <path d="M34 56 L80 88 L126 56" fill="none" stroke={NAVY} strokeWidth="2.4"
              strokeLinecap="round" strokeLinejoin="round" />
      </g>
      {/* the seal presses down on the flap point */}
      <g className="illo-part illo-stamp">
        <circle cx="80" cy="86" r="11" fill={ORANGE} stroke={NAVY} strokeWidth="2.2" />
        <circle cx="80" cy="86" r="5.5" stroke={NAVY} strokeWidth="1.6" strokeDasharray="2 2.4" />
      </g>
      {/* an airmail hint */}
      <path d="M120 30 h18 M126 38 h16" stroke={BLUE_LINE} strokeWidth="2.4" strokeLinecap="round" />
    </svg>
  )
}

// ---------------------------------------------------------------------------
// ShieldIllustration — compliance / the attorney disclaimer: a shield with a
// drawn check and a soft halo. Also rendered tiny next to legal lines.
export function ShieldIllustration({ size = 120, className = '' }) {
  return (
    <svg width={size} viewBox="0 0 160 120" fill="none" className={`illo ${className}`} aria-hidden="true">
      {/* halo */}
      <circle cx="80" cy="60" r="46" fill={BLUE_SOFT} className="illo-part illo-pulse" opacity="0.7" />
      {/* shield */}
      <g className="illo-part illo-rise">
        <path d="M80 16 L116 30 v28 c0 24 -15.5 39.5 -36 46 -20.5 -6.5 -36 -22 -36 -46 V30 Z"
              fill="#ffffff" stroke={NAVY} strokeWidth="2.6" strokeLinejoin="round" />
        <path d="M80 25 L108 36 v21.5 c0 18.5 -12 30.8 -28 36.3 -16 -5.5 -28 -17.8 -28 -36.3 V36 Z"
              fill="none" stroke={BLUE_LINE} strokeWidth="1.8" strokeLinejoin="round" />
      </g>
      {/* the check draws itself */}
      <path d="M64 60 l11 11 L98 46" pathLength="100" className="illo-draw illo-draw--2"
            stroke={BLUE} strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" />
      {/* one orange spark */}
      <circle cx="118" cy="24" r="3" fill={ORANGE} className="illo-part illo-pulse" />
    </svg>
  )
}
