// Minimal line-icon set. All inherit currentColor.
const S = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.6, strokeLinecap: 'round', strokeLinejoin: 'round' }

export const Icon = {
  home: (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><path d="M3 11l9-7 9 7" /><path d="M5 10v10h14V10" /></svg>),
  cases: (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 9h18M8 4v5" /></svg>),
  doc: (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5M9 13h6M9 17h6" /></svg>),
  chat: (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><path d="M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /></svg>),
  shield: (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><path d="M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z" /><path d="M9 12l2 2 4-4" /></svg>),
  bell: (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" /><path d="M13.7 21a2 2 0 0 1-3.4 0" /></svg>),
  form: (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M8 8h8M8 12h8M8 16h5" /></svg>),
  folder: (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /></svg>),
  gauge: (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><path d="M12 13l4-4" /><path d="M4 18a8 8 0 1 1 16 0" /></svg>),
  plane: (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><path d="M10 13L3 9l2-2 6 1 4-5 2 1-2 6 4 4-1 2-5-3-2 5-2-2z" /></svg>),
  flow: (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><circle cx="6" cy="6" r="2" /><circle cx="18" cy="18" r="2" /><path d="M8 6h6a4 4 0 0 1 0 8H8" /></svg>),
  gear: (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><circle cx="12" cy="12" r="3.2" /><path d="M19 12a7 7 0 0 0-.1-1l2-1.6-2-3.4-2.3 1a7 7 0 0 0-1.7-1l-.3-2.5H9.4l-.3 2.5a7 7 0 0 0-1.7 1l-2.3-1-2 3.4 2 1.6a7 7 0 0 0 0 2l-2 1.6 2 3.4 2.3-1a7 7 0 0 0 1.7 1l.3 2.5h5.2l.3-2.5a7 7 0 0 0 1.7-1l2.3 1 2-3.4-2-1.6a7 7 0 0 0 .1-1z" /></svg>),
  plus: (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><path d="M12 5v14M5 12h14" /></svg>),
  arrow: (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><path d="M5 12h14M13 6l6 6-6 6" /></svg>),
  back: (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><path d="M19 12H5M11 6l-6 6 6 6" /></svg>),
  check: (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><path d="M5 12l5 5L20 6" /></svg>),
  user: (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><circle cx="12" cy="8" r="3.5" /><path d="M4 20c1-4 4-6 8-6s7 2 8 6" /></svg>),
  building: (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><rect x="4" y="3" width="16" height="18" rx="1.5" /><path d="M8 7h2M8 11h2M8 15h2M14 7h2M14 11h2M14 15h2" /></svg>),
  scale: (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><path d="M12 3v18M7 21h10M5 7h14M5 7l-2 6h4zM19 7l-2 6h4z" /></svg>),
  trash: (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13" /></svg>),
  spark: (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" /></svg>),
  download: (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><path d="M12 4v11M7 11l5 5 5-5M5 20h14" /></svg>),
  mail: (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M4 7l8 6 8-6" /></svg>),
  plug: (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><path d="M9 2v6M15 2v6M6 8h12v3a6 6 0 0 1-12 0zM12 17v5" /></svg>),
  play: (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><path d="M7 4l13 8-13 8z" /></svg>),
  eye: (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7z" /><circle cx="12" cy="12" r="3" /></svg>),
  globe: (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18" /></svg>)
}
