"""Global route coverage CLI (brief Part 5 commands).

  python -m app.global_routes build-all [--max-live-builds N]
  python -m app.global_routes build-destination DEST
  python -m app.global_routes build-family FAMILY_ID
  python -m app.global_routes retry-failed
  python -m app.global_routes revalidate
  python -m app.global_routes verify-live [--limit N] [--families a,b,c]
  python -m app.global_routes coverage
  python -m app.global_routes unsupported [--limit N]
  python -m app.global_routes export
  python -m app.global_routes stop
  python -m app.global_routes resume

Operator tooling. Every command is resumable and duplicate-proof; stop is
safe at any point (tasks are idempotent and checkpointed).
"""
from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import select


def _session():
    from ..db import SessionLocal, create_all
    create_all()
    return SessionLocal()


def _log(msg: str):
    print(f"  {msg}", file=sys.stderr, flush=True)


def cmd_build_all(args) -> dict:
    from . import inventory, orchestrator
    db = _session()
    try:
        run = orchestrator.latest_resumable_run(db, "build-all")
        if run is None:
            run = orchestrator.start_run(db, "build-all",
                                         {"max_live_builds": args.max_live_builds})
        elif run.status == "stopped":
            # resume the stopped run's checkpoint instead of abandoning it
            run.status = "running"
            run.stopped_reason = ""
            db.commit()
        inv = orchestrator.run_inventory(db, run, log=_log)
        adapters = orchestrator.run_adapter_phase(
            db, run, max_live_builds=args.max_live_builds, log=_log)
        stale = orchestrator.mark_stale_families(db)
        exported = inventory.export_inventory(db)
        run.stats = {"inventory": {k: (v if isinstance(v, (int, str)) else "done")
                                   for k, v in inv.items()},
                     "adapters": adapters, "stale": stale, "export": exported}
        if "stopped_at" not in inv and run.status == "running":
            run.status = "complete"
        db.commit()
        return {"run": run.id, "status": run.status, "inventory": inv,
                "adapters": adapters, "export": exported}
    finally:
        db.close()


def cmd_build_destination(args) -> dict:
    from . import orchestrator
    db = _session()
    try:
        run = orchestrator.start_run(db, "build-destination", {"dest": args.dest})
        out = orchestrator.run_adapter_phase(db, run, only_destination=args.dest,
                                             log=_log)
        run.status = "complete"
        run.stats = out
        db.commit()
        return {"run": run.id, **out}
    finally:
        db.close()


def cmd_build_family(args) -> dict:
    from . import orchestrator
    db = _session()
    try:
        run = orchestrator.start_run(db, "build-family", {"family": args.family})
        out = orchestrator.run_adapter_phase(db, run, only_family=args.family,
                                             log=_log)
        run.status = "complete"
        run.stats = out
        db.commit()
        return {"run": run.id, **out}
    finally:
        db.close()


def cmd_retry_failed(args) -> dict:
    from .models import GlobalBuildTask
    from . import orchestrator
    db = _session()
    try:
        run = orchestrator.start_run(db, "retry-failed", {})
        requeued = 0
        for t in db.execute(select(GlobalBuildTask).where(
                GlobalBuildTask.status == "failed")).scalars().all():
            if t.error_class == "transient" and t.attempts < t.max_attempts:
                t.status = "queued"
                t.run_id = run.id
                requeued += 1
        db.commit()
        out = orchestrator.run_adapter_phase(db, run, log=_log)
        run.status = "complete"
        run.stats = dict(out, requeued=requeued)
        db.commit()
        return {"run": run.id, "requeued_transient": requeued, **out}
    finally:
        db.close()


