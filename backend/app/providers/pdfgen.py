"""Minimal dependency-free PDF writer for tamper-evident signed authorizations.

Produces a single-page PDF from plain text lines. Not a full typesetter — just
enough to render an authorization + its SHA-256 + audit metadata into a stable,
hashable artifact. Avoids adding reportlab as a hard dependency.
"""
from __future__ import annotations


def _esc(s: str) -> str:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def text_pdf(lines: list[str], *, title: str = "Ellis Authorization") -> bytes:
    # Build content stream: simple Helvetica, wrapped by the caller.
    y = 780
    parts = ["BT", "/F1 11 Tf", "12 TL", f"1 0 0 1 40 {y} Tm"]
    for ln in lines:
        parts.append(f"({_esc(ln)}) Tj")
        parts.append("T*")
    parts.append("ET")
    content = "\n".join(parts).encode("latin-1", "replace")

    objs = []
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>")
    objs.append(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    if title:
        objs.append(b"<< /Title (" + _esc(title).encode("latin-1", "replace") + b") >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    n = len(objs) + 1
    out += f"xref\n0 {n}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    trailer_root = b"/Root 1 0 R"
    info = b" /Info 6 0 R" if title else b""
    out += b"trailer\n<< /Size " + str(n).encode() + b" " + trailer_root + info + b" >>\n"
    out += b"startxref\n" + str(xref_pos).encode() + b"\n%%EOF"
    return bytes(out)


def image_pdf(jpeg: bytes, *, pixels: tuple[int, int], page: tuple[float, float],
              box: tuple[float, float, float, float]) -> bytes:
    """A single page of `page` size holding one JPEG, drawn inside `box`
    (x, y, width, height in PDF points, origin bottom-left).

    Written by hand for the same reason text_pdf is: this is the whole of
    Ellis's need for image composition, and it is not worth a new hard
    dependency. The caller merges the result onto a government form's own page.

    The image is FITTED, never stretched — it keeps its aspect ratio and is
    centred in the box. A face squeezed to fill a rectangle is a worse photo
    than a smaller correct one, and a consulate looks at the face.
    """
    pw, ph = page
    bx, by, bw, bh = box
    iw, ih = pixels
    if not (iw > 0 and ih > 0 and bw > 0 and bh > 0):
        raise ValueError("image and box must both have a positive size")
    scale = min(bw / iw, bh / ih)
    dw, dh = iw * scale, ih * scale
    dx, dy = bx + (bw - dw) / 2, by + (bh - dh) / 2
    content = (f"q\n{dw:.2f} 0 0 {dh:.2f} {dx:.2f} {dy:.2f} cm\n/Im0 Do\nQ"
               ).encode("latin-1")

    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pw:.2f} {ph:.2f}] "
         f"/Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>"
         ).encode("latin-1"),
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
        + content + b"\nendstream",
        (f"<< /Type /XObject /Subtype /Image /Width {iw} /Height {ih} "
         f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode "
         f"/Length {len(jpeg)} >>").encode("latin-1")
        + b"\nstream\n" + jpeg + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    n = len(objs) + 1
    out += f"xref\n0 {n}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += b"trailer\n<< /Size " + str(n).encode() + b" /Root 1 0 R >>\n"
    out += b"startxref\n" + str(xref_pos).encode() + b"\n%%EOF"
    return bytes(out)
