"""Export / import a RELEASED ROUTE as a portable, version-controllable file.

What makes Ellis able to file on a government portal is not source code: it is
a chain of rows in ellis.db (portal family -> adapter link -> released candidate
version carrying the typed flow -> release -> runtime binding), plus the
route's verified fee record and the option lists read from the portal. Losing
the database therefore loses the ability to file, even with the whole repo
checked out. This makes that chain a file that lives in git.

APPLICANT DATA IS NEVER INCLUDED. Only portal structure (selectors, flow
nodes, hostnames), published policy, the official fee, and the portal's own
public dropdown values. Nothing keyed to an applicant, case, session, vault
ref, or document is read by this module — see _TABLES.

  python route_bundle.py export                 # all released routes
  python route_bundle.py export --route VNM
  python route_bundle.py import backend/route_bundles/<file>.json
  python route_bundle.py verify backend/route_bundles/<file>.json
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import inspect, select, text

from app.db import SessionLocal
from app.adapter_factory import models as fm
from app.global_routes.models import FamilyAdapterLink, PortalFamily
from app import models as core

BUNDLE_DIR = Path(__file__).parent / "route_bundles"
BUNDLE_VERSION = 1

# Every table the bundle may touch. Applicant-scoped tables are absent by
# construction: adding one here is the only way to leak personal data, so the
# list is short, explicit, and covered by test_route_bundle.
_TABLES = ("portal_families", "portal_family_adapters", "global_route_pair_policies",
           "adapter_candidates", "adapter_candidate_versions", "adapter_releases",
           "adapter_runtime_bindings", "fee_records", "portal_field_option_cache")

# Columns that must never appear in a bundle even if a table gains them.
_FORBIDDEN_COLS = ("applicant_id", "application_id", "user_id", "email", "phone",
                   "passport_number", "answers", "content", "session_ref",
                   "credential_ref", "vault_ref")


# Columns holding an absolute path on the machine that produced the bundle.
# They identify a user's home directory and are meaningless after a restore
# (the authoritative flow is the DB row, not the on-disk twin), so they are
# blanked rather than shipped.
_LOCAL_PATH_COLS = ("storage_dir",)


def _jsonable(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def _rows(db, table: str, where: str = "", params: dict | None = None) -> list[dict]:
    cols = [c["name"] for c in inspect(db.bind).get_columns(table)]
    leaked = sorted(set(cols) & set(_FORBIDDEN_COLS))
    if leaked:
        raise RuntimeError(
            f"refusing to export {table}: applicant-scoped columns {leaked}")
    sql = f"SELECT * FROM {table}" + (f" WHERE {where}" if where else "")
    out = []
    for r in db.execute(text(sql), params or {}).mappings().all():
        row = {k: _jsonable(v) for k, v in dict(r).items()}
        for col in _LOCAL_PATH_COLS:
            if row.get(col):
                row[col] = ""
        out.append(row)
    return out


def export_routes(db, *, match: str = "") -> list[dict]:
    """One bundle per runtime binding — the thing that actually makes a route
    live. Everything the binding depends on travels with it."""
    bundles = []
    for b in db.execute(select(fm.AdapterRuntimeBinding)).scalars().all():
        if match and match.upper() not in (b.route_key or "").upper():
            continue
        link = db.execute(select(FamilyAdapterLink).where(
            FamilyAdapterLink.candidate_id == b.candidate_id)).scalars().first()
        family_id = getattr(link, "family_id", "") or ""
        bundle = {
            "bundle_version": BUNDLE_VERSION,
            "route_key": b.route_key,
            "candidate_id": b.candidate_id,
            "candidate_version": int(b.candidate_version),
            "family_id": family_id,
            "exported_at": datetime.now().astimezone().isoformat(),
            "tables": {},
        }
        t = bundle["tables"]
        t["portal_families"] = _rows(db, "portal_families",
                                     "family_id = :f", {"f": family_id})
        t["portal_family_adapters"] = _rows(db, "portal_family_adapters",
                                            "candidate_id = :c", {"c": b.candidate_id})
        t["global_route_pair_policies"] = _rows(db, "global_route_pair_policies",
                                                "portal_family_id = :f", {"f": family_id})
        t["adapter_candidates"] = _rows(db, "adapter_candidates",
                                        "id = :c", {"c": b.candidate_id})
        t["adapter_candidate_versions"] = _rows(db, "adapter_candidate_versions",
                                                "candidate_id = :c", {"c": b.candidate_id})
        t["adapter_releases"] = _rows(db, "adapter_releases",
                                      "candidate_id = :c", {"c": b.candidate_id})
        t["adapter_runtime_bindings"] = _rows(db, "adapter_runtime_bindings",
                                              "candidate_id = :c", {"c": b.candidate_id})
        t["portal_field_option_cache"] = _rows(db, "portal_field_option_cache",
                                               "candidate_id = :c", {"c": b.candidate_id})
        # The route's official fee, matched on the destination the family serves.
        dests = set()
        for row in t["portal_families"]:
            for d in _as_list(row.get("destinations")):
                dests.add(str(d))
        fees = []
        for rec in db.execute(select(core.FeeRecord)).scalars().all():
            if not dests or _dest_matches(rec.destination, dests):
                fees.append(rec.id)
        t["fee_records"] = [r for r in _rows(db, "fee_records") if r["id"] in fees]
        bundles.append(bundle)
    return bundles


def _as_list(value) -> list:
    """Raw SQL hands JSON columns back as TEXT, so a destinations list arrives
    as '["VNM"]'. Iterating that string yields characters — which silently
    matched nothing and dropped the route's fee from the bundle."""
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            out = json.loads(value)
            return out if isinstance(out, list) else []
        except ValueError:
            return []
    return []


