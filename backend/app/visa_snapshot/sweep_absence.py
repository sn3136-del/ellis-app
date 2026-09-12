"""Absence accounting for the consistency sweep: report only, nothing holds.

guard-20260912 T8. For every served record the four populations of
tstation.absence_populations are counted, and one absence_undocumented
finding is filed per contract cell where the record is PUBLISHED and its
grade depends on that cell: a required cell that prints "Not publicly
available" on an asserted absence with no proof. That is exactly the cell
ELLIS_ABSENCE_STRICT would turn into a gap, so the finding list is the work
queue that has to reach zero before T14 flips the switch. A cell nobody
researched is already an honest gap (its record already grades Medium) and
is counted, not filed.

Read only: no writer, no session, no commit.
"""
from __future__ import annotations

from datetime import datetime

from .consistency_sweep import make_finding

CODE = "absence_undocumented"


def check_rows(rows: list[tuple], *, now: datetime) -> dict:
    from . import kimi_primary, tstation
    findings = []
    records = []
    published_high_would_drop = 0
    for r, proj in rows:
        canonical = kimi_primary.canonical_key(r.cache_key or "")
        for rec in proj["qc_rows"]:
            records.append(rec)
            result = tstation.absence_populations(rec)
            asserted = sorted(f for f, reason in result["undocumented_reasons"].items()
                              if reason == "asserted_absence" and f in tstation.REQUIRED_FIELDS)
            published = not rec.get("_held")
            if published and asserted and rec.get("confidence_level") == "High":
                published_high_would_drop += 1
            if not published:
                continue
            for field in asserted:
                findings.append(make_finding(
                    CODE, canonical, field,
                    observed={"label": tstation.NOT_PUBLICLY_AVAILABLE, "product_index": rec.get("_product_index"),
                              "population": "undocumented_gap"},
                    expected="unpublished_evidence on a page competent for the destination",
                    surfaces=["qc", "export"], checked_at=now.isoformat(),
                    evidence={"published": True, "grades": [rec.get("confidence_level")],
                              "visa_type_name": rec.get("visa_type_name"),
                              "product_index": rec.get("_product_index")}))
    summary = tstation.absence_summary(records)
    summary["undocumented_findings"] = len(findings)
    summary["strict_switch"]["published_high_records_that_would_grade_medium"] = published_high_would_drop
    return {"findings": findings, "summary": summary}
