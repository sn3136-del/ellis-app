"""Mapping memory: the adapter builder remembers, and is still not trusted.

Two halves, and the second matters more than the first.

WHAT IS LEARNED. A field's signature is what the field IS — page, type, its
distilled name, its own words — never where it sat or which id the framework
minted this render. One remembered mapping is offered back on the next build of
the SAME PORTAL FAMILY, whatever kind of visa that family serves: these tests
drive a tourist e-visa family (vietnam-evisa) through the identical code path
as a generic one, because a family id is the only key memory has.

WHAT IS STILL REFUSED. Memory proposes; the one grounding chokepoint in
specgen disposes. A remembered mapping onto a password box, onto an element
this build never observed, or carrying a selector the observation does not
confirm is rejected exactly as a model's guess would be — and rejected loudly,
in generation_basis, rather than quietly filtered out where nobody would see
it. A remembered mapping naming a field Ellis has no word for is rejected too,
and becomes a candidate a human may add: proposed_vocabulary is a reading list,
never an edit to ELLIS_FIELDS.
"""
import pytest

from app.adapter_factory import mapping_memory, models as fm, specgen
from app.adapter_factory.build_workflow import create_request, record_consent

HOST = "portal.gov.example"


def _evidence(family_id: str) -> dict:
    return {"hostnames": [HOST], "operator": "Test Consular Authority",
            "verification": "synthetic_test_portal", "family_id": family_id}


def _el(name, *, selector=None, etype="text", label="", required=True,
        sensitive=False, **extra):
    return {"selector": selector if selector is not None else f"#{name}",
            "name": name, "label": label or name, "type": etype,
            "required": required, "sensitive": sensitive, **extra}


def _request(db, *, family_id, route_key):
    req = create_request(db, org_id="orgMM", user_id="u", application_id="",
                         route_key=route_key, destination="Testland",
                         visa_type="tourist", portal_evidence=_evidence(family_id),
                         runtime_mode="local_mock_demo")
    record_consent(db, req, user_id="u")
    return req


def _observed(db, req, elements, page_key="application"):
    job = fm.AdapterReconJob(build_request_id=req.id, org_id=req.org_id,
                             portal_hostnames=[HOST], status="complete",
                             pages_observed=1)
    db.add(job)
    db.commit()
    art = fm.AdapterReconArtifact(
        recon_job_id=job.id, page_key=page_key, hostname=HOST,
        url_pattern=f"https://{HOST}/apply",
        structure={"url_pattern": f"https://{HOST}/apply", "hostname": HOST,
                   "elements": elements})
    db.add(art)
    db.commit()
    return job, [art]


def _spec(db, req, job, arts):
    return specgen.generate_specification(db, build_request=req, recon_job=job,
                                          artifacts=arts, generator_name="test")


def _accepted(spec, portal_field):
    return next((m for m in spec.field_mappings
                 if m["portal_field"] == portal_field), None)


def _reasons(spec, portal_field):
    out = []
    for r in (spec.generation_basis or {}).get("rejected_mappings", []):
        if (r.get("proposal") or {}).get("portal_field") == portal_field:
            out.extend(r.get("reasons") or [])
    return out


# ---- the signature is the field, not the render ----
def test_signature_survives_volatile_ids_and_position():
    """Same box, two renders: Angular re-mints the id, ASP.NET re-nests the
    control, the field moves down the page. One signature."""
    first = {"name": "mat-input-7", "label": "Occupation", "type": "text",
             "selector": "#mat-input-7", "page_key": "application"}
    second = {"name": "mat-input-42", "label": "Occupation  *", "type": "text",
              "selector": "#mat-input-42", "page_key": "application"}
    assert mapping_memory.signature_for(first) == mapping_memory.signature_for(second)

    aspnet = {"name": "ctl00_MainContent_txtSurname", "label": "Surname",
              "type": "text", "selector": "#ctl00_MainContent_txtSurname",
              "page_key": "application"}
    renested = {"name": "ctl02$ContentPlaceHolder$txtSurname", "label": "Surname",
                "type": "text", "selector": "input:nth-of-type(9)",
                "page_key": "application"}
    assert mapping_memory.signature_for(aspnet) == mapping_memory.signature_for(renested)

    # Indonesia's arrival card stamps the epoch millisecond into the id.
    stamped = {"name": "spi_nationality_1785693541603", "label": "Nationality",
               "type": "select", "page_key": "application"}
    restamped = {"name": "spi_nationality_1785699999111", "label": "Nationality",
                 "type": "select", "page_key": "application"}
    assert mapping_memory.signature_for(stamped) == mapping_memory.signature_for(restamped)


