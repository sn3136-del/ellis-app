# Blocked-build diagnosis (inert inspection — the flagged app was never launched)

## Observed behavior
- `npm run dist:mac:dir` produces `release/mac-arm64/Ellis for Trip.com.app`
  containing an embedded Python runtime under `Contents/Resources/backend-venv`
  (a **copied CPython 3.14 interpreter** plus native extension `.so`/`.dylib`
  files from the venv) and the backend source + data as `extraResources`.
- Immediately after the build the bundle is intact (verified stable ~25s).
- Within minutes, the embedded runtime — and ultimately the **entire `.app`** —
  is removed from `release/`. No command in this session deletes `release/`.
- The user previously reported macOS flagging a build as malware / "damaged"
  and moving it to the Trash.

## Root cause
The app is **unsigned and un-notarized**, yet it bundles **nested native
executable code** that macOS does not trust:
- `codesign -dv <app>` → no signature (electron-builder logged
  `skipped macOS application code signing … 0 valid identities found`).
- `spctl --assess` → the bundle is not accepted.
- The bundled `python3.14` is a raw copied Mach-O; the venv ships native
  extensions (pydantic-core, cryptography, etc.) that are also unsigned.

macOS security (Gatekeeper policy + XProtect / malware remediation) refuses to
trust unsigned nested native code inside an app that has no valid Developer ID
signature and no Apple notarization ticket. The observed removal of the bundle
is macOS **correctly** quarantining/remediating untrusted nested executables —
not a false positive to bypass.

Environment: `profiles status` shows **no MDM enrollment** and no third-party
EDR support directories, so this is Apple's own built-in protection, not a
corporate agent.

## Conclusion
A self-contained macOS app that embeds a Python runtime **cannot run on macOS
unless every nested Mach-O is Developer-ID signed and the app is Apple
notarized + stapled.** Ad-hoc signing and `xattr` quarantine removal are NOT
acceptable substitutes (and are explicitly out of scope): they do not produce a
notarization ticket, so Gatekeeper/XProtect will still distrust the nested code.

## Required to proceed (not present on this machine)
1. Apple Developer Program membership.
2. A **Developer ID Application** certificate **with its private key** in the
   login Keychain (`security find-identity -v -p codesigning` currently returns
   `0 valid identities`).
3. Apple Team ID + notarization credentials for `notarytool`
   (App Store Connect API key, or Apple ID + app-specific password), stored via
   `xcrun notarytool store-credentials` (none configured).

Until these exist, the correct outcome per the objective is to **stop at the
signing gate** and not install any unsigned build.
