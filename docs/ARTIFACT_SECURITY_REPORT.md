# Electron artifact security report

**Date:** 2026-07-23
**Scope:** clean rebuild of the Ellis Electron app and a secret/private-data scan
of the packaged artifacts. No secret values are printed anywhere in this report.

## Build performed

```
rm -rf out release            # removed prior + previously-compromised artifacts
npm run build                 # electron-vite: main + preload + renderer
npx electron-builder --mac --dir   # packaged app.asar (unsigned; local scan build)
./scripts/scan-release-secrets.sh  # release gate
```

Result: `release/mac-arm64/Ellis.app` produced. Code signing skipped (no Developer
ID identity locally — expected; a signed build happens in the release pipeline
**after** credential rotation).

## Findings

| Check | Result |
|---|---|
| `resources/kimi.key` on disk | **absent** ✓ |
| `kimi.key` entry inside `app.asar` | **none** ✓ |
| `.env`, ADC, `*.pem`, `*.key`, service-account JSON in package | **none** ✓ |
| Private passport fixtures (`fixture_*`) anywhere in repo/package | **none** ✓ |
| Secret VALUE patterns (`sk-…`, `AKIA…`, `AIza…`, PEM, `MOONSHOT_API_KEY=`, `BROWSERBASE_API_KEY=`, Browserbase Live View URLs) in packaged contents | **none** ✓ |
| Packaged main process reads a bundled/drop-in `kimi.key` | **no** ✓ |
| Packaged main gates all client provider calls behind `clientProviderCallsAllowed()` = `!app.isPackaged` | **yes** (5 guard sites, 1 `app.isPackaged` check) ✓ |
| `release/` secret scan (`scripts/scan-release-secrets.sh`) | **CLEAN**, exit 0 ✓ |

No suspected secrets were found. Had any been found, only the file path, secret
category, and a redacted `sha256[:12]` fingerprint would be recorded here — never
the value.

## Provider-request path (packaged app)

The packaged/distributable Ellis makes **zero** direct external-provider calls.
All provider access (Kimi/Moonshot, Google Document AI, Browserbase, Stripe,
cloud storage, government portals) happens only in the authenticated FastAPI
backend, reached from the renderer over HTTP via
`src/renderer/src/lib/visaBackend.js`. Provider credentials live only in the
git-ignored `backend/.env` and, in production, a secret manager.

## Automated enforcement

- `npm test` → `tests/build_security.test.mjs` (8 tests): forbidden files in
  shippable dirs, private fixtures, secret values in `out/`, packaging-config
  exclusions, main-process guard, packaged-asar filename scan, **deep
  packaged-asar content scan**, and packaged-main backend-only guard.
- `scripts/scan-release-secrets.sh` — extracts every `app.asar` and scans it plus
  the release tree; prints only redacted fingerprints; exits non-zero on any hit.
- `.github/workflows/security.yml` — CI runs the build, packages, and runs the
  release scan as a gate on every push/PR, and fails if any credential/private
  file is tracked in git.

## Prior-key rotation status

The previously packaged Kimi key was compromised (it shipped in a past
`app.asar`) and is tracked for rotation in `docs/SECURITY_ROTATION.md`. The
rotated key is present only in `backend/.env` (git-ignored, untracked) and does
not appear in any current build, release directory, git object, resource, test
fixture, doc, or generated artifact (verified by the scans above).
