// Browser (web-mode) fallback for the Electron preload bridge.
//
// Loaded FIRST in main.jsx. It is INERT under Electron: the preload
// (contextBridge) sets window.ellis before the renderer runs, so the guard
// below skips entirely and the desktop path is unchanged.
//
// In a plain browser (npm run ellis:web) there is no preload, so this provides
// safe, browser-native implementations of the few desktop APIs the PRODUCTION
// screens use — currently just opening an external URL. Everything else falls
// through to a no-op proxy, matching lib/api.js's existing graceful
// degradation. Nothing here simulates a feature: the Visa Platform talks to the
// real backend over HTTP, and provider work (Kimi/Browserbase/Document AI),
// OCR, adapters and reconciliation all run server-side. Desktop-only features
// (native PDF-to-Desktop, native file dialogs, on-device Vision OCR) are demo-
// pipeline surfaces that are disabled in local_real_services mode; if one were
// reached it returns an inert value rather than a fake success.
if (typeof window !== 'undefined' && !window.ellis) {
  const noop = () => new Proxy(async () => ({}), {
    get: (_t, p) => (p === 'then' ? undefined : noop()),
    apply: () => Promise.resolve({})
  })
  const overrides = {
    // Real browser behavior — open the URL in a new tab.
    openExternal: (url) => {
      try { window.open(url, '_blank', 'noopener,noreferrer') } catch { /* popup blocked */ }
      return Promise.resolve({})
    },
    getSettings: async () => ({}),
    saveSettings: async () => ({}),
    listNotifs: async () => [],
    listTrips: async () => [],
    __webMode: true
  }
  window.ellis = new Proxy(overrides, { get: (t, p) => (p in t ? t[p] : noop()) })
}
