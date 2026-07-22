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
