"""Regional and transit policies that are not a route verdict.

The 2026-08-31 Trip.com evaluation asked Ellis two questions its route
answers could not carry: does Hainan's regional visa-free entry cover an
Indian citizen, and what is China's visa-free transit policy. Both were
answered with a generic route verdict and judged "not covered". The gap is
structural: these policies attach to a REGION or a TRANSIT pattern, not to
one passport x destination verdict, so they need their own store.

The store is a seed file with the same discipline as verified overrides:
every entry carries a government source URL (gated at load), a verified_at
date and a verifier. An entry with a non-government source never loads.
Entries surface in two places:
  - a question that names the policy (its trigger words) gets the note even
    when the route is unclear, alongside the clarify
  - a route lookup whose nationality the policy covers gets the note as
    context beside the record answer
"""
from __future__ import annotations

import json
import os
import threading

from .authority import hostname, is_government_host

_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data",
                     "database_seed", "special_policies.json")
_LOCK = threading.Lock()
_STATE: dict = {"mtime": None, "entries": []}


def _seed_path() -> str:
    return os.environ.get("ELLIS_SPECIAL_POLICIES", os.path.abspath(_PATH))


def _load() -> list[dict]:
    path = _seed_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    with _LOCK:
        if _STATE["mtime"] == mtime:
            return _STATE["entries"]
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return _STATE["entries"]
        entries = []
        for e in raw if isinstance(raw, list) else []:
            url = str(e.get("source_url") or "")
            # The same gate every verified fact passes: a policy note with
            # no government page behind it does not exist.
            if not (url and is_government_host(hostname(url))):
                continue
            if not (e.get("id") and e.get("title_en") and e.get("summary_en")):
                continue
            e = dict(e)
            e["triggers"] = [str(t).lower() for t in (e.get("triggers") or [])]
            e["applies_to"] = [str(c).upper() for c in
                               (e.get("applies_to") or [])]
            entries.append(e)
        _STATE.update(mtime=mtime, entries=entries)
        return entries


def reload() -> None:
    with _LOCK:
        _STATE["mtime"] = None


def _payload(e: dict) -> dict:
    return {"id": e["id"],
            "region": e.get("region") or "",
            "title": e["title_en"], "title_zh": e.get("title_zh") or "",
            "summary": e["summary_en"],
            "summary_zh": e.get("summary_zh") or "",
            "source_url": e["source_url"],
            "valid_until": e.get("valid_until") or "",
            "verified_at": e.get("verified_at") or ""}


def for_question(question: str) -> list[dict]:
    """Entries whose trigger words appear in the question, either script."""
    q = str(question or "").lower()
    if not q.strip():
        return []
    return [_payload(e) for e in _load()
            if any(t in q for t in e["triggers"])]


def for_route(route: dict) -> list[dict]:
    """Entries that cover this route's destination and nationality."""
    dest = str((route or {}).get("destination_country") or "").upper()
    nat = str((route or {}).get("passport_nationality") or "").upper()
    out = []
    for e in _load():
        if str(e.get("destination") or "").upper() != dest:
            continue
        covered = e["applies_to"]
        if covered and nat not in covered:
            continue
        out.append(_payload(e))
    return out


def attach(out: dict, *, question: str = "", route: dict | None = None) -> dict:
    """Merge the notes for a question and a route into the answer, once.

    When the asker's nationality is known, every attached note also says
    whether it covers THAT passport. The Trip.com evaluation asked whether
    Hainan's visa-free entry covers an Indian citizen: the decisive part of
    the answer is that India is not on the list, and a note that only
    described the program would leave the asker to guess."""
    nat = str((route or {}).get("passport_nationality") or "").upper()
    trip = str((route or {}).get("arrival_date") or "")
    by_id = {e["id"]: e for e in _load()}
    seen = {}
    for note in (for_question(question) + (for_route(route) if route else [])):
        seen.setdefault(note["id"], note)
    if seen:
        notes = []
        for note in seen.values():
            covered = (by_id.get(note["id"], {}).get("applies_to") or [])
            if nat and covered:
                note = dict(note, applies_to_you=(nat in covered))
            # A policy with a published end date, asked about for a trip
            # beyond it, says so instead of letting the date pass silently.
            until = note.get("valid_until") or ""
            if trip and until and trip > until:
                note = dict(note, beyond_verified_window=True)
            notes.append(note)
        out["special_policies"] = notes
    return out
