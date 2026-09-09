# Official PDF source extraction

The official-source fetcher detects PDFs by media type or leading PDF magic.
It extracts their text with the already-declared `pypdf` dependency instead of
decoding compressed PDF bytes as HTML. Extracted text still passes the same
official-host, nationality, document, purpose, date, quotation and field-value
checks as other source content. Successful extraction is not policy verification.

The parser runs in a disposable local Python process with isolated imports and
an environment containing no provider credentials. It uses no network, paid
OCR or conversion service. Two parsers can run per application process. A parser
has a five-second wall-clock deadline and is killed and reaped on timeout.
Linux additionally enforces a 768 MiB address-space ceiling and six-second CPU
ceiling. There is no equivalent address-space promise on macOS.

Limits are 8 MiB downloaded bytes, 80 PDF pages, 2 MiB decompressed content per
page, and 200,000 extracted characters. Exceeding a PDF limit fails the source
read; an incomplete prefix is not returned as a complete PDF. Empty, image-only,
encrypted and malformed documents produce explicit failure codes. Mixed
documents with detected image-only pages also require manual reading. The
fetcher does not send these PDF failures to the paid browser fallback. Ordinary
HTML challenges retain their existing rendering path. All streamed source
downloads are bounded to 8 MiB.

This is text extraction, not OCR or a visual audit. Existing OCR layers and
complex table layouts can contain errors; the normal evidence gates must still
establish a field's actual scope. A document that cannot be extracted within
these limits needs a separate, recorded manual official-source reading.

Freshness checks retain the full bounded retrieved text for deterministic
evidence validation, including previously reviewed nationality annexes beyond
the prompt excerpt. The model still receives at most 28,000 characters per page
in freshness comparisons and 8,000 in on-demand research. Enlarging the evidence
view does not enlarge those model input limits. A later changed or missing annex
cannot reuse a historical quote as current proof.

Validation on 2026-09-09: 261 focused tests passed, including 23 real-PDF fetch
and parser cases and four worker/proposal tests for evidence after the prompt
excerpt. Tests used local PDF fixtures and mocked HTTP/model providers; no paid
provider requests were made.
