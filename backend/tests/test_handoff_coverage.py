"""Every handoff a released adapter can raise must be one the applicant can act on.

The UI resolves a pause by looking the handoff name up in three maps. A name in
none of them renders the fallback card — "Action needed / Complete the required
step to continue." — with no panel behind it and no signal wired to its button.
The case never leaves that screen.

thailand-tdac shipped exactly that: its spec carried an upload_handoff node, so
the pause was named `document_upload`, which no map knew (2026-08-03). The
earlier sweep missed it because it grepped _pause() call sites, and this name is
written by SPECGEN into the flow — a different emitter entirely.

So this test reads the released adapters themselves rather than any source list.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

from app.portal.released_flow import APPLICANT_HANDOFFS, applicant_handoff

_UI = pathlib.Path(__file__).resolve().parents[2] / "src/renderer/src/lib/visaBackend.js"


def _js_map(name: str) -> set[str]:
    src = _UI.read_text()
    m = re.search(rf"export const {name}\s*=\s*\{{(.*?)\n\}}", src, re.S)
    assert m, f"{name} not found in visaBackend.js"
    return set(re.findall(r"^\s{2}(\w+):", m.group(1), re.M))


def test_the_ui_covers_every_applicant_handoff():
    """All three maps, not just one: copy without a panel still dead-ends."""
    for map_name in ("HANDOFF_UI", "HANDOFF_COPY", "HANDOFF_SIGNAL"):
        keys = _js_map(map_name)
        missing = sorted(h for h in APPLICANT_HANDOFFS if h not in keys)
        # HANDOFF_SIGNAL may omit a kind ONLY when its panel sends the signal
        # itself: LiveViewModal confirms login_challenge/identity, PaymentModal
        # sends complete_payment for three_ds, AppointmentCalendar handles
        # no_availability, and authorization is resolved by SignatureModal.
        if map_name == "HANDOFF_SIGNAL":
            missing = [h for h in missing
                       if h not in ("authorization", "login_challenge", "identity",
                                    "no_availability", "three_ds")]
        assert not missing, f"{map_name} has no entry for: {missing}"


_GENERATED = pathlib.Path(__file__).resolve().parents[1] / "app/portal_adapters/generated"


def _shipped_handoff_kinds() -> dict[str, set[str]]:
    """Every handoff_kind in every generated adapter flow committed to the repo.

    Reads the ARTIFACTS rather than a database: they are what ships, they are
    version-controlled, and the check stays hermetic.
    """
    out: dict[str, set[str]] = {}
    for path in sorted(_GENERATED.glob("*/*/flow.json")):
        try:
            flow = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        nodes = flow if isinstance(flow, list) else (flow or {}).get("nodes") or []
        for node in nodes:
            kind = (node or {}).get("handoff_kind") if isinstance(node, dict) else None
            if kind:
                out.setdefault(str(kind), set()).add(path.parent.parent.name)
    return out


def test_no_adapter_can_raise_a_handoff_the_applicant_cannot_act_on():
    kinds = _shipped_handoff_kinds()
    if not kinds:
        pytest.skip("no generated adapter flows in the tree")
    dead = {k: sorted(f)[:6] for k, f in kinds.items()
            if applicant_handoff(k) not in APPLICANT_HANDOFFS}
    assert not dead, (
        "adapters raise handoffs with no applicant-facing name — the UI would "
        f"show a bare 'Action needed' card: {json.dumps(dead, indent=1)}")


def test_the_spec_alias_maps_an_internal_name_onto_a_real_one():
    assert applicant_handoff("legally_personal_declaration") == "personal_declaration"
    assert applicant_handoff("captcha") == "captcha"      # already applicant-facing
    assert applicant_handoff("") == ""
