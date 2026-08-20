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

# The starter set: the nationalities and destinations a Trip.com tester
# reaches for first. Everything else warms itself on first lookup.
WARM_ROUTES = [("CHN", d) for d in (
    "JPN", "USA", "THA", "KOR", "SGP", "MYS", "VNM", "GBR", "FRA", "DEU",
    "ITA", "ESP", "AUS", "ARE", "TUR", "IDN")]


def _route(nat: str, dest: str) -> dict:
    return {"passport_nationality": nat, "passport_issuing_country": nat,
            "lawful_country_of_residence": nat,
            "travel_document_type": "ordinary_passport",
            "destination_country": dest, "visa_category": "tourist_visa",
            "travel_purpose": "tourism"}


def warm() -> int:
    from app.db import SessionLocal
    from app.visa_snapshot import kimi_primary
    db = SessionLocal()
    ok = fail = 0
    for nat, dest in WARM_ROUTES:
        route = _route(nat, dest)
        try:
            out = kimi_primary.get_route_guidance(db, route)
            disp = (out.get("guidance") or {}).get("disposition", "?")
            print(f"  {nat}->{dest}: {out.get('status')} {disp}"
                  f"{' (cached)' if out.get('cached') else ''}", flush=True)
            ok += 1
        except Exception as e:  # noqa: BLE001 — warmth is best-effort
            print(f"  {nat}->{dest}: FAILED {str(e)[:80]}", flush=True)
            fail += 1
    db.close()
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
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "warm"
    raise SystemExit({"warm": warm, "export": export,
                      "import": import_seed}[cmd]())
