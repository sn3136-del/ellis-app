## First run

1. `npm install`
2. `npm run dev` (development) or build a packaged app (below).
3. Open **Settings** and paste your OpenAI API key (stored locally on your device only).
4. Pick your role and create a case.

## Build a real app you can double-click

```bash
# macOS .app + .dmg
npm run dist:mac
```

The output lands in `release/`. Drag **Ellis.app** to Applications.

## Scripts

- `npm run dev` — run in development with hot reload.
- `npm run build` — type-check and bundle.
- `npm run dist` — package for the current platform.

## Data & privacy

All cases and settings are stored locally in your OS user-data directory (`ellis-state.json`). Your OpenAI key never leaves the device except to call OpenAI directly.
