"""Read -> ask -> transcribe for pages the flow does not map field-by-field.

The owner's contract for the DS-160 lane: every known question is asked in
Ellis up front, and when the government form shows a page Ellis has no fills
for, Ellis READS that page's own questions, asks the applicant in Ellis,
enters their answers, and moves on — the same pattern the Germany booking
form proved (read the live form, ask verbatim, transcribe), lifted into the
released-flow runtime.

Boundaries, all fail-closed:
  * OPT-IN per adapter (DYNAMIC_PAGE_ADAPTERS); every other released adapter
    behaves exactly as before.
  * NEVER on the review or sign steps — those are the applicant's personal
    attestations of everything, and this module refuses to walk them.
  * NEVER when a challenge is showing; a captcha page passes straight to the
    normal captcha handoff.
  * Only controls read from the live page are ever filled; the driver's own
    guards (sensitive selectors, verified radio clicks, text-matched advance
    buttons) all still apply, and an unanswered question is asked, never
    guessed.
  * Every page read is BANKED, so the next case can ask those questions up
    front instead of mid-run.
"""
from __future__ import annotations

import json
import os
import pathlib
import re

# Adapters that asked for the dynamic cycle. Everything else: untouched.
DYNAMIC_PAGE_ADAPTERS = {"usa-ceac-ds160"}

# Handoff nodes the cycle must never displace: the applicant's own ceremony.
NEVER_DYNAMIC_NODES = {"review_handoff", "sign_handoff"}
NEVER_DYNAMIC_KINDS = {"captcha", "credentials", "portal_terms_consent",
                       "payment", "payment_credentials"}

# The most pages the cycle will walk before handing back to the normal
# handoff — the DS-160's unmapped stretch is five pages; anything longer is
# not the situation this was built for.
MAX_PAGES = 6


def enabled(manifest: dict | None) -> bool:
    return (manifest or {}).get("adapter_id", "") in DYNAMIC_PAGE_ADAPTERS


def answer_key(field_name: str) -> str:
    """ds160_<field tail> — the same scheme the up-front wizard stores under,
    so an answer given at the start is the fill's answer here."""
    tail = re.sub(r"[^A-Za-z0-9]+", "_", str(field_name or "")).strip("_")
    # CEAC ids carry a long ASP.NET prefix; the bank keys by the short field
    # id, so the last camel-cased segment is the shared vocabulary.
    parts = tail.split("_")
    return "ds160_" + (parts[-1] if parts else tail)


def questions_from_observation(obs: dict, answers: dict) -> tuple[list, list]:
    """(answered, missing) question rows for the page observation.

    Each row: the page's own question text, its own options, and the key the
    answer lives under. `answered` rows carry the stored answer; `missing`
    rows are what the applicant must be asked."""
    answered, missing = [], []
    for f in (obs or {}).get("fields", []):
        key = answer_key(f.get("name", ""))
        row = {"key": key,
               "question": f.get("question", ""),
               "kind": f.get("kind", "text"),
               "mandatory": True,
               "options": [o.get("label", "") for o in (f.get("options") or [])
                           if isinstance(o, dict)],
               "_field": f}
        val = answers.get(key)
        if val in (None, ""):
            missing.append(row)
        else:
            row["_value"] = str(val)
            answered.append(row)
    return answered, missing


def fill_answered(driver, answered: list) -> list[str]:
    """Transcribe stored answers into the controls they were read from.
    Returns the keys that could not be entered (asked again rather than
    forced)."""
    failed = []
    for row in answered:
        f = row["_field"]
        val = row["_value"]
        kind = f.get("kind")
        ok = False
        try:
            if kind == "radio":
                res = driver.select_radio(f.get("options") or [], val)
                ok = bool(res.get("ok"))
            elif kind == "select":
                res = driver.select_search(f.get("selector", ""), val)
                ok = bool(res.get("ok"))
            else:
                res = driver.fill_observed_field(f.get("selector", ""), val)
                ok = bool(res.get("ok"))
        except Exception:  # noqa: BLE001 — an unfillable control is re-asked
            ok = False
        if not ok:
            failed.append(row["key"])
    return failed


