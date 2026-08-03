import os
import shutil
import sys
import tempfile

# Fresh SQLite file per test session; import app AFTER setting DATABASE_URL.
_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["ELLIS_VAULT_PASSPHRASE"] = "test-passphrase"
# Tests must NEVER write into the repo's data/ snapshots (research/export
# stages rewrite JSONL files): point ELLIS_DATA_DIR at a session-scoped COPY.
_REPO_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data")
_DATA_COPY = tempfile.mkdtemp(suffix="-ellis-data")
shutil.copytree(_REPO_DATA, _DATA_COPY, dirs_exist_ok=True)
os.environ["ELLIS_DATA_DIR"] = _DATA_COPY
# The per-host recon cooldown protects REAL government sites from build
# iteration. These "portals" are in-memory fixtures on a shared session DB, so
# the cooldown would only ever rate-limit one test against the next. The
# behaviour itself is exercised deliberately in test_recon_rate_limit.py.
os.environ["ELLIS_RECON_HOST_QUIET_SECONDS"] = "0"
# Hermetic tests: force local fallbacks regardless of any real .env present, so
# unit/e2e behavior is deterministic. Live-provider smoke tests are separate.
for _k in ("MOONSHOT_API_KEY", "GOOGLE_CLOUD_PROJECT", "GOOGLE_DOCUMENT_AI_OCR_PROCESSOR_ID",
           "BROWSERBASE_API_KEY", "STRIPE_SECRET_KEY", "CLERK_SECRET_KEY", "TEMPORAL_HOST"):
    os.environ[_k] = ""

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from app.db import create_all, SessionLocal  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    create_all()
    yield


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


AUTH = {"Authorization": "Bearer dev-token", "X-Org-Id": "org1", "X-User-Id": "user1"}
AUTH2 = {"Authorization": "Bearer dev-token", "X-Org-Id": "org2", "X-User-Id": "user2"}

# A valid ICAO 9303 TD3 specimen (check digits correct).
PASSPORT_MRZ = (
    "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\n"
    "L898902C36UTO7408122F1204159ZE184226B<<<<<10"
)
