"""Proof checks for the consistency sweep: report only, nothing holds.

A verified verdict rests on a proof: the provenance the override layer
attaches to the answer. tstation.verdict_provenance_supported admits any
non-empty note as that proof, so a row can read "source verified" with no
quotation behind it at all. The matchers that read a page against a value
(evidence_validator.supports_disposition and friends) are reached only from
the freshness and structured-evidence paths, never from the serve or grading
path. This module runs them over every served row and reports, per row:

  proof_missing_quote           the proof carries no quotation, or its
                                quotation does not support the verdict
  proof_off_jurisdiction        the proof's page is not competent for the
                                destination (source_authority), with the
                                authority kind recorded so destination,
                                eu_visa_law, traveller_government,
                                third_party_government and non_government
                                separate in the evidence
  proof_nationality_unnamed     the quotation supports the verdict but is not
                                anchored to this nationality (the immd.gov.hk
                                combined-list shape behind HKG to KHM)
  verdict_detail_missing        a disposition with requirement_detail None,
                                which every serve-time invariant skips
  verdict_page_scheme_conflict  an unconditional exemption citing a list-based
                                scheme page (the ten Korea rows on K-ETA)

Every finding carries source_url, host_owner, authority_kind, exemption, a
600 character quote and checked_at. Nothing here writes, holds or regrades:
the switches that turn these into refusals live in T7 and T11.
"""
from __future__ import annotations

from datetime import datetime

from .consistency_sweep import make_finding

QUOTE_CHARS = 600
CODES = ("proof_missing_quote", "proof_off_jurisdiction", "proof_nationality_unnamed",
         "verdict_detail_missing", "verdict_page_scheme_conflict")


def verdict_proof(provenance: dict | None) -> dict | None:
    """The proof behind the disposition, resolved the way
    tstation._reviewed_policy_end resolves it: the per-field proof for
    disposition when there is one, else the entry-level provenance when the
    entry claims the disposition, else nothing."""
    if not isinstance(provenance, dict):
        return None
    field_proofs = provenance.get("field_provenance")
    field_proofs = field_proofs if isinstance(field_proofs, dict) else {}
    claimed = provenance.get("fields")
    claimed = claimed if isinstance(claimed, (list, tuple, set, dict)) else ()
    proof = field_proofs.get("disposition")
    if isinstance(proof, dict):
        return dict(proof, fields=["disposition"])
    if "disposition" in field_proofs or "disposition" not in claimed:
        return None
    return dict(provenance, fields=["disposition"])


def quoted_passages(proof: dict) -> list[str]:
    """The literal quotations a proof carries, never the reviewer's own
    assertion: grade_evidence._quoted_passages minus its human-note escape."""
    raw = proof.get("quotes", [])
    if not isinstance(raw, (list, tuple)) or any(not isinstance(q, str) for q in raw):
        raw = []
    single = proof.get("quote")
    quotes = [q.strip() for q in (single, *raw) if isinstance(q, str) and q.strip()]
    note = proof.get("note")
    if isinstance(note, str) and "Quote:" in note:
        quotes.append(note.split("Quote:", 1)[1].split("Scope:", 1)[0].strip())
    source_quote = proof.get("source_quote")
    if isinstance(source_quote, str) and source_quote.strip():
        quotes.append(source_quote.strip())
    return [q for q in quotes if q]


def provenance_quality(provenance: dict | None) -> str:
    """quoted when the verdict proof carries a literal quotation, asserted
    when it rests on a note alone, none when there is no verdict proof."""
    proof = verdict_proof(provenance)
    if proof is None:
        return "none"
    return "quoted" if quoted_passages(proof) else "asserted"


def _evidence(proof: dict, route: dict, quote: str = "") -> dict:
    from .authority import hostname
    from .authority_ownership import government_owner
    from .source_authority import authority_for
    url = str(proof.get("source_url") or "")
    authority = authority_for(url, route, citation=(proof.get("note") or "") + " " + quote)
    return {"source_url": url, "host_owner": government_owner(hostname(url)) or "",
            "authority_kind": authority.kind, "exemption": authority.exemption,
            "quote": quote[:QUOTE_CHARS], "verified_at": proof.get("verified_at"),
            "verifier": proof.get("verifier")}


