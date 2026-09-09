"""Binary official PDFs are evidence only after bounded text extraction."""
from io import BytesIO
import hashlib
import subprocess
import sys
import threading
import time

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import (DecodedStreamObject, DictionaryObject, NameObject,
                           NumberObject)

from app.visa_snapshot import fetching, pdf_text

URL = "https://www.mofa.go.jp/official-fee-table.pdf"
RULE = "Indonesian ordinary passport tourists require a visa. Visa fee EUR90."


def make_pdf(text=RULE, *, pages=1, encrypted=False, image=False):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'),
            NameObject('/BaseFont'): NameObject('/Helvetica')})
        resources = DictionaryObject({NameObject('/Font'): DictionaryObject(
            {NameObject('/F1'): writer._add_object(font)})})
        page[NameObject('/Resources')] = writer._add_object(resources)
        if text is not None:
            stream = DecodedStreamObject()
            lines = text.splitlines()
            content = b'BT /F1 12 Tf 20 700 Td '
            for index, line in enumerate(lines):
                escaped = line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
                content += (b'0 -16 Td ' if index else b'') + b'(' + escaped.encode() + b') Tj '
            stream.set_data(content + b'ET')
            page[NameObject('/Contents')] = writer._add_object(stream.flate_encode())
        if image:
            obj = DecodedStreamObject()
            obj.set_data(b'\x00\x00\x00')
            obj.update({NameObject('/Type'): NameObject('/XObject'),
                NameObject('/Subtype'): NameObject('/Image'), NameObject('/Width'): NumberObject(1),
                NameObject('/Height'): NumberObject(1), NameObject('/ColorSpace'): NameObject('/DeviceRGB'),
                NameObject('/BitsPerComponent'): NumberObject(8)})
            resources[NameObject('/XObject')] = DictionaryObject(
                {NameObject('/Im0'): writer._add_object(obj)})
    if encrypted:
        writer.encrypt('test-only-document-password')
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def mock_http(monkeypatch, body, mime='application/pdf', status=200, redirect=False):
    real_client = httpx.Client
    def respond(request):
        if redirect and str(request.url) == URL:
            return httpx.Response(302, headers={'Location': '/final.pdf'}, request=request)
        return httpx.Response(status, headers={'Content-Type': mime}, content=body, request=request)
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(httpx, 'Client', lambda **kw: real_client(transport=transport, **kw))


@pytest.mark.parametrize('mime', ['application/pdf', 'application/pdf; charset=binary',
                                 'application/octet-stream', 'text/html'])
def test_real_compressed_pdf_is_extracted_not_misread_as_binary(monkeypatch, mime):
    body = make_pdf()
    assert RULE.encode() not in body  # the real rule is inside a compressed stream
    mock_http(monkeypatch, body, mime)
    result = fetching._default_fetch(URL)
    assert result.ok and result.content_text == RULE
    assert result.media_type == 'application/pdf'
    assert result.extraction_method == 'pypdf_text' and result.page_count == 1
    assert not result.challenge and result.links == [] and result.error == ''
    assert 'endobj' not in result.content_text and '%PDF' not in result.content_text
    assert result.content_hash == hashlib.sha256(body).hexdigest()


def test_short_pdf_rule_is_not_a_javascript_challenge(monkeypatch):
    mock_http(monkeypatch, make_pdf('Visa fee EUR90.'))
    result = fetching.fetch(URL)
    assert result.ok and result.content_text == 'Visa fee EUR90.'
    assert not result.challenge


def test_pdf_line_and_page_boundaries_survive_extraction():
    result = pdf_text.extract_pdf_text(make_pdf('Nationality | Visa fee\nIndonesia | EUR90', pages=2))
    assert result.error == '' and result.page_count == 2
    assert result.text == 'Nationality | Visa fee\nIndonesia | EUR90\n\nNationality | Visa fee\nIndonesia | EUR90'


@pytest.mark.parametrize('body,error', [
    (make_pdf(text=None), 'pdf_image_only_or_no_extractable_text'),
    (make_pdf(text=None, image=True), 'pdf_image_only_or_no_extractable_text'),
    (make_pdf(text='1', image=True), 'pdf_has_image_only_pages'),
    (make_pdf(encrypted=True), 'pdf_encrypted'),
    (b'%PDF-1.7\nnot a valid document\n%%EOF', 'pdf_malformed'),
    (b'<html><body>Not a PDF</body></html>', 'pdf_malformed'),
])
def test_unreadable_pdf_does_not_become_source_or_trigger_paid_render(monkeypatch, body, error):
    mock_http(monkeypatch, body)
    monkeypatch.setattr(fetching, '_FETCHER', None)
    monkeypatch.setattr(fetching, '_render_fetcher', lambda: pytest.fail('no browser/OCR fallback for PDF'))
    result = fetching.fetch(URL)
    assert not result.ok and result.content_text == '' and not result.challenge
    assert result.error == error and result.media_type == 'application/pdf'


