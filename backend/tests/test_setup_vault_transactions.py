"""Credentials and their tenant references commit in one real SQLite transaction."""
import json
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app import models, setup, vault
from app.db import Base

VALUE = "dummy-private-value-for-transaction-test"
MAIL = "dummy-private-email-value-for-transaction-test"


@pytest.fixture
def h(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'credentials.db'}", connect_args={"timeout": .05})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = sessions()
    opens = []
    def independent():
        opens.append(True)
        return sessions()
    monkeypatch.setattr(vault, "_db_session", independent)
    monkeypatch.setattr(vault, "_BACKENDS", {})
    monkeypatch.setattr(vault, "_DB_SEEN", set())
    state = SimpleNamespace(db=db, sessions=sessions, engine=engine, opens=opens)
    yield state
    db.close()
    engine.dispose()


def payload():
    return {"tenant_name": "Test tenant", "admin_email": "admin@example.invalid",
        "kimi_api_key": VALUE, "email_credential": MAIL,
        "email": {"provider": "smtp", "sender": "sender@example.invalid",
                  "host": "smtp.example.invalid", "username": "test-user"}}


def saved(h):
    return setup.save_setup(h.db, org_id="new-tenant", actor="fixture", payload=payload())


def test_new_setup_persists_both_secrets_without_a_second_sqlite_writer(h):
    started = time.monotonic()
    result = saved(h)
    assert time.monotonic() - started < 2
    assert result["setup_complete"] is True and h.opens == []
    assert vault._BACKENDS == {}  # shared writes never expose uncommitted cache entries
    assert VALUE not in json.dumps(result) and MAIL not in json.dumps(result)
    with h.sessions() as reader:
        row = reader.get(models.TenantSetup, "new-tenant")
        refs = dict(row.credential_refs)
        secrets = list(reader.scalars(select(models.VaultSecret)))
        assert len(secrets) == 2
        assert all(VALUE not in item.ciphertext and MAIL not in item.ciphertext for item in secrets)
    vault._BACKENDS.clear(); vault._DB_SEEN.clear()
    assert vault.reveal(refs["kimi_api_key"]) == VALUE
    assert vault.reveal(refs["email_credential"]) == MAIL


def test_uncommitted_shared_secret_is_neither_visible_nor_cached(h):
    setup._row(h.db, "not-committed")
    ref = vault.store(VALUE, db=h.db)["ref"]
    assert ref not in vault._BACKENDS
    with h.sessions() as other:
        assert other.get(models.VaultSecret, ref) is None
    h.db.rollback()
    with pytest.raises(KeyError):
        vault.reveal(ref)


def test_second_secret_write_failure_rolls_back_every_setup_change(h):
    inserts = []
    def fail_second(conn, cursor, statement, params, context, executemany):
        if statement.startswith("INSERT INTO vault_secrets"):
            inserts.append(True)
            if len(inserts) == 2:
                raise RuntimeError(VALUE)
    event.listen(h.engine, "before_cursor_execute", fail_second)
    with pytest.raises(vault.VaultPersistenceError) as failed:
        saved(h)
    assert VALUE not in str(failed.value)
    assert len(inserts) == 2 and not vault._BACKENDS
    with h.sessions() as reader:
        assert reader.get(models.TenantSetup, "new-tenant") is None
        assert list(reader.scalars(select(models.VaultSecret))) == []


def test_commit_failure_does_not_report_setup_success_or_cache_credentials(h, monkeypatch):
    monkeypatch.setattr(h.db, "commit", lambda: (_ for _ in ()).throw(RuntimeError(VALUE)))
    with pytest.raises(vault.VaultPersistenceError) as failed:
        saved(h)
    assert VALUE not in str(failed.value) and not vault._BACKENDS
    with h.sessions() as reader:
        assert reader.get(models.TenantSetup, "new-tenant") is None
        assert list(reader.scalars(select(models.VaultSecret))) == []