def test_signature_separates_genuinely_different_fields():
    base = {"name": "field1", "label": "Surname", "type": "text",
            "page_key": "application"}
    sigs = {
        mapping_memory.signature_for(base),
        mapping_memory.signature_for(dict(base, label="Given names")),
        mapping_memory.signature_for(dict(base, type="select")),
        mapping_memory.signature_for(dict(base, page_key="login")),
        mapping_memory.signature_for(dict(base, name="applicantSurname")),
        # Repeat groups: traveller 2's surname is not traveller 1's.
        mapping_memory.signature_for(dict(base, label="Traveller 2 surname")),
    }
    assert len(sigs) == 6


def test_signature_refuses_a_field_with_nothing_stable():
    """Two anonymous framework boxes would sign identically, and a memory that
    confuses two fields is worse than no memory: neither is learnable."""
    assert mapping_memory.signature_for(
        {"name": "mat-input-3", "label": "", "type": "text",
         "page_key": "application"}) == ""
    assert mapping_memory.signature_for({}) == ""


def test_radio_signs_by_the_question_not_the_answer():
    male = {"name": "gender", "label": "MALE", "group_label": "Gender",
            "type": "radio", "page_key": "application"}
    female = dict(male, label="FEMALE", selector="#gender_f")
    assert mapping_memory.signature_for(male) == mapping_memory.signature_for(female)


# ---- remember(): upsert, corroborate, never demote ----
def test_remember_upserts_and_bumps_observations(db):
    field = {"name": "fldQ17", "label": "Occupation", "type": "text",
             "page_key": "application"}
    mapping_memory.forget(db, family_id="mm-upsert")
    row = mapping_memory.remember(db, family_id="mm-upsert",
                                  mapping={"ellis_field": "occupation"},
                                  observed_field=field, source="released_adapter",
                                  actor="ellis")
    assert row.observations == 1 and row.confidence == "released"
    again = mapping_memory.remember(
        db, family_id="mm-upsert", mapping={"ellis_field": "occupation"},
        # A later render with a different id is the SAME field.
        observed_field=dict(field, name="fldQ17_1785693541603"),
        source="human_correction", actor="reviewer@ellis")
    assert again.id == row.id
    assert again.observations == 2
    # A human's witness outranks a released adapter's, and never demotes back.
    assert again.confidence == "confirmed" and again.source == "human_correction"
    mapping_memory.remember(db, family_id="mm-upsert",
                            mapping={"ellis_field": "occupation"},
                            observed_field=field, source="released_adapter")
    db.refresh(row)
    assert row.confidence == "confirmed" and row.observations == 3
    # A different Ellis field on the same box is a separate row, not a silent
    # overwrite: a human resolves the disagreement, memory does not.
    other = mapping_memory.remember(db, family_id="mm-upsert",
                                    mapping={"ellis_field": "position"},
                                    observed_field=field, source="human_correction")
    assert other.id != row.id
    assert mapping_memory.forget(db, family_id="mm-upsert") == 2


