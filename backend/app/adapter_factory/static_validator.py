"""Static validation of generated candidates (brief §18, §26 Layer 1).

Deterministic, read-only checks over the candidate bundle. Rejection reasons
map §18's forbidden classes onto the typed-flow artifact: because generated
adapters are data (never executed code), the code-injection classes are
checked as forbidden PATTERNS anywhere in the bundle's strings, and the
policy classes (scope, evidence, reconciliation, kill switch, rollback,
sensitive-action boundaries) are checked structurally.
"""
from __future__ import annotations

import json
import re

from .schema import validate_flow, validate_field_mapping, normalize_node

_FORBIDDEN_PATTERNS = [
    (r"\beval\s*\(", "eval"),
    (r"\bexec\s*\(", "exec"),
    (r"\b__import__\b", "dynamic import"),
    (r"subprocess|os\.system|popen", "shell command"),
    (r"open\s*\(\s*['\"]/", "unrestricted file access"),
    (r"document\.cookie|raw_cookie|cookiejar", "raw cookie access"),
    (r"authorization:\s*bearer", "authorization-header capture"),
    (r"captcha[_-]?solv", "CAPTCHA-solving logic"),
    (r"anticaptcha|2captcha", "CAPTCHA-solving service"),
    (r"card_number|cvv|cvc2?", "card-data access"),
    (r"intercept[_-]?otp|read[_-]?otp", "OTP access"),
    (r"kill[_-]?switch\s*=\s*(false|off|0)", "kill-switch disabling"),
    (r"approve[_-]?(self|release)|release\s*\(\s*self", "self-approval call"),
    (r"moonshot|kimi", "Kimi call from authenticated runtime"),
]


def validate_candidate(version_row) -> dict:
    """Returns {passed, checks: {name: {ok, detail[]}}} — evidence, not
    approval: nothing here can activate a candidate."""
    checks = {}

    def check(name, problems):
        checks[name] = {"ok": not problems, "detail": problems[:20]}

    manifest = version_row.manifest or {}
    flow = version_row.flow or []
    hosts = manifest.get("allowed_hostnames") or []

    # Manifest / route scope
    problems = []
    for f in ("adapter_id", "version", "route_key", "route_scope",
              "portal_operator", "allowed_hostnames", "artifact_kind"):
        if not manifest.get(f):
            problems.append(f"manifest missing {f}")
    if manifest.get("artifact_kind") != "typed_flow_v1":
        problems.append("unknown artifact kind — the runtime only executes typed_flow_v1")
    check("manifest", problems)

    # Hostname allowlist sanity + no wildcard expansion
    problems = []
    for h in hosts:
        if "/" in h or h.startswith("http") or "*" in h or h.strip() != h or not h:
            problems.append(f"invalid hostname allowlist entry {h!r}")
    if not hosts:
        problems.append("missing route scope: empty hostname allowlist")
    redirects = manifest.get("allowed_redirect_hosts") or []
    for h in redirects:
        if "*" in h or h.startswith("http"):
            problems.append(f"invalid redirect allowlist entry {h!r}")
    check("hostnames", problems)

    # Typed flow structural validation (deterministic selectors, sensitive
    # boundaries, retries bounded, irreversible⇒reconcile+evidence).
    check("flow", validate_flow(flow, allowed_hostnames=hosts))

    # Field mappings: no sensitive targets.
    problems = []
    for m in version_row.field_mappings or []:
        problems.extend(validate_field_mapping(m))
    check("mappings", problems)

    # Evidence rules present and honest.
    problems = []
    ev = version_row.evidence_rules or {}
    if not ev:
        problems.append("missing evidence rules")
    if ev.get("banner_text_sufficient") is not False:
        problems.append("visible text alone must never prove success")
    check("evidence_rules", problems)

    # Recovery + reconciliation + kill switch + rollback metadata.
    problems = []
    rec = version_row.recovery or {}
    if "reconcile" not in str(rec.get("resume", "")):
        problems.append("missing reconciliation-before-retry recovery rule")
    if rec.get("on_unexpected_state") != "fail_closed":
        problems.append("runtime must fail closed on unexpected states")
    if not rec.get("rate_limits"):
        problems.append("missing rate limits")
    if not version_row.kill_switch_key:
        problems.append("missing kill-switch metadata")
    if version_row.version > 1 and not version_row.rollback_to_version:
        problems.append("missing rollback target")
    check("recovery", problems)

    # Forbidden patterns anywhere in the EXECUTABLE bundle (§18). Provenance
    # metadata (which model generated the adapter) is excluded — recording that
    # Kimi authored a candidate is not a runtime Kimi call; the rule forbids the
    # runtime FLOW invoking Kimi, which this scan of flow/mappings/rules covers.
    scan_manifest = {k: v for k, v in manifest.items() if k != "generator"}
    blob = json.dumps({"m": scan_manifest, "f": flow,
                       "fm": version_row.field_mappings,
                       "dm": version_row.document_mappings,
                       "ev": ev, "rec": rec},
                      sort_keys=True, default=str).lower()
    problems = []
    for pattern, label in _FORBIDDEN_PATTERNS:
        if re.search(pattern, blob):
            problems.append(f"forbidden content: {label}")
    check("forbidden_patterns", problems)

    # Sensitive-action boundary: every sensitive/applicant step is a handoff.
    problems = []
    for raw in flow:
        n = normalize_node(raw)
        if (n.get("sensitive") or n.get("applicant_action")) and n["action"] != "APPLICANT_HANDOFF":
            problems.append(f"{n.get('node_id')}: sensitive step is not an applicant handoff")
    check("sensitive_boundaries", problems)

    passed = all(c["ok"] for c in checks.values())
    return {"passed": passed, "checks": checks,
            "classification": "STATIC_VALIDATED" if passed else "STATIC_FAILED",
            "activation_note": "Static validation is evidence only; release stays "
                               "a human admin action."}
