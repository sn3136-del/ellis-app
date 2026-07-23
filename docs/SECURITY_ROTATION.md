# Security: credential rotation notice

**Status: ACTION REQUIRED — rotate the Kimi/Moonshot API key immediately.**

## What happened

The Electron client historically loaded an "admin-provisioned" Kimi/Moonshot API
key from `resources/kimi.key`, which the electron-builder `files` glob
(`resources/**/*`) bundled into the distributable. Verification on
2026-07-22 confirmed the key was present **inside a shipped build**:

```
release/mac-arm64/Ellis.app/Contents/Resources/app.asar → /resources/kimi.key
```

A provider key packaged inside a distributable `.app` must be treated as
**compromised**: anyone with the distributable can extract it from `app.asar`.

## Remediation applied (this checkpoint)

1. Deleted `resources/kimi.key` from the working tree.
2. Removed every bundled/drop-in credential-file read from `src/main/index.js`
   (`resources/kimi.key`, `process.resourcesPath/kimi.key`,
   `userData/kimi.key`). The client can no longer read a key from a file.
3. Gated all client-side external-provider calls behind
   `clientProviderCallsAllowed()` = `!app.isPackaged` — a **packaged/distributable
   Ellis makes zero direct external-provider calls**. All real provider access
   (Kimi, Google Document AI, Browserbase, Stripe, storage, portals) happens only
   in the authenticated backend, reached via `src/renderer/src/lib/visaBackend.js`.
4. Hardened `package.json` `build.files` with negation globs excluding
   `*.key`, `*.pem`, `.env*`, ADC, service-account JSON, and private fixtures.
5. Deleted the compromised `release/mac-arm64` distributable (regenerable build
   artifact that bundled the key).
6. Added `tests/build_security.test.mjs` (runs in `npm test`) that fails the
   build if any provider key, private fixture, `.env`, ADC, or credential-like
   resource is present in a shippable directory or inside a packaged `app.asar`,
   or if the packaging config drops the exclusions.
7. Added secret redaction to the client error path (`redactSecrets`).

## Required manual action (cannot be automated from here)

- [ ] **Rotate the Moonshot/Kimi key** in the Moonshot console. Revoke the old
      one. The new key goes ONLY into the git-ignored `backend/.env` and the
      production secret manager — never into `resources/` or any client bundle.
- [ ] Review Moonshot usage/billing for anomalous calls from the leaked key.
- [ ] If the Google ADC or any other provider credential was ever placed in a
      client bundle, rotate those too. (ADC is mounted read-only into backend
      containers only and is git/docker-ignored — not known to have shipped.)
- [ ] Produce a fresh signed distributable only AFTER rotation; verify with
      `npm run test:security` before release.

## Invariant going forward

The Electron client never contains, receives, or ships a provider credential.
Providers are called only from backend services. `npm run test:security`
enforces this on every build.
