"""Warm, export and import the Database's decision cache.

The Database answers from the Kimi-primary decision cache: a cached route is
milliseconds, a fresh one is a full model pass (up to a minute). So popular
routes are decided ONCE here, exported to a seed the repo ships, and imported
into a fresh clone's database at first boot — Trip.com's first lookups land
on warm cache instead of a cold model call. Freshness stays honest: seeded
rows keep their real generated_at/fresh_until, and a stale one is served
instantly while the background refresh replaces it.

  warm    .venv/bin/python scripts/warm_database.py warm   (uses DATABASE_URL)
  export  .venv/bin/python scripts/warm_database.py export
  import  .venv/bin/python scripts/warm_database.py import  (idempotent)
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

SEED = pathlib.Path(__file__).resolve().parents[2] / "data" / "database_seed" / \
    "kimi_guidance_seed.json"

# The starter set. Trip.com's requirements name Phase 1 coverage as the
# stations TRIP Station covers, and the TEST stations as their two
# highest-traffic ones: Hong Kong and the US. So all three origins are warmed
# across the Phase 1 destination list. Everything else warms itself on first
# lookup.
PHASE1 = ("HKG", "TWN", "JPN", "KOR", "USA", "THA", "SGP", "MYS", "GBR",
          "RUS", "AUS", "IDN", "PHL", "FRA", "VNM", "ESP", "IND", "CAN")
# The origins: mainland China (the app's own applicants) plus Trip.com's two
# named test stations.
ORIGINS = ("CHN", "HKG", "USA")
WARM_ROUTES = [(o, d) for o in ORIGINS for d in PHASE1 if o != d]


def _route(nat: str, dest: str) -> dict:
    return {"passport_nationality": nat, "passport_issuing_country": nat,
            "lawful_country_of_residence": nat,
            "travel_document_type": "ordinary_passport",
            "destination_country": dest, "visa_category": "tourist_visa",
            "travel_purpose": "tourism"}


def _warm_one(pair: tuple) -> tuple:
    """One route, on this thread's own session. Returns (ok, line)."""
    from app.db import SessionLocal
    from app.visa_snapshot import kimi_primary
    nat, dest = pair
    db = SessionLocal()
    try:
        out = kimi_primary.get_route_guidance(db, _route(nat, dest))
        disp = (out.get("guidance") or {}).get("disposition", "?")
        return True, (f"  {nat}->{dest}: {out.get('status')} {disp}"
                      f"{' (cached)' if out.get('cached') else ''}")
    except Exception as e:  # noqa: BLE001 — warmth is best-effort
        return False, f"  {nat}->{dest}: FAILED {str(e)[:80]}"
    finally:
        db.close()


def warm(workers: int = 4) -> int:
    """Warm every starter route. The work is HTTP-bound on the model, so a
    small thread pool cuts a long serial pass to a few minutes; each worker
    holds its own session."""
    from concurrent.futures import ThreadPoolExecutor
    ok = fail = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for good, line in pool.map(_warm_one, WARM_ROUTES):
            print(line, flush=True)
            ok, fail = (ok + 1, fail) if good else (ok, fail + 1)
    print(f"warmed {ok}, failed {fail}")
    return 0


def export() -> int:
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.visa_snapshot.models import KimiRouteGuidanceCache as C
    db = SessionLocal()
    rows = []
    for r in db.execute(select(C)).scalars():
        rows.append({
            "cache_key": r.cache_key, "route": r.route, "status": r.status,
            "guidance": r.guidance, "missing_fields": r.missing_fields,
            "contradictions": r.contradictions, "model": r.model,
            "verification": r.verification or {},
            "generated_at": r.generated_at.isoformat() if r.generated_at else None,
            "fresh_until": r.fresh_until.isoformat() if r.fresh_until else None,
        })
    db.close()
    SEED.parent.mkdir(parents=True, exist_ok=True)
    SEED.write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    print(f"exported {len(rows)} cached decisions -> {SEED}")
    from app import i18n
    i18n.flush_cache()
    if i18n._cache_path().is_file():
        (SEED.parent / "i18n_cache.json").write_text(i18n._cache_path().read_text())
        print("exported translation cache")
    return 0


def import_seed() -> int:
    if not SEED.is_file():
        print("no seed file — nothing to import")
        return 0
    from datetime import datetime
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.visa_snapshot.models import KimiRouteGuidanceCache as C
    db = SessionLocal()
    added = 0
    for row in json.loads(SEED.read_text()):
        if db.execute(select(C).where(
                C.cache_key == row["cache_key"])).scalars().first():
            continue          # a locally-decided answer always wins
        db.add(C(cache_key=row["cache_key"], route=row["route"],
                 status=row["status"], guidance=row["guidance"],
                 missing_fields=row.get("missing_fields") or [],
                 contradictions=row.get("contradictions") or [],
                 model=row.get("model") or "",
                 verification=row.get("verification") or {},
                 generated_at=datetime.fromisoformat(row["generated_at"])
                 if row.get("generated_at") else None,
                 fresh_until=datetime.fromisoformat(row["fresh_until"])
                 if row.get("fresh_until") else None))
        added += 1
    db.commit()
    db.close()
    print(f"imported {added} cached decisions")
    # Seed the shipped translation cache so the language picker is instant on
    # the demo routes; a locally-cached translation always wins on merge.
    tx = SEED.parent / "i18n_cache.json"
    if tx.is_file():
        from app import i18n
        cur = i18n._cache_path()
        try:
            existing = json.loads(cur.read_text()) if cur.is_file() else {}
        except Exception:  # noqa: BLE001
            existing = {}
        merged = json.loads(tx.read_text())
        merged.update(existing)
        cur.parent.mkdir(parents=True, exist_ok=True)
        cur.write_text(json.dumps(merged, ensure_ascii=False))
        print(f"seeded {len(merged)} translations")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "warm"
    raise SystemExit({"warm": warm, "export": export,
                      "import": import_seed}[cmd]())