def cmd_revalidate(args) -> dict:
    from . import orchestrator, verify_live
    from .models import PortalFamily
    db = _session()
    try:
        run = orchestrator.start_run(db, "revalidate", {})
        marked = orchestrator.mark_stale_families(db)
        queued = orchestrator.queue_revalidation(db, run)
        stale_ids = [f.family_id for f in db.execute(
            select(PortalFamily).where(PortalFamily.stale.is_(True))).scalars()]
        results = verify_live.verify_families_live(db, stale_ids, log=_log) \
            if stale_ids else {"verified": 0, "failed": 0, "results": []}
        run.status = "complete"
        run.stats = {**marked, **queued, **{k: results[k] for k in ("verified", "failed")}}
        db.commit()
        return {"run": run.id, **run.stats}
    finally:
        db.close()


def cmd_verify_live(args) -> dict:
    from . import verify_live
    from .models import PortalFamily
    db = _session()
    try:
        if args.families:
            ids = [f.strip() for f in args.families.split(",") if f.strip()]
        else:
            ids = [f.family_id for f in db.execute(
                select(PortalFamily).where(
                    PortalFamily.active.is_(True),
                    PortalFamily.verification_status.in_(
                        ("verified_official_domain", "seed_unverified")))
                .order_by(PortalFamily.family_id)).scalars()][:args.limit]
        return verify_live.verify_families_live(db, ids, log=_log)
    finally:
        db.close()


def cmd_coverage(args) -> dict:
    from . import dashboard
    db = _session()
    try:
        return dashboard.coverage(db)
    finally:
        db.close()


def cmd_unsupported(args) -> dict:
    from . import dashboard
    db = _session()
    try:
        return dashboard.unsupported(db, limit=args.limit)
    finally:
        db.close()


def cmd_export(args) -> dict:
    from . import inventory
    db = _session()
    try:
        return inventory.export_inventory(db)
    finally:
        db.close()


def cmd_stop(args) -> dict:
    from .models import GlobalBuildRun
    from . import orchestrator
    db = _session()
    try:
        stopped = []
        for run in db.execute(select(GlobalBuildRun).where(
                GlobalBuildRun.status == "running")).scalars().all():
            orchestrator.stop_run(db, run.id, "operator stop command")
            stopped.append(run.id)
        return {"stopped_runs": stopped}
    finally:
        db.close()


def cmd_resume(args) -> dict:
    from . import orchestrator
    db = _session()
    try:
        run = orchestrator.latest_resumable_run(db, "build-all")
        if run is None:
            return {"resumed": False, "reason": "no stopped/running build-all run"}
        run.status = "running"
        run.stopped_reason = ""
        db.commit()
        inv = orchestrator.run_inventory(db, run, log=_log)
        out = orchestrator.run_adapter_phase(db, run, log=_log)
        if run.status == "running":
            run.status = "complete"
        db.commit()
        return {"resumed": True, "run": run.id, "inventory_steps": list(inv),
                "adapters": out}
    finally:
        db.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.global_routes")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("build-all")
    p.add_argument("--max-live-builds", type=int, default=None)
    p = sub.add_parser("build-destination")
    p.add_argument("dest")
    p = sub.add_parser("build-family")
    p.add_argument("family")
    sub.add_parser("retry-failed")
    sub.add_parser("revalidate")
    p = sub.add_parser("verify-live")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--families", default="")
    sub.add_parser("coverage")
    p = sub.add_parser("unsupported")
    p.add_argument("--limit", type=int, default=200)
    sub.add_parser("export")
    sub.add_parser("stop")
    sub.add_parser("resume")
    args = ap.parse_args(argv)
    fn = {
        "build-all": cmd_build_all, "build-destination": cmd_build_destination,
        "build-family": cmd_build_family, "retry-failed": cmd_retry_failed,
        "revalidate": cmd_revalidate, "verify-live": cmd_verify_live,
        "coverage": cmd_coverage, "unsupported": cmd_unsupported,
        "export": cmd_export, "stop": cmd_stop, "resume": cmd_resume,
    }[args.cmd]
    print(json.dumps(fn(args), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