@pytest.mark.parametrize("action,sql", [("rotate", "UPDATE vault_secrets"), ("revoke", "DELETE FROM vault_secrets")])
def test_failed_rotation_or_revocation_preserves_prior_durable_credential(h, action, sql):
    saved(h)
    before = h.db.get(models.TenantSetup, "new-tenant")
    ref, fingerprint = before.credential_refs["kimi_api_key"], before.credential_fingerprints["kimi_api_key"]
    assert vault.reveal(ref) == VALUE
    def fail(conn, cursor, statement, params, context, executemany):
        if statement.startswith(sql):
            raise RuntimeError(MAIL)
    event.listen(h.engine, "before_cursor_execute", fail)
    with pytest.raises(vault.VaultPersistenceError) as failed:
        if action == "rotate":
            setup.rotate_component(h.db, org_id="new-tenant", component="kimi_api_key", new_value=MAIL, actor="fixture")
        else:
            setup.revoke_component(h.db, org_id="new-tenant", component="kimi_api_key", actor="fixture")
    assert MAIL not in str(failed.value)
    with h.sessions() as reader:
        row = reader.get(models.TenantSetup, "new-tenant")
        assert row.credential_refs["kimi_api_key"] == ref
        assert row.credential_fingerprints["kimi_api_key"] == fingerprint
    vault._BACKENDS.clear()
    assert vault.reveal(ref) == VALUE


def test_rotation_and_revocation_remain_durable_after_cache_loss(h):
    saved(h)
    result = setup.rotate_component(h.db, org_id="new-tenant", component="kimi_api_key", new_value=MAIL, actor="fixture")
    assert result["rotated"] is True
    ref = h.db.get(models.TenantSetup, "new-tenant").credential_refs["kimi_api_key"]
    vault._BACKENDS.clear(); vault._DB_SEEN.clear()
    assert vault.reveal(ref) == MAIL
    assert setup.revoke_component(h.db, org_id="new-tenant", component="kimi_api_key", actor="fixture")["revoked"] is True
    with pytest.raises(KeyError):
        vault.reveal(ref)
    with h.sessions() as reader:
        assert reader.get(models.VaultSecret, ref) is None
        assert "kimi_api_key" not in reader.get(models.TenantSetup, "new-tenant").credential_refs


def test_shared_durable_secret_cannot_revive_after_another_process_deletes_it(h):
    saved(h)
    ref = h.db.get(models.TenantSetup, "new-tenant").credential_refs["kimi_api_key"]
    vault._DB_SEEN.clear()
    assert vault.reveal(ref) == VALUE
    with h.sessions() as other:
        other.delete(other.get(models.VaultSecret, ref)); other.commit()
    with pytest.raises(KeyError):
        vault.reveal(ref)


def test_standalone_rotation_cannot_resurrect_an_externally_revoked_cached_ref(h):
    ref = vault.store(VALUE)["ref"]
    assert ref in vault._BACKENDS
    with h.sessions() as other:
        other.delete(other.get(models.VaultSecret, ref)); other.commit()
    with pytest.raises(KeyError):
        vault.rotate(ref, MAIL)
    assert ref not in vault._BACKENDS
    with h.sessions() as other:
        assert other.get(models.VaultSecret, ref) is None


def test_standalone_vault_callers_still_persist_without_session_argument(h):
    ref = vault.store(VALUE)["ref"]
    assert len(h.opens) == 1
    vault._BACKENDS.clear()
    assert vault.reveal(ref) == VALUE
    vault.rotate(ref, MAIL)
    assert vault.reveal(ref) == MAIL
    assert vault.destroy(ref) is True
    with pytest.raises(KeyError):
        vault.reveal(ref)


@pytest.mark.parametrize("action,sql", [("store", "INSERT INTO vault_secrets"),
    ("rotate", "UPDATE vault_secrets"), ("destroy", "DELETE FROM vault_secrets")])
def test_standalone_vault_mutations_never_claim_success_after_database_failure(h, action, sql):
    ref = vault.store(VALUE)["ref"]
    cache_before = dict(vault._BACKENDS)
    def fail(conn, cursor, statement, params, context, executemany):
        if statement.startswith(sql):
            raise RuntimeError(MAIL)
    event.listen(h.engine, "before_cursor_execute", fail)
    with pytest.raises(vault.VaultPersistenceError) as failed:
        if action == "store":
            vault.store(MAIL)
        elif action == "rotate":
            vault.rotate(ref, MAIL)
        else:
            vault.destroy(ref)
    assert MAIL not in str(failed.value)
    assert vault._BACKENDS == cache_before
    assert vault.reveal(ref) == VALUE
