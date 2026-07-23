"""LIVE Temporal cluster worker-restart durability drill.

This is NOT the in-process time-skipping test (that's test_temporal.py). This
drives a workflow against the REAL Temporal cluster in docker-compose and
physically STOPS + RESTARTS the worker CONTAINER mid-case to prove:

  1. a case reaches a human-action state,
  2. the workflow stays durable while the worker container is DOWN
     (Temporal describe → RUNNING with no worker),
  3. the restarted worker resumes the case via the proper signal,
  4. the case completes, and
  5. payment / booking / submission each happen EXACTLY ONCE across the
     restarts (counted from the DB-persisted portal side-effect ledger), and
  6. reconciliation short-circuits a repeated payment (uncertain-outcome path).

Gated behind LIVE_TEMPORAL=1 and a running stack:
    cd backend && docker compose up -d
    LIVE_TEMPORAL=1 .venv/bin/python -m pytest tests/test_live_temporal_restart.py -s
Normal `pytest` skips it (keeps the hermetic suite Docker-free).
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import uuid

import pytest

RUN = os.getenv("LIVE_TEMPORAL") == "1"
pytestmark = pytest.mark.skipif(not RUN, reason="set LIVE_TEMPORAL=1 with docker compose up")

TEMPORAL_ADDR = os.getenv("TEMPORAL_ADDR", "localhost:7233")
PG_DSN = os.getenv("ELLIS_PG_DSN", "postgresql://ellis:ellis@localhost:5432/ellis")
COMPOSE_DIR = os.path.join(os.path.dirname(__file__), "..")

STATE_ORDER = [
    "DRAFT", "APPLICANT_REVIEW_REQUIRED", "AUTHORIZATION_PENDING",
    "PORTAL_ACCOUNT_CREATING", "CAPTCHA_ACTION_REQUIRED", "PORTAL_VERIFICATION_REQUIRED",
    "PORTAL_LOGIN_REQUIRED", "PAYMENT_APPROVAL_REQUIRED", "PAYMENT_ACTION_REQUIRED",
    "PAYMENT_PROCESSING", "APPOINTMENT_BOOKING", "PERSONAL_DECLARATION_REQUIRED",
    "SUBMITTING", "COMPLETED",
]


def _compose(*args: str) -> None:
    subprocess.run(["docker", "compose", *args], cwd=COMPOSE_DIR, check=True,
                   capture_output=True, text=True)


def _portal_state(case_id: str) -> dict | None:
    import psycopg
    with psycopg.connect(PG_DSN) as conn:
        row = conn.execute("select state from portal_states where case_id = %s", (case_id,)).fetchone()
    return row[0] if row else None


async def _wait_state(handle, wf_state_fn, target: str, timeout: float = 30.0) -> str:
    """Poll the workflow query until it reaches (or passes) target."""
    want = STATE_ORDER.index(target)
    deadline = asyncio.get_event_loop().time() + timeout
    last = "?"
    while asyncio.get_event_loop().time() < deadline:
        try:
            last = await handle.query(wf_state_fn)
            if STATE_ORDER.index(last) >= want:
                return last
        except Exception:
            pass
        await asyncio.sleep(0.4)
    raise AssertionError(f"workflow did not reach {target} (last={last})")


@pytest.mark.asyncio
async def test_live_worker_restart_durability_no_duplicates():
    from temporalio.client import Client
    from temporalio.client import WorkflowExecutionStatus
    from app.temporal_app import VisaProcessingWorkflow

    client = await Client.connect(TEMPORAL_ADDR)
    case_id = "live-" + uuid.uuid4().hex[:8]
    wf_id = "wf-" + case_id
    state_q = VisaProcessingWorkflow.state

    print(f"\n[drill] starting workflow {wf_id} on live cluster {TEMPORAL_ADDR}")
    handle = await client.start_workflow(
        VisaProcessingWorkflow.run,
        {"case_id": case_id, "email": f"{case_id}@example.com",
         "password": "LongPassword123!", "answers": {"full_name": "Applicant"}},
        id=wf_id, task_queue="visa")

    # 1) drive to the first human-action state
    await _wait_state(handle, state_q, "APPLICANT_REVIEW_REQUIRED")
    await handle.signal(VisaProcessingWorkflow.approve_review)
    await handle.signal(VisaProcessingWorkflow.sign_authorization)
    await _wait_state(handle, state_q, "CAPTCHA_ACTION_REQUIRED")
    await handle.signal(VisaProcessingWorkflow.solve_captcha)
    await _wait_state(handle, state_q, "PORTAL_VERIFICATION_REQUIRED")

    # applicant reads the verification token (from the persisted portal outbox)
    st = _portal_state(case_id)
    assert st, "portal state should be persisted in Postgres"
    token = next(e["link"].split("token=")[1] for e in reversed(st["emails"])
                 if e["kind"] == "verification")
    await handle.signal(VisaProcessingWorkflow.verify_email, token)

    # 2) reach PAYMENT_APPROVAL_REQUIRED — a human-action state — then KILL worker
    reached = await _wait_state(handle, state_q, "PAYMENT_APPROVAL_REQUIRED")
    print(f"[drill] reached human-action state: {reached}; stopping worker container")
    _compose("stop", "worker")

    # 3) workflow stays durable with NO worker running (ask Temporal directly)
    await asyncio.sleep(2)
    desc = await handle.describe()
    print(f"[drill] worker DOWN — Temporal reports status={desc.status.name}")
    assert desc.status == WorkflowExecutionStatus.RUNNING, "workflow must survive worker loss"

    # 4) restart the worker and resume via signals
    print("[drill] restarting worker container")
    _compose("start", "worker")
    await asyncio.sleep(3)
    await handle.signal(VisaProcessingWorkflow.approve_payment)
    await handle.signal(VisaProcessingWorkflow.complete_payment)

    # 5) second restart AFTER pay+book, while parked on the declaration signal
    await _wait_state(handle, state_q, "PERSONAL_DECLARATION_REQUIRED", timeout=45)
    print("[drill] pay+book done; stopping worker again before declaration")
    _compose("stop", "worker")
    await asyncio.sleep(2)
    desc = await handle.describe()
    assert desc.status == WorkflowExecutionStatus.RUNNING
    _compose("start", "worker")
    await asyncio.sleep(3)
    await handle.signal(VisaProcessingWorkflow.complete_declaration)

    # 6) case completes
    result = await asyncio.wait_for(handle.result(), timeout=60)
    print(f"[drill] workflow result: {result['state']}")
    assert result["state"] == "COMPLETED"
    assert result["confirmation"]["referenceNo"].startswith("REF-")
    assert result["receipt"] and result["appointment"]

    # 7) EXACTLY ONE of each side effect, counted from the durable portal ledger
    st = _portal_state(case_id)
    log = st.get("log", [])
    charges = [e for e in log if e["event"] == "charge"]
    books = [e for e in log if e["event"] == "book"]
    submits = [e for e in log if e["event"] == "submit"]
    print(f"[drill] side-effect ledger: charges={len(charges)} books={len(books)} submits={len(submits)}")
    assert len(charges) == 1, f"payment must not duplicate across restart, got {len(charges)}"
    assert len(books) == 1, f"booking must not duplicate across restart, got {len(books)}"
    assert len(submits) == 1, f"submission must not duplicate across restart, got {len(submits)}"

    # 8) reconciliation short-circuits a repeat payment (uncertain-outcome path):
    #    replay the pay activity's reconcile op against the persisted portal — it
    #    must return reconciled=True and add NO second charge.
    from app.portal.mock_portal import MockPortal
    from app.temporal_app import _pay_op
    portal = MockPortal.from_state(st)
    app_id = result["application_id"]
    sess = next(iter(portal.sessions))
    again = _pay_op(case_id, sess, app_id)(portal)
    assert again.get("reconciled") is True, "repeat pay must reconcile, not re-charge"
    assert len([e for e in portal.log if e["event"] == "charge"]) == 1
    print("[drill] reconciliation verified: repeat payment short-circuited. PASS")
