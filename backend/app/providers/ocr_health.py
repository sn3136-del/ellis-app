"""Document AI health diagnostic. Returns ONLY non-sensitive booleans + a
redacted error category — never tokens, credential paths, or document content."""
from __future__ import annotations

import base64

import httpx

from ..config import settings
from . import documentai

# 1x1 transparent PNG — a minimal valid document to probe reachability without
# sending any real content.
_PROBE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def _reach(processor: str) -> tuple[bool, str]:
    s = settings()
    tok = documentai._access_token()
    if not tok:
        return False, "no_adc"
    url = (f"https://{s.document_ai_location}-documentai.googleapis.com/v1/projects/"
           f"{s.google_cloud_project}/locations/{s.document_ai_location}/processors/{processor}:process")
    headers = {"authorization": f"Bearer {tok}", "content-type": "application/json"}
    if s.gcp_quota_project:
        headers["x-goog-user-project"] = s.gcp_quota_project
    try:
        r = httpx.post(url, headers=headers,
                       json={"rawDocument": {"content": base64.b64encode(_PROBE).decode(),
                                             "mimeType": "image/png"}}, timeout=30)
        if r.status_code == 200:
            return True, "ok"
        # A 400 on the tiny probe means the processor+auth are fine but rejected
        # our degenerate 1x1 image — i.e. the endpoint IS reachable.
        if r.status_code == 400:
            return True, "ok_probe_rejected"
        cat = {401: "unauthenticated", 403: "permission_denied",
               404: "processor_not_found", 429: "rate_limited"}.get(r.status_code, f"http_{r.status_code}")
        return False, cat
    except Exception as e:
        return False, type(e).__name__


def diagnostic() -> dict:
    s = settings()
    adc = documentai.is_authenticated()
    ocr_ok, ocr_cat = _reach(s.document_ai_ocr_processor) if (adc and s.document_ai_ocr_processor) else (False, "no_adc")
    form_ok, form_cat = _reach(s.document_ai_form_processor) if (adc and s.document_ai_form_processor) else (False, "no_adc")
    return {
        "adc_detected": adc,
        "project_resolved": bool(s.google_cloud_project),
        "ocr_processor_reachable": ocr_ok,
        "form_parser_reachable": form_ok,
        "region": s.document_ai_location,
        "quota_project_set": bool(s.gcp_quota_project),
        "redacted_error_category": None if (ocr_ok and form_ok) else {"ocr": ocr_cat, "form": form_cat},
    }
