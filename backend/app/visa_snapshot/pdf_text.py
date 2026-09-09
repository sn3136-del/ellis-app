"""Bounded, offline extraction of public PDF text in a disposable process.

No OCR, credentials, network access or conversion service is used. A timeout
terminates the parser rather than leaving a CPU/memory-heavy daemon behind.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

MAX_PDF_BYTES = 8 * 1024 * 1024
MAX_PDF_PAGES = 80
MAX_PDF_TEXT_CHARS = 200_000
MAX_PAGE_STREAM_BYTES = 2 * 1024 * 1024
PDF_TIMEOUT_SECONDS = 5.0
_SLOTS = threading.BoundedSemaphore(2)


@dataclass(frozen=True)
class PDFText:
    text: str = ""
    error: str = ""
    page_count: int = 0


def extract_pdf_text(data: bytes, *, timeout_seconds: float = PDF_TIMEOUT_SECONDS) -> PDFText:
    if not data or len(data) > MAX_PDF_BYTES:
        return PDFText(error="pdf_download_size_limit" if data else "pdf_empty_document")
    timeout = min(PDF_TIMEOUT_SECONDS, max(0.0, timeout_seconds))
    if timeout <= 0:
        return PDFText(error="pdf_extraction_timeout")
    if not _SLOTS.acquire(blocking=False):
        return PDFText(error="pdf_extraction_capacity")
    proc = None
    try:
        # Execute a fixed local worker, not a URL or document-supplied command.
        # -I excludes cwd/PYTHONPATH; the child receives no provider credentials.
        command = [sys.executable, "-I", str(Path(__file__).with_name("pdf_text.py")),
                   "--worker", str(MAX_PDF_PAGES), str(MAX_PDF_TEXT_CHARS),
                   str(MAX_PAGE_STREAM_BYTES)]
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, close_fds=True,
                                env={"PATH": os.defpath})
        try:
            output, _ = proc.communicate(data, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return PDFText(error="pdf_extraction_timeout")
        if proc.returncode != 0 or len(output) > MAX_PDF_TEXT_CHARS * 6 + 1024:
            return PDFText(error="pdf_extraction_failed")
        result = json.loads(output)
        text, error, count = result.get("text", ""), result.get("error", ""), result.get("page_count", 0)
        if (not isinstance(text, str) or len(text) > MAX_PDF_TEXT_CHARS
                or not isinstance(error, str) or not isinstance(count, int)
                or not 0 <= count <= MAX_PDF_PAGES):
            return PDFText(error="pdf_extraction_failed")
        if error:
            allowed = {"pdf_encrypted", "pdf_page_limit", "pdf_text_limit", "pdf_page_stream_limit",
                       "pdf_image_only_or_no_extractable_text", "pdf_has_image_only_pages",
                       "pdf_malformed", "pdf_parser_unavailable"}
            return PDFText(error=error if error in allowed else "pdf_extraction_failed")
        return PDFText(text=text, page_count=count) if text.strip() else PDFText(
            error="pdf_image_only_or_no_extractable_text")
    except Exception:
        # Parser, OS and logging errors must not leak document bytes or paths.
        return PDFText(error="pdf_extraction_failed")
    finally:
        try:
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.communicate()
        finally:
            _SLOTS.release()


def _worker(data: bytes, max_pages: int, max_chars: int, max_stream: int) -> dict:
    import io
    import logging
    import re
    logging.disable(logging.CRITICAL)
    try:
        from pypdf import PdfReader
    except ImportError:
        return {"error": "pdf_parser_unavailable"}
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        # Even an empty-password-encrypted PDF needs explicit treatment; never
        # silently decrypt or interpret its permissions as source verification.
        if reader.is_encrypted:
            return {"error": "pdf_encrypted"}
        if len(reader.pages) > max_pages:
            return {"error": "pdf_page_limit"}
        parts, size, image_only_pages = [], 0, 0
        for page in reader.pages:
            content = page.get_contents()
            if content is not None and len(content.get_data()) > max_stream:
                return {"error": "pdf_page_stream_limit"}
            text = page.extract_text() or ""
            # Keep line/page boundaries; do not flatten a fee table into prose.
            text = "\n".join(line.rstrip() for line in text.splitlines()).strip()
            resources = page.get("/Resources") or {}
            if hasattr(resources, "get_object"):
                resources = resources.get_object()
            objects = resources.get("/XObject") or {}
            has_image = any(obj.get_object().get("/Subtype") == "/Image"
                            for obj in objects.get_object().values()) if objects else False
            if has_image and len(re.sub(r"\W|\d", "", text)) < 30:
                image_only_pages += 1
            if text:
                size += len(text) + (2 if parts else 0)
                if size > max_chars:
                    # A truncated appendix must not certify the full source.
                    return {"error": "pdf_text_limit"}
                parts.append(text)
        if not parts:
            return {"error": "pdf_image_only_or_no_extractable_text"}
        if image_only_pages:
            return {"error": "pdf_has_image_only_pages"}
        return {"text": "\n\n".join(parts), "page_count": len(reader.pages)}
    except Exception:
        return {"error": "pdf_malformed"}


if __name__ == "__main__":
    # Resource ceilings are secondary to the parent's killable wall-clock cap.
    # Linux production supports address-space limits; macOS may not.
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CPU, (6, 6))
        if sys.platform.startswith("linux"):
            resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
    except (ImportError, OSError, ValueError):
        pass
    data = sys.stdin.buffer.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        result = {"error": "pdf_download_size_limit"}
    else:
        result = _worker(data, int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]))
    sys.stdout.write(json.dumps(result, ensure_ascii=True))
