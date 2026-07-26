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

from sqlalchemy import select as _select


def current_browser_session(db, application_id: str):
    """THE case's live portal session — one definition shared by the driver
    and the applicant's secure window. They used to disagree (an unordered
    'first open row' vs 'newest open row'), so the applicant watched one
    session while Ellis drove another and the window stayed blank forever."""
    from . import models
    return db.execute(_select(models.BrowserSession).where(
        models.BrowserSession.application_id == application_id,
        models.BrowserSession.status == "open").order_by(
        models.BrowserSession.created_at.desc(),
        models.BrowserSession.id.desc())).scalars().first()


def retire_other_sessions(db, application_id: str, keep_id: str) -> int:
    """At most ONE open session per case: close every other open row (and
    release it at the provider) so nothing can drift onto a stale window."""
    from . import models
    from .providers import browser as bb
    n = 0
    for row in db.execute(_select(models.BrowserSession).where(
            models.BrowserSession.application_id == application_id,
            models.BrowserSession.status == "open")).scalars().all():
        if row.id == keep_id:
            continue
        try:
            bb.close_session(row.provider_session_id)
        except Exception:  # noqa: BLE001 — best effort at the provider
            pass
        row.status = "closed"
        n += 1
    if n:
        db.commit()
    return n

from .db import SessionLocal, create_all
from . import models
from .portal.mock_portal import MockPortal


class DbPortalStore:
    """Loads/saves a per-case MockPortal snapshot in the portal_states table.

    Mock-only component: constructing it in a real-only runtime mode is a
    programming error and refuses immediately (brief section 3 — no code path
    may bind a case to MockPortal outside test/local_mock_demo)."""

    def __init__(self, ensure_schema: bool = True):
        from .config import settings
        s = settings()
        if not s.mock_portal_allowed:
            raise RuntimeError(
                f"DbPortalStore is MockPortal-backed and prohibited in runtime "
                f"mode '{s.runtime_mode}'")
        if ensure_schema:
            create_all()

    def load(self, case_id: str) -> MockPortal:
        db = SessionLocal()
        try:
            row = db.get(models.PortalState, case_id)
            if row and row.state:
                return MockPortal.from_state(row.state)
            # First activity for a NEW case starts with a fresh portal; the
            # temporal workflow only reaches here for cases it was started
            # with, so this is initialization, not silent fabrication.
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