def check_route(route: dict, merged: dict, provenance: dict | None, qc_rows: list[dict], *,
                now: datetime, canonical_key: str) -> list:
    """The five checks for one served route. ``merged`` is the guidance after
    the override layer, ``provenance`` its source_verified block."""
    from . import kimi_primary
    from .evidence_validator import supports_disposition
    from .source_authority import is_competent
    findings = []
    checked_at = now.isoformat()
    disposition = str(merged.get("disposition") or "").upper()
    nationality = str(route.get("passport_nationality") or "").upper()
    published = [rec for rec in qc_rows if not rec.get("_held")]
    grades = sorted({str(rec.get("confidence_level") or "") for rec in qc_rows})
    base = {"published": bool(published), "grades": grades,
            "disposition": disposition, "requirement_detail": merged.get("requirement_detail")}

    def add(code, field_name, observed, expected, evidence):
        findings.append(make_finding(code, canonical_key, field_name, observed=observed,
                                     expected=expected, surfaces=["proof"],
                                     evidence={**base, **evidence}, checked_at=checked_at))

    # 4. a verdict with no subcategory
    if disposition and merged.get("requirement_detail") in (None, ""):
        add("verdict_detail_missing", "requirement_detail", None,
            list(kimi_primary.DETAIL_FAMILY.get(disposition, ())),
            {"source_url": merged.get("source_url") or "",
             "diagnostics": [d for d in (merged.get("_diagnostics") or []) if "requirement_detail" in str(d)]})
    # 5. an unconditional exemption on a scheme page
    detail = str(merged.get("requirement_detail") or "")
    if disposition == "VISA_EXEMPT" and detail in ("", "unconditional_visa_free"):
        cited = [merged.get("source_url"), merged.get("official_portal_url")]
        if isinstance(provenance, dict):
            cited.append(provenance.get("source_url"))
        pages = [c for c in cited if c and kimi_primary.is_authorization_page(c)]
        if pages:
            add("verdict_page_scheme_conflict", "disposition", disposition,
                "a conditional exemption or the scheme itself",
                {"source_url": pages[0], "cited_pages": pages,
                 "authority_kind": "scheme_page",
                 # Whether the serve-time rule withholds this row (T7): only a
                 # scheme whose list the registry holds as established does.
                 "scheme_list_established": kimi_primary.established_scheme_page(*pages)})
    # 1 to 3. the verdict proof
    proof = verdict_proof(provenance)
    if proof is None:
        return findings
    passages = quoted_passages(proof)
    url = str(proof.get("source_url") or "")
    if not passages:
        add("proof_missing_quote", "disposition", "asserted", "quoted",
            {**_evidence(proof, route), "reason": "no quotation in the verdict proof",
             "note": str(proof.get("note") or "")[:QUOTE_CHARS]})
    else:
        text = "\n".join(passages)
        if not supports_disposition(text, disposition):
            add("proof_missing_quote", "disposition", "quoted but unsupporting", "supporting quote",
                {**_evidence(proof, route, text), "reason": "quotation does not state the verdict"})
        elif not supports_disposition(text, disposition, nationality=nationality):
            add("proof_nationality_unnamed", "disposition", "unanchored", nationality,
                {**_evidence(proof, route, text),
                 "reason": "quotation supports the verdict but does not name this nationality"})
    if url and not is_competent(url, route, "disposition",
                                citation=(proof.get("note") or "") + " " + " ".join(passages)):
        add("proof_off_jurisdiction", "source_url", url, route.get("destination_country"),
            _evidence(proof, route, "\n".join(passages)))
    return findings


def check_rows(rows: list[tuple], *, now: datetime) -> dict:
    """Run check_route over the sweep's (row, projections) pairs. The override
    layer is read again here (a pure read) because the reader projection
    withholds a held answer's provenance."""
    from . import kimi_primary, verified_overrides
    findings = []
    quality = {"quoted": 0, "asserted": 0, "none": 0}
    for r, proj in rows:
        route = proj["route"]
        merged, prov = verified_overrides.apply(dict(kimi_primary.served_guidance(r)), route)
        quality[provenance_quality(prov)] += 1
        findings.extend(check_route(route, merged, prov, proj["qc_rows"], now=now,
                                    canonical_key=kimi_primary.canonical_key(r.cache_key or "")))
    by_code = {code: 0 for code in CODES}
    published = {code: 0 for code in CODES}
    high = {code: 0 for code in CODES}
    kinds: dict = {}
    for f in findings:
        by_code[f.code] += 1
        if f.evidence.get("published"):
            published[f.code] += 1
        if "High" in (f.evidence.get("grades") or []):
            high[f.code] += 1
        if f.code == "proof_off_jurisdiction":
            kinds[f.evidence.get("authority_kind")] = kinds.get(f.evidence.get("authority_kind"), 0) + 1
    # The report phase of ELLIS_REQUIRE_DETAIL (T7): what each switch state
    # would do to the rows filed here, measured before T14 flips it.
    detail_rows = [f for f in findings if f.code == "verdict_detail_missing"
                   and f.evidence.get("disposition") in kimi_primary.DETAIL_FAMILY]
    switch = {"mode": kimi_primary.require_detail_mode(), "routes": len(detail_rows),
              "cap_would_regrade_high_routes": sum("High" in (f.evidence.get("grades") or []) for f in detail_rows),
              "on_would_hold_published_routes": sum(bool(f.evidence.get("published")) for f in detail_rows)}
    return {"findings": findings,
            "summary": {"counts": by_code, "published": published, "high": high,
                        "off_jurisdiction_by_kind": kinds, "provenance_quality": quality,
                        "require_detail_switch": switch}}
