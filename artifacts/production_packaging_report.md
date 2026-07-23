# Real-services production packaging — status report

## Outcome (honest)
A signed + notarized + stapled, real-services-only, self-contained macOS Ellis
app **could not be produced on this machine** because the Apple signing and
notarization prerequisites are absent. All preservation-safe and
correctness-critical work that does NOT depend on Apple credentials was
completed and committed. **No unsigned build is installed** (macOS security
removed the previously-installed unsigned builds; see diagnosis).

## Preservation
- Preservation tag: `pre-real-production-packaging-20260723-161953`
  (+ `pre-packaging-head-9c64a20`).
- Starting HEAD: `9c64a20`. Checkpoint: `595d4b9`. Real-services commit: `f5e037b`.
- Worktree: `/Users/sammynawaly/Documents/ellis-packaging` (branch `packaging-work`).
- Baseline manifests: `artifacts/pre_package_git_ls_files.txt` (342 files),
  `pre_package_migrations.txt` (6), `pre_package_lock_hashes.txt`.
- Feature inventory: `artifacts/pre_package_feature_inventory.json` —
  **39/39 named features present**, **85 backend routes**, **94 Python modules**,
  **22 renderer files**. No source feature loss.

## Blocked-build diagnosis (`artifacts/blocked_build_diagnosis.md`)
Unsigned, un-notarized app bundling nested native Mach-O (a copied CPython 3.14
interpreter + venv `.so`/`.dylib`) → macOS security (Gatekeeper + XProtect)
distrusts and **removes** it. Confirmed live: both `release/mac-arm64/*.app` and
the installed `/Applications/Ellis.app` were removed by macOS while unsigned; no
MDM/EDR present, so this is Apple's built-in protection. This is correct OS
behavior, not a false positive — the fix is Developer-ID signing + notarization,
NOT `xattr`/ad-hoc bypass.

## Real-services-only runtime (DONE, committed `f5e037b`)
- New `local_real_services` runtime mode: real providers + local SQLite +
  dev-token auth, with the absolute real-only boundary (mock-forbidden,
  `production_mode` on, fail closed). Added to `REAL_ONLY_MODES`.
- Packaged Electron app + bundled backend default to `local_real_services`
  (never `local_mock_demo`); the simulated Trip.com demo pipeline is disabled;
  renderer shows no SIMULATED banner and `DemoDisabled` for the demo view.
- Backend DB written under `~/Library/Application Support` (userData), never in
  the app bundle.
- Build-time tests: `local_real_services` is real-only; never constructs
  MockPortal; the adapter-factory SyntheticPortal observer fails closed to
  `None` in every real-only mode. **Backend 355 passed, 3 skipped; JS 96 passed.**

## Runtime feasibility (Section 3)
- The manually-pruned 79 MB venv is NOT suitable as the production runtime
  (it removed Playwright/Temporal/PostgreSQL) — retained only as a checkpoint.
- A framework-independent runtime is **feasible**: PyInstaller 6.21.0 installs
  and runs on Python 3.14. It was NOT built out, because the resulting unsigned
  native binaries hit the SAME notarization gate and cannot be validated
  end-to-end per the task's own success criteria (and installing unsigned is
  out of scope). Build it once the signing prerequisites below exist.

## Supply chain (`artifacts/security_scan.md`, `sbom_python.txt`)
- Source secret scan: clean (only self-match is the scanner's own patterns).
- No credential files tracked in git. SBOM: 59 Python + 442 Node packages.
- The pasted Browserbase `bb_live_…` key is not in any file/commit; still treat
  it as exposed and rotate it.

## REQUIRED to finish (must be provided by the user — cannot be obtained here)
1. Apple Developer Program membership.
2. **Developer ID Application** certificate + private key in the login Keychain
   (`security find-identity -v -p codesigning` → currently `0 valid identities`).
3. Apple Team ID + notarization credentials via
   `xcrun notarytool store-credentials` (App Store Connect API key, or
   Apple ID + app-specific password). None configured.

## Remaining implementation blockers (after credentials exist)
- Build the framework-independent runtime (PyInstaller one-folder) and verify
  each dependency; embed interpreter + native libs; no `/Library/Frameworks`
  dependency; no dev-machine paths.
- Sign every nested Mach-O (Electron frameworks, helpers, Python exe, `.so`,
  Playwright driver) → notarize with `notarytool submit --wait` → `stapler
  staple` → `spctl --assess` accept.
- Secure architecture hardening still to add: dynamic loopback port (not fixed
  8000), ephemeral per-launch Electron↔backend token, migrations-before-healthy
  gate, real startup-error screen, worker/reconciliation supervision.
- macOS Keychain credential flow for Browserbase/Kimi/Document AI/DB/Temporal.

## Current machine state
- `/Applications/Ellis.app`: absent (unsigned build removed by macOS).
- `/Applications/Ellis.app.bak-stale`: the user's original Jul-21 build (left
  untouched; not restored, not relied upon).
- `release/mac-arm64/`: empty (unsigned outputs removed by macOS).
