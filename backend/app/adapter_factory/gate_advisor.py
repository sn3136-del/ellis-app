"""The parked build explains itself: gate report in, concrete next steps out.

A build that does not release leaves behind exactly the evidence a person
needs — sixteen deterministic gates with verbatim reasons, and, where the
grounding chokepoint refused a mapping, the artifact and field it refused.
Reading that has been a human's job every single time: decode sixteen gate
names, open the specification, find which artifact carried which field, then
hand-edit curation. This module does the decoding, so the next build starts
from an instruction instead of an investigation.

It is a TRANSLATION and nothing more. No model runs here and nothing is
inferred: every sentence traces to a stored gate reason, a stored rejection,
or a curated portal-family record. Two builds with the same report get the
same advice, forever.

ADVISORY ONLY — this is a safety property, not a limitation:
  - it never releases and never returns a release action (see ADVISORY_ACTIONS);
  - it never writes. Gates may be recomputed to READ them when no report was
    stored, but no gate result is ever recorded, overwritten, or softened;
  - vocabulary candidates are suggestions for a human to accept into
    ELLIS_FIELDS by hand. The grounding chokepoint in specgen stays the one
    validation place, and nothing named here reaches a released adapter.

Family-agnostic by construction: report, family record and specification are
all keyed by family_id, so a tourist arrival card and a work-visa petition
portal are decoded by the same code path with no edition-specific branch.
"""
from __future__ import annotations

import re

from sqlalchemy import select

# The only actions this advisor may propose. "release" is deliberately absent:
# releasing is the gates' own decision and an advisor never gets a vote.
ADVISORY_ACTIONS = (
    "start_attended_observation",
    "curate_form_path",
    "add_vocabulary",
    "review_rejected_mapping",
    "declare_handoff",
    "review_blocker",
)

# Handoff kinds in the order a build meets them, so advice is stable.
HANDOFF_KINDS = ("credentials", "captcha", "otp", "payment_credentials",
                 "appointment_selection", "legally_personal_declaration")

_HANDOFF_BY_GATE = {
    "account_flow_mapped_where_applicable": ("credentials",),
    "payment_confirmation_preserved": ("payment_credentials",),
    "submission_confirmation_preserved": ("legally_personal_declaration",),
}

# Gate 10 names the kinds it wants inside a bracketed list; the same reason
# also names the kinds it OBSERVED, which are not necessarily missing.
_HANDOFF_LIST_RE = re.compile(r"handoff node\(s\) for \[([^\]]*)\]")

# Capability-gate problems travel in the same `missing` list as the gates and
# name their handoff in prose.
_HANDOFF_PHRASES = (
    ("credentials", ("no credentials handoff",)),
    ("otp", ("without an otp handoff", "otp handoff for the emailed")),
    ("payment_credentials", ("exact-amount applicant payment handoff",)),
    ("appointment_selection", ("appointment-selection handoff",)),
    ("legally_personal_declaration", ("legally-personal declaration handoff",
                                      "declaration/final-confirmation handoff")),
)

_HANDOFF_DETAIL = {
    "credentials": (
        "the portal family requires an account but the flow carries no "
        "credentials handoff. Signing in stays the applicant's own step, so "
        "the flow must contain that handoff — or the family's account_required "
        "flag is wrong and should be corrected instead."),
    "captcha": (
        "a CAPTCHA stands in this flow with no applicant handoff for it. When "
        "the portal only shows it at review/submit, credential-free recon can "
        "never observe it: declare it on the portal family "
        "(entry_gate.declared_handoffs) so the flow carries the handoff "
        "honestly."),
    "otp": (
        "a one-time code stands in this flow with no applicant handoff for it. "
        "When the portal only sends it after sign-in, declare it on the portal "
        "family (entry_gate.declared_handoffs); Ellis never reads an OTP."),
    "payment_credentials": (
        "the flow reaches a fee or payment step with no exact-amount applicant "
        "payment handoff. Ellis reads the official fee and the applicant pays "
        "personally, so the handoff is what makes the step releasable."),
    "appointment_selection": (
        "the flow reserves an appointment with no applicant selection handoff. "
        "The slot is always the applicant's own choice."),
    "legally_personal_declaration": (
        "the flow submits with no applicant declaration handoff. The final "
        "legally personal statement is never Ellis's to make."),
}

