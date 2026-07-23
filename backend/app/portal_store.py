"""DB-backed portal store for the real-Temporal worker.

Temporal makes the *workflow* durable across worker restarts; this store makes
the mock portal's own state durable too, so reconciliation stays correct after a
worker is killed and restarted mid-case. Each activity loads the portal, runs one
operation, and saves it (see app.temporal_app._run_op).

In production the portal is the real external government portal (persistent on
its own server); this store is the mock's stand-in for that external durability.
Activities go through here, never hold portal state in worker memory across a
restart.
"""
from __future__ import annotations

from .db import SessionLocal, create_all
from . import models
from .portal.mock_portal import MockPortal


class DbPortalStore:
    """Loads/saves a per-case MockPortal snapshot in the portal_states table."""

    def __init__(self, ensure_schema: bool = True):
        if ensure_schema:
            create_all()

    def load(self, case_id: str) -> MockPortal:
        db = SessionLocal()
        try:
            row = db.get(models.PortalState, case_id)
            if row and row.state:
                return MockPortal.from_state(row.state)
            return MockPortal()
        finally:
            db.close()

    def save(self, case_id: str, portal: MockPortal) -> None:
        db = SessionLocal()
        try:
            row = db.get(models.PortalState, case_id)
            if not row:
                row = models.PortalState(case_id=case_id, state=portal.to_state())
                db.add(row)
            else:
                # Reassign so SQLAlchemy detects the JSON change.
                row.state = portal.to_state()
            db.commit()
        finally:
            db.close()

    def snapshot(self, case_id: str) -> dict | None:
        """Read-only snapshot for assertions/monitoring (no MockPortal build)."""
        db = SessionLocal()
        try:
            row = db.get(models.PortalState, case_id)
            return dict(row.state) if row and row.state else None
        finally:
            db.close()
