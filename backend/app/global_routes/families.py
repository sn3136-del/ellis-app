"""Portal-family registry: deduplicated real official portals/platforms.

sync_families() loads the curated seed registry, upserts PortalFamily rows and
runs DETERMINISTIC verification:
  - every hostname on the curated government-suffix allowlist
    (visa_snapshot.authority.is_government_host) -> verified_official_domain;
  - otherwise the family STAYS seed_unverified (contractors and non-standard
    government domains need live/official-link evidence) — fail closed.

assign_families_to_pairs() binds each electronic pair policy to the family
serving its destination (evisa/eta), and upgrades visa-exempt pairs to
ENTRY_PREPARATION where the destination requires an electronic arrival card.

audit_official_portal_records() cross-checks previously stored
OfficialPortalRecord rows against the family registry and DOWNGRADES records
whose hostname belongs to a different destination's platform (data
contamination becomes 'conflicted', never silently served).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select, update

from ..visa_snapshot.authority import hostname as host_of
from ..visa_snapshot.authority import is_government_host
from ..visa_snapshot.models import OfficialPortalRecord
from ..visa_snapshot.registry import REFERENCE_DIR
from . import ELECTRONIC_OUTCOMES, FAMILY_VERIFICATION, PORTAL_FAMILY_KINDS
from .models import PortalFamily, RoutePairPolicy

FAMILIES_SEED = REFERENCE_DIR / "portal_families.json"

_OUTCOME_TO_KINDS = {
    "EVISA": ("evisa_portal", "immigration_authority"),
    "ELECTRONIC_AUTHORIZATION": ("eta_portal",),
}


# TERMS_CHOICE marks the portal's own terms/agreement gate: the declared
# agree-control is only ever transcribed at RUNTIME after the applicant has
# SIGNED the verbatim terms in Ellis (portal_terms_consent handoff). During
# recon the replay CAPTURES the terms text as evidence and, to observe the
# form behind the gate, replays the choice in a throwaway observation session
# that never carries applicant data and never submits anything.
_ENTRY_GATE_VOCAB = ("CLICK", "SCROLL_TO_BOTTOM", "CHECK", "TERMS_CHOICE")


def _validate_entry_gate(family_id: str, gate: dict) -> None:
    """A curated entry gate must stay inside the reversible vocabulary and
    declare where it ends — a typo here must fail the seed load, not a build."""
    actions = gate.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError(f"portal family {family_id}: entry_gate needs actions")
    for a in actions:
        if (a or {}).get("action") not in _ENTRY_GATE_VOCAB:
            raise ValueError(f"portal family {family_id}: entry_gate action "
                             f"{(a or {}).get('action')!r} outside "
                             f"{_ENTRY_GATE_VOCAB}")
        if a.get("action") != "SCROLL_TO_BOTTOM" and not str(a.get("selector") or "").strip():
            raise ValueError(f"portal family {family_id}: entry_gate "
                             f"{a['action']} needs a selector")
        if a.get("action") == "TERMS_CHOICE" and not str(
                a.get("terms_text_selector") or "").strip():
            raise ValueError(f"portal family {family_id}: TERMS_CHOICE needs a "
                             f"terms_text_selector capturing the verbatim terms")
    if not str(gate.get("expect_path") or "").startswith("/"):
        raise ValueError(f"portal family {family_id}: entry_gate needs an "
                         f"absolute expect_path")
    for k in gate.get("declared_handoffs") or []:
        if k not in ("captcha", "otp", "portal_terms_consent"):
            raise ValueError(f"portal family {family_id}: entry_gate declared "
                             f"handoff {k!r} unknown")


def load_seed() -> list[dict]:
    data = json.loads(FAMILIES_SEED.read_text())
    if data.get("registry") != "portal_families":
        raise ValueError("portal_families.json: wrong registry name")
    entries = data.get("entries") or []
    seen = set()
    for e in entries:
        if e["kind"] not in PORTAL_FAMILY_KINDS:
            raise ValueError(f"portal family {e['family_id']}: unknown kind {e['kind']}")
        if e["family_id"] in seen:
            raise ValueError(f"duplicate family_id {e['family_id']}")
        seen.add(e["family_id"])
        if e.get("entry_gate"):
            _validate_entry_gate(e["family_id"], e["entry_gate"])
    return entries


def sync_families(db) -> dict:
    """Upsert seed families and verify official identity deterministically."""
    created = updated = verified = unverified = 0
    for e in load_seed():
        fam = db.execute(select(PortalFamily).where(
            PortalFamily.family_id == e["family_id"])).scalars().first()
        hostnames = e.get("hostnames") or []
        all_gov = bool(hostnames) and all(is_government_host(h) for h in hostnames)
        if fam is None:
            fam = PortalFamily(family_id=e["family_id"],
                               verification_status="seed_unverified")
            db.add(fam)
            created += 1
        else:
            updated += 1
        fam.name = e["name"]
        fam.kind = e["kind"]
        fam.operator = e.get("operator", "")
        fam.operator_kind = e.get("operator_kind", "government")
        fam.base_url = e["base_url"]
        fam.hostnames = hostnames
        fam.destinations = e.get("destinations") or []
        fam.nationality_scope = e.get("nationality_scope") or []
        fam.supported_outcomes = e.get("supported_outcomes") or []
        fam.account_required = bool(e.get("account_required"))
        fam.entry_gate = e.get("entry_gate") or {}
        # Curated form paths are PATHS ONLY — they tell recon where this
        # family's real form lives and can never widen the hostname allowlist.
        fam.form_paths = [str(p) for p in (e.get("form_paths") or [])
                          if isinstance(p, str) and p.startswith("/")]
        fam.notes = e.get("notes", "")
        # Deterministic identity verification — never invented, never assumed.
        # A live_verified family keeps its stronger status; conflicted/
        # unavailable statuses are preserved until explicitly re-verified.
        if fam.verification_status in ("seed_unverified", "verified_official_domain"):
            if all_gov:
                fam.verification_status = "verified_official_domain"
                fam.verification_evidence = dict(fam.verification_evidence or {},
                                                 domain_check="all hostnames on curated government allowlist")
                verified += 1
            else:
                fam.verification_status = "seed_unverified"
                fam.verification_evidence = dict(fam.verification_evidence or {},
                                                 domain_check="hostnames NOT all on government allowlist; "
                                                              "requires live/official-link verification")
                unverified += 1
    db.commit()
    return {"created": created, "updated": updated,
            "verified_official_domain": verified, "still_unverified": unverified}


def _family_index(db) -> dict[str, list[PortalFamily]]:
    """destination alpha-3 -> families serving it (active only)."""
    idx: dict[str, list[PortalFamily]] = {}
    for fam in db.execute(select(PortalFamily).where(PortalFamily.active.is_(True))).scalars():
        for dest in fam.destinations or []:
            idx.setdefault(dest, []).append(fam)
    return idx


def pick_family(db_or_index, destination: str, outcome: str, nationality: str = "") -> PortalFamily | None:
    """Deterministically pick the family serving (destination, outcome).
    Prefers kind-matched government-operated families; respects a family's
    nationality scope. Returns None when no real family exists (honest gap)."""
    idx = db_or_index if isinstance(db_or_index, dict) else _family_index(db_or_index)
    kinds = _OUTCOME_TO_KINDS.get(outcome, ())
    candidates = []
    for fam in idx.get(destination, []):
        if outcome not in (fam.supported_outcomes or []):
            continue
        if fam.nationality_scope and nationality and nationality not in fam.nationality_scope:
            continue
        rank = (0 if fam.kind in kinds else 1,
                0 if fam.operator_kind == "government" else 1,
                fam.family_id)
        candidates.append((rank, fam))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])
    return candidates[0][1]


def arrival_card_family(idx: dict, destination: str) -> PortalFamily | None:
    for fam in idx.get(destination, []):
        if fam.kind == "arrival_card":
            return fam
    return None


def assign_families_to_pairs(db) -> dict:
    """Bind electronic pairs to their portal family; upgrade exempt pairs to
    ENTRY_PREPARATION where a VERIFIED electronic arrival card is required;
    clear stale bindings on every pair whose outcome no longer uses a portal
    (e.g. a research override changed an eVisa row to embassy)."""
    idx = _family_index(db)
    bound = missing_family = entry_prep = cleared = 0
    portal_scope = tuple(ELECTRONIC_OUTCOMES) + ("ENTRY_PREPARATION",)
    for p in db.execute(select(RoutePairPolicy).where(
            RoutePairPolicy.portal_family_id != "",
            RoutePairPolicy.route_outcome.notin_(portal_scope))).scalars():
        p.portal_family_id = ""
        cleared += 1
    pairs = db.execute(select(RoutePairPolicy).where(
        RoutePairPolicy.route_outcome.in_(
            tuple(ELECTRONIC_OUTCOMES) + ("VISA_EXEMPT", "ENTRY_PREPARATION")))).scalars()
    for p in pairs:
        if p.route_outcome in ELECTRONIC_OUTCOMES:
            fam = pick_family(idx, p.destination_country, p.route_outcome,
                              p.passport_nationality)
            if fam is not None:
                if p.portal_family_id != fam.family_id:
                    p.portal_family_id = fam.family_id
                bound += 1
            else:
                p.portal_family_id = ""
                missing_family += 1
        else:  # visa-exempt: a VERIFIED arrival-card platform makes it
               # ENTRY_PREPARATION; an unverified seed never relabels a route.
            fam = arrival_card_family(idx, p.destination_country)
            fam_verified = fam is not None and fam.verification_status in (
                "verified_official_domain", "verified_live")
            if fam_verified:
                p.route_outcome = "ENTRY_PREPARATION"
                p.portal_family_id = fam.family_id
                entry_prep += 1
            elif p.route_outcome == "ENTRY_PREPARATION":
                # arrival-card family gone or unverified -> plain exemption
                p.route_outcome = "VISA_EXEMPT"
                p.portal_family_id = ""
    db.commit()
    return {"electronic_bound": bound, "electronic_missing_family": missing_family,
            "entry_preparation_pairs": entry_prep, "stale_bindings_cleared": cleared}


def audit_official_portal_records(db) -> dict:
    """Downgrade stored portal records whose hostname belongs to a DIFFERENT
    destination's platform (contaminated data must never be served)."""
    host_to_dests: dict[str, set[str]] = {}
    for fam in db.execute(select(PortalFamily)).scalars():
        for h in fam.hostnames or []:
            host_to_dests.setdefault(h.lower(), set()).update(fam.destinations or [])
    downgraded = []
    for rec in db.execute(select(OfficialPortalRecord)).scalars():
        h = host_of(rec.url or "")
        if not h:
            continue
        known = host_to_dests.get(h.lower())
        if known and rec.destination_country not in known:
            if rec.verification_status != "conflicted":
                rec.verification_status = "conflicted"
                downgraded.append({
                    "portal_uid": rec.portal_uid,
                    "reason": f"hostname {h} belongs to platform serving "
                              f"{sorted(known)}, not {rec.destination_country}"})
    db.commit()
    return {"portal_records_downgraded": len(downgraded), "details": downgraded}


def mark_live_verified(db, family_id: str, evidence: dict) -> None:
    """Record a successful credential-free live observation (read-only)."""
    fam = db.execute(select(PortalFamily).where(
        PortalFamily.family_id == family_id)).scalars().first()
    if fam is None:
        raise ValueError(f"unknown portal family {family_id}")
    fam.verification_status = "verified_live"
    fam.verification_evidence = dict(fam.verification_evidence or {}, live=evidence)
    fam.last_verified_at = datetime.now(timezone.utc)
    fam.stale = False
    db.commit()


def mark_unreachable(db, family_id: str, reason: str) -> None:
    fam = db.execute(select(PortalFamily).where(
        PortalFamily.family_id == family_id)).scalars().first()
    if fam is None:
        return
    fam.verification_evidence = dict(fam.verification_evidence or {},
                                     last_error=reason[:400])
    # Reachability failure is honest evidence of UNAVAILABILITY only when the
    # domain itself was verified; an unverified seed just stays unverified.
    if fam.verification_status == "verified_live":
        fam.stale = True
    db.commit()


assert set(FAMILY_VERIFICATION) >= {"seed_unverified", "verified_official_domain", "verified_live"}