def test_remember_declines_the_unlearnable(db):
    assert mapping_memory.remember(db, family_id="", mapping={"ellis_field": "email"},
                                   observed_field={"name": "x", "label": "Email"},
                                   source="human_correction") is None
    assert mapping_memory.remember(db, family_id="mm-none", mapping={},
                                   observed_field={"name": "x", "label": "Email"},
                                   source="human_correction") is None
    assert mapping_memory.remember(db, family_id="mm-none",
                                   mapping={"ellis_field": "email"},
                                   observed_field={"name": "mat-input-4", "label": ""},
                                   source="human_correction") is None
    with pytest.raises(ValueError):
        mapping_memory.remember(db, family_id="mm-none",
                                mapping={"ellis_field": "email"},
                                observed_field={"name": "x", "label": "Email"},
                                source="trust_me")
    with pytest.raises(ValueError):
        mapping_memory.forget(db, family_id="")


# ---- a learned mapping is ACCEPTED, through the real chokepoint ----
def test_learned_mapping_is_accepted_when_its_element_is_observed(db):
    """The box nothing else can read. No curated id, no name hint — the only
    thing that knows what it is, is the correction a human made last build."""
    els = [_el("q17", label="Vaka ndogo ya kazi"),
           _el("email", label="Email address"),
           _el("surname", label="Surname"),
           _el("photo", etype="file", label="Photograph")]
    mapping_memory.forget(db, family_id="mm-accept")

    cold = _request(db, family_id="mm-accept", route_key="rk|mm-cold")
    job, arts = _observed(db, cold, els)
    assert _accepted(_spec(db, cold, job, arts), "q17") is None

    mapping_memory.remember(db, family_id="mm-accept",
                            mapping={"ellis_field": "occupation"},
                            observed_field={**els[0], "page_key": "application"},
                            source="human_correction", actor="reviewer@ellis")

    warm = _request(db, family_id="mm-accept", route_key="rk|mm-warm")
    job2, arts2 = _observed(db, warm, els)
    got = _accepted(_spec(db, warm, job2, arts2), "q17")
    assert got is not None and got["ellis_field"] == "occupation"
    # Grounded in THIS build's observation, not in the remembered row.
    assert got["selector"] == "#q17" and got["page_key"] == "application"
    # Another family's build learns nothing from it.
    stranger = _request(db, family_id="mm-other-family", route_key="rk|mm-stranger")
    job3, arts3 = _observed(db, stranger, els)
    assert _accepted(_spec(db, stranger, job3, arts3), "q17") is None


def test_learned_mapping_follows_a_portal_that_re_mints_its_ids(db):
    """The point of the signature: the form is unchanged, every id is new."""
    mapping_memory.forget(db, family_id="mm-remint")
    before = _el("mat-input-7", label="Occupation")
    mapping_memory.remember(db, family_id="mm-remint",
                            mapping={"ellis_field": "occupation"},
                            observed_field={**before, "page_key": "application"},
                            source="human_correction")
    after = _el("mat-input-91", label="Occupation")
    req = _request(db, family_id="mm-remint", route_key="rk|mm-remint")
    job, arts = _observed(db, req, [after, _el("email", label="Email address"),
                                    _el("surname", label="Surname")])
    got = _accepted(_spec(db, req, job, arts), "mat-input-91")
    assert got is not None and got["ellis_field"] == "occupation"
    assert got["selector"] == "#mat-input-91"


# ---- and REFUSED wherever grounding refuses ----
def test_learned_mapping_onto_a_sensitive_element_is_refused(db):
    """Nothing is filtered before the chokepoint: the refusal is on the record
    where a human can read it, not silently dropped inside memory."""
    mapping_memory.forget(db, family_id="mm-sensitive")
    pwd = _el("accountPassword", etype="password", label="Password", sensitive=True)
    mapping_memory.remember(db, family_id="mm-sensitive",
                            mapping={"ellis_field": "occupation"},
                            observed_field={**pwd, "page_key": "application"},
                            source="human_correction")
    req = _request(db, family_id="mm-sensitive", route_key="rk|mm-sensitive")
    job, arts = _observed(db, req, [pwd, _el("email", label="Email address"),
                                    _el("surname", label="Surname")])
    spec = _spec(db, req, job, arts)
    assert _accepted(spec, "accountPassword") is None
    assert "sensitive_target_refused" in _reasons(spec, "accountPassword")