def _bank_path() -> pathlib.Path:
    base = os.environ.get("ELLIS_DATA_DIR") or os.path.join(
        os.path.expanduser("~"), "Library", "Application Support", "Ellis")
    return pathlib.Path(base) / "learned_pages" / "usa-ceac-ds160.json"


def bank_page(obs: dict) -> None:
    """Remember this page's questions so the NEXT case asks them up front.
    Best-effort: a failed write never blocks a run."""
    try:
        path = _bank_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if path.is_file():
            data = json.loads(path.read_text())
        for f in (obs or {}).get("fields", []):
            key = answer_key(f.get("name", ""))
            if key in data:
                continue
            data[key] = {"question": f.get("question", ""),
                         "kind": f.get("kind", "text"),
                         "options": [o.get("label", "") for o in
                                     (f.get("options") or [])
                                     if isinstance(o, dict)]}
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        pass


def learned_questions() -> dict:
    """The banked pages, for the up-front wizard."""
    try:
        path = _bank_path()
        if path.is_file():
            return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        pass
    return {}


def cycle(driver, *, node: dict, answers: dict) -> dict:
    """Walk unknown question-pages until the flow's expected page appears.

    Returns one of:
      {"status": "pass"}                       — nothing dynamic to do here;
                                                 the caller runs its normal
                                                 handoff behaviour.
      {"status": "ok", "pages_walked": n}      — page(s) read, filled from
                                                 stored answers, advanced.
      {"status": "ask", "questions": [...]}    — the page asks something Ellis
                                                 has no answer for; pause and
                                                 ask the applicant in Ellis.
    """
    node_id = str(node.get("node_id") or "")
    kind = str(node.get("handoff_kind") or "")
    if node_id in NEVER_DYNAMIC_NODES or kind in NEVER_DYNAMIC_KINDS:
        return {"status": "pass"}
    walked = 0
    for _ in range(MAX_PAGES):
        # A challenge on screen belongs to the applicant — always.
        probe = getattr(driver, "captcha_state", None)
        if probe is not None:
            try:
                if (probe(False) or {}).get("present"):
                    return {"status": "pass"}
            except Exception:  # noqa: BLE001
                return {"status": "pass"}
        read = getattr(driver, "read_dynamic_questions", None)
        obs = read() if read else {"ok": False}
        if not obs.get("ok"):
            return {"status": "pass"}
        # A document-upload handoff whose page really shows a file control is
        # ON its page: hand back to the normal upload behaviour.
        if kind == "document_upload" and obs.get("has_file_input"):
            return {"status": "pass"}
        answered, missing = questions_from_observation(obs, answers)
        if not answered and not missing:
            return {"status": "pass"} if walked == 0 else \
                {"status": "ok", "pages_walked": walked}
        bank_page(obs)
        if missing:
            qs = [{"key": m["key"], "question": m["question"],
                   "why": "The official form asks this on the current page.",
                   "format": "", "mandatory": True,
                   "kind": "choice" if m["options"] else "text",
                   "options": m["options"]} for m in missing]
            return {"status": "ask", "questions": qs}
        refused = fill_answered(driver, answered)
        if refused:
            # An answer the control would not take is a question, not a guess.
            qs = [{"key": k, "question": next(
                       (a["question"] for a in answered if a["key"] == k), k),
                   "why": "The official form did not accept the stored answer.",
                   "format": "", "mandatory": True, "kind": "text",
                   "options": []} for k in refused]
            return {"status": "ask", "questions": qs}
        adv = getattr(driver, "click_next_button", None)
        res = adv() if adv else {"ok": False}
        if not res.get("ok"):
            return {"status": "pass"} if walked == 0 else \
                {"status": "ok", "pages_walked": walked}
        walked += 1
    return {"status": "ok", "pages_walked": walked}
