"""Route-record import CLI (brief section 11).

  python -m app.routes_import validate data/global_visa_routes.jsonl
  python -m app.routes_import dry-run  data/global_visa_routes.jsonl
  python -m app.routes_import apply    data/global_visa_routes.jsonl
  python -m app.routes_import history  <route_key>
  python -m app.routes_import rollback <route_key> <version>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _session():
    from .db import SessionLocal, create_all
    create_all()
    return SessionLocal()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.routes_import")
    ap.add_argument("command", choices=["validate", "dry-run", "apply", "history", "rollback"])
    ap.add_argument("target", help="JSONL path (validate/dry-run/apply) or route_key")
    ap.add_argument("version", nargs="?", type=int, help="version (rollback only)")
    args = ap.parse_args(argv)

    from .visa_snapshot import importer

    if args.command in ("validate", "dry-run", "apply"):
        path = Path(args.target)
        if not path.exists():
            print(json.dumps({"error": f"file not found: {path}"}))
            return 2
        if args.command == "validate":
            rep = importer.validate_file(path)
            print(json.dumps(rep.as_dict(), indent=2, sort_keys=True))
            return 0 if rep.invalid == 0 else 1
        db = _session()
        try:
            rep = importer.import_file(db, path, apply=(args.command == "apply"))
            print(json.dumps(rep.as_dict(), indent=2, sort_keys=True))
            return 0 if rep.invalid == 0 else 1
        finally:
            db.close()

    db = _session()
    try:
        if args.command == "history":
            print(json.dumps(importer.history(db, args.target), indent=2, sort_keys=True))
            return 0
        if args.version is None:
            print(json.dumps({"error": "rollback requires <route_key> <version>"}))
            return 2
        print(json.dumps(importer.rollback(db, args.target, args.version),
                         indent=2, sort_keys=True))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
