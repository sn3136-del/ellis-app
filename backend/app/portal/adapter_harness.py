"""Adapter development kit: dry-validation harness for route-specific adapters.

Validates ANY adapter definition against the full framework contract WITHOUT
touching a live portal — used by adapter authors, the admin console, and CI.
This is what "contract-test evidence" is generated from before an adapter may
progress through the adapters_admin lifecycle toward production approval.

Checks are deterministic and read-only:
  structural   — contract.validate_adapter (fields, policies, domains, driver)
  safety       — prohibited actions, CAPTCHA never automatable, personal steps
  domains      — every URL on the approved allowlist; allowlist hosts sane
  selectors    — deterministic selector mappings present + well-formed for the
                 declared required fields (no free-text 'AI figures it out')
  extraction   — confirmation/receipt extraction configured
  recovery     — resume behavior + rate limits declared
  live-binding — the REAL driver's fail-closed gates (never bound outside a
                 production-approved adapter in a real-only mode)

Nothing here can activate an adapter: activation stays a human admin action in
adapters_admin, and generated evidence explicitly records that.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from .contract import validate_adapter

_SELECTOR_OK = ("#", ".", "[", "input", "select", "textarea", "button", "form")


@dataclass
class HarnessReport:
    adapter_id: str = ""
    adapter_version: int = 0
    passed: bool = False
    checks: dict = field(default_factory=dict)     # name -> {ok, detail[]}
    activation_note: str = ("Dry validation only. Activation requires the "
                            "adapters_admin lifecycle with an authenticated human "
                            "administrator; this harness cannot approve anything.")

    def as_dict(self) -> dict:
        return {"adapter_id": self.adapter_id, "adapter_version": self.adapter_version,
                "passed": self.passed, "checks": self.checks,
                "activation_note": self.activation_note}


def _check(report: HarnessReport, name: str, problems: list[str]):
    report.checks[name] = {"ok": not problems, "detail": problems[:20]}


def dry_validate(adapter) -> HarnessReport:
    r = HarnessReport(adapter_id=getattr(adapter, "adapter_id", "?"),
                      adapter_version=int(getattr(adapter, "adapter_version", 0) or 0))

    # structural: reuse the canonical contract validator.
    _check(r, "structural", validate_adapter(adapter))

    # safety: personal steps can never be automated away.
    problems = []
    prohibited = set(getattr(adapter, "prohibited_actions", []) or [])
    if "solve_captcha" not in prohibited:
        problems.append("solve_captcha must be prohibited")
    for action in ("enter_otp", "enter_payment_secret", "sign_declaration"):
        if action in set(getattr(adapter, "allowed_actions", []) or []):
            problems.append(f"'{action}' may never be an allowed automated action")
    if getattr(adapter, "personal_declaration_required", False) and \
            getattr(adapter, "representative_submission", "") == "automated":
        problems.append("personal declaration required but submission claims 'automated'")
    _check(r, "safety", problems)

    # domains: URLs confined to the allowlist; allowlist entries are bare hosts.
    problems = []
    allow = [d.lower() for d in getattr(adapter, "approved_domains", []) or []]
    for d in allow:
        if "/" in d or d.startswith("http"):
            problems.append(f"approved_domains entry must be a bare hostname: {d!r}")
    for f in ("registration_url", "login_url", "application_url", "appointment_url"):
        url = getattr(adapter, f, "") or ""
        host = urlparse(url).netloc.lower()
        if host and not any(host == a or host.endswith("." + a) for a in allow):
            problems.append(f"{f} host {host!r} not on the approved allowlist")
    _check(r, "domains", problems)

    # selectors: deterministic mappings, one per declared field where mapped.
    problems = []
    for group in ("registration_mappings", "application_mappings"):
        for m in getattr(adapter, group, []) or []:
            sel = (m.get("selector") or "").strip()
            if not sel:
                problems.append(f"{group}: field {m.get('field')!r} has no selector")
            elif not sel.startswith(_SELECTOR_OK):
                problems.append(f"{group}: selector {sel!r} for {m.get('field')!r} is not "
                                "a deterministic CSS selector")
    mapped = {m.get("field") for m in getattr(adapter, "application_mappings", []) or []}
    required = set(getattr(adapter, "required_applicant_fields", []) or [])
    unmapped = sorted(required - mapped - {"email"})
    if getattr(adapter, "production_approval_status", "") == "production_approved" and unmapped:
        problems.append(f"production adapter leaves required fields unmapped: {unmapped}")
    _check(r, "selectors", problems)

    # extraction: official evidence extraction must be configured.
    problems = []
    for f in ("confirmation_extraction", "receipt_extraction"):
        if not getattr(adapter, f, ""):
            problems.append(f"{f} not configured — success evidence cannot be read")
    _check(r, "extraction", problems)

    # recovery: reconciliation + rate limits declared.
    problems = []
    resume = (getattr(adapter, "resume_behavior", "") or "").lower()
    if "reconcile" not in resume:
        problems.append("resume_behavior must describe reconciliation before retry")
    rl = getattr(adapter, "rate_limits", None) or {}
    if not rl:
        problems.append("rate_limits must be declared")
    _check(r, "recovery", problems)

    # live-binding: prove the real driver refuses this adapter unless it is
    # production-approved+enabled in a real-only runtime (fail-closed gates).
    problems = []
    approved = (getattr(adapter, "production_approval_status", "") == "production_approved"
                and bool(getattr(adapter, "production_enabled", False)))
    if not approved:
        try:
            from .live_driver import BrowserbaseLiveViewDriver
            from .driver_factory import RealOnlyStop
            try:
                BrowserbaseLiveViewDriver(adapter, session={"id": "harness"},
                                          page=object(), require_real_key=False)
                problems.append("live driver BOUND to an unapproved adapter — fail-closed gate broken")
            except RealOnlyStop:
                pass  # expected: refused
        except Exception as e:  # noqa: BLE001
            problems.append(f"live-binding check errored: {e}")
    _check(r, "live_binding_fail_closed", problems)

    r.passed = all(c["ok"] for c in r.checks.values())
    return r
