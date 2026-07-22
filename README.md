# Ellis

The AI workspace for immigration operations. A desktop application (Electron + React) that runs the full immigration lifecycle for the **United States (USCIS / DOS)** and **Canada (IRCC)**, including cross-border cases such as China → USA or Canada for work, study, and travel.

Every capability is powered by the **OpenAI API**.

## Roles

On launch, Ellis asks who you are. Each role gets its own interface:

- **Immigrant** — track your case, know what to upload, ask Ellis anything, handle notices and travel.
- **Employer** — automate onboarding, run compliance audits, manage renewals and risk across the workforce, hand off to counsel.
- **Counsel** — receive complete, structured case files; review documents, evidence, forms, risks, notices, compliance, and travel.

## Capabilities (all backed by OpenAI)

- **Document review** — extract every field from passports, notices, permits, pay stubs; flag what is missing or expiring.
- **Ask Ellis** — case-grounded Q&A with citations, plus a general immigration assistant.
- **Risk flags** — compliance and travel risk scan across the whole case.
- **Notice / RFE summaries** — turn a government notice into clear deadlines and actions.
- **Form preparation** — pre-fill USCIS forms (I-129, I-539, DS-160, I-765) and IRCC forms (IMM 1295, 1294, 5257) from case facts.
- **Evidence packets** — attorney-ready handoff with exhibits and open items.
- **Compliance audit** — score + findings for status, work authorization, worksite/LCA or LMIA, I-9, wages.
- **Travel risk** — go / caution / hold recommendation with a re-entry checklist.
- **Run the lifecycle** — Ellis determines the current stage and generates the next actions with owners and due dates.

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