# Rejections the advisor can explain in full. Anything else stays verbatim.
_REJECTION_EXPLANATION = {
    "non_deterministic_selector": (
        "The cited selector is not a deterministic target (a deep ancestor "
        "path or positional chain), so the runtime could not be trusted to "
        "find the same element twice. Re-observe the element and cite a "
        "stable id/name selector."),
    "selector_mismatch_with_observation": (
        "The proposal cited a selector that is not the one recon observed for "
        "that field name. The observation is the truth; the proposal is not."),
    "unknown_ellis_field": (
        "The proposal named an Ellis answer key that does not exist. Grounding "
        "refuses it rather than inventing a field: a human decides whether the "
        "vocabulary should grow."),
}

_MAX_NAMED = 8          # how many artifact/field pairs a single fix enumerates
_MAX_DETAIL = 900


class UnknownSubject(Exception):
    """The advisor was pointed at something that is not a build or a version."""


# --------------------------------------------------------------- resolution --
def _resolve(db, subject):
    """(build_request, candidate, version) for a build id, a version, a
    candidate, or any of those rows."""
    from . import models as fm
    build = candidate = version = None
    if isinstance(subject, fm.AdapterBuildRequest):
        build = subject
    elif isinstance(subject, fm.AdapterCandidateVersion):
        version = subject
    elif isinstance(subject, fm.AdapterCandidate):
        candidate = subject
    elif isinstance(subject, str) and subject:
        build = db.get(fm.AdapterBuildRequest, subject)
        if build is None:
            version = db.get(fm.AdapterCandidateVersion, subject)
        if build is None and version is None:
            candidate = db.get(fm.AdapterCandidate, subject)
        if build is None and version is None and candidate is None:
            raise UnknownSubject(f"no build, candidate or version {subject!r}")
    else:
        raise UnknownSubject(f"cannot advise on {type(subject).__name__}")

    if version is not None and candidate is None:
        candidate = db.get(fm.AdapterCandidate, version.candidate_id)
    if candidate is None and build is not None:
        candidate = db.get(fm.AdapterCandidate, build.current_candidate_id or "") \
            or db.execute(select(fm.AdapterCandidate).where(
                fm.AdapterCandidate.build_request_id == build.id)).scalars().first()
    if build is None and candidate is not None:
        build = db.get(fm.AdapterBuildRequest, candidate.build_request_id or "")
    if version is None and candidate is not None:
        version = db.execute(select(fm.AdapterCandidateVersion).where(
            fm.AdapterCandidateVersion.candidate_id == candidate.id,
            fm.AdapterCandidateVersion.version == candidate.current_version)
            ).scalars().first() or db.execute(select(fm.AdapterCandidateVersion)
            .where(fm.AdapterCandidateVersion.candidate_id == candidate.id)
            .order_by(fm.AdapterCandidateVersion.version.desc())).scalars().first()
    return build, candidate, version


def _link_and_family(db, build, candidate):
    """The family adapter link and the curated portal family, by family_id when
    the build carries one and by build/candidate otherwise."""
    from ..global_routes.models import FamilyAdapterLink, PortalFamily
    family_id = ((build.portal_evidence or {}).get("family_id", "")
                 if build is not None else "")
    link = None
    if family_id:
        link = db.execute(select(FamilyAdapterLink).where(
            FamilyAdapterLink.family_id == family_id)).scalars().first()
    if link is None and build is not None:
        link = db.execute(select(FamilyAdapterLink).where(
            FamilyAdapterLink.build_request_id == build.id)).scalars().first()
    if link is None and candidate is not None:
        link = db.execute(select(FamilyAdapterLink).where(
            FamilyAdapterLink.candidate_id == candidate.id)).scalars().first()
    if not family_id and link is not None:
        family_id = link.family_id
    family = db.execute(select(PortalFamily).where(
        PortalFamily.family_id == family_id)).scalars().first() if family_id else None
    return link, family, family_id


