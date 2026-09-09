import copy

import pytest

from app.visa_snapshot import health_context, kimi_primary


@pytest.mark.parametrize("original", ["not_applicable", "always_required", "conditional"])
@pytest.mark.parametrize("route", [
    {"passport_nationality":"IND", "lawful_country_of_residence":"IND"},
    {"passport_nationality":"IND", "recent_travel_countries":["BRA"], "transit_countries":["BRA"]},
])
def test_triggered_health_rule_never_reuses_cached_traveller_eligibility(original, route):
    item = {"name":"Vaccination certificate", "applicability":original,
            "trigger":"Arrival from a risk country within 9 days or transit longer than 12 hours",
            "trigger_countries":["BRA"], "question":"Have you visited a risk country within 9 days?"}
    guidance = {"disposition":"VISA_EXEMPT", "health_requirements":[item]}
    before = copy.deepcopy(guidance)
    projected = health_context.apply(guidance,route)
    rule = projected["health_requirements"][0]
    assert rule["applicability"] == "conditional"
    assert rule["source_applicability"] == original
    assert rule["context_status"] == "needs_itinerary_check"
    for key in ("name","trigger","trigger_countries","question"):
        assert rule[key] == item[key]
    assert guidance == before
    assert projected["disposition"] == guidance["disposition"]


def test_universal_requirements_and_unconditional_nonrequirements_survive():
    rules = [{"name":"Universal vaccination", "applicability":"always_required", "trigger":None},
             {"name":"Universal declaration", "applicability":"always_required", "trigger":"Required for all travellers"},
             {"name":"Retired test", "applicability":"not_applicable", "trigger":None}]
    g = {"health_requirements":rules}
    assert health_context.apply(g,{}) is g


def test_unknown_country_list_does_not_erase_text_condition_and_malformed_is_not_laundered():
    rule = {"name":"Certificate", "applicability":"not_applicable", "trigger_countries":[],
            "trigger":"Arrival from a risk country", "question":None}
    result = health_context.apply({"health_requirements":[rule,"bad item"]},{})
    assert result["health_requirements"][0]["applicability"] == "conditional"
    assert result["health_requirements"][1] == "bad item"
    assert health_context.apply({"health_requirements":"bad list"},{}) == {"health_requirements":"bad list"}


def test_public_route_wrapper_projects_health_without_changing_canonical_guidance(monkeypatch):
    cached = {"health_requirements":[{"name":"Certificate","applicability":"not_applicable",
                                      "trigger":"Recent travel from a risk country"}]}
    monkeypatch.setattr(kimi_primary,"_destination_guidance",lambda *a,**kw:{"guidance":cached})
    out = kimi_primary.get_route_guidance(None,{"passport_nationality":"IND","destination_country":"VNM"})
    assert out["guidance"]["health_requirements"][0]["applicability"] == "conditional"
    assert cached["health_requirements"][0]["applicability"] == "not_applicable"
