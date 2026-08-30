"""Which mission handles an applicant, decided by where they live.

The query tool asks for a departure city and the requirements document marks
it required, but the city reached the model on a cache miss and was absent
from the cache key, so on any already-cached route it could not change the
answer at all. Four lookups of China to Japan from Beijing, Shanghai,
Guangzhou and a made-up town returned byte-identical answers.

The city is deliberately NOT put into the key. Doing that would mint a cache
entry per city anyone types and invalidate the warm cache in one move. What
goes in is the DISTRICT the city resolves to, using the slot the key already
carries. A destination with no district table resolves to "default", which is
exactly what every key holds today, so nothing already cached is disturbed.

Field 18 of their dictionary asks for 领区信息: "如目的地有领区划分，列出对应
关系" — where the destination divides into consular districts, list the
correspondence. So the table itself is the deliverable; resolving one row of
it for a given traveller is what makes the departure city mean something.
"""
from __future__ import annotations

import re
import unicodedata

from .registry import load_registry


def _norm(value: str) -> str:
    """Fold a city or province to a comparable key.

    Accepts what a traveller actually types: Beijing, beijing, BEIJING, 北京,
    北京市, Bei Jing. Punctuation, spacing and the administrative suffix all
    fall away."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    text = re.sub(r"\b(shi|city|province|prov\.?|municipality)\b", " ", text)
    text = re.sub(r"[市省区縣县自治区特别行政区]+$", "", text)
    text = re.sub(r"[^0-9a-z一-鿿]+", "", text)
    return text


def _table() -> dict:
    """destination ISO3 -> list of districts, from the verified registry."""
    try:
        reg = load_registry("consular_jurisdictions") or {}
    except Exception:  # noqa: BLE001 — a broken registry must not break lookups
        return {}
    out: dict[str, list] = {}
    for entry in reg.get("entries") or []:
        dest = str(entry.get("destination") or "").upper()
        if not dest:
            continue
        out.setdefault(dest, []).append(entry)
    return out


def districts_for(destination: str, nationality: str = "") -> list:
    """Every district the destination publishes for this applicant's country."""
    rows = _table().get(str(destination or "").upper(), [])
    nat = str(nationality or "").upper()
    if not nat:
        return rows
    scoped = [r for r in rows if str(r.get("applicant_country") or "").upper() == nat]
    return scoped or [r for r in rows if not r.get("applicant_country")]


def resolve(destination: str, city: str, nationality: str = "") -> str:
    """The district key for this applicant, or "default" when unknown.

    "default" is the value every cache key already carries, so a destination
    with no table, or a city that matches nothing, behaves exactly as before.
    """
    rows = districts_for(destination, nationality)
    if not rows:
        return "default"
    key = _norm(city)
    if not key:
        return "default"
    for row in rows:
        seat = _norm(row.get("city"))
        if seat and seat == key:
            return str(row.get("id") or seat)
        for area in (row.get("areas") or []) + (row.get("areas_zh") or []):
            if _norm(area) == key:
                return str(row.get("id") or _norm(row.get("city")))
    return "default"


def describe(destination: str, nationality: str = "", city: str = "") -> str | None:
    """Field 18's text: the correspondence, with the applicant's row marked.

    Their caliber asks for the mapping rather than a single answer, so the
    whole table is stated and the row that applies is pointed out."""
    rows = districts_for(destination, nationality)
    if not rows:
        return None
    picked = resolve(destination, city, nationality) if city else "default"
    parts = []
    for row in rows:
        areas = row.get("areas") or []
        line = f"{row.get('mission')}: {', '.join(areas)}" if areas else str(row.get("mission") or "")
        if picked != "default" and str(row.get("id") or "") == picked:
            line += "  <- covers your departure city"
        parts.append(line)
    return ". ".join(p for p in parts if p) or None