def _specification(db, build, version):
    from . import models as fm
    if version is not None and version.specification_id:
        spec = db.get(fm.AdapterSpecification, version.specification_id)
        if spec is not None:
            return spec
    if build is None:
        return None
    spec_id = (build.portal_evidence or {}).get("spec_id", "")
    if spec_id:
        spec = db.get(fm.AdapterSpecification, spec_id)
        if spec is not None:
            return spec
    return db.execute(select(fm.AdapterSpecification).where(
        fm.AdapterSpecification.build_request_id == build.id).order_by(
        fm.AdapterSpecification.version.desc())).scalars().first()


# ------------------------------------------------------------- gate reading --
def _normalized_gates(report: dict) -> dict:
    """{gate: {passed, reason}} from any recorded shape. Older reports stored
    plain booleans; the verbatim `missing` lines carry the reasons."""
    gates: dict[str, dict] = {}
    for name, val in ((report or {}).get("gates") or {}).items():
        if isinstance(val, dict):
            gates[str(name)] = {"passed": bool(val.get("passed")),
                                "reason": str(val.get("reason") or "")}
        else:
            gates[str(name)] = {"passed": bool(val), "reason": ""}
    for line in (report or {}).get("missing") or []:
        name, _, reason = str(line).partition(":")
        name, reason = name.strip(), reason.strip()
        if not name:
            continue
        entry = gates.get(name)
        if entry is None:
            gates[name] = {"passed": False, "reason": reason}
        else:
            entry["passed"] = False
            entry["reason"] = entry["reason"] or reason
    return gates


def _gate_report(db, build, candidate, version, family, link):
    """(gates, source). The RECORDED report is preferred — it is what the
    fail-closed release actually decided. Recomputation is read-only and used
    only when nothing was recorded, so an applicant-initiated build (which has
    no family link) can still be advised."""
    if link is not None and (link.gate_report or {}):
        return _normalized_gates(link.gate_report), "recorded_gate_report"
    if build is not None and candidate is not None and version is not None \
            and family is not None:
        from ..global_routes import release_gates
        result = release_gates.evaluate_gates(
            db, build_request=build, candidate=candidate, version=version,
            family=family)
        return _normalized_gates(result), "recomputed_read_only"
    return {}, "none"


def _failing(gates: dict) -> list[str]:
    """Failing gate names in brief order; anything the report added beyond the
    sixteen (capability cross-checks) follows, stably ordered."""
    from ..global_routes.release_gates import GATE_NAMES
    named = [g for g in GATE_NAMES if g in gates and not gates[g]["passed"]]
    extra = sorted(k for k, v in gates.items()
                   if not v["passed"] and k not in GATE_NAMES)
    return named + extra


# ------------------------------------------------------------ spec evidence --
_VOCAB_FIELD_KEYS = ("ellis_field", "suggested_ellis_field", "field", "key", "name")


def _proposed_vocabulary(spec, version) -> list[dict]:
    """Vocabulary candidates a build proposed, read from wherever the spec
    result carries them. Shape-tolerant on purpose: the proposer owns the
    shape, the advisor only reports it — and reporting is all that happens,
    since ELLIS_FIELDS is never extended by anything but a human."""
    raw = []
    for holder in ((spec.generation_basis if spec is not None else None),
                   (version.manifest if version is not None else None)):
        if not isinstance(holder, dict):
            continue
        got = holder.get("proposed_vocabulary")
        if isinstance(got, dict):
            got = got.get("candidates") or got.get("fields") or []
        if isinstance(got, list) and got:
            raw = got
            break
    out, seen = [], set()
    for item in raw:
        if isinstance(item, str):
            entry = {"ellis_field": item.strip()}
        elif isinstance(item, dict):
            field = ""
            for k in _VOCAB_FIELD_KEYS:
                if str(item.get(k) or "").strip():
                    field = str(item[k]).strip()
                    break
            entry = {"ellis_field": field}
            for k in ("portal_field", "label", "page_key", "artifact_id",
                      "selector", "input_type", "why", "observations",
                      "families"):
                if item.get(k) not in (None, "", [], {}):
                    entry[k] = item[k]
        else:
            continue
        if not entry["ellis_field"] or entry["ellis_field"] in seen:
            continue
        seen.add(entry["ellis_field"])
        out.append(entry)
    return out


