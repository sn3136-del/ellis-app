"""Deterministic upload preprocessing for document OCR.

Runs BEFORE any OCR provider sees the bytes:
 - EXIF orientation correction (phone photos carry rotation in metadata);
 - candidate rotations (90/180/270) for photos taken sideways/upside-down —
   the OCR layer retries a rotation ONLY when the first pass found no MRZ;
 - PDF page handling: page count + per-page single-page PDFs so a multipage
   scan can be inspected page by page and the biodata page selected;
 - glare / readability statistics (over-exposure fraction, contrast) that feed
   the quality warnings shown to the applicant.

Everything here is pure image/PDF mechanics — no provider calls, no PII leaves
the process, and failures always fall back to the original bytes (preprocessing
may improve an upload, never lose one).
"""
from __future__ import annotations

import io

# Rotations attempted (in order) when the un-rotated pass finds no MRZ.
CANDIDATE_ROTATIONS = (90, 180, 270)

# Readability thresholds (grayscale 0-255).
_GLARE_FRACTION_WARN = 0.30   # >30% of pixels near-white -> likely glare/overexposure
_LOW_CONTRAST_STD = 28.0      # std-dev below this -> washed-out / blurry capture


def _pil():
    from PIL import Image, ImageOps
    return Image, ImageOps


def is_image(mime: str) -> bool:
    return str(mime or "").lower().startswith("image/")


def is_pdf(mime: str) -> bool:
    return str(mime or "").lower() == "application/pdf"


def exif_normalize(content: bytes, mime: str) -> tuple[bytes, list[str]]:
    """Apply the EXIF orientation tag so the pixels match how the photo was
    taken. Returns (bytes, warnings); the original bytes on any failure."""
    if not content or not is_image(mime):
        return content, []
    try:
        Image, ImageOps = _pil()
        img = Image.open(io.BytesIO(content))
        exif = img.getexif()
        orientation = exif.get(274) if exif else None  # 274 = Orientation tag
        if not orientation or orientation == 1:
            return content, []
        fixed = ImageOps.exif_transpose(img)
        out = io.BytesIO()
        fmt = "PNG" if "png" in mime.lower() else "JPEG"
        if fmt == "JPEG" and fixed.mode not in ("RGB", "L"):
            fixed = fixed.convert("RGB")
        fixed.save(out, format=fmt, quality=92)
        return out.getvalue(), [f"EXIF orientation {orientation} corrected automatically"]
    except Exception:  # noqa: BLE001 - preprocessing never loses an upload
        return content, []


def rotate(content: bytes, mime: str, degrees: int) -> bytes | None:
    """Rotate an image by 90/180/270 degrees. None on failure."""
    if not content or not is_image(mime):
        return None
    try:
        Image, _ = _pil()
        img = Image.open(io.BytesIO(content))
        rotated = img.rotate(-degrees, expand=True)  # PIL rotates counter-clockwise
        out = io.BytesIO()
        fmt = "PNG" if "png" in mime.lower() else "JPEG"
        if fmt == "JPEG" and rotated.mode not in ("RGB", "L"):
            rotated = rotated.convert("RGB")
        rotated.save(out, format=fmt, quality=92)
        return out.getvalue()
    except Exception:  # noqa: BLE001
        return None


def readability_warnings(content: bytes, mime: str) -> list[str]:
    """Deterministic glare / contrast diagnostics on the grayscale histogram.
    Warnings only — never a rejection by itself (the classifier decides)."""
    if not content or not is_image(mime):
        return []
    try:
        Image, _ = _pil()
        img = Image.open(io.BytesIO(content)).convert("L")
        img.thumbnail((640, 640))
        hist = img.histogram()
        total = sum(hist) or 1
        near_white = sum(hist[250:]) / total
        # Std-dev from the histogram.
        mean = sum(i * c for i, c in enumerate(hist)) / total
        var = sum(c * (i - mean) ** 2 for i, c in enumerate(hist)) / total
        std = var ** 0.5
        out = []
        if near_white > _GLARE_FRACTION_WARN:
            out.append("possible glare/over-exposure — retake without direct light if "
                       "fields are unreadable")
        if std < _LOW_CONTRAST_STD:
            out.append("low contrast — the photo may be blurry or washed out")
        return out
    except Exception:  # noqa: BLE001
        return []


def pdf_page_count(content: bytes) -> int:
    try:
        from pypdf import PdfReader
        return len(PdfReader(io.BytesIO(content)).pages)
    except Exception:  # noqa: BLE001
        return 0


def pdf_pages(content: bytes, *, max_pages: int = 10) -> list[bytes]:
    """Split a PDF into single-page PDFs (bounded) so each page can be OCR'd
    and classified independently and the biodata page selected automatically."""
    try:
        from pypdf import PdfReader, PdfWriter
        reader = PdfReader(io.BytesIO(content))
        out: list[bytes] = []
        for page in reader.pages[:max_pages]:
            writer = PdfWriter()
            writer.add_page(page)
            buf = io.BytesIO()
            writer.write(buf)
            out.append(buf.getvalue())
        return out
    except Exception:  # noqa: BLE001
        return []
