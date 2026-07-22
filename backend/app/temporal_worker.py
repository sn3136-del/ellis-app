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


async def _main():  # pragma: no cover - requires a running Temporal server
    from temporalio.client import Client
    from temporalio.worker import Worker
    from .temporal_app import VisaProcessingWorkflow, ALL_ACTIVITIES

    host = settings().temporal_host or "localhost:7233"
    client = await Client.connect(host)
    worker = Worker(client, task_queue="visa", workflows=[VisaProcessingWorkflow],
                    activities=ALL_ACTIVITIES)
    print(f"Temporal worker connected to {host}, task queue 'visa'")
    await worker.run()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_main())