def _stale_memory(monkeypatch, proposals):
    """A remembered row that no longer describes the live page — the portal
    moved on, or somebody hand-edited the store."""
    monkeypatch.setattr(mapping_memory, "lookup",
                        lambda db, family_id, fields: list(proposals))


def test_learned_mapping_with_no_observed_element_is_refused(db, monkeypatch):
    req = _request(db, family_id="mm-ghost", route_key="rk|mm-ghost")
    job, arts = _observed(db, req, [_el("email", label="Email address"),
                                    _el("surname", label="Surname")])
    _stale_memory(monkeypatch, [{"ellis_field": "occupation",
                                 "portal_field": "fieldRemovedLastTuesday",
                                 "selector": "#fieldRemovedLastTuesday",
                                 "page_key": "application", "artifact_id": arts[0].id,
                                 "required": True, "learned": True}])
    spec = _spec(db, req, job, arts)
    assert _accepted(spec, "fieldRemovedLastTuesday") is None
    assert "ungrounded_no_observed_element" in _reasons(spec, "fieldRemovedLastTuesday")


def test_learned_mapping_with_a_mismatched_selector_is_refused(db, monkeypatch):
    req = _request(db, family_id="mm-mismatch", route_key="rk|mm-mismatch")
    job, arts = _observed(db, req, [_el("q17", label="Vaka ndogo ya kazi"),
                                    _el("email", label="Email address"),
                                    _el("surname", label="Surname")])
    _stale_memory(monkeypatch, [{"ellis_field": "occupation", "portal_field": "q17",
                                 "selector": "#q17_old", "page_key": "application",
                                 "artifact_id": arts[0].id, "required": True,
                                 "learned": True}])
    spec = _spec(db, req, job, arts)
    assert _accepted(spec, "q17") is None
    assert "selector_mismatch_with_observation" in _reasons(spec, "q17")


def test_learned_mapping_with_a_non_deterministic_selector_is_refused(db, monkeypatch):
    req = _request(db, family_id="mm-path", route_key="rk|mm-path")
    deep = _el("q18", selector="div.form > div:nth-child(4) input",
               label="Vaka ndogo ya kazi")
    job, arts = _observed(db, req, [deep, _el("email", label="Email address"),
                                    _el("surname", label="Surname")])
    _stale_memory(monkeypatch, [{"ellis_field": "occupation", "portal_field": "q18",
                                 "selector": deep["selector"], "page_key": "application",
                                 "artifact_id": arts[0].id, "required": True,
                                 "learned": True}])
    spec = _spec(db, req, job, arts)
    assert _accepted(spec, "q18") is None
    assert "non_deterministic_selector" in _reasons(spec, "q18")


# ---- proposed_vocabulary: a reading list, never an edit ----
def test_proposed_vocabulary_lists_an_unknown_but_groundable_field(db):
    """The portal asks something Ellis has no word for. The mapping is still
    refused; the WORD is proposed, for a human to add once."""
    mapping_memory.forget(db, family_id="mm-vocab")
    unknown = _el("sponsorTaxId", label="Sponsor Tax ID (required)")
    mapping_memory.remember(db, family_id="mm-vocab",
                            mapping={"ellis_field": "sponsor_tax_id"},
                            observed_field={**unknown, "page_key": "application"},
                            source="human_correction", actor="reviewer@ellis")
    req = _request(db, family_id="mm-vocab", route_key="rk|mm-vocab")
    job, arts = _observed(db, req, [unknown, _el("email", label="Email address"),
                                    _el("surname", label="Surname")])
    spec = _spec(db, req, job, arts)

    assert _accepted(spec, "sponsorTaxId") is None
    assert not any(m["ellis_field"] == "sponsor_tax_id" for m in spec.field_mappings)
    assert _reasons(spec, "sponsorTaxId") == ["unknown_ellis_field"]
    vocab = (spec.generation_basis or {}).get("proposed_vocabulary") or []
    entry = next(v for v in vocab if v["portal_field"] == "sponsorTaxId")
    assert entry["suggested_ellis_field"] == "sponsor_tax_id"
    assert entry["page_key"] == "application" and entry["input_type"] == "text"
    assert entry["label"] == "Sponsor Tax ID (required)"
    assert entry["artifact_id"] == arts[0].id
    # The vocabulary itself is untouched: a build never grows it.
    assert "sponsor_tax_id" not in specgen.ELLIS_FIELDS
    # And a field that only failed on grounding is NOT a vocabulary candidate.
    assert not any(v["portal_field"] == "email" for v in vocab)