def _rejections(spec) -> list[dict]:
    basis = (spec.generation_basis or {}) if spec is not None else {}
    out = []
    for row in basis.get("rejected_mappings") or []:
        if not isinstance(row, dict):
            continue
        prop = row.get("proposal") or {}
        out.append({
            "reasons": [str(r) for r in (row.get("reasons") or [])],
            "portal_field": str(prop.get("portal_field") or ""),
            "ellis_field": str(prop.get("ellis_field") or ""),
            "artifact_id": str(prop.get("artifact_id") or ""),
            "page_key": str(prop.get("page_key") or ""),
            "selector": str(prop.get("selector") or "")})
    return out


# ------------------------------------------------------------------- fixes ---
def _fix(kind: str, title: str, detail: str, action: str, **extra) -> dict:
    if action not in ADVISORY_ACTIONS:
        raise ValueError(f"{action!r} is not an advisory action")
    return {"kind": kind, "title": title, "detail": detail[:_MAX_DETAIL],
            "action": action, **extra}


def _account_required(family, build) -> bool:
    if family is not None:
        return bool(getattr(family, "account_required", False))
    return bool((build.portal_evidence or {}).get("account_required")) \
        if build is not None else False


def _named_pairs(rows) -> str:
    named = "; ".join(
        f"field {r['portal_field'] or '(unnamed)'!r} on artifact "
        f"{r['artifact_id'] or '(unrecorded)'}"
        + (f" (page {r['page_key']})" if r["page_key"] else "")
        + (f" -> ellis field {r['ellis_field']!r}" if r["ellis_field"] else "")
        for r in rows[:_MAX_NAMED])
    if len(rows) > _MAX_NAMED:
        named += f"; and {len(rows) - _MAX_NAMED} more"
    return named


def _form_fixes(gates, family, family_id, build) -> tuple[list[dict], set[str]]:
    """Gate 5 translated. A login-walled family cannot be mapped credential-
    free by any amount of retrying, so the honest next step is the consented
    attended observation; anything else is a curation problem."""
    g = gates.get("required_fields_mapped")
    if g is None or g["passed"]:
        return [], set()
    reason = g["reason"]
    low = reason.lower()
    # The gate's own words outrank the family record: a portal whose form was
    # only ever seen from a signed-in session IS login-walled, whatever a stale
    # seed row says about account_required.
    consent_missing = "consent" in low and "signed-in session" in low
    if consent_missing or _account_required(family, build):
        if consent_missing:
            detail = (
                "A signed-in observation of this form exists but the "
                "applicant's consent to learn the portal from it was not "
                "recorded, so the gate refuses to use it. Record that consent "
                "(app/authorized_observation.py) on the build and re-run; "
                "without it nothing observed in that session may be used.")
        else:
            detail = (
                f"Portal family {family_id or '(unknown)'} requires an account, "
                f"so it shows no application form to a credential-free visitor "
                f"and recon can never map its fields. The next build needs ONE "
                f"consented attended observation: an applicant who is making "
                f"this application anyway agrees that their own signed-in run "
                f"may record the page STRUCTURE — field names, types, selectors, "
                f"never values, never documents, never their password. Record "
                f"that consent (app/authorized_observation.py), let them drive, "
                f"and re-run the build; the gate then reads the evidence as a "
                f"signed-in observation and says so.")
        if reason:
            detail += f" Gate reason, verbatim: {reason}"
        return [_fix("login_walled_form",
                     "Learn this form from a consented attended observation",
                     detail, "start_attended_observation",
                     gate="required_fields_mapped",
                     family_id=family_id)], {"required_fields_mapped"}
    detail = (
        "Recon reached the portal but no page it observed carried a mappable "
        "application form. That is a curation gap, not a portal failure: add "
        "the verified path that renders the real form to the family's "
        "form_paths, or declare the in-session entry gate (entry_gate.actions) "
        "that reveals it. Both are checked against the live portal by a human.")
    if reason:
        detail += f" Gate reason, verbatim: {reason}"
    return [_fix("form_not_reachable",
                 "Curate the path or entry gate that renders the form",
                 detail, "curate_form_path", gate="required_fields_mapped",
                 family_id=family_id)], {"required_fields_mapped"}


