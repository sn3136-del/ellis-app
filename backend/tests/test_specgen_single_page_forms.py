"""Two portal shapes the skeleton refused to build, pinned as regressions:
the single-page form whose only control is Submit (Malaysia MDAC), and the
declared-account portal whose sign-in is external to its public pages (UAE
ICP via UAE PASS)."""
from app.adapter_factory.specgen import _page_roles, _skeleton_flow


class _Art:
    def __init__(self, page_key, elements, url="https://portal.gov.x/apply",
                 content_class="public_page"):
        self.page_key = page_key
        self.content_class = content_class
        self.structure = {"url_pattern": url, "elements": elements}


def _form_art(page_key="register", with_save=False):
    els = [
        {"selector": "#name", "name": "name", "label": "Name", "type": "text"},
        {"selector": "#passNo", "name": "passNo", "label": "Passport", "type": "text"},
        {"selector": "#dob", "name": "dob", "label": "DD/MM/YYYY", "type": "text"},
        {"selector": "#nat", "name": "nationality", "label": "Nationality", "type": "select"},
        {"selector": "#submitRegistration", "name": "submitRegistration",
         "label": "Submit", "type": "submit"},
    ]
    if with_save:
        els.append({"selector": "#saveBtn", "name": "save",
                    "label": "Save", "type": "button"})
    return _Art(page_key, els)


MAPPINGS = [
    {"page_key": "register", "portal_field": "name", "selector": "#name",
     "ellis_field": "full_name"},
    {"page_key": "register", "portal_field": "passNo", "selector": "#passNo",
     "ellis_field": "passport_number"},
    {"page_key": "register", "portal_field": "dob", "selector": "#dob",
     "ellis_field": "birth_date", "format": "DD/MM/YYYY"},
]


def test_single_page_form_with_only_submit_still_builds_fill_nodes():
    """MDAC's one page has no save control BY DESIGN — its only button
    submits. That page was treated as unbuildable and every fill dropped."""
    roles = {"application": _form_art()}
    nodes = _skeleton_flow("portal.gov.x", roles, MAPPINGS)
    actions = [n["action"] for n in nodes]
    assert actions.count("FILL_NON_SENSITIVE") == 3
    # And nothing in the fill segment clicks the submit control.
    ids = [n["node_id"] for n in nodes]
    assert "save_form" not in ids
    assert "form_filled" in ids


def test_single_page_submit_keeps_declaration_and_reconcile_guards():
    """The submit still happens — on the SAME page — behind the applicant's
    declaration and the reconcile-first guard, exactly like a dedicated
    submission page."""
    roles = _page_roles({"register": _form_art()})
    assert roles.get("submit") is roles.get("application")
    nodes = _skeleton_flow("portal.gov.x", roles, MAPPINGS)
    ids = [n["node_id"] for n in nodes]
    assert ids.index("declaration_handoff") < ids.index("submit")
    assert ids.index("reconcile_submission") < ids.index("submit")
    submit = next(n for n in nodes if n["node_id"] == "submit")
    assert submit["irreversibility"] == "irreversible"


def test_form_with_a_save_control_is_unchanged():
    roles = {"application": _form_art(with_save=True)}
    nodes = _skeleton_flow("portal.gov.x", roles, MAPPINGS)
    ids = [n["node_id"] for n in nodes]
    assert "save_form" in ids and "form_filled" not in ids


def test_declared_account_portal_gets_the_credentials_handoff():
    """UAE ICP signs in through UAE PASS — no password page exists on its
    public pages, yet the family declares account_required. The flow must
    declare the applicant's sign-in instead of silently omitting it (the
    exact gap that held uae-icp one gate from release)."""
    roles = {"application": _form_art(with_save=True)}
    nodes = _skeleton_flow("portal.gov.x", roles, MAPPINGS, account_required=True)
    handoffs = [n for n in nodes if n["action"] == "APPLICANT_HANDOFF"]
    assert any(n.get("handoff_kind") == "credentials" for n in handoffs)
    ids = [n["node_id"] for n in nodes]
    assert ids.index("login_handoff") < ids.index("goto_form")
    # Without the declaration, no phantom handoff appears.
    nodes2 = _skeleton_flow("portal.gov.x", roles, MAPPINGS, account_required=False)
    assert not any(n.get("handoff_kind") == "credentials" for n in nodes2
                   if n["action"] == "APPLICANT_HANDOFF")
