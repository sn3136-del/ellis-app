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

## Run it on your laptop (Trip.com testers)

**Mac** (installs Homebrew, Node and Python if missing, unlocks the keys, opens Ellis at http://127.0.0.1:5199/):

```bash
git clone -b h1b-edition https://github.com/sn3136-del/ellis-app.git ellis-database && cd ellis-database && ELLIS_UNLOCK=<passphrase from the owner> bash scripts/setup-mac.sh
```

**Windows** — use WSL (Ubuntu): open "Ubuntu" from the Start menu (install it once with `wsl --install` in PowerShell, then reboot), then run the same command with `scripts/setup-linux.sh` instead of `scripts/setup-mac.sh`. Ellis opens at http://127.0.0.1:5199/ in your normal Windows browser.

**Networks where GitHub, Google or npm are blocked or slow** (mainland China): the launchers detect it and switch to the Tsinghua/npmmirror mirrors on their own; the Google credential check is bounded to six seconds and the Database does not use Google at all. If `git clone` itself cannot reach GitHub, download the ZIP of the branch from the GitHub page in a browser and run the same `bash scripts/...` command inside the unzipped folder.

**Stop:** Ctrl+C in the terminal. **Update later:** `git pull` inside the folder, then `npm run ellis:web`.
