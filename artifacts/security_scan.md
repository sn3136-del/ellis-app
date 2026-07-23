# Supply-chain & secret scan (source + declared dependencies)

## Secret scan (source tree, excluding node_modules and the bundled runtime)
- Patterns: OpenAI-style `sk-…`, AWS `AKIA…`, PEM private-key blocks,
  `MOONSHOT_API_KEY=…`, `BROWSERBASE_API_KEY=…`, `bb_live_…`.
- Result: **1 file matched — `scripts/scan-release-secrets.sh`**, which contains
  those patterns as its own literal scanner rules. That is a self-match, NOT a
  secret. **No real credential value is present in the source tree.**
- The Browserbase key pasted earlier in chat is NOT written into any file,
  commit, or artifact. It must still be treated as exposed and rotated.
- No `.env`, `*.key`, `*.pem`, `service-account*.json`, or `kimi.key` is tracked
  in git (`git ls-files` check: none).

## SBOM
- Python: `artifacts/sbom_python.txt` (59 resolved packages; PyInstaller and its
  build-only deps excluded).
- Node: 442 packages in `package-lock.json` (sha256[:16] `0691a07a541ade78`).

## Notes
- The bundled Python runtime legitimately ships `certifi/cacert.pem` (a PUBLIC CA
  bundle). It is allowlisted narrowly (by the `backend-venv` runtime path) for
  the FILENAME rule only; secret-VALUE scanning still applies to it. The whole
  runtime is NOT excluded from scanning.
- No malicious-package/advisory scan was run against a public service to avoid
  submitting anything; run `pip-audit` / `npm audit` locally as a follow-up.
