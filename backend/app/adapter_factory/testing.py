"""Layered adapter testing (brief §26) with TRUTHFUL classifications.

Layer 1 static  → STATIC_VALIDATED       (static_validator, no execution)
Layer 3 contract→ CONTRACT_TESTED        (flow vs recorded sanitized structures)
Layer 2 synthetic→ SYNTHETIC_TESTED      (full drive of synthetic portals with
                                          simulated applicant handoffs and a
                                          side-effect ledger audit)

Live layers (public navigation, authenticated, official sandbox, controlled
production) are NOT run here — they require authorized real access and are
recorded only when actually executed. Nothing in this module can claim them.
"""
from __future__ import annotations

from sqlalchemy import select

from ..portal.synthetic import SyntheticPortal
from . import models as fm
from .compiler import compile_flow
from .static_validator import validate_candidate


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
                                      "CHECK", "READ_TEXT", "READ_FEE") \
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
        simulate_applicant(portal, result.get("handoff_kind", ""))
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
            "email": "t@example.com"}


def run_synthetic_layer(db, version_row, *, scenarios=None,
                        hostname: str = "portal.gov.example") -> fm.AdapterTestRun:
    """§26 Layer 2: the synthetic corpus. A scenario passes only when the flow
    terminates safely AND the ledger shows no duplicate side effects AND no
    misleading banner was believed."""
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


def layers_passed(db, version_row) -> set[str]:
    runs = db.execute(select(fm.AdapterTestRun).where(
        fm.AdapterTestRun.candidate_version_id == version_row.id,
        fm.AdapterTestRun.passed.is_(True))).scalars().all()
    return {r.classification for r in runs}