def test_no_vocabulary_key_when_every_proposal_is_nameable(db):
    req = _request(db, family_id="mm-quiet", route_key="rk|mm-quiet")
    job, arts = _observed(db, req, [_el("email", label="Email address"),
                                    _el("surname", label="Surname")])
    spec = _spec(db, req, job, arts)
    assert "proposed_vocabulary" not in (spec.generation_basis or {})


# ---- CROSS-EDITION: the same machinery on a tourist family ----
def test_memory_serves_a_tourist_family_without_clobbering_curated_semantics(db):
    """vietnam-evisa, a tourist e-visa family, through the identical code path.

    Two things at once: memory maps the box Vietnam's form asks that no curated
    id covers, and it leaves the curated basic_* entries alone where the two
    agree — those carry the DD/MM/YYYY portal format and the applicant-question
    metadata memory does not store, and swapping them for a bare learned
    proposal would silently stop a question being asked.
    """
    mapping_memory.forget(db, family_id="vietnam-evisa")
    els = [_el("basic_ttcnHo", label="Surname"),
           _el("basic_ttcnTonGiao", label="Religion"),
           _el("basic_hcNgayCapStr", label="Date of issue", placeholder="DD/MM/YYYY"),
           _el("basic_ttcdGhiChuBoSung", label="Ghi chú bổ sung về nơi lưu trú"),
           _el("basic_anhMat", etype="file", label="Portrait photograph")]

    cold = _request(db, family_id="vietnam-evisa", route_key="rk|vn-cold")
    job, arts = _observed(db, cold, els)
    assert _accepted(_spec(db, cold, job, arts), "basic_ttcdGhiChuBoSung") is None

    # A human corrects the one box, and (harmlessly) confirms one Ellis
    # already knew from its curated map.
    for name, ellis in (("basic_ttcdGhiChuBoSung", "accommodation"),
                        ("basic_ttcnTonGiao", "religion")):
        el = next(e for e in els if e["name"] == name)
        mapping_memory.remember(db, family_id="vietnam-evisa",
                                mapping={"ellis_field": ellis},
                                observed_field={**el, "page_key": "application"},
                                source="human_correction", actor="reviewer@ellis")

    warm = _request(db, family_id="vietnam-evisa", route_key="rk|vn-warm")
    job2, arts2 = _observed(db, warm, els)
    spec = _spec(db, warm, job2, arts2)

    learned = _accepted(spec, "basic_ttcdGhiChuBoSung")
    assert learned is not None and learned["ellis_field"] == "accommodation"
    religion = _accepted(spec, "basic_ttcnTonGiao")
    assert religion["ellis_field"] == "religion"
    assert religion["question"]["key"] == "religion"      # curated metadata kept
    issue = _accepted(spec, "basic_hcNgayCapStr")
    assert issue["format"] == "DD/MM/YYYY"
    mapping_memory.forget(db, family_id="vietnam-evisa")


