"""JSON Schema validation for snapshot JSONL files."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

from .registry import SCHEMA_DIR

SCHEMAS = {
    "route": "global_visa_route.schema.json",
    "policy": "global_visa_policy.schema.json",
    "fee": "global_fee_schedule.schema.json",
    "portal": "global_portal.schema.json",
    "consular_jurisdiction": "global_consular_jurisdiction.schema.json",
    "source_evidence": "global_source_evidence.schema.json",
    "coverage_report": "global_coverage_report.schema.json",
}


@lru_cache(maxsize=None)
def get_validator(kind: str) -> Draft202012Validator:
    fname = SCHEMAS.get(kind)
    if not fname:
        raise ValueError(f"unknown schema kind: {kind}")
    schema = json.loads((SCHEMA_DIR / fname).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_record(kind: str, record: dict) -> list[str]:
    """Return a list of human-readable validation errors (empty = valid)."""
    v = get_validator(kind)
    errors = []
    for err in sorted(v.iter_errors(record), key=lambda e: list(e.absolute_path)):
        loc = "/".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"{loc}: {err.message}")
    return errors


def validate_jsonl(kind: str, path: Path, *, max_errors: int = 50) -> dict:
    """Validate a JSONL file line by line. Returns a summary dict (no PII)."""
    total = 0
    bad: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                bad.append({"line": lineno, "errors": [f"invalid json: {e.msg}"]})
                continue
            errs = validate_record(kind, rec)
            if errs:
                bad.append({"line": lineno, "errors": errs[:10]})
            if len(bad) >= max_errors:
                break
    return {"kind": kind, "file": str(path), "records": total, "invalid": len(bad), "errors": bad, "valid": not bad}
