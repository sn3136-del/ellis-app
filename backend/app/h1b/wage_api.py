"""H1B wage-level determination + occupation-classification endpoints.

These are SUGGESTION surfaces. The wage level and the SOC/NAICS codes are legal
representations on the ETA-9035 / I-129, not lookups: Ellis COMPUTES/SUGGESTS
them from OFFICIAL, free government data and a human confirms. Nothing here is
ever written into a filing automatically, and nothing here defaults a suggested
value into a filed answer.

Party-scoped (petitioner-or-admin): the offered wage, the worksite, and the SOC
are petitioner-private, so a beneficiary-bound caller is refused. Authorization
rides api.py's per-party helpers (findings #3/#14/#20), the same doctrine the
counsel surfaces use.

Data seams — imported LAZILY, and every one degrades HONESTLY when unconfigured
(never a fabricated wage or code):
  - app/h1b/wage_data: is_available / lookup_area / compute_wage_level
    (Agent 1; DOL OFLC OEWS, a pure offline computation over the cached wage
    CSVs, refreshed each July 1).
  - app/providers/occupation: classify_nioccs / search_onet
    (Agent 2; CDC NIOCCS SOC+NAICS, O*NET Web Services).
Execution-class honesty: every computed value carries its source + as_of date +
the geo/label caveats (GeoLvl >= 2 means DOL fell off the worksite MSA; a
High/Annual Wage label voids the 2080-hour conversion). When the data or the
network is unavailable the payload says so — it never invents a level or a code.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from .. import models
from ..db import get_session
from ..security import Principal, get_principal, require_owner
from . import models as h1b_models
from .api import _authorize_step_action
from .disclaimer import DISCLAIMER_VERSION, disclaimer

router = APIRouter(prefix="/h1b", tags=["h1b-wage"])

# Framing pinned on every suggestion payload: the value is Ellis's computation
# from official data, and a human confirms it — it is never a filed answer.
_SUGGESTION_NOTE = (
    "A suggestion computed from official government data. The wage level and the "
    "SOC/NAICS codes are legal representations on the ETA-9035/I-129 that a human "
    "must confirm; Ellis never writes a suggested value into a filing.")

# CDC's own status for NIOCCS, carried on every classification payload so a
# consumer knows to cache it and confirm with a human, never hard-depend on it.
_NIOCCS_CAVEAT = (
    "CDC states NIOCCS is no longer supported (agency RIF), though the service "
    "is still live. Treat every classification as advisory: cache it and confirm "
    "the code with a human before use.")


def _load_parent(db, case_id: str, principal: Principal) -> models.VisaApplication:
    parent = db.get(models.VisaApplication, case_id)
    if parent is None or parent.visa_type != "h1b":
        raise HTTPException(404, "h1b case not found")
    require_owner(principal, parent.org_id)
    return parent


def _petitioner_answers(db, case_id: str) -> dict:
    pet = db.execute(select(h1b_models.CaseParty).where(
        h1b_models.CaseParty.application_id == case_id,
        h1b_models.CaseParty.role == "petitioner")).scalars().first()
    return dict((pet.answers or {}) if pet else {})


def _to_number(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value or "").strip().replace(",", "").replace("$", "")
    try:
        return float(s)
    except ValueError:
        return None


def _wage_inputs(pet: dict) -> dict:
    return {
        "soc_code": str(pet.get("soc_code") or "").strip(),
        "state": str(pet.get("worksite_state") or "").strip(),
        "city": str(pet.get("worksite_city") or "").strip(),
        "county": str(pet.get("worksite_county") or "").strip(),
        "area": str(pet.get("worksite_area") or "").strip(),
        "offered_wage": _to_number(pet.get("wage_offer")),
        "wage_unit": (str(pet.get("wage_offer_unit") or "year").strip().lower()
                      or "year"),
    }


def _missing_inputs(inputs: dict) -> list[str]:
    missing = []
    if not inputs["soc_code"]:
        missing.append("soc_code")
    if inputs["offered_wage"] is None:
        missing.append("wage_offer")
    if not (inputs["area"] or inputs["state"] or inputs["city"]
            or inputs["county"]):
        missing.append("worksite")
    return missing


def _wage_data_module():
    try:
        from . import wage_data
    except Exception:  # noqa: BLE001
        return None
    return wage_data


def _resolve_area(wd, inputs: dict) -> tuple[str, dict | None]:
    """(area_code, lookup) — resolve the worksite to an OFLC area code. A
    directly-supplied area code wins; otherwise the worksite COUNTY (then city),
    state-qualified, is looked up. Returns ('', lookup) when ambiguous or
    unresolved so the caller degrades honestly (a wrong OEWS area is a real
    filing error), surfacing the lookup's candidates for a human to pick."""
    if inputs["area"]:
        return inputs["area"], None
    lookup = getattr(wd, "lookup_area", None)
    if not callable(lookup):
        return "", None
    last = None
    for place in (inputs["county"], inputs["city"]):
        if not place:
            continue
        query = f"{place}, {inputs['state']}" if inputs["state"] else place
        try:
            out = lookup(query)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(out, dict):
            last = out
            if out.get("area_code") and not out.get("ambiguous"):
                return str(out["area_code"]), out
    return "", last


