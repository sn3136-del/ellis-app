"""Temporal worker entry point for real deployment.

Runs against TEMPORAL_HOST (e.g. temporal:7233 in docker compose). Registers the
VisaProcessingWorkflow + activities on the 'visa' task queue. In-process unit
tests use temporalio's time-skipping test server instead (see tests/test_temporal.py).

Activation: set TEMPORAL_HOST and start this process:
    python -m app.temporal_worker
"""
from __future__ import annotations

import asyncio

from .config import settings


def _wire_portal_store():
    """Wire the portal store for the current runtime mode.

    Mock-allowed modes get the DB-backed MockPortal store (restart-durability
    demos). Real-only modes REFUSE to start: there is no live portal store yet
    and the worker must never run cases against a mock there (fail closed)."""
    from .config import settings
    from .temporal_app import set_portal_store
    s = settings()
    if not s.mock_portal_allowed:
        raise SystemExit(
            f"temporal worker refusing to start: runtime mode '{s.runtime_mode}' "
            "is real-only and no production-approved live portal store exists. "
            "MockPortal-backed DbPortalStore is prohibited outside test/local_mock_demo.")
    from .portal_store import DbPortalStore
    set_portal_store(DbPortalStore())


async def _main():  # pragma: no cover - requires a running Temporal server
    from temporalio.client import Client
    from temporalio.worker import Worker
    from .temporal_app import VisaProcessingWorkflow, ALL_ACTIVITIES

    # Durable, DB-backed portal so reconciliation survives a worker restart
    # (mode-gated: refuses to start in real-only modes).
    _wire_portal_store()

    host = settings().temporal_host or "localhost:7233"
    # Resilient connect — the Temporal frontend may still be warming up even
    # after its container reports healthy; retry a few times before giving up.
    client = None
    last_err = None
    for attempt in range(30):
        try:
            client = await Client.connect(host)
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"Temporal not ready ({attempt+1}/30): {e}; retrying...", flush=True)
            await asyncio.sleep(2)
    if client is None:
        raise RuntimeError(f"could not connect to Temporal at {host}: {last_err}")
    worker = Worker(client, task_queue="visa", workflows=[VisaProcessingWorkflow],
                    activities=ALL_ACTIVITIES)
    print(f"Temporal worker connected to {host}, task queue 'visa' (DB portal store active)", flush=True)
    await worker.run()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_main())