def _vocabulary_fix(vocabulary) -> list[dict]:
    if not vocabulary:
        return []
    fields = [v["ellis_field"] for v in vocabulary]
    shown = ", ".join(fields[:12]) + (f", and {len(fields) - 12} more"
                                      if len(fields) > 12 else "")
    detail = (
        f"This build observed {len(fields)} portal field(s) with no Ellis "
        f"answer key to bind them to, and proposed candidate names: {shown}. "
        f"These are CANDIDATES. Nothing is added automatically — a human "
        f"accepts a name into ELLIS_FIELDS (and its applicant-facing wording), "
        f"and only then can the grounding chokepoint accept a mapping onto it. "
        f"Every accepted name makes the next build on this portal, and on any "
        f"portal that asks the same thing, map more of the form.")
    return [_fix("vocabulary_gap",
                 f"Consider {len(fields)} vocabulary candidate(s) for ELLIS_FIELDS",
                 detail, "add_vocabulary", fields=fields,
                 candidates=vocabulary)]


def _rejection_fixes(rejections, vocabulary) -> list[dict]:
    """One fix per refusal reason, naming the artifact and field refused. The
    unknown_ellis_field group is RESIDUAL: a refusal the build already turned
    into a vocabulary candidate belongs to that fix, not this one. Matching is
    by the PORTAL element as well as by name — the proposed name is derived
    from the portal's label and need not equal the name that was refused."""
    proposed = {v["ellis_field"] for v in vocabulary}
    proposed_elements = {(v.get("artifact_id", ""), v.get("portal_field", ""))
                         for v in vocabulary}
    out = []
    for reason in ("non_deterministic_selector",
                   "selector_mismatch_with_observation",
                   "unknown_ellis_field"):
        rows = [r for r in rejections if reason in r["reasons"]]
        if reason == "unknown_ellis_field":
            rows = [r for r in rows
                    if r["ellis_field"] not in proposed
                    and (r["artifact_id"], r["portal_field"]) not in proposed_elements]
        if not rows:
            continue
        detail = (f"{_REJECTION_EXPLANATION[reason]} Refused: "
                  f"{_named_pairs(rows)}.")
        out.append(_fix("rejected_mapping",
                        f"{len(rows)} mapping(s) refused: {reason}",
                        detail, "review_rejected_mapping", reason=reason,
                        count=len(rows)))
    return out


def _handoff_fixes(gates) -> tuple[list[dict], set[str]]:
    found: dict[str, str] = {}
    for name, g in gates.items():
        if g["passed"]:
            continue
        low = g["reason"].lower()
        for kind in _HANDOFF_BY_GATE.get(name, ()):
            found.setdefault(kind, name)
        if name == "captcha_otp_handoffs_preserved":
            m = _HANDOFF_LIST_RE.search(low)
            listed = [k for k in re.findall(r"[a-z_]+", m.group(1))
                      if k in ("captcha", "otp")] if m else []
            for kind in (listed or ["captcha", "otp"]):
                found.setdefault(kind, name)
        for kind, phrases in _HANDOFF_PHRASES:
            if any(p in low for p in phrases):
                found.setdefault(kind, name)
    fixes, gates_used = [], set()
    for kind in HANDOFF_KINDS:
        if kind not in found:
            continue
        gates_used.add(found[kind])
        fixes.append(_fix(
            "missing_handoff", f"Declare the {kind} applicant handoff",
            f"Gate {found[kind]} is blocked because {_HANDOFF_DETAIL[kind]}",
            "declare_handoff", handoff_kind=kind, gate=found[kind]))
    return fixes, gates_used


def _blocker_fixes(gates, failing, addressed) -> list[dict]:
    """Every remaining failing gate, verbatim. An advisor with no specific
    remedy says the gate's own words rather than inventing one."""
    return [_fix("gate_blocked", f"Gate {name} is not satisfied",
                 gates[name]["reason"] or
                 "the report recorded this gate as failing with no reason text",
                 "review_blocker", gate=name)
            for name in failing if name not in addressed]


