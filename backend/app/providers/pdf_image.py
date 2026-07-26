"""PDF → JPEG conversion for portal uploads that require an image.

E-visa portals (Vietnam included) accept only image formats for the photo and
passport-biodata uploads, but applicants routinely hold those as PDFs (scanner
and phone-share exports). Two strategies, best first:

1. Lossless extraction: a "PDF photo" is usually a single embedded JPEG on
   page one — pypdf pulls the original bytes out untouched.
2. Rasterize: render page one with pypdfium2 at print resolution and encode
   JPEG.

Both write a 0600 temp file the CALLER deletes after upload. Failure returns
None — the caller falls back to the original file rather than blocking the
application (the portal's own validation then speaks for itself)."""
from __future__ import annotations

import io
import os
import tempfile

RENDER_SCALE = 200 / 72.0     # ~200 DPI — crisp for ID photos, modest bytes
JPEG_QUALITY = 92
MAX_DIMENSION = 2400          # portals reject huge uploads; bound the raster


def _write_jpeg(img, prefix: str) -> str:
    from PIL import Image  # noqa: F401 — img is a PIL image
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    if max(img.size) > MAX_DIMENSION:
        ratio = MAX_DIMENSION / max(img.size)
        img = img.resize((max(1, int(img.width * ratio)),
                          max(1, int(img.height * ratio))))
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".jpg")
    with os.fdopen(fd, "wb") as fh:
        img.save(fh, format="JPEG", quality=JPEG_QUALITY)
    os.chmod(path, 0o600)
    return path


def _extract_embedded_image(pdf_path: str):
    """The single dominant embedded image from page one, or None. Lossless
    when the PDF is just a wrapped photo/scan."""
    try:
        from pypdf import PdfReader
        from PIL import Image
        reader = PdfReader(pdf_path)
        if not reader.pages:
            return None
        images = list(reader.pages[0].images)
        if len(images) != 1:
            return None    # composite page: rasterizing is the faithful path
        return Image.open(io.BytesIO(images[0].data))
    except Exception:  # noqa: BLE001 — extraction is opportunistic
        return None


def _rasterize_first_page(pdf_path: str):
    try:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(pdf_path)
        try:
            if len(doc) < 1:
                return None
            page = doc[0]
            try:
                return page.render(scale=RENDER_SCALE).to_pil()
            finally:
                page.close()
        finally:
            doc.close()
    except Exception:  # noqa: BLE001
        return None


def pdf_first_page_jpeg(pdf_path: str) -> str | None:
    """Convert a PDF's first page to a JPEG temp file (0600). None = could
    not convert; the caller keeps the original file."""
    if not str(pdf_path).lower().endswith(".pdf") or not os.path.exists(pdf_path):
        return None
    img = _extract_embedded_image(pdf_path) or _rasterize_first_page(pdf_path)
    if img is None:
        return None
    try:
        return _write_jpeg(img, prefix="ellis-upload-")
    except Exception:  # noqa: BLE001
        return None
