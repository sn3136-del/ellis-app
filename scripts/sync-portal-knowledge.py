"""Make every portal Ellis knows about available in the APP database.

route_bundle already moves RELEASED routes (the flow, the binding, the fee,
the dropdown cache). This moves the other half: the portal KNOWLEDGE — which
official portals exist, their verified identity, their curated entry gates and
form paths, and the honest build state of each. Without it the app shows 91
portals when the factory knows 110, and shows nothing about why a portal is
not released.

Everything copied here is portal structure or build bookkeeping. Nothing is
applicant data: the guard below re-checks every row against the same forbidden
markers route_bundle uses, and refuses the whole sync if one appears.
"""
import json
import pathlib
import re
import sqlite3
import sys

# A field NAME is structure; a VALUE is data. "passport_number" appears
# legitimately as an ellis_field in every specification's field_mappings —
# that is the whole point of a mapping — so the guard looks for value SHAPES
# and for the markers that can only be data.
FORBIDDEN = ("vault://", "session_ref")
VALUE_SHAPES = (
    re.compile(r"\b[A-Z]{1,2}\d{6,9}\b"),                  # passport number
    re.compile(r"[\w.+-]+@(?!example\.)[\w-]+\.[a-z]{2,}", re.I),  # real email
    re.compile(r"\b\d{13,19}\b"),                            # card-shaped
)

# Order matters: parents before children.
TABLES = [
    "portal_families",
    "adapter_build_requests",
    "adapter_recon_jobs",
    "adapter_recon_artifacts",
    "adapter_specifications",
    "adapter_candidates",
    "adapter_candidate_versions",
    "adapter_test_runs",
    "portal_family_adapters",
]

src = sqlite3.connect("file:ellis.db?mode=ro", uri=True, timeout=90)
src.row_factory = sqlite3.Row
app_path = pathlib.Path.home() / "Library/Application Support/Ellis/ellis.db"
dst = sqlite3.connect(str(app_path), timeout=90)


def columns(conn, table):
    return [c[1] for c in conn.execute(f"pragma table_info({table})")]


def pk(conn, table):
    for c in conn.execute(f"pragma table_info({table})"):
        if c[5]:
            return c[1]
    return "id"


report = {}
for table in TABLES:
    scols = columns(src, table)
    dcols = columns(dst, table)
    if not dcols:
        report[table] = "table missing in app db — skipped"
        continue
    shared = [c for c in scols if c in dcols]
    key = pk(src, table)
    if key not in shared:
        report[table] = f"no shared primary key ({key}) — skipped"
        continue
    have = {r[0] for r in dst.execute(f"select {key} from {table}")}
    # Surrogate ids differ between databases while the NATURAL key is the same
    # row (portal_families.family_id, portal_family_adapters.family_id). Match
    # on the natural key too, or the insert collides on its unique constraint.
    NATURAL = {"portal_families": "family_id",
               "portal_family_adapters": "family_id"}
    nat = NATURAL.get(table)
    nat_map = {}
    if nat and nat in shared:
        nat_map = {r[0]: r[1] for r in
                   dst.execute(f"select {nat}, {key} from {table}")}
    rows = list(src.execute(f"select {','.join(shared)} from {table}"))
    inserted = updated = 0
    for r in rows:
        blob = json.dumps({k: (str(r[k])[:400] if r[k] is not None else None)
                           for k in shared})
        for bad in FORBIDDEN:
            if bad in blob:
                print(f"REFUSED: {bad!r} found in {table}.{r[key]} — sync aborted")
                sys.exit(1)
        for shape in VALUE_SHAPES:
            hit = shape.search(blob)
            if hit:
                print(f"REFUSED: value-shaped {hit.group(0)[:12]!r} in "
                      f"{table}.{r[key]} — sync aborted")
                sys.exit(1)
        vals = [r[c] for c in shared]
        existing_key = r[key] if r[key] in have else (
            nat_map.get(r[nat]) if nat else None)
        if existing_key is not None:
            sets = ",".join(f"{c}=?" for c in shared if c != key)
            dst.execute(f"update {table} set {sets} where {key}=?",
                        [r[c] for c in shared if c != key] + [existing_key])
            updated += 1
        else:
            dst.execute(
                f"insert into {table} ({','.join(shared)}) "
                f"values ({','.join('?' * len(shared))})", vals)
            inserted += 1
    dst.commit()
    report[table] = f"+{inserted} new, {updated} updated ({len(rows)} total)"

print("=== portal knowledge synced into the app database ===")
for t, r in report.items():
    print(f"  {t:30} {r}")

print("\n=== app database now ===")
for t in ("portal_families", "portal_family_adapters", "adapter_test_runs",
          "adapter_recon_artifacts"):
    try:
        n = dst.execute(f"select count(*) from {t}").fetchone()[0]
        print(f"  {t:30} {n}")
    except Exception as e:  # noqa: BLE001
        print(f"  {t:30} error: {e}")
rel = dst.execute(
    "select count(*) from portal_family_adapters where released=1").fetchone()[0]
print(f"  released adapters              {rel}")
