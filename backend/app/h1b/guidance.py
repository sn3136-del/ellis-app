"""Curated, deterministic H1B route facts. No model in the loop: H1B is
statutory, so guidance is data with official sources, reviewed by a human when
it changes. Amounts and windows carry an as_of date; anything stale must be
re-verified before it is shown next to a payment.

Sources verified 2026-08-09 (research pass with live official pages).
"""
from __future__ import annotations

CONTINUATION_KIND = "h1b_petition"

AS_OF = "2026-08-09"

# Case kinds. cap_initial requires the annual electronic registration (the
# FY2027 cap is exhausted; the FY2028 window is expected ~March 2027). The
# year-round kinds skip registration and can genuinely file today.
CASE_KINDS = ("cap_initial", "extension", "amendment", "transfer", "cap_exempt")

_YEAR_ROUND = ("extension", "amendment", "transfer", "cap_exempt")

# USCIS/DOL fee facts (USD). Verified against uscis.gov / flag.dol.gov pages
# on AS_OF. The $100k proclamation payment is vacated (D. Mass. 2026-06-08,
# stay denied 2026-07-24) and the proclamation self-expires 2026-09-20; it is
# deliberately NOT in this table — if it revives it enters as its own reviewed
# row, feature-gated.
FEES = {
    "registration": {"amount": 215, "per": "beneficiary",
                     "source": "https://www.uscis.gov/working-in-the-united-states/temporary-workers/h-1b-specialty-occupations"},
    "i129_base_online": {"amount": 730, "per": "petition",
                         "source": "https://www.uscis.gov/g-1055"},
    "i129_base_paper": {"amount": 780, "per": "petition",
                        "source": "https://www.uscis.gov/g-1055"},
    "i129_base_small_nonprofit": {"amount": 460, "per": "petition",
                                  "source": "https://www.uscis.gov/g-1055"},
    "asylum_program": {"amount": 600, "per": "petition", "small_employer": 300,
                       "nonprofit": 0,
                       "source": "https://www.uscis.gov/g-1055"},
    "acwia_training": {"amount": 1500, "per": "petition", "small_employer": 750,
                       "source": "https://www.uscis.gov/g-1055"},
    "fraud_prevention": {"amount": 500, "per": "petition",
                         "note": "initial and change-of-employer petitions only",
                         "source": "https://www.uscis.gov/g-1055"},
    "pl_114_113": {"amount": 4000, "per": "petition",
                   "note": "employers with 50+ employees, majority H-1B/L",
                   "source": "https://www.uscis.gov/g-1055"},
    "premium_processing": {"amount": 2965, "per": "petition",
                           "note": "optional; raised from 2805 on 2026-03-01",
                           "source": "https://www.uscis.gov/forms/all-forms/how-do-i-request-premium-processing"},
    "lca": {"amount": 0, "per": "application",
            "source": "https://flag.dol.gov/programs/lca"},
}

# Statutory sequencing facts each step's release logic consults.
STEP_FACTS = {
    "lca": {
        "portal": "flag.dol.gov",
        "form": "ETA-9035E",
        "acting_party": "petitioner",
        "processing": "certified or denied within 7 working days",
        "filing_window": "no earlier than 6 months before employment start",
        "validity": "up to 3 years",
        "source": "https://flag.dol.gov/programs/lca",
    },
    "registration": {
        "portal": "my.uscis.gov",
        "form": "H-1B electronic registration",
        "acting_party": "petitioner",
        "window": "annual, ~early March; FY2028 window expected March 2027",
        "note": "beneficiary-centric, wage-weighted selection (OEWS IV=4 ... I=1, "
                "lowest-wins across an individual's registrations)",
        "source": "https://www.uscis.gov/working-in-the-united-states/temporary-workers/h-1b-specialty-occupations/h-1b-electronic-registration-process",
    },
    "i129": {
        "portal": "my.uscis.gov",
        "form": "I-129 (edition 02/27/26 only)",
        "acting_party": "petitioner",
        "prerequisite": "certified LCA; for cap cases also a selection notice",
        "source": "https://www.uscis.gov/i-129",
    },
    "ds160_consular": {
        "portal": "ceac.state.gov",
        "form": "DS-160 + consular interview (China posts for CN beneficiaries)",
        "acting_party": "beneficiary",
        "prerequisite": "approved I-129 (I-797) receipt/approval evidence",
        # China specifics, verified 2026-08-09. Interview waiver/dropbox no
        # longer exists for H-1B (eliminated 2025-09/10); in-person interview
        # is mandatory at a post in the country of nationality/residence.
        "china_posts": ["Beijing", "Shanghai", "Guangzhou", "Shenyang"],
        "scheduling": "ustraveldocs.com/cn (CGI Federal); MRV receipt + DS-160 "
                      "confirmation required; passport return via CITIC pickup "
                      "or EMS",
        "mrv_fee_usd": 205,
        "interview_waiver": "not available for H-1B",
        "reciprocity_note": "H visa foil for PRC nationals: 12 months, multiple "
                            "entry, regardless of petition validity — travelers "
                            "restamp annually",
        "admin_processing_note": "221(g)/TAL export-control checks are the "
                                 "dominant delay for technology roles",
        "source": "https://travel.state.gov/content/travel/en/News/visas-news/interview-waiver-update-sept-18-2025.html",
    },
}


def step_plan(case_kind: str, *, beneficiary_abroad: bool) -> list[dict]:
    """The ordered filing plan for a case kind. Registration exists only for
    cap_initial; the consular leg exists only when the beneficiary needs a
    visa stamp abroad."""
    if case_kind not in CASE_KINDS:
        raise ValueError(f"unknown h1b case kind: {case_kind}")
    steps = [{"step_key": "lca", "acting_party": "petitioner", "depends_on": []}]
    if case_kind == "cap_initial":
        # Registration precedes everything else in season but does not depend
        # on the LCA; both feed the I-129.
        steps.append({"step_key": "registration", "acting_party": "petitioner",
                      "depends_on": []})
        steps.append({"step_key": "i129", "acting_party": "petitioner",
                      "depends_on": ["lca", "registration"]})
    else:
        steps.append({"step_key": "i129", "acting_party": "petitioner",
                      "depends_on": ["lca"]})
    if beneficiary_abroad:
        steps.append({"step_key": "ds160_consular", "acting_party": "beneficiary",
                      "depends_on": ["i129"]})
    return steps


def build_guidance(case_kind: str, *, beneficiary_abroad: bool) -> dict:
    """The deterministic CaseRouteGuidance payload for the parent case."""
    plan = step_plan(case_kind, beneficiary_abroad=beneficiary_abroad)
    return {
        "status": "curated",
        "as_of": AS_OF,
        "case_kind": case_kind,
        "year_round": case_kind in _YEAR_ROUND,
        "steps": [{**s, **{k: v for k, v in STEP_FACTS[s["step_key"]].items()
                           if k != "acting_party"}} for s in plan],
        "fees": FEES,
        "sources": sorted({f["source"] for f in FEES.values()}
                          | {s["source"] for s in STEP_FACTS.values()}),
    }
