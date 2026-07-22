"""Google Document AI REST client (Enterprise OCR + Form Parser).

Real implementation against the process endpoint. Authentication needs a Google
access token — from a service-account key (GOOGLE_APPLICATION_CREDENTIALS) or
`gcloud auth application-default`. When no token is obtainable, this provider
reports unauthenticated and the OCR chain falls back to Kimi K3 vision.

Processor endpoint (from config):
  https://{loc}-documentai.googleapis.com/v1/projects/{project}/locations/{loc}/processors/{id}:process
"""
from __future__ import annotations

import base64
import json
import os
import subprocess

import httpx

from ..config import settings


def _access_token() -> str | None:
    # 1) explicit env token, 2) service-account / ADC via google-auth,
    # 3) gcloud ADC CLI. Any of these activates the live provider.
    tok = os.getenv("GOOGLE_ACCESS_TOKEN", "").strip()
    if tok:
        return tok
    try:  # google-auth path (service account / ADC well-known file)
        import google.auth  # type: ignore
        import google.auth.transport.requests  # type: ignore
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(google.auth.transport.requests.Request())
        return creds.token
    except Exception:
        pass
    try:  # gcloud CLI path
        out = subprocess.run(["gcloud", "auth", "application-default", "print-access-token"],
                             capture_output=True, text=True, timeout=15)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return None


def is_authenticated() -> bool:
    return _access_token() is not None


def process_document(content: bytes, mime: str, *, use_form_parser: bool = False) -> dict:
    """Call Document AI :process and return the raw document JSON.
    Raises RuntimeError('DOCAI_UNAUTHENTICATED') when no token is available."""
    s = settings()
    token = _access_token()
    if not token:
        raise RuntimeError("DOCAI_UNAUTHENTICATED")
    proc = s.document_ai_form_processor if use_form_parser else s.document_ai_ocr_processor
    loc = s.document_ai_location
    url = (f"https://{loc}-documentai.googleapis.com/v1/projects/{s.google_cloud_project}"
           f"/locations/{loc}/processors/{proc}:process")
    body = {"rawDocument": {"content": base64.b64encode(content).decode(), "mimeType": mime}}
    headers = {"authorization": f"Bearer {token}", "content-type": "application/json"}
    # ADC user credentials need a quota/billing project header.
    if s.gcp_quota_project:
        headers["x-goog-user-project"] = s.gcp_quota_project
    r = httpx.post(url, headers=headers, json=body, timeout=120)
    if r.status_code != 200:
        # Redacted error only — never surface document content in errors.
        raise RuntimeError(f"DOCAI_HTTP_{r.status_code}")
    return r.json().get("document", {})


def extract_text(doc_json: dict) -> str:
    return doc_json.get("text", "")
