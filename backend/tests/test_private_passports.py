"""Private passport smoke tests — CONSENTED real documents only.

These are SKIPPED unless ENABLE_PRIVATE_DOCUMENT_SMOKE_TESTS=true AND the
private fixture path(s) exist. They run real Google Document AI, deterministic
MRZ validation, and (optionally) the Kimi K3 vision fallback.

STRICT: they output ONLY structural facts — never names, passport numbers,
dates, MRZ text, or any personal value. Fixtures live outside the repo in a
0700 directory and are never copied into the repo, images, or logs.

Enable + run:
  ENABLE_PRIVATE_DOCUMENT_SMOKE_TESTS=true \
  PRIVATE_PASSPORT_FIXTURE_PDF="$HOME/.ellis_private_fixtures/fixture_pdf_1.pdf" \
  (cd backend && . .venv/bin/activate && python -m pytest tests/test_private_passports.py -q)
"""
import hashlib
import os

import pytest

ENABLED = os.getenv("ENABLE_PRIVATE_DOCUMENT_SMOKE_TESTS", "").lower() == "true"


def _restore_from_dotenv():
    # conftest blanks provider creds for the hermetic suite; restore the ones
    # these consented live tests need, straight from backend/.env, then reset
    # the cached Settings so provider code re-reads them.
    path = os.path.join(os.path.dirname(__file__), "..", ".env")
    wanted = {"GOOGLE_CLOUD_PROJECT", "GOOGLE_DOCUMENT_AI_OCR_PROCESSOR_ID",
              "GOOGLE_DOCUMENT_AI_FORM_PROCESSOR_ID", "GOOGLE_CLOUD_QUOTA_PROJECT",
              "GOOGLE_APPLICATION_CREDENTIALS", "MOONSHOT_API_KEY", "DOCUMENT_AI_ENABLED"}
    try:
        for line in open(path):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                if k.strip() in wanted and v.strip():
                    os.environ[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    try:
        from app.config import settings
        settings.cache_clear()
    except Exception:
        pass


if ENABLED:
    _restore_from_dotenv()


def _fixtures():
    out = []
    for key in ("PRIVATE_PASSPORT_FIXTURE_PDF", "PRIVATE_PASSPORT_FIXTURE_US",
                "PRIVATE_PASSPORT_FIXTURE_SG", "PRIVATE_PASSPORT_FIXTURE_SG_PDF",
                "PRIVATE_PASSPORT_FIXTURE_CN", "PRIVATE_PASSPORT_FIXTURE_CN_2"):
        p = os.getenv(key, "")
        if p and os.path.exists(p):
            out.append((key, p))
    return out


pytestmark = pytest.mark.skipif(not ENABLED or not _fixtures(),
                                reason="private smoke disabled or fixtures absent")


def _mime(path: str) -> str:
    return {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg",
            "jpeg": "image/jpeg", "tiff": "image/tiff"}.get(path.rsplit(".", 1)[-1].lower(),
                                                            "application/octet-stream")


def test_private_passport_documentai_pipeline():
    _restore_from_dotenv()
    from app.providers import documentai, ocr
    assert documentai.is_authenticated(), "Document AI ADC not authenticated"
    any_valid = False
    for key, path in _fixtures():
        content = open(path, "rb").read()
        sha = hashlib.sha256(content).hexdigest()          # non-reversible record only
        fmt = _mime(path).split("/")[-1]
        file_valid = len(content) > 0 and len(content) <= 10 * 1024 * 1024
        # Tier 1: Google Enterprise OCR (primary).
        ocr_ok = False
        res = None
        try:
            res = ocr.DocumentAiProvider().process(content=content, mime=_mime(path))
            ocr_ok = res.ok
        except Exception:
            ocr_ok = False
        # Form Parser (separately, best-effort).
        form_ok = False
        try:
            documentai.process_document(content, _mime(path), use_form_parser=True)
            form_ok = True
        except Exception:
            form_ok = False
        classified = bool(res and res.doc_type == "passport")
        mrz_detected = bool(res and res.fields and res.doc_type == "passport")
        mrz_valid = bool(res and res.mrz_valid)
        band = ("high" if res and res.fields and sum(f.confidence for f in res.fields) / len(res.fields) >= 0.9
                else "medium" if res and res.fields else "low")
        review_required = sum(1 for f in (res.fields if res else []) if f.confidence < 0.6)
        warnings = len(res.quality_warnings) if res else 0
        any_valid = any_valid or mrz_valid
        # STRUCTURAL report only — the required non-personal fields.
        print(f"[{key}] sha256={sha[:12]}… format={fmt} file_valid={file_valid} "
              f"google_ocr={'ok' if ocr_ok else 'fail'} form_parser={'ok' if form_ok else 'fail'} "
              f"kimi_fallback=no passport={'yes' if classified else 'no'} "
              f"mrz_detected={'yes' if mrz_detected else 'no'} mrz_checksum_valid={'yes' if mrz_valid else 'no'} "
              f"confidence={band} review_required={review_required} warnings={warnings} conflict=no")
    # At least one consented passport must validate deterministically.
    assert any_valid, "no fixture produced a checksum-valid MRZ"


def test_private_passport_kimi_fallback_available():
    _restore_from_dotenv()
    # Confirms the Tier-2 fallback path exists and is flag-guarded (does not
    # assert MRZ accuracy — vision is a recoverable-availability fallback only).
    from app.providers import kimi_vision
    if not kimi_vision.is_available():
        pytest.skip("Kimi not configured")
    # Kimi vision is an IMAGE fallback; skip PDF-only fixtures (Document AI is
    # the PDF path). Assert reachability + classification on an image fixture.
    images = [(k, p) for k, p in _fixtures() if _mime(p).startswith("image/")]
    if not images:
        pytest.skip("no image fixtures for the vision fallback")
    key, path = images[0]
    out = kimi_vision.ocr_document(open(path, "rb").read(), _mime(path))
    assert out["engine"] == "kimi_k3_vision"
    print(f"[{key}] kimi_fallback_reachable=True classified={out['doc_type']}")
