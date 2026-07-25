"""Document OCR + intelligence.

Primary production provider: Google Cloud Document AI (activation: set
GOOGLE_CLOUD_PROJECT + GOOGLE_DOCUMENT_AI_OCR_PROCESSOR_ID + DOCUMENT_AI_ENABLED).
Local provider: deterministic MRZ parser so passports extract fully and the
pipeline + tests run with no cloud credentials.

MRZ parsing and ICAO 9303 check-digit validation are deterministic and provider-
independent — never delegated to an LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import settings

# --- ICAO 9303 MRZ (TD3 passport) -------------------------------------------
_WEIGHTS = [7, 3, 1]


def _char_val(c: str) -> int:
    if c == "<":
        return 0
    if c.isdigit():
        return int(c)
    return ord(c) - 55  # A=10..Z=35


def mrz_check_digit(s: str) -> str:
    total = sum(_char_val(c) * _WEIGHTS[i % 3] for i, c in enumerate(s))
    return str(total % 10)


@dataclass
class ExtractedField:
    key: str
    value: str
    confidence: float
    page: int = 1
    source: str = "ocr"


@dataclass
class OcrResult:
    ok: bool
    doc_type: str = ""
    fields: list[ExtractedField] = field(default_factory=list)
    quality_warnings: list[str] = field(default_factory=list)
    mrz_valid: bool = False
    engine: str = ""
    # Transient recognized text, used ONLY for in-request page classification.
    # Deliberately excluded from as_dict() so it is never persisted or logged
    # (OCR text is PII). Callers must not store it.
    recognized_text: str = ""

    def as_dict(self) -> dict:
        return {
            "ok": self.ok, "doc_type": self.doc_type, "mrz_valid": self.mrz_valid,
            "engine": self.engine, "quality_warnings": self.quality_warnings,
            "fields": [vars(f) for f in self.fields],
        }


def _repair_numeric(field: str) -> str:
    # Repair common OCR confusions in a numeric MRZ field (O->0, I/L->1, etc.).
    return (field.replace("O", "0").replace("Q", "0").replace("D", "0")
                 .replace("I", "1").replace("L", "1").replace("S", "5")
                 .replace("B", "8").replace("Z", "2").replace("G", "6"))


# A passport name is letters only, so a digit read inside a name field is always
# a misread letter. Map the common confusions back (0->O, 1->I, 5->S, 8->B) and
# drop any residual digit so 'N0EMI ELIAS' becomes 'NOEMI ELIAS'. Total and
# idempotent; the single source of name normalization on the backend.
_NAME_DIGIT_TO_ALPHA = str.maketrans({"0": "O", "1": "I", "5": "S", "8": "B"})


def normalize_name(s: str) -> str:
    import re
    if not s:
        return ""
    s = s.upper().replace("<", " ").translate(_NAME_DIGIT_TO_ALPHA)
    s = re.sub(r"[^A-Z '-]+", " ", s)   # letters-only zone (+ space, ' , -)
    return re.sub(r"\s+", " ", s).strip()


def _find_td3_lines(text: str):
    """Locate the two TD3 MRZ lines anywhere in OCR text, tolerant of spaces,
    surrounding text, leading characters, and line-wrapping. Uses search (not
    line-anchored match) so an MRZ preceded by other glyphs is still found."""
    import re
    u = text.upper().replace(" ", "")
    # Line 1: 'P' + subtype + issuing country + name zone with '<<'. China uses
    # 'PO'+country; most others 'P<'+country. Grab a 44-char window.
    m1 = re.search(r"P[A-Z<][A-Z<]{3}[A-Z<]*<<[A-Z<]{2,}", u)
    l1 = u[m1.start():m1.start() + 44] if m1 else None
    # Line 2: 9 doc chars + check + 3-letter nationality + 6-digit DOB + check +
    # sex + 6-digit expiry + check ... (tolerant of OCR letter/digit confusion).
    m2 = re.search(r"[A-Z0-9<]{9}[0-9OILSBZG][A-Z<]{3}[0-9OILSBZG]{6}[0-9OILSBZG][MFX<]", u)
    l2 = u[m2.start():m2.start() + 44] if m2 else None
    return l1, l2


def parse_mrz(text: str) -> dict | None:
    """Parse a TD3 (2×44) MRZ block and validate check digits (with OCR repair)."""
    l1, l2 = _find_td3_lines(text)
    if not l1 or not l2:
        # Fallback: the simple older heuristic.
        lines = [ln.strip().replace(" ", "") for ln in text.splitlines() if "<" in ln]
        cand = [ln for ln in lines if len(ln) >= 30]
        if len(cand) < 2:
            return None
        l1, l2 = cand[0], cand[1]
    l1, l2 = l1[:44].ljust(44, "<"), l2[:44].ljust(44, "<")
    if not l1.startswith("P"):
        return None
    issuing = l1[2:5].replace("<", "")
    names = l1[5:].split("<<", 1)
    # Names are letters only: normalize OCR digit-for-letter misreads so a name
    # is never stored with a digit (e.g. 'N0EMI' -> 'NOEMI').
    surname = normalize_name(names[0])
    given = normalize_name(names[1]) if len(names) > 1 else ""
    passport_no = l2[0:9]
    nationality = l2[10:13].replace("<", "")
    # Date fields are strictly numeric YYMMDD — repair OCR letter/digit confusion
    # before validating check digits (Chinese passports OCR with O/0 noise).
    birth = _repair_numeric(l2[13:19])
    sex = l2[20]
    expiry = _repair_numeric(l2[21:27])
    pn_check = _repair_numeric(l2[9])
    birth_check = _repair_numeric(l2[19])
    exp_check = _repair_numeric(l2[27])
    checks = {
        "passport": mrz_check_digit(passport_no) == pn_check,
        "birth": mrz_check_digit(birth) == birth_check,
        "expiry": mrz_check_digit(expiry) == exp_check,
    }
    return {
        "surname": surname, "given_names": given, "passport_number": passport_no.replace("<", ""),
        "nationality": nationality, "issuing_country": issuing, "birth_date": birth,
        "sex": sex, "expiry_date": expiry, "check_digits": checks,
        "document_code": l1[0:2].replace("<", ""),   # ICAO doc type (P, PO, PD…)
        "mrz_valid": all(checks.values()),
        "mrz_lines": [l1, l2],
    }


class LocalOcrProvider:
    """Extracts from text the caller already has (e.g. an embedded PDF text
    layer or a fixture). Deterministic — perfect for tests."""
    name = "local_mrz_provider"

    def process(self, *, text: str = "", mime: str = "application/pdf", **_) -> OcrResult:
        warnings = []
        if not text:
            warnings.append("no extractable text layer; image OCR requires Document AI")
            return OcrResult(ok=True, doc_type="unknown", quality_warnings=warnings,
                             engine=self.name, recognized_text="")
        mrz = parse_mrz(text)
        if mrz:
            fields = _fields_from_mrz(mrz, warnings)
            return OcrResult(ok=True, doc_type="passport", fields=fields,
                             quality_warnings=warnings, mrz_valid=mrz["mrz_valid"],
                             engine=self.name, recognized_text=text)
        # Non-passport: pull simple key: value pairs.
        fields = []
        for m in re.finditer(r"([A-Za-z ]{3,40}):\s*([^\n]{1,80})", text):
            fields.append(ExtractedField(m.group(1).strip().lower().replace(" ", "_"),
                                         m.group(2).strip(), 0.7))
        doc_type = "bank_statement" if re.search(r"balance|account", text, re.I) else "document"
        return OcrResult(ok=True, doc_type=doc_type, fields=fields, engine=self.name,
                         recognized_text=text)


def _fields_from_mrz(mrz: dict, warnings: list[str]) -> list[ExtractedField]:
    """Extracted fields carry CANONICAL ISO dates (YYYY-MM-DD) — the raw MRZ
    YYMMDD stays inside the mrz dict for checksum work, but nothing downstream
    (answers, UI, forms) ever sees an un-normalized date value."""
    from .. import dates as dates_mod
    birth_iso = dates_mod.to_iso(dates_mod.parse_mrz_date(mrz["birth_date"], kind="birth"))
    expiry_iso = dates_mod.to_iso(dates_mod.parse_mrz_date(mrz["expiry_date"], kind="expiry"))
    fields = [
        ExtractedField("surname", mrz["surname"], 0.99),
        ExtractedField("given_names", mrz["given_names"], 0.99),
        ExtractedField("passport_number", mrz["passport_number"], 0.99),
        ExtractedField("nationality", mrz["nationality"], 0.98),
        ExtractedField("issuing_country", mrz["issuing_country"], 0.98),
        ExtractedField("birth_date", birth_iso or mrz["birth_date"], 0.98),
        ExtractedField("sex", mrz["sex"], 0.98),
        ExtractedField("expiry_date", expiry_iso or mrz["expiry_date"], 0.98),
    ]
    if not mrz["mrz_valid"]:
        warnings.append("MRZ check digit mismatch — verify manually")
    return fields


class DocumentAiProvider:
    """Google Document AI Enterprise OCR. Runs deterministic MRZ validation on
    the recognized text regardless of the OCR engine."""
    name = "google_document_ai"

    def process(self, *, content: bytes = b"", mime: str = "application/pdf", text: str = "", **_) -> OcrResult:
        from . import documentai
        doc = documentai.process_document(content, mime)
        recognized = documentai.extract_text(doc) or text
        mrz = parse_mrz(recognized)
        warnings: list[str] = []
        if mrz:
            return OcrResult(ok=True, doc_type="passport", fields=_fields_from_mrz(mrz, warnings),
                             quality_warnings=warnings, mrz_valid=mrz["mrz_valid"],
                             engine=self.name, recognized_text=recognized)
        res = LocalOcrProvider().process(text=recognized, mime=mime)
        res.engine = self.name
        res.recognized_text = recognized
        return res


class KimiVisionProvider:
    """Live Kimi K3 multimodal OCR tier. Reads the image, extracts the MRZ, then
    validates it deterministically. The model is never trusted for check digits."""
    name = "kimi_k3_vision"

    def process(self, *, content: bytes = b"", mime: str = "image/jpeg", text: str = "", **_) -> OcrResult:
        from . import kimi_vision
        if not content:
            return LocalOcrProvider().process(text=text, mime=mime)
        out = kimi_vision.ocr_document(content, mime)
        warnings: list[str] = []
        mrz = parse_mrz(out["mrz_text"]) if out["mrz_text"] else None
        if mrz:
            fields = _fields_from_mrz(mrz, warnings)
            return OcrResult(ok=True, doc_type="passport", fields=fields,
                             quality_warnings=warnings, mrz_valid=mrz["mrz_valid"],
                             engine=self.name,
                             recognized_text=(out.get("text") or out.get("mrz_text") or ""))
        # No MRZ recognized: fall back to text extraction from the vision result.
        res = LocalOcrProvider().process(text=out["text"], mime=mime)
        res.doc_type = out.get("doc_type") or res.doc_type
        res.engine = self.name
        if not out["text"]:
            res.quality_warnings.append("no MRZ or text recognized by vision OCR")
        return res


def get_provider(*, has_image: bool = False):
    """Select the best available OCR tier.
    Document AI (if authenticated) → Kimi K3 vision (if key + image) → local text."""
    s = settings()
    if s.google_cloud_project and s.document_ai_ocr_processor and s.document_ai_enabled:
        from . import documentai
        if documentai.is_authenticated():
            return DocumentAiProvider()
    from . import kimi_vision
    if has_image and kimi_vision.is_available():
        return KimiVisionProvider()
    return LocalOcrProvider()


def _run_tiers(*, content: bytes = b"", text: str = "", mime: str = "application/pdf"):
    """One pass through the OCR hierarchy:
      Tier 1: Google Document AI (primary).
      Tier 2: Kimi K3 vision (ONLY on a recoverable Tier-1 failure AND when the
              ENABLE_KIMI_OCR_FALLBACK flag is on AND the input is an image).
      Tier 3: local deterministic text parser.
    Returns (OcrResult, meta) where meta records which engine ran and whether a
    fallback occurred — never any personal values. A PERSISTENT Document AI auth/
    config failure is surfaced in meta (not silently hidden behind Kimi)."""
    s = settings()
    meta = {"primary": None, "fallback_used": False, "primary_error": None, "docai_degraded": False}
    is_image = bool(content) and mime.startswith("image/")

    docai_configured = bool(s.google_cloud_project and s.document_ai_ocr_processor and s.document_ai_enabled)
    if docai_configured:
        from . import documentai
        if documentai.is_authenticated():
            try:
                res = DocumentAiProvider().process(content=content, text=text, mime=mime)
                meta["primary"] = "google_document_ai"
                return res, meta
            except Exception as e:
                meta["primary"] = "google_document_ai"
                meta["primary_error"] = str(e)[:40]   # already redacted (DOCAI_HTTP_xxx)
        else:
            meta["docai_degraded"] = True              # configured but not authenticated
            meta["primary_error"] = "DOCAI_UNAUTHENTICATED"

    # Tier 2 — flag-guarded image fallback.
    from . import kimi_vision
    if is_image and s.kimi_ocr_fallback and kimi_vision.is_available():
        res = KimiVisionProvider().process(content=content, text=text, mime=mime)
        meta["fallback_used"] = True
        meta["fallback_engine"] = "kimi_k3_vision"
        return res, meta

    # Tier 3 — local deterministic.
    res = LocalOcrProvider().process(text=text, mime=mime)
    if meta["primary"] is None and not meta["docai_degraded"]:
        meta["primary"] = "local_text"
    return res, meta


def process_with_failover(*, content: bytes = b"", text: str = "",
                          mime: str = "application/pdf",
                          expect_passport: bool = False):
    """The production OCR entry point: deterministic preprocessing (EXIF
    orientation, glare/contrast diagnostics), one pass through the provider
    hierarchy, then — when a passport is expected but no checksum-valid MRZ was
    recognized — bounded automatic recovery:
      * images: retry the hierarchy at 90/180/270 degrees until an MRZ
        validates (rotated phone photos are accepted automatically);
      * multipage PDFs: OCR each page individually and select the page whose
        MRZ validates (the biodata page is picked automatically).
    All retries are recorded in meta; nothing is ever invented."""
    from . import preprocess

    pre_warnings: list[str] = []
    if content and preprocess.is_image(mime):
        content, w = preprocess.exif_normalize(content, mime)
        pre_warnings += w
        pre_warnings += preprocess.readability_warnings(content, mime)

    res, meta = _run_tiers(content=content, text=text, mime=mime)

    if expect_passport and content and not res.mrz_valid:
        if preprocess.is_image(mime):
            for degrees in preprocess.CANDIDATE_ROTATIONS:
                rotated = preprocess.rotate(content, mime, degrees)
                if rotated is None:
                    break
                res2, meta2 = _run_tiers(content=rotated, text=text, mime=mime)
                if res2.mrz_valid:
                    meta2["rotation_applied"] = degrees
                    pre_warnings.append(
                        f"image was rotated {degrees}° and corrected automatically")
                    res, meta = res2, meta2
                    break
        elif preprocess.is_pdf(mime) and preprocess.pdf_page_count(content) > 1:
            for idx, page_bytes in enumerate(preprocess.pdf_pages(content)):
                res2, meta2 = _run_tiers(content=page_bytes, text="", mime=mime)
                if res2.mrz_valid:
                    meta2["pdf_page_selected"] = idx + 1
                    pre_warnings.append(
                        f"passport biodata page found on PDF page {idx + 1}")
                    res, meta = res2, meta2
                    break

    if pre_warnings:
        res.quality_warnings = pre_warnings + list(res.quality_warnings or [])
    return res, meta


def cross_document_conflicts(docs: list[dict]) -> list[dict]:
    """Detect inconsistencies across documents (e.g. passport number or name
    mismatch). Deterministic, provider-independent."""
    conflicts = []
    seen: dict[str, set] = {}
    for d in docs:
        for f in d.get("fields", []):
            key, val = f["key"], str(f["value"]).strip().upper()
            if key in ("passport_number", "surname", "given_names", "birth_date"):
                seen.setdefault(key, set()).add(val)
    for key, vals in seen.items():
        vals = {v for v in vals if v}
        if len(vals) > 1:
            conflicts.append({"field": key, "values": sorted(vals)})
    return conflicts
