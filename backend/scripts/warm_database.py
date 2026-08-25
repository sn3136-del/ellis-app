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
ORIGINS = ("CHN", "HKG", "USA", "TWN", "JPN", "KOR", "SGP", "MYS", "THA",
           "GBR", "FRA", "DEU", "AUS", "CAN", "IND", "IDN", "PHL", "VNM",
           "ESP", "RUS")
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
    """Write the shipped seed.

    Only rows for the CURRENT cache version are exported. A row written under
    an older answer schema can never be served again (the version is part of
    the key), so shipping it would be dead weight in the clone Trip.com
    downloads and would misrepresent how much is actually warm."""
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.visa_snapshot.kimi_primary import CACHE_VERSION
    from app.visa_snapshot.models import KimiRouteGuidanceCache as C
    db = SessionLocal()
    rows, skipped = [], 0
    for r in db.execute(select(C)).scalars():
        if f"|{CACHE_VERSION}" not in (r.cache_key or ""):
            skipped += 1
            continue
        # An operator's release is a statement that a person ON THIS INSTALL
        # checked this answer. Shipping it in the repo seed would un-hold
        # low-confidence answers on machines where no one did.
        ver = dict(r.verification or {})
        ver.pop("operator_released", None)
        rows.append({
            "cache_key": r.cache_key, "route": r.route, "status": r.status,
            "guidance": r.guidance, "missing_fields": r.missing_fields,
            "contradictions": r.contradictions, "model": r.model,
            "verification": ver,
            "generated_at": r.generated_at.isoformat() if r.generated_at else None,
            "fresh_until": r.fresh_until.isoformat() if r.fresh_until else None,
        })
    db.close()
    SEED.parent.mkdir(parents=True, exist_ok=True)
    SEED.write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    print(f"exported {len(rows)} cached decisions ({CACHE_VERSION}) -> {SEED}")
    if skipped:
        print(f"skipped {skipped} rows from older answer schemas")
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


def recheck() -> int:
    """Check EVERY cached answer against its own official government page.

    This is the "is all of it still correct?" sweep: run it on a schedule
    (cron) or by hand before a hand-off. Each row's page is fetched, the
    stored answer compared against the page text, drift corrected (or filed
    for a person when it collides with a human-verified fact), and the
    freshness window extended only for what was actually checked."""
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.visa_snapshot import freshness
    from app.visa_snapshot.models import KimiRouteGuidanceCache as C
    from app.visa_snapshot.kimi_primary import CACHE_VERSION
    db = SessionLocal()
    # Current-version rows only: rows written under an older answer schema can
    # never be served (the version is part of the key), so checking their
    # pages is pure waste. They are deleted here rather than skipped forever.
    keys, dead = [], 0
    for r in db.execute(select(C).order_by(C.fresh_until)).scalars():
        if f"|{CACHE_VERSION}" in r.cache_key:
            keys.append(r.cache_key)
        else:
            db.delete(r); dead += 1
    db.commit()
    db.close()
    if dead:
        print(f"purged {dead} unreachable rows from older answer schemas", flush=True)
    print(f"rechecking {len(keys)} current answers", flush=True)

    def _one(key):
        s = SessionLocal()
        try:
            row = s.execute(select(C).where(C.cache_key == key)).scalars().first()
            if row is None:
                return key, {"outcome": "gone"}
            return key, freshness.recheck_row(s, row)
        except Exception as e:  # noqa: BLE001 — one route must not sink the sweep
            return key, {"outcome": "error", "err": str(e)[:80]}
        finally:
            s.close()

    counts = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for key, r in pool.map(_one, keys):
            out = r["outcome"]
            counts[out] = counts.get(out, 0) + 1
            extra = ""
            if out == "checked":
                extra = (" CONSISTENT" if r.get("consistent") else
                         f" changed={r.get('changed')} disputed={r.get('disputed')}"
                         f" generic_skipped={r.get('generic_skipped')}")
            print(f"  {key[:44]:<46} {out}{extra}", flush=True)
    print("recheck summary:", counts)
    return 0


def checkurls() -> int:
    """Check every link every stored answer carries, and strip the dead ones.
    Unique URLs are checked once, concurrently; a dead link is removed from
    every answer that carried it."""
    from concurrent.futures import ThreadPoolExecutor
    from urllib.parse import urlparse
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.visa_snapshot import url_health
    from app.visa_snapshot.authority import is_government_host
    from app.visa_snapshot.models import KimiRouteGuidanceCache as C
    db = SessionLocal()
    rows = list(db.execute(select(C)).scalars())
    urls = sorted({u for r in rows for u in url_health.collect_urls(r.guidance or {})})
    # Official first: a link off a government domain is dead to us whatever
    # its server says.
    unofficial = {u for u in urls if not is_government_host(urlparse(u).hostname or "")}
    for u in sorted(unofficial):
        print(f"  NOT OFFICIAL {u}", flush=True)
    urls = [u for u in urls if u not in unofficial]
    print(f"checking {len(urls)} unique links across {len(rows)} answers", flush=True)
    with ThreadPoolExecutor(max_workers=12) as pool:
        dead = {u for u, d in zip(urls, pool.map(url_health.url_is_dead, urls)) if d}
    dead |= unofficial
    for u in sorted(dead):
        print(f"  DEAD {u}", flush=True)
    stripped = 0
    for r in rows:
        g = dict(r.guidance or {})
        if url_health.strip_dead_links(g, dead=dead):
            r.guidance = g
            stripped += 1
    db.commit(); db.close()
    print(f"{len(dead)} dead links removed from {stripped} answers")
    return 0


def revalidate() -> int:
    """Re-run deterministic validation over every stored answer (no model
    call): after a validation rule changes, rows marked uncertain by a rule
    that was wrong become complete again, with the full freshness window."""
    from datetime import timedelta
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.visa_snapshot import kimi_primary as k
    from app.visa_snapshot.models import KimiRouteGuidanceCache as C
    db = SessionLocal()
    flipped = 0
    for r in db.execute(select(C)).scalars():
        clean, missing, contra = k.validate_answer(dict(r.guidance or {}))
        status = k.STATUS_PRIMARY if clean.get("disposition") and not missing and not contra \
            else k.STATUS_UNCERTAIN
        if status != r.status or missing != (r.missing_fields or []) or contra != (r.contradictions or []):
            if status == k.STATUS_PRIMARY and r.status != k.STATUS_PRIMARY:
                r.fresh_until = (r.generated_at or k._now()) + timedelta(days=k.TTL_DAYS)
                flipped += 1
            r.guidance, r.status, r.missing_fields, r.contradictions = clean, status, missing, contra
    db.commit(); db.close()
    print(f"revalidated; {flipped} rows became complete")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "warm"
    raise SystemExit({"warm": warm, "export": export, "import": import_seed,
                      "recheck": recheck, "revalidate": revalidate,
                      "checkurls": checkurls}[cmd]())
