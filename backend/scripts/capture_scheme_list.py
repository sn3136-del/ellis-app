"""Capture a programme's eligible-passport list from its official page.

A nationality list is READ, never typed. This script fetches a candidate
source_url through the bounded fetcher (the path the freshness sweep uses),
applies the competence test to the FINAL hostname (a redirect off the
destination's domain is a failed read), decodes a page that embeds its
content as a JSON-escaped HTML blob (the Home Affairs visa pages), cuts the
bounded list section between two anchor phrases, resolves each list item to
an ISO3 code through the country registry, and writes a REVIEW file with the
quote, its sha256 and the proposed list.

It never writes data/database_seed/scheme_lists.json. A person reads the
review file and the entry it proposes (--emit-entry prints it) and commits
the entry; the registry's own load gates then re-verify the quote hash and
every nationality against the quote. With --compare-entry <id> the capture
is compared against the registry's stored list and the outcome is written
as "unchanged" or "changed": a changed capture NEVER writes, it marks the
entry stale for the weekly timer and the sweep to act on.

Usage:
  capture_scheme_list.py --id kor_keta --destination KOR --url <url> \
      --start "<anchor phrase>" --end "<anchor phrase>" --out review.json
      [--emit-entry] [--compare-entry kor_keta] [--fixture page.txt]
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REVIEW_SCHEMA = 1


class CaptureRefused(Exception):
    """A bounded, printable failure: the class of reason, never page text."""


def _decode_embedded(html: str) -> str:
    """Home Affairs pages carry the visa text as an HTML-escaped JSON string
    inside the page. Unescape the entities and the JSON escapes so the same
    text extractor reads what a browser renders."""
    text = html_lib.unescape(html)
    text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)
    text = text.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
    return text


def page_text(fetch_result) -> str:
    from app.visa_snapshot.fetching import html_to_text
    text = fetch_result.content_text or ""
    raw = getattr(fetch_result, "raw_html", None)
    if raw:
        text = text + "\n" + html_to_text(_decode_embedded(raw))
    return text


def cut_section(text: str, start: str, end: str) -> str:
    """The text between the first ``start`` anchor and the next ``end`` anchor,
    both exclusive. Empty when either anchor is missing."""
    norm = text.replace("\xa0", " ")
    i = norm.find(start)
    if i < 0:
        return ""
    j = norm.find(end, i + len(start))
    if j < 0:
        return ""
    return norm[i + len(start):j].strip("\n ")


def resolve_items(quote: str) -> tuple[list[str], list[str]]:
    from app.visa_snapshot.scheme_registry import country_code, list_items
    codes, unresolved = [], []
    for item in list_items(quote):
        code = country_code(item)
        if code:
            if code not in codes:
                codes.append(code)
        else:
            unresolved.append(item)
    return codes, unresolved


def fetch_page(url: str, *, fixture: str | None = None):
    """The bounded fetch, or a saved page for tests. Returns (result, raw_html)."""
    from app.visa_snapshot.fetching import FetchResult, fetch
    if fixture:
        body = Path(fixture).read_text(encoding="utf-8")
        from urllib.parse import urlparse
        return FetchResult(requested_url=url, ok=True, final_url=url,
                           final_hostname=(urlparse(url).hostname or "").lower(), http_status=200,
                           content_text=body, retrieved_at=datetime.now(timezone.utc).isoformat()), ""
    result = fetch(url, timeout_seconds=30)
    raw = ""
    if result.ok and result.final_url:
        # The text extractor drops script content; the embedded JSON blob
        # lives there, so the raw body is read once more for decoding.
        try:
            import httpx
            with httpx.Client(follow_redirects=True, timeout=30,
                              headers={"User-Agent": "Mozilla/5.0 EllisVisaResearch/1.0"}) as c:
                r = c.get(result.final_url)
                if r.status_code == 200 and (r.url.host or "").lower() == result.final_hostname:
                    raw = r.text
        except Exception:  # noqa: BLE001 - the plain text still stands
            raw = ""
    return result, raw


def capture(*, scheme_id: str, destination: str, url: str, start: str, end: str,
            fixture: str | None = None, checked_at: str | None = None) -> dict:
    from app.visa_snapshot.scheme_registry import quote_hash
    from app.visa_snapshot.source_authority import is_competent
    when = checked_at or datetime.now(timezone.utc).date().isoformat()
    result, raw = fetch_page(url, fixture=fixture)
    review = {"schema_version": REVIEW_SCHEMA, "id": scheme_id, "destination": destination.upper(),
              "requested_url": url, "final_url": result.final_url, "checked_at": when,
              "fetched_at": datetime.now(timezone.utc).isoformat(), "outcome": "",
              "list_quote": "", "quote_sha256": "", "proposed_eligible_nationalities": [],
              "unresolved_items": []}
    if not result.ok:
        review["outcome"] = "fetch_failed"
        review["reason"] = "anti_bot_challenge" if result.challenge else (result.error or "fetch failed")[:80]
        return review
    if not is_competent(result.final_url, {"destination_country": destination.upper()}):
        review["outcome"] = "refused_non_competent_host"
        review["reason"] = f"final host {result.final_hostname} is not the destination's government"
        return review
    result.raw_html = raw  # type: ignore[attr-defined]
    text = page_text(result)
    quote = cut_section(text, start, end)
    if not quote:
        review["outcome"] = "anchors_not_found"
        return review
    codes, unresolved = resolve_items(quote)
    review.update(outcome="captured", list_quote=quote, quote_sha256=quote_hash(quote),
                  proposed_eligible_nationalities=codes, unresolved_items=unresolved)
    return review


def compare_with_entry(review: dict, entry_id: str) -> dict:
    """Never writes: reports unchanged or changed against the stored list."""
    from app.visa_snapshot import scheme_registry
    stored = next((e for e in scheme_registry.entries() if e["id"] == entry_id), None)
    if stored is None:
        return {"compared_to": entry_id, "result": "no_stored_entry"}
    if review.get("outcome") != "captured":
        return {"compared_to": entry_id, "result": "capture_failed", "stale": True}
    same = (review["quote_sha256"] == stored.get("quote_sha256")
            and sorted(review["proposed_eligible_nationalities"]) == sorted(stored.get("eligible_nationalities") or []))
    return {"compared_to": entry_id, "result": "unchanged" if same else "changed", "stale": not same,
            "stored_quote_sha256": stored.get("quote_sha256"),
            "added": sorted(set(review["proposed_eligible_nationalities"]) - set(stored.get("eligible_nationalities") or [])),
            "removed": sorted(set(stored.get("eligible_nationalities") or []) - set(review["proposed_eligible_nationalities"]))}


def emit_entry(review: dict, *, scheme_kind: str, name: str, requirement_details: list[str],
               patterns: list[str], portal_marker: str, excluded_documents: list[str] | None = None) -> dict:
    """The registry entry a reviewer commits after reading the review file."""
    established = review.get("outcome") == "captured" and bool(review.get("proposed_eligible_nationalities"))
    return {"id": review["id"], "destination": review["destination"], "scheme_kind": scheme_kind,
            "name": name, "requirement_details": requirement_details, "program_id": review["id"],
            "list_state": "established" if established else "not_established",
            "eligible_nationalities": review.get("proposed_eligible_nationalities") or [],
            "excluded_documents": excluded_documents or [],
            "refusal_note": "", "patterns": patterns, "portal_marker": portal_marker,
            "source_url": review.get("final_url") or review.get("requested_url"),
            "list_quote": review.get("list_quote") or "", "quote_sha256": review.get("quote_sha256") or "",
            "checked_at": review["checked_at"] if established else "",
            "valid_until": "", "capture_outcome": review.get("outcome"),
            "capture_reason": review.get("reason", "")}


def write_review(review: dict, out: str) -> None:
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".capture-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(review, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--id", required=True)
    ap.add_argument("--destination", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fixture", help="a saved page text instead of a fetch (tests)")
    ap.add_argument("--compare-entry", help="compare against this stored registry entry; never writes")
    ap.add_argument("--emit-entry", action="store_true", help="print the registry entry to commit")
    ap.add_argument("--scheme-kind", default="eta")
    ap.add_argument("--name", default="")
    ap.add_argument("--requirement-detail", action="append", default=[])
    ap.add_argument("--pattern", action="append", default=[])
    ap.add_argument("--portal-marker", default="")
    args = ap.parse_args(argv)
    review = capture(scheme_id=args.id, destination=args.destination, url=args.url,
                     start=args.start, end=args.end, fixture=args.fixture)
    if args.compare_entry:
        review["comparison"] = compare_with_entry(review, args.compare_entry)
    write_review(review, args.out)
    if args.emit_entry:
        print(json.dumps(emit_entry(review, scheme_kind=args.scheme_kind, name=args.name or args.id,
                                    requirement_details=args.requirement_detail, patterns=args.pattern,
                                    portal_marker=args.portal_marker), ensure_ascii=False, indent=1))
    else:
        print(json.dumps({k: review.get(k) for k in ("id", "outcome", "reason", "final_url",
                                                      "proposed_eligible_nationalities", "unresolved_items",
                                                      "comparison")}, ensure_ascii=False))
    return 0 if review["outcome"] == "captured" else 2


if __name__ == "__main__":
    sys.exit(main())