def _park_fixes(db, build, candidate) -> list[dict]:
    """A build that parked BEFORE the gates ran has review tasks instead of a
    gate report; they are the next steps."""
    from . import models as fm
    ids = [i for i in {getattr(candidate, "id", ""), getattr(build, "id", "")} if i]
    if not ids:
        return []
    tasks = db.execute(select(fm.AdapterReviewTask).where(
        fm.AdapterReviewTask.candidate_id.in_(ids),
        fm.AdapterReviewTask.status == "open").order_by(
        fm.AdapterReviewTask.created_at.desc())).scalars().all()
    return [_fix("build_parked", f"Open review task: {t.kind}",
                 t.reason or "no reason recorded", "review_blocker",
                 task_id=t.id) for t in tasks[:10]]


# ------------------------------------------------------------------ advice ---
def advise(db, build_or_version) -> dict:
    """Translate a parked build's gate report into concrete next steps.

    Read-only and deterministic. Returns {blocking_gate, human_summary,
    fixes[{kind, title, detail, action}], ...}; the fixes are for a HUMAN to
    perform — none of them is applied here and none of them releases."""
    build, candidate, version = _resolve(db, build_or_version)
    link, family, family_id = _link_and_family(db, build, candidate)
    gates, source = _gate_report(db, build, candidate, version, family, link)
    spec = _specification(db, build, version)
    vocabulary = _proposed_vocabulary(spec, version)
    rejections = _rejections(spec)
    failing = _failing(gates)

    fixes: list[dict] = []
    addressed: set[str] = set()
    form_fixes, used = _form_fixes(gates, family, family_id, build)
    fixes += form_fixes
    addressed |= used
    fixes += _vocabulary_fix(vocabulary)
    fixes += _rejection_fixes(rejections, vocabulary)
    handoff_fixes, used = _handoff_fixes(gates)
    fixes += handoff_fixes
    addressed |= used
    fixes += _blocker_fixes(gates, failing, addressed)
    if not gates:
        fixes += _park_fixes(db, build, candidate)

    from .statemachine import RELEASED_STATES
    state = getattr(build, "state", "") or ""
    released = bool(link.released) if link is not None else state in RELEASED_STATES
    subject = (f"Build {build.id} " if build is not None else "Candidate ")
    where = f"portal family {family_id}" if family_id else \
        f"route {getattr(build, 'route_key', '') or '(unrecorded)'}"
    if gates:
        head = (f"{subject}({where}) is at {state or 'an unrecorded state'}: "
                f"{len(failing)} of {len(gates)} release gates fail.")
        if failing:
            first = gates[failing[0]]["reason"]
            head += (f" First blocker: {failing[0]}"
                     + (f" — {first}" if first else "") + ".")
        elif not released:
            head += (" No gate is failing in the recorded report, so the block "
                     "is outside the gates — check the build's own state.")
    elif released:
        head = (f"{subject}({where}) is at {state or 'an unrecorded state'} and "
                f"nothing is blocking it. No sixteen-gate report is on record: "
                f"it released through the factory's own evidence gates rather "
                f"than a portal-family gate run.")
    else:
        head = (f"{subject}({where}) is at {state or 'an unrecorded state'} "
                f"with no gate report on record: it parked before the release "
                f"gates ran.")
    if fixes:
        head += (" Suggested next steps, advisory only — nothing here has been "
                 "applied, added, or released: "
                 + "; ".join(f"{i}) {f['title']}" for i, f in enumerate(fixes, 1))
                 + ".")
    else:
        head += " No next step can be derived from the evidence on record."

    return {
        "build_id": getattr(build, "id", ""),
        "candidate_id": getattr(candidate, "id", ""),
        "version": int(getattr(version, "version", 0) or 0),
        "route_key": getattr(build, "route_key", "") or "",
        "family_id": family_id,
        "state": state,
        "released": released,
        "advisory": True,
        "gate_report_source": source,
        "gates_failing": failing,
        "blocking_gate": failing[0] if failing else "",
        "human_summary": head,
        "fixes": fixes,
    }
