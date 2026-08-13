"""Travel-authorization + Schengen stay endpoints.

Three reads, no writes. Nothing on this router files, pays, books or submits
anything — it serves curated truth about visa-waiver travel authorizations and
delegates the Schengen 90/180 arithmetic to its own module.

Org scoping rides the ordinary security helper: every route requires an
authenticated principal, and each response echoes the org it was served to so a
cached payload can never be replayed under another tenant's name. There is no
per-org resource here to own — the curated authorization table is the same for
everybody — so there is nothing for require_owner to compare; the moment a
caller-owned case is read on this router, that changes.

/travel/schengen-stay imports app/schengen_stay.py LAZILY, inside the handler.
That module is being built alongside this one, and a module-level import would
turn a half-finished sibling into an application that will not boot. A lazy
import turns the same situation into one endpoint that says, precisely, that
the calculator is not available yet — which is the honest shape and the one
that keeps the other two endpoints serving.
"""
from __future__ import annotations

import inspect
import json

from fastapi import APIRouter, Depends, HTTPException, Query

from . import travel_authorization as ta
from .dates import is_iso
from .security import Principal, get_principal

router = APIRouter(prefix="/travel", tags=["travel"])

# Candidate entry points for the Schengen calculator, richest first:
# stay_report assembles the count together with the caveats that must travel
# with it, so a caller cannot end up showing days without them; days_used is
# the count alone. The rest of the shortlist exists because this router was
# written while the module was still a stub, and it is what keeps a
# half-finished sibling from turning into an application that will not boot.
_SCHENGEN_ENTRYPOINTS = ("stay_report", "days_used", "assess_stay", "assess",
                         "evaluate_stay", "evaluate", "calculate_stay",
                         "calculate", "compute_stay", "compute")

# Keyword names the calculator might use for the same inputs.
_STAYS_KWARGS = ("trips", "stays", "entries", "periods", "history")
_AS_OF_KWARGS = ("on_date", "as_of", "reference_date", "date")
_ROUTE_KWARGS = ("route", "destination")


def _unavailable(reason: str, detail: str = "") -> dict:
    """The one honest shape for 'Ellis cannot answer this right now'. It never
    carries a days figure, not even a zero: a fabricated remaining-days number
    is how a traveller overstays."""
    return {"status": "unavailable", "available": False, "reason": reason,
            "detail": detail, "days_used": None, "days_remaining": None,
            "note": ("Ellis will not estimate a Schengen day count it cannot "
                     "compute. Count the days on the European Commission's "
                     "own short-stay calculator until this is available."),
            "official_calculator": (
                "https://home-affairs.ec.europa.eu/policies/schengen-borders-and-visa/"
                "border-crossing/short-stay-visa-calculator_en")}


def _parse_stays(raw: str) -> list[dict]:
    """`stays` is a JSON array of {"entry": "YYYY-MM-DD", "exit": "YYYY-MM-DD"}.
    A malformed value is a 400, never a silently-dropped trip — dropping one
    would understate the days used, which is the dangerous direction."""
    if not (raw or "").strip():
        return []
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(400, f"stays must be a JSON array: {exc}")
    if not isinstance(parsed, list):
        raise HTTPException(400, "stays must be a JSON array of "
                                 '{"entry": "YYYY-MM-DD", "exit": "YYYY-MM-DD"}')
    out = []
    for i, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise HTTPException(400, f"stays[{i}] must be an object")
        entry, exit_ = item.get("entry"), item.get("exit")
        for label, value in (("entry", entry), ("exit", exit_)):
            if not is_iso(value):
                raise HTTPException(
                    400, f"stays[{i}].{label} must be an ISO date "
                         f"(YYYY-MM-DD); got {value!r}")
        if exit_ < entry:
            raise HTTPException(400, f"stays[{i}]: exit precedes entry")
        out.append({"entry": entry, "exit": exit_})
    return out