def test_pdf_redirect_preserves_final_authority_and_content_hash(monkeypatch):
    body = make_pdf()
    mock_http(monkeypatch, body, redirect=True)
    result = fetching._default_fetch(URL)
    assert result.ok and result.final_url == 'https://www.mofa.go.jp/final.pdf'
    assert result.final_hostname == 'www.mofa.go.jp'
    assert result.redirect_chain == [URL, result.final_url]
    assert result.content_hash == hashlib.sha256(body).hexdigest()


@pytest.mark.parametrize('attribute,value,error', [
    ('MAX_PDF_PAGES', 1, 'pdf_page_limit'),
    ('MAX_PDF_TEXT_CHARS', 10, 'pdf_text_limit'),
    ('MAX_PAGE_STREAM_BYTES', 20, 'pdf_page_stream_limit'),
])
def test_pdf_limits_fail_honestly_without_truncated_evidence(monkeypatch, attribute, value, error):
    monkeypatch.setattr(pdf_text, attribute, value)
    result = pdf_text.extract_pdf_text(make_pdf(pages=2))
    assert result.error == error and result.text == ''


def test_download_size_cap_applies_before_pdf_parser_and_paid_fallback(monkeypatch):
    mock_http(monkeypatch, make_pdf())
    monkeypatch.setattr(fetching, 'MAX_DOWNLOAD_BYTES', 64)
    monkeypatch.setattr(pdf_text, 'extract_pdf_text', lambda *_a, **_k: pytest.fail('oversized body must not parse'))
    monkeypatch.setattr(fetching, '_render_fetcher', lambda: pytest.fail('oversized source must not render'))
    result = fetching.fetch(URL)
    assert not result.ok and result.error == 'source_download_size_limit'
    assert result.content_text == ''


def test_encrypted_pdf_does_not_attempt_password_guessing():
    result = pdf_text.extract_pdf_text(make_pdf(encrypted=True))
    assert result.error == 'pdf_encrypted' and not result.text


def test_real_hung_parser_is_killed_and_capacity_released(monkeypatch):
    actual_popen = subprocess.Popen
    children = []
    def slow_child(command, **kwargs):
        assert command[1] == '-I' and command[3] == '--worker'
        assert kwargs['env'] == {'PATH': __import__('os').defpath}
        child = actual_popen([sys.executable, '-I', '-c', 'import time; time.sleep(30)'], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(pdf_text.subprocess, 'Popen', slow_child)
    monkeypatch.setattr(pdf_text, '_SLOTS', threading.BoundedSemaphore(1))
    started = time.monotonic()
    result = pdf_text.extract_pdf_text(make_pdf(), timeout_seconds=0.08)
    assert result.error == 'pdf_extraction_timeout'
    assert time.monotonic() - started < 2
    assert children[0].poll() is not None
    assert pdf_text._SLOTS.acquire(blocking=False)
    pdf_text._SLOTS.release()


def test_busy_parser_pool_is_bounded_and_does_not_spawn(monkeypatch):
    slots = threading.BoundedSemaphore(1); slots.acquire()
    monkeypatch.setattr(pdf_text, '_SLOTS', slots)
    monkeypatch.setattr(pdf_text.subprocess, 'Popen', lambda *_a, **_k: pytest.fail('no capacity'))
    result = pdf_text.extract_pdf_text(make_pdf())
    assert result.error == 'pdf_extraction_capacity' and not result.text


def test_http_error_pdf_is_not_parsed_or_rendered(monkeypatch):
    mock_http(monkeypatch, make_pdf(), status=403)
    monkeypatch.setattr(pdf_text, 'extract_pdf_text', lambda *_a, **_k: pytest.fail('HTTP failure'))
    monkeypatch.setattr(fetching, '_render_fetcher', lambda: pytest.fail('HTTP PDF failure'))
    result = fetching.fetch(URL)
    assert not result.ok and result.error == 'http 403' and result.content_text == ''


def test_html_still_uses_structural_text_and_links(monkeypatch):
    html = '<h1>Tourist visa requirements</h1><p>' + RULE * 3 + '</p><a href="/details">Details</a>'
    mock_http(monkeypatch, html.encode(), mime='text/html; charset=utf-8')
    result = fetching._default_fetch('https://www.mofa.go.jp/visas')
    assert result.ok and result.extraction_method == 'html_text'
    assert result.content_text.startswith('Tourist visa requirements\n')
    assert result.links == ['https://www.mofa.go.jp/details']


def test_html_javascript_shell_still_routes_to_existing_renderer(monkeypatch):
    mock_http(monkeypatch, b'<html>Enable Javascript</html>', mime='text/html')
    expected = fetching.FetchResult(requested_url=URL, ok=True, content_text=RULE)
    monkeypatch.setattr(fetching, '_render_fetcher', lambda: lambda *_a, **_k: expected)
    assert fetching.fetch(URL) is expected
