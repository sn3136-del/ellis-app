"""Keep itinerary-dependent health conditions out of a canonical verdict.

A passport route cannot establish recent travel, origin, transit duration,
age or vaccination history. This response projection preserves the stated
condition without treating the first cached traveller's applicability as
everyone else's. It does not adjudicate medical or border eligibility.
"""
from __future__ import annotations

import re


_UNIVERSAL = re.compile(
    r"^(?:(?:required|mandatory|applies)\s+(?:for|to)\s+)?all\s+"
    r"(?:travell?ers|arrivals|visitors|passengers)"
    r"(?:\s+regardless of (?:nationality|origin|travel history))?[.!]?$", re.I)


def apply(guidance: dict, route: dict | None = None) -> dict:
    """Return a copy only when a health condition needs contextual checking.

    Even a matching country alone cannot prove a trigger: the official rule
    may depend on time spent in transit, age or travel dates. Retain the
    condition and question for assessment rather than declaring eligibility.
    """
    if not isinstance(guidance, dict):
        return guidance
    health = guidance.get("health_requirements")
    if not isinstance(health, list):
        return guidance  # malformed data remains visible to invariant checks
    result = []
    changed = False
    for item in health:
        if not isinstance(item, dict):
            result.append(item)
            continue
        trigger = item.get("trigger")
        text = trigger.strip() if isinstance(trigger, str) else ""
        countries = item.get("trigger_countries")
        has_countries = isinstance(countries, list) and bool(countries)
        contextual = has_countries or bool(text and not _UNIVERSAL.fullmatch(text))
        if not contextual or item.get("applicability") not in (
                "always_required", "not_applicable", "conditional"):
            result.append(item)
            continue
        projected = dict(item, applicability="conditional",
                         context_status="needs_itinerary_check",
                         context_note="Check the stated condition against your itinerary and recent travel.")
        # Preserve how the cached item was labelled, without changing or
        # promoting its source evidence. The trigger/question stay exact.
        projected.setdefault("source_applicability", item["applicability"])
        result.append(projected)
        changed = True
    return dict(guidance, health_requirements=result) if changed else guidance