def _call_schengen(stays: list[dict], as_of: str, route: str = "") -> dict:
    """Find the calculator's entry point and call it with whichever of the
    inputs it actually accepts. Every failure mode returns the unavailable
    shape with the real reason in it."""
    try:
        from . import schengen_stay
    except Exception as exc:  # pragma: no cover - import failure is environmental
        return _unavailable("the Schengen stay calculator could not be imported",
                            f"{type(exc).__name__}: {exc}")

    fn = next((getattr(schengen_stay, n) for n in _SCHENGEN_ENTRYPOINTS
               if callable(getattr(schengen_stay, n, None))), None)
    if fn is None:
        return _unavailable(
            "the Schengen stay calculator is not implemented yet",
            "app/schengen_stay.py exposes none of the expected entry points "
            f"({', '.join(_SCHENGEN_ENTRYPOINTS)}); it is still a stub.")

    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):  # pragma: no cover - exotic callables
        params = {}
    kwargs: dict = {}
    stays_kw = next((n for n in _STAYS_KWARGS if n in params), "")
    if stays_kw:
        kwargs[stays_kw] = stays
    as_of_kw = next((n for n in _AS_OF_KWARGS if n in params), "")
    if as_of_kw and as_of:
        kwargs[as_of_kw] = as_of
    route_kw = next((n for n in _ROUTE_KWARGS if n in params), "")
    if route_kw and route:
        kwargs[route_kw] = route

    # Named parameter when the calculator has one; otherwise the stays go in
    # positionally and any as_of rides alongside as a keyword.
    args = () if stays_kw else (stays,)
    try:
        result = fn(*args, **kwargs)
    except NotImplementedError as exc:
        return _unavailable("the Schengen stay calculator is not finished",
                            str(exc) or "the module raised NotImplementedError")
    except TypeError as exc:
        return _unavailable(
            "the Schengen stay calculator has a different interface than this "
            "endpoint expects", f"{fn.__name__}: {exc}")

    if not isinstance(result, dict):
        return _unavailable(
            "the Schengen stay calculator returned an unexpected shape",
            f"{fn.__name__} returned {type(result).__name__}, expected a dict")
    return {"status": "computed", "available": True, **result}


# --- Endpoints ---------------------------------------------------------------

@router.get("/authorizations")
def list_authorizations(nationality: str = Query(default=""),
                        destination: str = Query(default=""),
                        purpose: str = Query(default="tourism"),
                        principal: Principal = Depends(get_principal)):
    """Without a destination this is the catalogue: every visa-waiver travel
    authorization Ellis curates, with its fee, validity and whether Ellis can
    fill it. With a destination it becomes the traveller's own answer — and a
    destination Ellis curates nothing for says exactly that, rather than
    returning an empty list that reads like 'nothing required'."""
    payload = {"org_id": principal.org_id, "as_of": ta.AS_OF,
               "curated_destinations": sorted(
                   {a["destination"] for a in ta.summary()})}
    if not (destination or "").strip():
        payload.update({
            "status": "catalogue",
            "authorizations": ta.summary(),
            "note": ("This is the whole curated set, not a decision about any "
                     "traveller. Pass nationality and destination to ask "
                     "whether one applies.")})
        return payload
    payload.update(ta.applicable(nationality, destination, purpose))
    return payload


@router.get("/authorizations/{key}")
def get_authorization(key: str, principal: Principal = Depends(get_principal)):
    """One authorization in full: the official link, the sourced fee and
    validity, whether Ellis can fill the form, and the acts that stay the
    traveller's own. For an app-only authorization the checklist IS the
    deliverable."""
    try:
        rec = ta.detail(key)
    except ta.UnknownAuthorization as exc:
        raise HTTPException(404, str(exc))
    rec["org_id"] = principal.org_id
    return rec


@router.get("/schengen-stay")
def schengen_stay(stays: str = Query(default="", description=
                  'JSON array of {"entry": "YYYY-MM-DD", "exit": "YYYY-MM-DD"}'),
                  as_of: str = Query(default=""),
                  route: str = Query(default=""),
                  principal: Principal = Depends(get_principal)):
    """The 90-days-in-any-180 arithmetic, delegated to app/schengen_stay.py.

    Ellis computes days; it never decides admissibility. A border officer does
    that, and the calculator's own output is a count, not a permission — which
    is why `decides_admissibility` is stated on every response rather than left
    for a caller to infer.
    """
    parsed = _parse_stays(stays)
    result = _call_schengen(parsed, (as_of or "").strip(), (route or "").strip())
    return {"org_id": principal.org_id, "stays_considered": parsed,
            "requested_as_of": (as_of or "").strip(),
            "requested_route": (route or "").strip(),
            "decides_admissibility": False,
            "note": ("A day count is not a decision. Admission to the Schengen "
                     "area is decided by a border officer at the crossing, on "
                     "the day."),
            **result}