def _dest_matches(destination: str, iso3_set: set) -> bool:
    """Fee records key on a display name ('Vietnam'); families on ISO-3."""
    from app.visa_snapshot.registry import _country_index
    d = str(destination or "").strip()
    if d.upper() in iso3_set:
        return True
    try:
        for code in iso3_set:
            entry = _country_index().get(code.upper()) or {}
            name = str(entry.get("name") or "")
            common = str(entry.get("common_name") or "")
            if d.lower() in (name.lower(), common.lower()):
                return True
    except Exception:  # noqa: BLE001 — registry optional
        pass
    return False


def assert_no_personal_data(bundle: dict) -> None:
    """A bundle goes to GitHub, so prove it carries no applicant data."""
    blob = json.dumps(bundle)
    for col in _FORBIDDEN_COLS:
        if f'"{col}"' in blob:
            raise RuntimeError(f"bundle contains a forbidden field: {col}")
    for table in bundle.get("tables", {}):
        if table not in _TABLES:
            raise RuntimeError(f"bundle contains an unexpected table: {table}")


def import_bundle(db, bundle: dict, *, overwrite: bool = False) -> dict:
    """Restore a route into this database. Existing rows are left alone unless
    overwrite=True — a restore must never silently replace a live binding."""
    assert_no_personal_data(bundle)
    report = {"inserted": {}, "skipped": {}}
    for table in _TABLES:
        rows = bundle.get("tables", {}).get(table) or []
        ins = skip = 0
        cols_present = {c["name"] for c in inspect(db.bind).get_columns(table)}
        for row in rows:
            row = {k: v for k, v in row.items() if k in cols_present}
            pk = "family_id" if table == "portal_families" else "id"
            if pk not in row:
                pk = next(iter(row))
            exists = db.execute(
                text(f"SELECT 1 FROM {table} WHERE {pk} = :v"),
                {"v": row[pk]}).first()
            if exists and not overwrite:
                skip += 1
                continue
            if exists:
                db.execute(text(f"DELETE FROM {table} WHERE {pk} = :v"), {"v": row[pk]})
            names = ", ".join(row)
            binds = ", ".join(f":{k}" for k in row)
            payload = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
                       for k, v in row.items()}
            db.execute(text(f"INSERT INTO {table} ({names}) VALUES ({binds})"), payload)
            ins += 1
        report["inserted"][table] = ins
        report["skipped"][table] = skip
    db.commit()
    return report


def _write(bundles: list[dict]) -> list[Path]:
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for b in bundles:
        assert_no_personal_data(b)
        name = f"{b['family_id'] or b['candidate_id'][:8]}-v{b['candidate_version']}.json"
        path = BUNDLE_DIR / name
        path.write_text(json.dumps(b, indent=2, sort_keys=True, ensure_ascii=False))
        written.append(path)
    return written


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "export"
    db = SessionLocal()
    try:
        if cmd == "export":
            match = ""
            if "--route" in sys.argv:
                match = sys.argv[sys.argv.index("--route") + 1]
            bundles = export_routes(db, match=match)
            if not bundles:
                print("no released routes to export")
                return 0
            for p in _write(bundles):
                data = json.loads(p.read_text())
                counts = {k: len(v) for k, v in data["tables"].items() if v}
                print(f"wrote {p.relative_to(Path(__file__).parent.parent)}")
                print(f"   route  {data['route_key'][:70]}")
                print(f"   rows   {counts}")
            return 0
        if cmd in ("import", "verify"):
            path = Path(sys.argv[2])
            bundle = json.loads(path.read_text())
            assert_no_personal_data(bundle)
            if cmd == "verify":
                print(f"{path.name}: no applicant data; "
                      f"tables={ {k: len(v) for k, v in bundle['tables'].items() if v} }")
                return 0
            rep = import_bundle(db, bundle, overwrite="--overwrite" in sys.argv)
            print("restored:", {k: v for k, v in rep["inserted"].items() if v})
            print("already present (skipped):",
                  {k: v for k, v in rep["skipped"].items() if v})
            return 0
        print(__doc__)
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
