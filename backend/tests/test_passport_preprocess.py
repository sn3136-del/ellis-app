"""Passport upload preprocessing: EXIF orientation, automatic rotation retry,
multipage-PDF biodata-page selection, and readability diagnostics.

The OCR provider hierarchy is faked at the _run_tiers seam so these tests are
hermetic and deterministic — the preprocessing decisions (when to rotate, when
to split pages, what to record in meta) are the code under test."""
import io

import pytest
from PIL import Image

from app.providers import ocr, preprocess
from app.providers.ocr import OcrResult


def _jpeg(size=(80, 60), color=(120, 120, 120), exif_orientation=None) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    if exif_orientation:
        exif = Image.Exif()
        exif[274] = exif_orientation
        img.save(buf, format="JPEG", exif=exif)
    else:
        img.save(buf, format="JPEG")
    return buf.getvalue()


def _pdf_with_pages(n: int) -> bytes:
    from pypdf import PdfWriter
    writer = PdfWriter()
    for _ in range(n):
        writer.add_blank_page(width=200, height=280)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ---- preprocessing primitives ------------------------------------------------
def test_exif_orientation_is_corrected_automatically():
    original = _jpeg(size=(80, 60), exif_orientation=6)   # 90° CW stored sideways
    fixed, warnings = preprocess.exif_normalize(original, "image/jpeg")
    assert any("EXIF orientation" in w and "corrected automatically" in w
               for w in warnings)
    img = Image.open(io.BytesIO(fixed))
    assert img.size == (60, 80)                            # transposed


def test_exif_normalize_is_noop_without_orientation():
    original = _jpeg()
    fixed, warnings = preprocess.exif_normalize(original, "image/jpeg")
    assert fixed == original and warnings == []


def test_rotate_produces_the_requested_orientation():
    original = _jpeg(size=(80, 60))
    for degrees, expected in ((90, (60, 80)), (180, (80, 60)), (270, (60, 80))):
        rotated = preprocess.rotate(original, "image/jpeg", degrees)
        assert Image.open(io.BytesIO(rotated)).size == expected


def test_pdf_pages_split_and_count():
    pdf = _pdf_with_pages(3)
    assert preprocess.pdf_page_count(pdf) == 3
    pages = preprocess.pdf_pages(pdf)
    assert len(pages) == 3
    assert all(preprocess.pdf_page_count(p) == 1 for p in pages)


def test_readability_flags_overexposure():
    glare = _jpeg(color=(255, 255, 255))
    assert any("glare" in w for w in preprocess.readability_warnings(glare, "image/jpeg"))


def test_preprocess_never_loses_an_upload_on_garbage_bytes():
    junk = b"\x00\x01not-an-image"
    fixed, warnings = preprocess.exif_normalize(junk, "image/jpeg")
    assert fixed == junk
    assert preprocess.rotate(junk, "image/jpeg", 90) is None
    assert preprocess.pdf_page_count(junk) == 0


# ---- rotation retry through the OCR pipeline ---------------------------------
@pytest.fixture()
def fake_tiers(monkeypatch):
    """Replace the provider hierarchy: records every OCR attempt and answers
    from a queue keyed by call index."""
    calls = []

    def install(responses):
        def _fake(*, content=b"", text="", mime="application/pdf"):
            calls.append({"content": content, "mime": mime})
            res = responses[min(len(calls) - 1, len(responses) - 1)]
            return res, {"primary": "fake", "fallback_used": False,
                         "primary_error": None, "docai_degraded": False}
        monkeypatch.setattr(ocr, "_run_tiers", _fake)
        return calls
    return install


def _no_mrz():
    return OcrResult(ok=True, doc_type="unknown", mrz_valid=False,
                     engine="fake", recognized_text="")


def _valid_mrz():
    return OcrResult(ok=True, doc_type="passport", mrz_valid=True,
                     engine="fake", recognized_text="P<USA...")


def test_rotated_passport_image_is_retried_until_mrz_validates(fake_tiers):
    calls = fake_tiers([_no_mrz(), _no_mrz(), _valid_mrz()])   # 0°, 90° fail; 180° ok
    res, meta = ocr.process_with_failover(
        content=_jpeg(size=(80, 60)), mime="image/jpeg", expect_passport=True)
    assert res.mrz_valid is True
    assert meta["rotation_applied"] == 180
    assert any("rotated 180°" in w and "automatically" in w
               for w in res.quality_warnings)
    assert len(calls) == 3


def test_no_rotation_retry_without_expect_passport(fake_tiers):
    calls = fake_tiers([_no_mrz()])
    res, meta = ocr.process_with_failover(
        content=_jpeg(), mime="image/jpeg", expect_passport=False)
    assert len(calls) == 1 and "rotation_applied" not in meta


def test_no_rotation_retry_when_first_pass_already_valid(fake_tiers):
    calls = fake_tiers([_valid_mrz()])
    res, meta = ocr.process_with_failover(
        content=_jpeg(), mime="image/jpeg", expect_passport=True)
    assert len(calls) == 1 and res.mrz_valid is True


def test_multipage_pdf_selects_the_biodata_page(fake_tiers):
    # Whole-PDF pass finds no MRZ; page 1 fails; page 2 is the biodata page.
    calls = fake_tiers([_no_mrz(), _no_mrz(), _valid_mrz()])
    res, meta = ocr.process_with_failover(
        content=_pdf_with_pages(3), mime="application/pdf", expect_passport=True)
    assert res.mrz_valid is True
    assert meta["pdf_page_selected"] == 2
    assert any("PDF page 2" in w for w in res.quality_warnings)


def test_single_page_pdf_gets_no_page_retry(fake_tiers):
    calls = fake_tiers([_no_mrz()])
    res, meta = ocr.process_with_failover(
        content=_pdf_with_pages(1), mime="application/pdf", expect_passport=True)
    assert len(calls) == 1 and "pdf_page_selected" not in meta
