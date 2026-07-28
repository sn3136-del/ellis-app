"""Read the official portal's dropdown lists once, so applicants get the
form's exact choices before any run.

  python warm_options.py                 # every released route
  python warm_options.py --route VNM     # routes whose key contains VNM

Read-only against the real portal: walks its entry gate, opens each dropdown,
scrolls it to the end, closes it. Nothing is typed or submitted.
"""
from __future__ import annotations

import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.adapter_factory import models as fm
from app.portal.option_warmup import warm_option_lists


def main() -> int:
    match = ""
    if "--route" in sys.argv:
        match = sys.argv[sys.argv.index("--route") + 1].upper()
    db = SessionLocal()
    try:
        bindings = db.execute(select(fm.AdapterRuntimeBinding)).scalars().all()
        if not bindings:
            print("no released routes bound — nothing to warm")
            return 0
        for b in bindings:
            if match and match not in (b.route_key or "").upper():
                continue
            print(f"\n=== {b.route_key}  (candidate {b.candidate_id[:8]} v{b.candidate_version})")
            try:
                rep = warm_option_lists(
                    db, candidate_id=b.candidate_id,
                    candidate_version=int(b.candidate_version),
                    tier=b.tier or "sandbox",
                    progress=lambda m: print(f"  · {m}"))
            except Exception as e:  # noqa: BLE001 — report, continue
                print(f"  ! could not warm: {e}")
                continue
            for r in rep["read"]:
                mark = "full list" if r["complete"] else "PARTIAL — offered as suggestions"
                print(f"  ✓ {r['key']}: {r['count']} options ({mark})")
            for s in rep["skipped"]:
                print(f"  – {s['key']}: {s['reason']}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
