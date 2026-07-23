// Thin wrapper around the secure preload bridge (window.ellis), plus the
// shared domain metadata the Trip.com surfaces use.

// Graceful degradation: when the Electron preload bridge is unavailable (e.g.
// the renderer is opened in a plain browser for preview/testing), fall back to a
// no-op stub so the app still renders instead of hard-crashing. In the real
// Electron app window.ellis is always present, so this stub is never used —
// production behavior is unchanged. The Visa Platform screen talks to the
// backend over HTTP (visaBackend.js), so it works fully under the stub too.
function makeEllisStub() {
  const recur = () => new Proxy(async () => ({}), {
    get: (_t, p) => (p === 'then' ? undefined : recur()),
    apply: () => Promise.resolve({})
  })
  const overrides = {
    getSettings: async () => ({}),
    saveSettings: async () => ({}),
    listNotifs: async () => [],
    listTrips: async () => []
  }
  return new Proxy(overrides, { get: (t, p) => (p in t ? t[p] : recur()) })
}

export const ellis = (typeof window !== 'undefined' && window.ellis) ? window.ellis : makeEllisStub()

// Comprehensive country list (origin can also be typed freely via datalist).
export const COUNTRIES = [
  'Afghanistan', 'Albania', 'Algeria', 'Argentina', 'Australia', 'Austria', 'Bangladesh', 'Belgium',
  'Bolivia', 'Brazil', 'Bulgaria', 'Cambodia', 'Cameroon', 'Canada', 'Chile', 'China', 'Colombia',
  'Costa Rica', 'Croatia', 'Cuba', 'Czechia', 'Denmark', 'Dominican Republic', 'Ecuador', 'Egypt',
  'El Salvador', 'Ethiopia', 'Finland', 'France', 'Germany', 'Ghana', 'Greece', 'Guatemala', 'Haiti',
  'Honduras', 'Hong Kong', 'Hungary', 'India', 'Indonesia', 'Iran', 'Iraq', 'Ireland', 'Israel',
  'Italy', 'Jamaica', 'Japan', 'Jordan', 'Kazakhstan', 'Kenya', 'South Korea', 'Kuwait', 'Lebanon',
  'Malaysia', 'Mexico', 'Morocco', 'Nepal', 'Netherlands', 'New Zealand', 'Nigeria', 'Norway',
  'Pakistan', 'Peru', 'Philippines', 'Poland', 'Portugal', 'Qatar', 'Romania', 'Russia', 'Saudi Arabia',
  'Senegal', 'Singapore', 'South Africa', 'Spain', 'Sri Lanka', 'Sweden', 'Switzerland', 'Taiwan',
  'Thailand', 'Tunisia', 'Turkey', 'Ukraine', 'United Arab Emirates', 'United Kingdom', 'United States',
  'Uzbekistan', 'Venezuela', 'Vietnam', 'Zimbabwe', 'Other'
]

export function fmtDate(ts) {
  if (!ts) return ''
  return new Date(ts).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}
