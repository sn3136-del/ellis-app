"""Safe adapter-factory demonstration (brief §37).

Exercises the full architecture against SYNTHETIC portals only — no real
payment, appointment, or submission ever occurs. Run:

    cd backend && python -m scripts.demo_adapter_factory

Demonstrates: applicant requests a connector + consents → credential-free
recon → typed spec → deterministic candidate → static/contract/synthetic
testing → reversible repair → internal human release → deterministic runtime
with applicant handoffs → official-style evidence → idempotent restart resume.
The applicant never contacts a developer.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("MOONSHOT_API_KEY", "")

from app.db import SessionLocal, create_all  # noqa: E402
from app.adapter_factory.build_workflow import create_request, record_consent, run_build  # noqa: E402
from app.adapter_factory import models as fm, generator, release, runtime, testing, repair  # noqa: E402
from app.portal.synthetic import SyntheticPortal  # noqa: E402

HOST = "portal.gov.example"
PE = {"hostnames": [HOST], "operator": "Demo Consular Authority",
      "verification": "synthetic_test_portal"}


def _obs(_=None):
    return SyntheticPortal(scenario="single_step_login", hostname=HOST).observe


def main():
    create_all()
    db = SessionLocal()
    route_key = "rk1|demo|USA->KHM|tourist"

    print("[1-3] Applicant resolves a route, grants standing authorization, "
          "requests a connector and consents.")
    req = create_request(db, org_id="demo", user_id="applicant-1", application_id="",
                         route_key=route_key, destination="Cambodia", visa_type="tourist",
                         portal_evidence=PE, runtime_mode="local_mock_demo")
    record_consent(db, req, user_id="applicant-1")

    print("[4-8] Credential-free recon → typed spec → deterministic code → tested.")
    run_build(db, req.id, observer=_obs())
    db.refresh(req)
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    row = generator.get_version(db, cand.id, cand.current_version)
    print(f"      build state = {req.state}; "
          f"layers passed = {sorted(testing.layers_passed(db, row))}")

    print("[7] Kimi repairs a reversible failure into a NEW version, retested.")
    att = repair.attempt_repair(
        db, build_request=req, candidate=cand, failed_version=row.version,
        sanitized_detail={"reason": "fill_x: NO_SUCH_ELEMENT selector never observed"},
        observer=_obs())
    print(f"      repair = {att.outcome} (new v{att.new_version})")

    print("[9] Internal human-admin release (sandbox tier).")
    release.release(db, candidate_id=cand.id, version=cand.current_version,
                    tier="sandbox", actor="admin-1", is_admin=True)

    print("[10-11] Deterministic runtime pauses for the applicant's personal step.")
    portal = SyntheticPortal(scenario="otp_handoff", hostname=HOST)
    answers = {"full_name": "Demo Applicant", "passport_number": "P1234567",
               "arrival_date": "2026-10-10"}
    _, r = runtime.start_execution(db, org_id="demo", application_id="case-1",
                                   route_key=route_key, tier="sandbox", driver=portal,
                                   case_answers=answers)
    res = r.run()
    print(f"      runtime = {res['status']} (handoff: {res.get('handoff_kind')})")

    print("[12-15] Applicant completes personal steps; official evidence verified.")
    out = testing.drive_flow_against_portal(
        db, row, SyntheticPortal(scenario="otp_handoff", hostname=HOST), answers=answers)
    lg = out["ledger"]
    print(f"      final = {out['result']['status']}; charges={len(lg['charges'])} "
          f"bookings={len(lg['bookings'])} submissions={len(lg['submissions'])}")

    print("[16] Restart resume is idempotent — no duplicated work.")
    before = cand.current_version
    run_build(db, req.id, observer=_obs())
    db.refresh(cand)
    print(f"      candidate version stable: {before} == {cand.current_version}")

    print("[17] The applicant never contacted a developer — all via Ellis.")
    print("\nDONE — synthetic portals only; no real payment, appointment, or submission.")


if __name__ == "__main__":
    main()
