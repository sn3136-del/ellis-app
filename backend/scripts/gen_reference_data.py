"""One-time deterministic generator for the canonical reference registries.

Emits versioned JSON registries under data/reference/ from ISO sources
(pycountry: ISO 3166-1, ISO 4217, ISO 639) plus explicitly curated
additions (territories' parent sovereignty, special nationality classes,
travel-document types, tourist-visa categories).

Deterministic: same inputs -> byte-identical outputs (sorted keys, sorted
entries, fixed generated_at taken from the snapshot date, no wall clock).

Usage: .venv/bin/python scripts/gen_reference_data.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pycountry

SNAPSHOT_DATE = "2026-07-23"
REGISTRY_VERSION = 1
OUT_DIR = Path(__file__).resolve().parents[2] / "data" / "reference"

# ---------------------------------------------------------------------------
# Curated: ISO 3166-1 codes that are dependent territories / SARs / special
# regions, with their parent sovereignty. Everything not listed is treated as
# a sovereign state for registry purposes (registry data, not a legal claim).
# ---------------------------------------------------------------------------
TERRITORY_PARENT = {
    "AI": "GB", "AS": "US", "AW": "NL", "AX": "FI", "BL": "FR", "BM": "GB",
    "BQ": "NL", "BV": "NO", "CC": "AU", "CK": "NZ", "CW": "NL", "CX": "AU",
    "FK": "GB", "FO": "DK", "GF": "FR", "GG": "GB", "GI": "GB", "GL": "DK",
    "GP": "FR", "GS": "GB", "GU": "US", "HK": "CN", "HM": "AU", "IM": "GB",
    "IO": "GB", "JE": "GB", "KY": "GB", "MF": "FR", "MO": "CN", "MP": "US",
    "MQ": "FR", "MS": "GB", "NC": "FR", "NF": "AU", "NU": "NZ", "PF": "FR",
    "PM": "FR", "PN": "GB", "PR": "US", "RE": "FR", "SH": "GB", "SJ": "NO",
    "SX": "NL", "TC": "GB", "TF": "FR", "TK": "NZ", "UM": "US", "VG": "GB",
    "VI": "US", "WF": "FR", "YT": "FR", "EH": None, "PS": None, "AQ": None,
    "TW": None,  # separate customs/passport territory; parent left null deliberately
}

# Special administrative regions and passport-issuing territories that issue
# their own travel documents distinct from the parent state's ordinary passport.
OWN_PASSPORT_TERRITORIES = {"HK", "MO", "TW", "FO", "GL", "AW", "CW", "SX", "GG", "JE", "IM", "GI", "BM", "KY", "VG", "TC", "MS", "AI", "FK", "SH"}

# Curated special nationality / travel-document classes that do not map 1:1
# onto an ISO country nationality.
SPECIAL_NATIONALITIES = [
    {"code": "XXA", "name": "Stateless person", "kind": "special", "notes": "1954 Convention travel document holders; nationality recorded as stateless."},
    {"code": "XXB", "name": "Refugee (1951 Convention)", "kind": "special", "notes": "Refugee travel document holders under the 1951 Convention."},
    {"code": "XXC", "name": "Refugee (other)", "kind": "special", "notes": "Refugee travel document holders outside the 1951 Convention."},
    {"code": "XXX", "name": "Unspecified nationality", "kind": "special", "notes": "Nationality unspecified on travel document."},
    {"code": "GBD", "name": "British Overseas Territories Citizen", "kind": "british_class", "notes": "BOTC — distinct from British Citizen (GBR)."},
    {"code": "GBN", "name": "British National (Overseas)", "kind": "british_class", "notes": "BN(O) — Hong Kong-linked British nationality class."},
    {"code": "GBO", "name": "British Overseas Citizen", "kind": "british_class", "notes": "BOC."},
    {"code": "GBP", "name": "British Protected Person", "kind": "british_class", "notes": "BPP."},
    {"code": "GBS", "name": "British Subject", "kind": "british_class", "notes": "British Subject under the British Nationality Act 1981."},
]

TRAVEL_DOCUMENT_TYPES = [
    {"code": "ordinary_passport", "name": "Ordinary passport", "icao_code_prefix": "P", "notes": "Standard national passport."},
    {"code": "diplomatic_passport", "name": "Diplomatic passport", "icao_code_prefix": "PD", "notes": "Issued to diplomats; different visa rules frequently apply."},
    {"code": "service_passport", "name": "Service / official passport", "icao_code_prefix": "PS", "notes": "Issued to government officials on duty travel."},
    {"code": "emergency_passport", "name": "Emergency / temporary passport", "icao_code_prefix": "PE", "notes": "Short-validity document issued in urgent cases; often restricted recognition."},
    {"code": "refugee_travel_document", "name": "Refugee travel document (1951 Convention)", "icao_code_prefix": "PT", "notes": "Issued to recognized refugees by the country of residence."},
    {"code": "stateless_travel_document", "name": "Stateless-person travel document (1954 Convention)", "icao_code_prefix": "PT", "notes": "Issued to recognized stateless persons by the country of residence."},
    {"code": "alien_passport", "name": "Alien's passport / certificate of identity", "icao_code_prefix": "PT", "notes": "Issued to non-citizen residents unable to obtain a national passport."},
    {"code": "laissez_passer", "name": "Laissez-passer", "icao_code_prefix": "PT", "notes": "Issued by states or international organizations (e.g. UN)."},
]

TOURIST_VISA_CATEGORIES = [
    {"code": "tourist_visa", "name": "Tourist visa (consular)", "subtypes": ["single_entry", "double_entry", "multiple_entry", "group_tourist"], "notes": "Sticker/label visa issued by an embassy, consulate, or authorized visa center."},
    {"code": "evisa_tourist", "name": "Tourist eVisa", "subtypes": ["single_entry", "multiple_entry"], "notes": "Electronic visa applied for and issued online through an official portal."},
    {"code": "eta", "name": "Electronic travel authorization", "subtypes": ["standard"], "notes": "Pre-travel electronic authorization for otherwise visa-free or visa-waiver travellers (e.g. ESTA, UK ETA, NZeTA, Canadian eTA, K-ETA)."},
    {"code": "visa_on_arrival_tourist", "name": "Tourist visa on arrival", "subtypes": ["single_entry", "multiple_entry"], "notes": "Visa granted at the border/airport on arrival; may require pre-registration."},
    {"code": "visa_free_entry", "name": "Visa-free entry (tourism)", "subtypes": ["standard"], "notes": "No visa or authorization required for a tourist stay within the permitted period; a disposition, present so route records can bind conditions/evidence."},
    {"code": "transit_authorization", "name": "Transit visa / authorization", "subtypes": ["airside_transit", "landside_transit"], "notes": "Authorization required to transit; included because tourist itineraries traverse transit states."},
]

RESIDENCE_STATUSES = [
    "citizen", "permanent_resident", "temporary_resident", "student", "worker",
    "refugee_status_holder", "asylum_seeker", "stateless_resident", "visitor", "other", "unknown",
]


def _countries():
    entries = []
    for c in sorted(pycountry.countries, key=lambda c: c.alpha_2):
        code = c.alpha_2
        is_territory = code in TERRITORY_PARENT
        entries.append({
            "alpha_2": code,
            "alpha_3": c.alpha_3,
            "numeric": c.numeric,
            "name": c.name,
            "official_name": getattr(c, "official_name", None),
            "common_name": getattr(c, "common_name", None),
            "flag": getattr(c, "flag", None),
            "is_territory": is_territory,
            "parent_alpha_2": TERRITORY_PARENT.get(code) if is_territory else None,
            "issues_own_passport": (not is_territory) or code in OWN_PASSPORT_TERRITORIES,
        })
    return entries


def _territories(countries):
    return [c for c in countries if c["is_territory"]]


def _nationalities(countries):
    entries = []
    for c in countries:
        # Territories mostly do not confer a distinct nationality; SARs,
        # Taiwan and Palestinian Authority documents are handled as passport
        # issuers + distinct document classes.
        if c["is_territory"] and c["alpha_2"] not in {"HK", "MO", "TW", "PS"}:
            continue
        entries.append({
            "code": c["alpha_3"],
            "alpha_2": c["alpha_2"],
            "name": c["name"],
            "kind": "territory_document_class" if c["is_territory"] else "state_nationality",
            "notes": None,
        })
    for s in SPECIAL_NATIONALITIES:
        entries.append({"code": s["code"], "alpha_2": None, "name": s["name"], "kind": s["kind"], "notes": s["notes"]})
    return sorted(entries, key=lambda e: e["code"])


def _passport_issuers(countries):
    return [
        {"alpha_2": c["alpha_2"], "alpha_3": c["alpha_3"], "name": c["name"],
         "is_territory": c["is_territory"], "parent_alpha_2": c["parent_alpha_2"]}
        for c in countries if c["issues_own_passport"]
    ]


def _residence_jurisdictions(countries):
    return [
        {"alpha_2": c["alpha_2"], "alpha_3": c["alpha_3"], "name": c["name"],
         "is_territory": c["is_territory"], "parent_alpha_2": c["parent_alpha_2"],
         "residence_statuses": RESIDENCE_STATUSES}
        for c in countries
    ]


def _currencies():
    return [
        {"code": c.alpha_3, "numeric": getattr(c, "numeric", None), "name": c.name}
        for c in sorted(pycountry.currencies, key=lambda c: c.alpha_3)
    ]


def _languages():
    entries = []
    for l in pycountry.languages:
        a2 = getattr(l, "alpha_2", None)
        if not a2:
            continue  # registry scope: ISO 639-1 languages
        entries.append({"alpha_2": a2, "alpha_3": l.alpha_3, "name": l.name})
    return sorted(entries, key=lambda e: e["alpha_2"])


def _consular_jurisdictions():
    # Structural registry: entries are added by the verified research pipeline
    # (each backed by SourceEvidence) — never guessed at generation time.
    return []


def registry(name, entries, notes=None, source=None):
    return {
        "registry": name,
        "version": REGISTRY_VERSION,
        "generated_at": SNAPSHOT_DATE,
        "snapshot_date": SNAPSHOT_DATE,
        "source": source or "ISO via pycountry 26.2.16 + explicitly curated additions (see backend/scripts/gen_reference_data.py)",
        "notes": notes,
        "entry_count": len(entries),
        "entries": entries,
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    countries = _countries()
    files = {
        "countries.json": registry("countries", countries, notes="All ISO 3166-1 entries; territories flagged with parent sovereignty."),
        "territories.json": registry("territories", _territories(countries), notes="Dependent territories, SARs, and special regions with parent mapping (parent null where deliberately unassigned)."),
        "nationalities.json": registry("nationalities", _nationalities(countries), notes="State nationalities (ISO alpha-3) + SAR/Taiwan document classes + special classes (stateless, refugee, British nationality classes)."),
        "passport_issuers.json": registry("passport_issuers", _passport_issuers(countries), notes="Jurisdictions issuing their own travel documents, incl. SARs and territories with distinct passports."),
        "residence_jurisdictions.json": registry("residence_jurisdictions", _residence_jurisdictions(countries), notes="Every country/territory as a possible jurisdiction of lawful residence, with normalized residence statuses."),
        "consular_jurisdictions.json": registry("consular_jurisdictions", _consular_jurisdictions(), notes="Populated only by the verified research pipeline with SourceEvidence backing; empty at generation time by design.", source="verified research pipeline"),
        "currencies.json": registry("currencies", _currencies(), notes="ISO 4217 active currencies."),
        "languages.json": registry("languages", _languages(), notes="ISO 639-1 languages."),
        "travel_document_types.json": registry("travel_document_types", TRAVEL_DOCUMENT_TYPES, notes="Curated travel-document taxonomy incl. refugee/stateless/alien/emergency documents.", source="curated"),
        "tourist_visa_categories.json": registry("tourist_visa_categories", TOURIST_VISA_CATEGORIES, notes="Curated tourist-visa category/subtype taxonomy.", source="curated"),
    }
    for fname, payload in files.items():
        path = OUT_DIR / fname
        path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {path.relative_to(OUT_DIR.parents[1])} ({payload['entry_count']} entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
