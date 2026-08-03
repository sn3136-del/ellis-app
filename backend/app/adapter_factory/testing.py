"""Layered adapter testing (brief §26) with TRUTHFUL classifications.

Layer 1 static  → STATIC_VALIDATED       (static_validator, no execution)
Layer 3 contract→ CONTRACT_TESTED        (flow vs recorded sanitized structures)
Layer 2 behavioral, one of:
  SYNTHETIC_TESTED       (mock/test modes: full drive of synthetic portals with
                          simulated applicant handoffs + side-effect ledger audit)
  LIVE_STRUCTURAL_TESTED (real modes: safe REVERSIBLE public navigation of the
                          flow's own pages through the credential-free live
                          observer — every navigation target must resolve on an
                          allowed hostname and every selector an interactive node
                          touches must exist in the freshly observed structure.
                          No login, no fill, no click, no side effects.)

The behavioral layer is mode-aware because SyntheticPortal may never be
instantiated in real runtime modes (local_real_services/staging/production):
`run_behavioral_layer` picks the corpus in mock-allowed modes and the live
structural layer otherwise. Authenticated/sandboxed/production live layers are
still NOT run here — they require authorized real access and are recorded only
when actually executed. Nothing in this module can claim them.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from ..config import settings
from . import models as fm
from .compiler import compile_flow
from .static_validator import validate_candidate

if TYPE_CHECKING:  # SyntheticPortal is imported lazily — never in real modes.
    from ..portal.synthetic import SyntheticPortal


def _record(db, version_row, layer, classification, passed, summary) -> fm.AdapterTestRun:
    run = fm.AdapterTestRun(candidate_version_id=version_row.id, layer=layer,
                            classification=classification, passed=passed,
                            summary=summary, executed_by="ellis-factory")
    db.add(run)
    db.commit()
    return run


def run_static_layer(db, version_row) -> fm.AdapterTestRun:
    report = validate_candidate(version_row)
    return _record(db, version_row, "static",
                   "STATIC_VALIDATED" if report["passed"] else "STATIC_FAILED",
                   report["passed"],
                   {"checks": {k: v["ok"] for k, v in report["checks"].items()},
                    "detail": {k: v["detail"] for k, v in report["checks"].items()
                               if not v["ok"]}})


def run_contract_layer(db, version_row, artifacts) -> fm.AdapterTestRun:
    """Every selector the flow touches must exist in a recorded sanitized
    structure — no live portal contact (§26 Layer 3)."""
    observed = set()
    for a in artifacts:
        for el in (a.structure or {}).get("elements", []):
            observed.add(el.get("selector"))
    problems = []
    try:
        compiled = compile_flow(version_row)
    except Exception as e:  # noqa: BLE001
        return _record(db, version_row, "contract", "CONTRACT_FAILED", False,
                       {"error": str(e)[:300]})
    for node in compiled.nodes.values():
        sel = node.get("selector")
        if sel and node["action"] in ("CLICK", "FILL_NON_SENSITIVE", "SELECT",
                                      "SELECT_SEARCH", "SELECT_RADIO", "CHECK",
                                      "READ_TEXT", "READ_FEE",
                                      "UPLOAD_AUTHORIZED_DOCUMENT") \
                and sel not in observed:
            problems.append(f"{node['node_id']}: selector {sel!r} was never observed")
    passed = not problems
    return _record(db, version_row, "contract",
                   "CONTRACT_TESTED" if passed else "CONTRACT_FAILED", passed,
                   {"selectors_checked": True, "problems": problems[:10]})


def simulate_applicant(portal: SyntheticPortal, handoff_kind: str):
    """Test-only simulation of the APPLICANT completing a personal step in the
    secure browser. The pipeline itself never performs these."""
    if handoff_kind in ("credentials", "passkey"):
        portal.human_login(otp=(portal.scenario in ("otp_handoff", "passkey_handoff")))
    elif handoff_kind == "otp":
        portal.human_login(otp=True)
    elif handoff_kind == "captcha":
        portal.human_solve_captcha()
        portal.human_login()
    elif handoff_kind == "payment_credentials":
        portal.human_pay()
    elif handoff_kind == "legally_personal_declaration":
        pass  # confirmation only; the submit node performs the portal action
    elif handoff_kind == "appointment_selection":
        pass  # the applicant's pick is injected into answers by the caller


def drive_flow_against_portal(db, version_row, portal: SyntheticPortal, *,
                              answers: dict, max_handoffs: int = 8) -> dict:
    """Drive the candidate flow end-to-end against one synthetic portal,
    simulating the applicant at every handoff. Returns the outcome + ledger."""
    from .runtime import FlowRunner   # local import to avoid cycles
    compiled = compile_flow(version_row)
    execution = fm.AdapterExecution(org_id="test", application_id="synthetic",
                                    candidate_id=version_row.candidate_id,
                                    candidate_version=version_row.version,
                                    tier="synthetic_test", status="running")
    db.add(execution)
    db.commit()
    runner = FlowRunner(db, execution=execution, compiled=compiled, driver=portal,
                        case_answers=answers, documents=[{"name": "passport.pdf"}])
    result = runner.run()
    for _ in range(max_handoffs):
        if result.get("status") != "paused_applicant_action":
            break
        kind = result.get("handoff_kind", "")
        simulate_applicant(portal, kind)
        paused_node = compiled.nodes.get(result.get("node") or "") or {}
        if kind == "appointment_selection":
            # The applicant picks a slot from the inventory Ellis just read —
            # the booking node refuses to proceed without their explicit choice.
            offered = (result.get("detail") or {}).get("slots") or []
            if not offered:
                offered = runner.slots_seen or []
            if offered:
                runner.answers[FlowRunner.CHOSEN_SLOT_KEY] = offered[0]["slotId"]
        # A booking node that paused to ask for the slot must RE-RUN once the
        # choice exists — resuming at the next node would skip the reservation.
        if paused_node.get("action") == "BOOK_APPOINTMENT":
            nxt = result["node"]
        else:
            nxt = compiled.next_of(result["node"], "ok")
        result = runner.run(resume_from=nxt)
    return {"result": result, "ledger": portal.ledger,
            "network": portal.network_events()}


DEFAULT_SYNTHETIC_SCENARIOS = [
    "single_step_login", "otp_handoff", "captcha_handoff", "prompt_injection",
    "misleading_success_banner", "payment_timeout", "appointment_race",
    "submission_timeout", "fee_change",
]

_ANSWERS = {"full_name": "Test Applicant", "passport_number": "X0000000",
            "arrival_date": "2026-10-10", "departure_date": "2026-10-20",
            "email": "t@example.com",
            "address_line1": "1 Test Street", "address_city": "Testville",
            "address_region": "TS", "address_postal_code": "00000",
            "address_country": "USA"}


def run_synthetic_layer(db, version_row, *, scenarios=None,
                        hostname: str = "portal.gov.example") -> fm.AdapterTestRun:
    """§26 Layer 2: the synthetic corpus. A scenario passes only when the flow
    terminates safely AND the ledger shows no duplicate side effects AND no
    misleading banner was believed."""
    # Lazy import + hard mode gate: the synthetic corpus exists ONLY where mock
    # portals are allowed. Real modes use run_live_structural_layer instead.
    if not settings().mock_portal_allowed:
        raise RuntimeError(
            "SyntheticPortal may not be instantiated in a real runtime mode — "
            "use run_live_structural_layer for the behavioral layer")
    from ..portal.synthetic import SyntheticPortal
    scenarios = scenarios or DEFAULT_SYNTHETIC_SCENARIOS
    per_scenario = {}
    all_ok = True
    for scenario in scenarios:
        portal = SyntheticPortal(scenario=scenario, hostname=hostname)
        out = drive_flow_against_portal(db, version_row, portal, answers=dict(_ANSWERS))
        status = out["result"].get("status")
        ledger = out["ledger"]
        problems = []
        if len(ledger["submissions"]) > 1:
            problems.append("duplicate submission")
        if len(ledger["charges"]) > 1:
            problems.append("duplicate charge")
        if scenario == "misleading_success_banner" and status == "completed" \
                and not ledger["submissions"]:
            problems.append("believed a success banner with no official evidence")
        if scenario in ("payment_timeout", "submission_timeout"):
            # Timeouts around irreversible actions must end reconciled-or-
            # uncertain — never a blind retry (ledger len>1) or false success.
            if len(ledger["charges"]) > 1 or len(ledger["submissions"]) > 1:
                problems.append("retried an irreversible action without reconciling")
        if status == "failed" and scenario not in ("fee_change",):
            # fee_change is EXPECTED to stop for a fresh exact-amount approval.
            problems.append(f"flow failed: {out['result'].get('reason', '')[:120]}")
        ok = not problems
        all_ok = all_ok and ok
        per_scenario[scenario] = {"status": status, "ok": ok, "problems": problems,
                                  "charges": len(ledger["charges"]),
                                  "submissions": len(ledger["submissions"]),
                                  "bookings": len(ledger["bookings"])}
    run = _record(db, version_row, "synthetic",
                  "SYNTHETIC_TESTED" if all_ok else "SYNTHETIC_FAILED", all_ok,
                  {"scenarios": per_scenario})
    for scenario, detail in per_scenario.items():
        db.add(fm.AdapterTestArtifact(test_run_id=run.id, kind=f"scenario:{scenario}",
                                      content=detail))
    db.commit()
    return run


# Interactive actions whose selectors must exist in freshly observed structure.
_INTERACTIVE_ACTIONS = ("CLICK", "FILL_NON_SENSITIVE", "SELECT", "SELECT_SEARCH",
                        "SELECT_RADIO", "CHECK", "READ_TEXT", "READ_FEE",
                        "UPLOAD_AUTHORIZED_DOCUMENT")


def _transient_failure(obs) -> bool:
    """Is this failure the network's fault rather than the portal's answer?

    Timeouts, connection resets and 5xx are transient. A 404 or 403 is the
    portal telling the truth about that URL and must never be retried away —
    retrying a refusal until it passes is exactly how a gate stops meaning
    anything.
    """
    if obs is None:
        return True
    status = int(obs.get("status") or 0)
    if status >= 500:
        return True
    if status:                      # any other real HTTP answer stands
        return False
    err = str(obs.get("error") or "").lower()
    return any(k in err for k in ("timeout", "econnreset", "err_connection",
                                  "err_network", "socket hang up",
                                  "err_tunnel_connection_failed"))


def _observe_flow_structure(compiled, observer, problems: list[str],
                            prefix: str = "") -> tuple[set[str], int]:
    """One session's worth of safe, reversible, credential-free observation:
    every NAVIGATE target, plus — when the manifest declares an entry gate —
    the declared entry-gate replay to the gated application form. Returns the
    freshly observed sanitized selector set and the page count. The observer
    never authenticates and never fills/clicks beyond the DECLARED gate."""
    from .recon import sanitize_structure
    observed_selectors: set[str] = set()
    pages_observed = 0
    visited: dict[str, bool] = {}
    for node in compiled.nodes.values():
        if node.get("action") != "NAVIGATE":
            continue
        for url in node.get("allowed_url_patterns") or []:
            if url in visited:
                continue
            obs = observer(url)
            ok = bool(obs and obs.get("ok"))
            # A page recon reached and mapped is a page that exists. When
            # re-observation times out or 5xxs, that is the network or a slow
            # government SPA, not a vanished portal — one retry, only for
            # transient failures, never for a 404/403 (a real answer that must
            # stand). Malaysia's MDAC form times out on roughly one load in
            # three and was failing the whole live layer for it.
            if not ok and _transient_failure(obs):
                obs = observer(url)
                ok = bool(obs and obs.get("ok"))
            visited[url] = ok
            if not ok:
                err = (obs or {}).get("error") or f"status {(obs or {}).get('status')}"
                problems.append(f"{prefix}{node['node_id']}: {url} not observable ({err})")
                continue
            pages_observed += 1
            art = sanitize_structure(obs)   # same chokepoint as recon (§14)
            for el in art.get("elements", []):
                observed_selectors.add(el.get("selector"))
    gate = (compiled.manifest or {}).get("entry_gate") or {}
    if gate:
        replay = getattr(observer, "observe_with_entry_gate", None)
        if replay is None:
            problems.append(f"{prefix}entry_gate: observer cannot replay the "
                            f"declared entry gate")
        else:
            base = gate.get("base_url") or ""
            if not base:
                for node in compiled.nodes.values():
                    if node.get("action") == "NAVIGATE" and \
                            (node.get("allowed_url_patterns") or []):
                        base = node["allowed_url_patterns"][0]
                        break
            obs = replay(base, gate)
            # A gate that recon already walked is a gate that exists. Replaying
            # it in a FRESH session hits the slow half of these portals: a
            # Cloudflare interstitial serving an empty DOM for the first few
            # seconds (Cambodia), an invisible Turnstile the click is a no-op
            # until it self-populates (Thailand). One retry for a transient
            # failure only — a gate whose control is genuinely gone still fails.
            if not (obs and obs.get("ok")) and _transient_failure(obs):
                obs = replay(base, gate)
            if not (obs and obs.get("ok")):
                problems.append(f"{prefix}entry_gate: replay failed "
                                f"({str((obs or {}).get('error', ''))[:120]})")
            else:
                pages_observed += 1
                art = sanitize_structure(obs)
                for el in art.get("elements", []):
                    observed_selectors.add(el.get("selector"))
    return observed_selectors, pages_observed


def run_live_structural_layer(db, version_row, observer) -> fm.AdapterTestRun:
    """Behavioral layer for REAL modes: safe, reversible, credential-free
    verification of the generated flow against the LIVE portal.

    Re-observes every NAVIGATE target (and replays a declared entry gate to
    re-observe the gated application form) through the live observer — public
    navigation and declared reversible acknowledgments only; it never
    authenticates and never fills or clicks beyond the declared gate — and
    verifies (a) each target resolves OK on an allowed hostname, and (b) every
    selector an interactive node touches exists in the freshly observed,
    sanitized live structure. When the observer can spawn an INDEPENDENT
    second session, every selector is verified in that fresh session too
    (selectors_verified_repeated_sessions evidence). Nothing here creates an
    account, submits, books, or pays; a challenged/unreachable page is an
    honest failure, never bypassed."""
    if observer is None:
        return _record(db, version_row, "live_structural", "LIVE_STRUCTURAL_FAILED",
                       False, {"error": "no live observer available"})
    try:
        compiled = compile_flow(version_row)
    except Exception as e:  # noqa: BLE001
        return _record(db, version_row, "live_structural", "LIVE_STRUCTURAL_FAILED",
                       False, {"error": str(e)[:300]})
    problems: list[str] = []
    try:
        observed_selectors, pages_observed = _observe_flow_structure(
            compiled, observer, problems)
    except Exception as e:  # noqa: BLE001 — an observer crash is an honest failure
        return _record(db, version_row, "live_structural", "LIVE_STRUCTURAL_FAILED",
                       False, {"error": f"live observation crashed: {str(e)[:240]}"})
    # Second, INDEPENDENT session (fresh browser session — never the recon
    # session): selector stability across sessions is release-gate evidence.
    sessions = 1
    second_selectors: set[str] | None = None
    spawn = getattr(observer, "spawn_independent", None)
    try:
        second = spawn() if spawn else None
    except Exception as e:  # noqa: BLE001
        second = None
        problems.append(f"session2: could not open an independent session "
                        f"({str(e)[:120]})")
    if second is not None:
        try:
            second_selectors, _ = _observe_flow_structure(
                compiled, second, problems, prefix="session2: ")
            sessions = 2
        except Exception as e:  # noqa: BLE001 — record, don't crash the stage
            problems.append(f"session2: observation crashed ({str(e)[:120]})")
        finally:
            close = getattr(second, "close", None)
            if close:
                try:
                    close()
                except Exception:  # noqa: BLE001 - session cleanup best-effort
                    pass
    for node in compiled.nodes.values():
        sel = node.get("selector")
        if sel and node.get("action") in _INTERACTIVE_ACTIONS:
            if sel not in observed_selectors:
                problems.append(f"{node['node_id']}: selector {sel!r} not present in "
                                f"live structure")
            elif second_selectors is not None and sel not in second_selectors:
                problems.append(f"{node['node_id']}: selector {sel!r} unstable — "
                                f"missing in the second independent session")
    passed = not problems and pages_observed > 0
    if pages_observed == 0 and not problems:
        problems.append("flow contains no observable navigation target")
        passed = False
    return _record(db, version_row, "live_structural",
                   "LIVE_STRUCTURAL_TESTED" if passed else "LIVE_STRUCTURAL_FAILED",
                   passed, {"pages_observed": pages_observed,
                            "selectors_live": len(observed_selectors),
                            "independent_sessions": sessions,
                            "problems": problems[:10]})


def run_behavioral_layer(db, version_row, *, observer=None,
                         hostname: str = "portal.gov.example") -> fm.AdapterTestRun:
    """Mode-aware behavioral layer: synthetic corpus where mock portals are
    allowed (tests/demo), live structural verification in real modes. This is
    the single dispatch point build_workflow and repair use, so no code path
    can instantiate SyntheticPortal in local_real_services/staging/production."""
    if settings().mock_portal_allowed:
        return run_synthetic_layer(db, version_row, hostname=hostname)
    return run_live_structural_layer(db, version_row, observer)


def layers_passed(db, version_row) -> set[str]:
    runs = db.execute(select(fm.AdapterTestRun).where(
        fm.AdapterTestRun.candidate_version_id == version_row.id,
        fm.AdapterTestRun.passed.is_(True))).scalars().all()
    return {r.classification for r in runs}
