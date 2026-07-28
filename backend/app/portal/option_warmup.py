"""Read a released portal's own dropdown lists, in full, once per adapter
version — so applicants are asked with the OFFICIAL form's exact choices
before any of their own browser sessions open.

Strictly read-only: it walks the portal's entry gate (scroll, acknowledge,
continue — all reversible), opens each search-combobox, scrolls it to the end,
and closes it with Escape. Nothing is typed into the form, nothing is
submitted, no account is used, and no applicant case or session is touched —
the warm-up runs on its own throwaway execution row.

A list read here cannot be applicant-specific: the form is untouched, so a
dependent list (the wards of a province nobody has chosen) is simply empty and
is skipped rather than cached. That is why a deliberate warm read counts as
corroborated on its own — see remember_field_options.
"""
from __future__ import annotations

from sqlalchemy import select

from ..adapter_factory import models as fm
from ..adapter_factory.compiler import compile_flow
from ..adapter_factory.runtime import FlowRunner
from .released_flow import remember_field_options

# A dedicated read gets a generous budget — no applicant is waiting on it, and
# a long government list (Vietnam publishes 83 border gates) needs many scrolls.
WARM_MAX_OPTIONS = 400
WARM_MAX_SCROLLS = 60

_FILL_ACTIONS = ("FILL_NON_SENSITIVE", "SELECT_SEARCH")


def warm_option_lists(db, *, candidate_id: str, candidate_version: int,
                      tier: str = "sandbox", progress=None) -> dict:
    """Walk to the form and record every select's full list. Returns a report
    of what was read; raises only on a failure to reach the form at all."""
    def say(msg):
        if progress:
            progress(msg)

    version_row = db.execute(select(fm.AdapterCandidateVersion).where(
        fm.AdapterCandidateVersion.candidate_id == candidate_id,
        fm.AdapterCandidateVersion.version == int(candidate_version))
    ).scalars().first()
    if version_row is None:
        raise RuntimeError("no such adapter candidate version")
    hosts = list((version_row.manifest or {}).get("allowed_hostnames") or [])
    if not hosts:
        raise RuntimeError("adapter version declares no allowed hostnames")

    from .live_browser import LiveBrowserSession
    from ..adapter_factory.live_driver import BrowserbasePageDriver

    compiled = compile_flow(version_row)
    # Its own execution row: never an applicant's case, so warming can never
    # rewind or disturb a real application in flight.
    execution = fm.AdapterExecution(
        org_id="platform", application_id=f"warm-{candidate_id[:8]}",
        candidate_id=candidate_id, candidate_version=int(candidate_version),
        tier=tier, status="running")
    db.add(execution)
    db.commit()

    session = LiveBrowserSession(allowed_hostnames=hosts)
    report: dict = {"read": [], "skipped": [], "session_id": ""}
    try:
        page = session._ensure_page()
        report["session_id"] = (getattr(session, "session", None) or {}).get("id", "")
        driver = BrowserbasePageDriver(page, allowed_hostnames=hosts)
        say("walking the portal's entry gate")
        # The gate's acknowledgement dialog renders a beat late; a first walk
        # can reach it before its checkboxes exist. Re-walk from the top rather
        # than give up — the gate is reversible, so repeating it is safe.
        res = {}
        for attempt in range(3):
            execution.current_node = ""
            db.commit()
            runner = FlowRunner(db, execution=execution, compiled=compiled,
                                driver=driver, case_answers={})
            # Stop at the first field: the entry gate is behind us and the form
            # is on screen, but nothing has been filled in.
            res = runner.run(stop_before=lambda n: n.get("action") in _FILL_ACTIONS)
            if res.get("status") in ("boundary", "completed"):
                break
            say(f"entry gate not ready ({res.get('reason') or res.get('status')}) — retrying")
        if res.get("status") not in ("boundary", "completed"):
            raise RuntimeError(
                f"could not reach the form: {res.get('reason') or res.get('status')}")
        for node in (version_row.flow or []):
            if node.get("action") != "SELECT_SEARCH":
                continue
            key = str(node.get("input_source") or "").strip()
            nid = str(node.get("node_id") or "")
            if not key:
                continue
            say(f"reading the portal's list for {key}")
            try:
                out = driver.list_options(node["selector"],
                                          max_options=WARM_MAX_OPTIONS,
                                          max_scrolls=WARM_MAX_SCROLLS) or {}
            except Exception as e:  # noqa: BLE001 — one bad widget is not fatal
                report["skipped"].append({"key": key, "reason": str(e)[:80]})
                continue
            opts = [str(o).strip() for o in (out.get("options") or []) if str(o).strip()]
            if not opts:
                # Empty on an untouched form = a dependent list (e.g. wards
                # before a province is chosen). Nothing honest to cache.
                report["skipped"].append({"key": key, "reason": "list is empty until another answer is set"})
                continue
            remember_field_options(
                db, candidate_id=candidate_id, candidate_version=int(candidate_version),
                field_key=key, node_id=nid, options=opts,
                complete=bool(out.get("complete")), deliberate=True)
            report["read"].append({"key": key, "count": len(opts),
                                   "complete": bool(out.get("complete"))})
        execution.status = "completed"
        db.commit()
        return report
    finally:
        try:
            session.close()
        except Exception:  # noqa: BLE001 — cleanup must never mask the result
            pass
