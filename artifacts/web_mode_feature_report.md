# Browser (web) mode — feature compatibility audit

`npm run ellis:web` runs Ellis from source with NO Electron: FastAPI backend +
worker + migrations on 127.0.0.1, the renderer served by Vite on 127.0.0.1:5199,
opened in the default browser. Runtime mode `local_real_services` (real-only).

## Fully working in the browser (no adaptation)
The entire production **Visa Platform** already talks to the backend over HTTP
and uses standard browser APIs, so it works unchanged:
- Applicant intake / "Start your visa" wizard, route resolution, on-demand route
  research, research-jobs progress.
- Passport & document upload via the browser file picker (`<input type=file>` +
  `FileReader` → base64 → `POST /cases/{id}/documents`).
- **Server-side OCR**: MRZ validation + Document AI, incl. the letters-only name
  fix — verified live over HTTP: `N0EMI → NOEMI` (given_names=NOEMI, surname=ELIAS).
- Standing authorization, appointment preferences, final review + signature,
  exact-amount payment authorization.
- Adapter factory (build/consent/progress), Admin console (adapter-factory,
  snapshot, research jobs, review queues), capability release / kill / rollback.
- Kimi, Browserbase, Document AI — all run **server-side**; the browser only
  sees booleans via `/capabilities`. Credentials never reach browser JS.
- Reconciliation + durable worker, restart recovery, audit, privacy export/erase.
- i18n: English, Simplified Chinese, Traditional Chinese.
- External source links: `openExternal` → `window.open` (web shim); Browserbase
  Live View iframe permitted via a scoped `frame-src` in the web CSP.

## Requires adaptation / desktop-only — and NOT reachable in real-services mode
These are **demo-pipeline** surfaces, disabled when `runtime_mode` is
`local_real_services` (the browser shows `DemoDisabled`, never a simulation):
- The simulated Trip.com demo portal (`TripPortal.jsx`) — uses Electron IPC
  (`trips:*`, `trips:agent:*`) and on-device Apple Vision OCR.
- `Settings.jsx` — local demo-engine config (Ollama/Claude/SMTP) over IPC.
- `pdf.js` `exportPdfToDesktop` (native PDF-to-Desktop) and native file dialogs
  (`pickDocuments`) — the production doc flow uses the browser file picker
  instead. On-device Vision OCR is replaced by server-side Document AI/MRZ.

The web shim (`src/renderer/src/webShim.js`) is inert under Electron (the preload
sets `window.ellis` first) and, in the browser, provides real `openExternal`
(new tab) and a no-op proxy for demo-only desktop methods — it never fabricates a
success. No feature is silently disabled: unreachable desktop-only surfaces show
an explicit blocker.

## Preservation
Pre vs post inventory (`artifacts/pre_package_feature_inventory.json` vs
`post_web_feature_inventory.json`): **39/39 features, 85 routes, 94 Python
modules — zero lost.** The Electron implementation (`src/main`, `src/preload`) is
unchanged; browser mode is an additional safe path.