@router.post("/cases/{case_id}/wage-analysis")
def wage_analysis(case_id: str, locale: str = "en",
                  principal: Principal = Depends(get_principal),
                  db=Depends(get_session)):
    """Compute the OEWS wage LEVEL for this case's worksite + SOC + offered wage
    from DOL OFLC data. Petitioner-or-admin (the wage/worksite/SOC are
    petitioner-private). A SUGGESTION only — never written to the filing, never a
    fabricated level: when the data is not downloaded, the worksite cannot be
    resolved, or the case lacks inputs, the payload says so honestly."""
    _load_parent(db, case_id, principal)
    _authorize_step_action(db, case_id, principal, "petitioner")

    base = {"case_id": case_id, "kind": "wage_level_suggestion",
            "is_suggestion": True, "note": _SUGGESTION_NOTE,
            "attorney_disclaimer": disclaimer(locale),
            "disclaimer_version": DISCLAIMER_VERSION}

    inputs = _wage_inputs(_petitioner_answers(db, case_id))
    missing = _missing_inputs(inputs)
    if missing:
        return {**base, "available": False, "level": None,
                "reason": "the case is missing the facts needed to compute a wage "
                          "level; the petitioner must provide them first",
                "missing_inputs": missing, "soc_code": inputs["soc_code"]}

    wd = _wage_data_module()
    is_avail = getattr(wd, "is_available", None)
    compute = getattr(wd, "compute_wage_level", None)
    available = False
    if callable(is_avail) and callable(compute):
        try:
            available = bool(is_avail())
        except Exception:  # noqa: BLE001
            available = False
    if not (available and callable(compute)):
        return {**base, "available": False, "level": None,
                "reason": "the DOL OFLC wage data is not downloaded on this "
                          "server, so no wage level can be computed",
                "data_status": "unavailable", "soc_code": inputs["soc_code"]}

    area_code, lookup = _resolve_area(wd, inputs)
    if not area_code:
        return {**base, "available": False, "level": None,
                "reason": "the worksite could not be resolved to a single OFLC "
                          "wage area; confirm the worksite county",
                "data_status": "worksite_unresolved",
                "area_lookup": lookup, "soc_code": inputs["soc_code"]}

    try:
        result = compute(area_code=area_code, soc_code=inputs["soc_code"],
                         offered_wage=inputs["offered_wage"],
                         wage_unit=inputs["wage_unit"])
    except Exception:  # noqa: BLE001 — a bad signature/runtime failure degrades
        result = None
    if not isinstance(result, dict):
        return {**base, "available": False, "level": None,
                "reason": "the OFLC wage computation could not be completed",
                "data_status": "unresolved", "soc_code": inputs["soc_code"]}

    level_wages = result.get("level_wages") or {}
    return {**base,
            "available": bool(result.get("available")),
            "area_code": result.get("area_code") or area_code,
            "soc_code": result.get("soc_code") or inputs["soc_code"],
            "soc_title": result.get("soc_title"),
            "level": result.get("level"),
            "level_wages": result.get("level_wages"),
            # The Level-I annual wage is the prevailing-wage floor.
            "prevailing_wage": level_wages.get(1),
            "offered_annual": result.get("offered_annual"),
            "meets_prevailing": result.get("meets_prevailing"),
            "geo_level": result.get("geo_level"),
            # GeoLvl >= 2 means DOL fell off the worksite MSA — surfaced, never
            # silently converted away.
            "geo_caveat": result.get("geo_caveat") or "",
            "label": result.get("label") or "",
            "invalid_2080_conversion": bool(result.get("invalid_2080_conversion")),
            # A High/Annual Wage label voids the 2080-hour conversion.
            "label_caveat": (result.get("status")
                             if result.get("invalid_2080_conversion") else ""),
            "caveats": result.get("caveats") or [],
            "status": result.get("status"),
            "source": result.get("source") or "",
            "as_of": result.get("as_of") or ""}


