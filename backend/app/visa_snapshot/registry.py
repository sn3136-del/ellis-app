"""Read-only access to the canonical reference registries under data/reference/."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
REFERENCE_DIR = REPO_ROOT / "data" / "reference"
SCHEMA_DIR = REPO_ROOT / "data" / "schemas"
SNAPSHOT_DIR = REPO_ROOT / "data" / "snapshots"

REGISTRY_FILES = [
    "countries", "territories", "nationalities", "passport_issuers",
    "residence_jurisdictions", "consular_jurisdictions", "currencies",
    "languages", "travel_document_types", "tourist_visa_categories",
]


class RegistryError(ValueError):
    pass


@lru_cache(maxsize=None)
def load_registry(name: str) -> dict:
    if name not in REGISTRY_FILES:
        raise RegistryError(f"unknown registry: {name}")
    path = REFERENCE_DIR / f"{name}.json"
    if not path.exists():
        raise RegistryError(f"registry file missing: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("registry") != name:
        raise RegistryError(f"registry name mismatch in {path}")
    return data


@lru_cache(maxsize=None)
def _country_index() -> dict:
    """alpha-2 and alpha-3 (and lowercase name) -> country entry."""
    idx: dict[str, dict] = {}
    for e in load_registry("countries")["entries"]:
        idx[e["alpha_2"]] = e
        idx[e["alpha_3"]] = e
        idx[e["name"].lower()] = e
        if e.get("common_name"):
            idx[e["common_name"].lower()] = e
    return idx


@lru_cache(maxsize=None)
def _nationality_codes() -> frozenset[str]:
    return frozenset(e["code"] for e in load_registry("nationalities")["entries"])


@lru_cache(maxsize=None)
def _document_type_codes() -> frozenset[str]:
    return frozenset(e["code"] for e in load_registry("travel_document_types")["entries"])


@lru_cache(maxsize=None)
def _category_index() -> dict[str, list[str]]:
    return {e["code"]: e["subtypes"] for e in load_registry("tourist_visa_categories")["entries"]}


@lru_cache(maxsize=None)
def _currency_codes() -> frozenset[str]:
    return frozenset(e["code"] for e in load_registry("currencies")["entries"])


def normalize_country(value: str, *, field: str = "country") -> str:
    """Normalize any accepted country identifier to ISO alpha-3. Raises RegistryError."""
    if not value or not isinstance(value, str):
        raise RegistryError(f"{field}: empty value")
    v = value.strip()
    entry = _country_index().get(v.upper()) or _country_index().get(v.lower())
    if not entry:
        raise RegistryError(f"{field}: unknown country {value!r}")
    return entry["alpha_3"]


def normalize_nationality(value: str) -> str:
    """Normalize a nationality to registry code (ISO alpha-3 or special class)."""
    if not value or not isinstance(value, str):
        raise RegistryError("nationality: empty value")
    v = value.strip().upper()
    if v in _nationality_codes():
        return v
    # accept alpha-2 / names by mapping through the country registry
    entry = _country_index().get(v) or _country_index().get(value.strip().lower())
    if entry and entry["alpha_3"] in _nationality_codes():
        return entry["alpha_3"]
    raise RegistryError(f"nationality: unknown {value!r}")


def normalize_document_type(value: str) -> str:
    v = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if v in _document_type_codes():
        return v
    raise RegistryError(f"travel_document_type: unknown {value!r}")


def normalize_category(value: str, subtype: str | None = None) -> tuple[str, str]:
    v = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    cats = _category_index()
    if v not in cats:
        raise RegistryError(f"visa_category: unknown {value!r}")
    s = (subtype or "").strip().lower().replace("-", "_").replace(" ", "_") or "any"
    if s != "any" and s not in cats[v]:
        raise RegistryError(f"visa_subtype: {subtype!r} not valid for category {v}")
    return v, s


def normalize_currency(value: str) -> str:
    v = (value or "").strip().upper()
    if v not in _currency_codes():
        raise RegistryError(f"currency: unknown {value!r}")
    return v
