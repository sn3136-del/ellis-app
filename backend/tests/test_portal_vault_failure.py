"""A failed durable token revocation cannot run or strand a portal job."""
import json

import pytest
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.orm import sessionmaker

from app import models, portal_queue, service, vault
from app.db import Base


@pytest.mark.parametrize("reference_field,value", [
    ("token_ref", "dummy-one-time-secret"),
    ("card_ref", '{"number":"dummy-private-card"}'),
])
@pytest.mark.parametrize("new_claim_during_failure", [False, True])
def test_revocation_failure_marks_owned_run_retryable_without_portal_action(
        tmp_path, monkeypatch, reference_field, value, new_claim_during_failure):
    engine = create_engine(f"sqlite:///{tmp_path / 'queue.db'}", connect_args={"timeout": .05})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(portal_queue, "SessionLocal", sessions)
    monkeypatch.setattr(vault, "_db_session", sessions)
    monkeypatch.setattr(vault, "_BACKENDS", {})
    monkeypatch.setattr(vault, "_DB_SEEN", set())
    ref = vault.store(value)["ref"]
    with sessions() as db:
        run = models.PortalRun(org_id="fixture", application_id="fixture-case",
                               status="running", claimed_by="fixture-worker",
                               signal_name="verify_email", signal_kwargs={reference_field: ref})
        db.add(run)
        db.commit()
        run_id = run.id

    raced = []
    def unavailable(conn, cursor, statement, params, context, executemany):
        if statement.startswith("DELETE FROM vault_secrets"):
            raise RuntimeError(value)
        if (new_claim_during_failure and not raced
                and statement.startswith("UPDATE portal_runs")):
            raced.append(True)
            # A real second writer takes ownership immediately before the
            # stale worker's failure UPDATE reaches SQLite.
            with sessions() as other:
                other.execute(update(models.PortalRun).where(models.PortalRun.id == run_id)
                              .values(claimed_by="new-worker"))
                other.commit()
    event.listen(engine, "before_cursor_execute", unavailable)
    monkeypatch.setattr(service, "signal", lambda *a, **kw: pytest.fail("portal action after failed revocation"))
    try:
        portal_queue.execute_run(run_id, "fixture-worker")
        with sessions() as db:
            run = db.get(models.PortalRun, run_id)
            assert value not in json.dumps({"error": run.error, "kwargs": run.signal_kwargs})
            progress = list(db.scalars(select(models.CaseProgressEvent)))
            if new_claim_during_failure:
                assert raced and run.status == "running" and run.claimed_by == "new-worker"
                assert run.finished_at is None and not run.error and progress == []
            else:
                assert run.status == "failed" and run.finished_at is not None
                assert run.current_step_key == "recoverable_failure"
                assert "retry" in run.error.lower()
                assert [(p.step_key, p.status) for p in progress] == [("recoverable_failure", "failed")]
        assert vault.reveal(ref) == value  # failed deletion preserves the durable secret
    finally:
        engine.dispose()