def test_memory_that_disagrees_with_itself_proposes_nothing(db):
    """Two answers for one box would put two values in one government field.
    A confirmed row outranks a released one; a tie proposes nothing and the
    disagreement waits for a human."""
    mapping_memory.forget(db, family_id="mm-split")
    el = _el("q19", label="Vaka ndogo ya kazi")
    field = {**el, "page_key": "application"}
    rows = [{"ellis_field": "occupation", "source": "human_correction"},
            {"ellis_field": "position", "source": "human_correction"}]
    for r in rows:
        mapping_memory.remember(db, family_id="mm-split",
                                mapping={"ellis_field": r["ellis_field"]},
                                observed_field=field, source=r["source"])
    lookup_field = {**field, "artifact_id": "a1"}
    assert mapping_memory.lookup(db, "mm-split", [lookup_field]) == []

    req = _request(db, family_id="mm-split", route_key="rk|mm-split")
    job, arts = _observed(db, req, [el, _el("email", label="Email address"),
                                    _el("surname", label="Surname")])
    assert _accepted(_spec(db, req, job, arts), "q19") is None

    # One of them witnessed by a human, the other only by a released adapter:
    # the disagreement is settled, and exactly one proposal comes back.
    mapping_memory.forget(db, family_id="mm-split", ellis_field="position")
    mapping_memory.remember(db, family_id="mm-split",
                            mapping={"ellis_field": "position"},
                            observed_field=field, source="released_adapter")
    proposals = mapping_memory.lookup(db, "mm-split", [lookup_field])
    assert [p["ellis_field"] for p in proposals] == ["occupation"]
    mapping_memory.forget(db, family_id="mm-split")


def test_lookup_is_inert_without_a_family_or_a_memory(db):
    fields = [{"name": "q17", "label": "Occupation", "type": "text",
               "page_key": "application", "artifact_id": "a1", "selector": "#q17"}]
    assert mapping_memory.lookup(db, "", fields) == []
    assert mapping_memory.lookup(db, "mm-empty-family", fields) == []
    assert mapping_memory.lookup(db, "mm-empty-family", []) == []


def test_what_an_attended_session_learns_is_recalled_by_a_later_public_build(db):
    """The loop only pays off if the two observers address ONE row.

    An attended session names the page by the order the applicant reached it
    ("attended_1_apply"); public recon slugs the same page "apply". Signed
    raw, a mapping a human corrected on their own run would never be recalled
    by any later build — the capture would be write-only — and two attended
    sessions that visited the pages in a different order would sign the same
    field twice. Tourist family: this is the shared path, not an edition's.
    """
    mapping_memory.forget(db, family_id="vietnam-evisa")
    walked = {"name": "basic_ghiChuNoiO", "label": "Ghi chu ve noi luu tru",
              "type": "text", "selector": "#basic_ghiChuNoiO",
              "page_key": "attended_1_apply"}
    mapping_memory.remember(db, family_id="vietnam-evisa",
                            mapping={"ellis_field": "accommodation"},
                            observed_field=walked, source="human_correction",
                            actor="operator@ellis.example")

    # The same field as PUBLIC recon sees it, on a re-render that moved it and
    # re-minted nothing an id could carry.
    publicly = {"name": "basic_ghiChuNoiO", "label": "Ghi chu ve noi luu tru",
                "type": "text", "selector": "#basic_ghiChuNoiO",
                "page_key": "apply", "artifact_id": "art-public"}
    assert [p["ellis_field"] for p in
            mapping_memory.lookup(db, "vietnam-evisa", [publicly])] == \
        ["accommodation"]

    # And a SECOND attended session that reached the page third, not first.
    later = dict(publicly, page_key="attended_3_apply", artifact_id="art-att3")
    assert [p["ellis_field"] for p in
            mapping_memory.lookup(db, "vietnam-evisa", [later])] == \
        ["accommodation"]

    # Normalizing the visit index must not merge two genuinely different
    # pages: "apply" and "review" are still different fields.
    other_page = dict(publicly, page_key="attended_2_review")
    assert mapping_memory.lookup(db, "vietnam-evisa", [other_page]) == []
    mapping_memory.forget(db, family_id="vietnam-evisa")