class ClassifyOccupationBody(BaseModel):
    industry_text: str = ""
    occupation_text: str = ""


def _normalize_suggestions(items) -> list[dict]:
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        out.append({"code": it.get("code") or "",
                    "title": it.get("title") or "",
                    "probability": it.get("probability")})
    return out


def _classify_nioccs(industry_text: str, occupation_text: str) -> dict:
    unavailable = {"available": False, "live": False, "caveat": _NIOCCS_CAVEAT,
                   "confirm_before_use": True, "soc": [], "naics": []}
    try:
        from ..providers import occupation as occ
    except Exception:  # noqa: BLE001
        return {**unavailable, "reason": "occupation classifier module unavailable"}
    fn = getattr(occ, "classify_nioccs", None)
    if not callable(fn):
        return {**unavailable, "reason": "NIOCCS classifier is not configured"}
    try:
        out = fn(industry_text=industry_text, occupation_text=occupation_text)
    except Exception:  # noqa: BLE001 — InvalidClassificationInput / transport
        return {**unavailable, "reason": "the NIOCCS request could not be made"}
    if not isinstance(out, dict):
        return {**unavailable, "reason": "NIOCCS returned no usable result"}
    soc = _normalize_suggestions(out.get("soc"))
    naics = _normalize_suggestions(out.get("naics"))
    live = bool(out.get("live"))
    return {"available": live and bool(soc or naics),
            "live": live,
            "source": out.get("source") or "nioccs",
            "caveat": out.get("caveat") or _NIOCCS_CAVEAT,
            "note": out.get("note") or "",
            "confirm_before_use": True,
            "soc": soc, "naics": naics}


def _search_onet(keyword: str) -> dict:
    kw = (keyword or "").strip()
    if not kw:
        return {"available": False, "reason": "no occupation_text to search",
                "matches": []}
    try:
        from ..providers import occupation as occ
    except Exception:  # noqa: BLE001
        return {"available": False, "reason": "occupation module unavailable",
                "matches": []}
    fn = getattr(occ, "search_onet", None)
    if not callable(fn):
        return {"available": False,
                "reason": "the O*NET Web Services key is not configured",
                "matches": []}
    try:
        out = fn(kw)
    except Exception:  # noqa: BLE001
        return {"available": False, "reason": "the O*NET request could not be made",
                "matches": []}
    if not isinstance(out, dict):
        return {"available": False, "reason": "O*NET returned no usable result",
                "matches": []}
    live = bool(out.get("live"))
    matches = [{"code": m.get("code") or "", "title": m.get("title") or ""}
               for m in (out.get("matches") or []) if isinstance(m, dict)]
    return {"available": live, "live": live,
            "source": out.get("source") or "onet",
            "note": out.get("note") or "", "matches": matches}


@router.post("/cases/{case_id}/classify-occupation")
def classify_occupation(case_id: str, body: ClassifyOccupationBody,
                        locale: str = "en",
                        principal: Principal = Depends(get_principal),
                        db=Depends(get_session)):
    """Suggest SOC + NAICS codes from CDC NIOCCS and O*NET matches for the given
    industry/occupation text. Petitioner-or-admin (the SOC/NAICS become filing
    facts). Each suggestion carries its probability and the confirm-before-use
    framing; NIOCCS carries CDC's 'no longer supported' caveat. Honest-degrade
    when a provider is unconfigured or unreachable — never a fabricated code."""
    _load_parent(db, case_id, principal)
    _authorize_step_action(db, case_id, principal, "petitioner")
    industry_text = (body.industry_text or "").strip()
    occupation_text = (body.occupation_text or "").strip()
    if not (industry_text or occupation_text):
        raise HTTPException(422, "provide industry_text and/or occupation_text "
                                 "to classify")
    return {"case_id": case_id, "is_suggestion": True, "note": _SUGGESTION_NOTE,
            "nioccs": _classify_nioccs(industry_text, occupation_text),
            "onet": _search_onet(occupation_text),
            "attorney_disclaimer": disclaimer(locale),
            "disclaimer_version": DISCLAIMER_VERSION}
