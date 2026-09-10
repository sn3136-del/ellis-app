# Data Caliber Manual — T-Station Visa Information Base

Current implementation rules, revised 2026-09-10. This document describes the
verification and serving contract. It does not certify that every stored field
has been verified or that every official page is readable.

## Route identity and record projection

One canonical decision covers each nationality, destination, travel purpose and
travel document. Residence, itinerary and travel dates do not create another
model verdict. Residence still matters for explicitly scoped consular fees and
procedure. Evidenced effective-date rules determine dated policy changes.

The mapper in backend/app/visa_snapshot/tstation.py projects the agreed field
dictionary, with one record per visa product. Missing facts remain missing.
Calendar months and years preserve exact wording in max_stay_text and exported
special_conditions; they are not multiplied by 30 or 365. Hour-based validity
converts to days only for exact multiples of 24. Otherwise numeric fields remain
null and exact text is retained. Processing estimates are not guarantees.

Dictionary field 24, `info_validity`, is only the policy's explicitly recorded
published end date. If no end date is known it stays null; an internal cache
deadline must never fill this field. Product-specific policy dates take
precedence, and a separate permission cannot inherit another permission's
expiry. `freshness_valid_until` is separate metadata for the internal recheck
deadline. Neither that deadline nor a successful refresh extends the policy.
Reviewed disposition provenance can also supply its `effective_to` policy date,
using the bound's own official-jurisdiction source, verification date and exact
quoted date. An inherited `policy_interval_evidence` notice retains its original
evidence; bare interval dates or unrelated field dates do not fill this column.

The user-approved completion target counts documented Not applicable and Not
published states as complete across all 25 fields. Unknown, unsupported,
optional-empty and pending-review cells remain gaps; policy values must not be
invented to raise the score. `acceptance_summary` separately exposes documented
completed cells/records and disposition counts. These measure recorded states,
not an independent verification that every recorded disposition is correct.

Both literal non-null diagnostics remain: filled cells / all 25 cells, and
records with all 25 non-null / all records. They exclude no blanks. The separate
operational applicable 20 metric uses the configured 20 required fields and
excludes not-applicable/source-confirmed not-published cells from its denominator.

On the 2026-09-10 02:29 UTC snapshot, documented 25 completeness is 29,093 /
35,500 = 81.9521%, including 3,225 disposition cells; 0 of 1,420 records are
complete across all 25. Operational applicable 20 completeness is 87.32%; literal
non-null25 cells are 72.9718%. None is a claim of 100% completion or accuracy.
The documented-state result was recomputed with the follow-up metric code from
the recorded snapshot, not inferred from the older response's summary.
The export splits item 5 into requirement and subcategory, producing 26 columns;
the subcategory does not become a 26th contractual field. The offline acceptance
auditor validates the exact dictionary and the API's declared complete row count.

An ETA, an advance visa and a visa on arrival are different permission types.
An electronic visa is still a visa. Alternate products keep their own fees,
validity, documents and procedure. ETA procedure cannot become an alternate
visa's facts. Electronic and paper visa procedures are isolated when different.

## Source verification and confidence

The records expose source_check separately from confidence:

| Source check | Meaning |
| --- | --- |
| human-quote | Explicit human verdict provenance, official source, substantive note and actual verification date. |
| ai-quote | Recorded verdict evidence attributed to an AI check. Missing historical verifier identity defaults to AI. |
| grounded-consistent | A successful current evidence-contract check was consistent and explicitly verified disposition. |
| reference | A source link exists, but required verdict evidence is absent. |
| unchecked | No source link is available. |

The configured display has two grades, High and Low. Complete official-source
checked records with supported required values and no unresolved dispute can
be High. Accepted AI and human source checks use the same grade; authorship
remains explicit in provenance. Missing evidence, field gaps, public edits,
invalid provenance and disputes remain Low. A model's self-rating or an
official URL alone is never verification. There is no current Medium output.
Trip.com's written standard describes a three-level ladder; the configured
binary display must not be described as an unchanged implementation of that
ladder or evidence of contractual acceptance.

A fresh automated check can support a grade when historical human provenance
is incomplete; it cannot retrospectively establish human verification.
At grading time, imported manual notes are checked for substantive text,
official source and valid nonfuture date. The grader does not reinterpret
every multilingual note's semantic relevance. The operator's factual judgment
remains auditable through source and per-field provenance.

Source labels describe evidence, while completeness and link coverage are
separate measurements. Each record's held and review_required flags describe
the entire canonical route, including its other products. Display grade and
publication are separate: a completeness-only Low does not itself create a
new hold. The existing source/conflict gate and explicit route hold metadata
govern publication; the binary relabeling did not change those gates.

## Evidence contract and corrections

An automated verdict check counts only under the current evidence contract
(version 2), after a successful route-supported read with disposition in
verified_fields. Legacy consistent flags and ancillary-only checks cannot
verify a verdict.

Fetched evidence must match nationality, purpose, document and effective
period. Corrections require a quote present in the source and support for the
exact field value. A number elsewhere on a page, another nationality's table
row or an ETA eligibility page alone is insufficient proof. Ambiguous tables
and unsupported claims remain for review. Shared authority rules check official
domains and jurisdiction; commercial pages are not official verification.

Overrides validate vocabulary and shapes. Fees/products need a verified
verdict or detail. Invalid historical machinery is quarantined with diagnostics
while independently valid ancillary facts can survive. Per-field provenance
retains human or AI authorship. A narrow correction does not verify unrelated
fields or sibling products.

## Freshness and unresolved fields

The systemd timer starts a cycle every six hours. Four workers check canonical
routes independently, with a 75-second route budget and a five-hour cycle
budget. The 5,000-row selection cap exceeds the current inventory; work that
cannot be attempted before the deadline stays visible in the backlog. A
15-minute duplicate-attempt window prevents immediate repeats without skipping
routes checked late in the previous six-hour cycle. Source rotation covers
supporting fee and detail pages as well as the headline source.

Scheduled and on-access checks attempt to read official sources. A failed read
is a failed attempt, not new verification, and can preserve an earlier effective
check. A later readable but irrelevant page cannot perpetuate an unsupported
source claim.

Whole-row freshness extension requires support for all populated substantive
fields and no outstanding disputes, unsupported proposals or unread required
sources. Verifying the verdict alone cannot renew fees, stays and products.
Operator reporting distinguishes attempts, successful reads, field coverage and
freshness. A completed sweep does not establish that every field is accurate.
The console reports the actual timer and durable worker status, including
unfinished attempts, partial checks, unreadable sources and errors. A missing
timer or dead worker cannot be displayed as a confirmed upcoming/completed run.

The monthly reporter and hardened offline auditor recompute all 25 metrics from
the full records response, including public pending/unpublished metadata, rather
than trusting a supplied summary. The monthly report retains operational metrics
alongside literal 25 and documented 25 counts. Keep the timestamped snapshot and
hash for reproducibility. Scheduled attempts, successful reads and recorded
completion dispositions are distinct from verified policy accuracy.

## Historical documentation

The current bilingual HTML/PDF manuals and `requirement-ledger.html` replace the
September 3 warranty-date and three-tier descriptions. They follow the current
Markdown/manual and export dictionary. Earlier acceptance archives remain dated
historical evidence; their PASS labels or signatures cannot be inferred as
acceptance of this release.

## Reader and saved-workflow gates

Lookup, questions, comparisons, intake and dataset grading share the verdict
evidence rule. Held responses retain only identity and safe status metadata;
guidance, workflow steps, provenance notes and other claim-bearing containers
are removed. Held and comparison replies use deterministic prose so old
conversation history cannot restore withdrawn claims. Independently verified
special-policy notices retain their own scope.

Saved routed cases use the current canonical cached answer when available,
apply the same gate and refresh their verdict, continuation kind and checklist.
Uploaded documents and submission bindings are preserved. A new hold blocks
document-stage completion, application start, service transitions and appointment
packets. Visa-on-arrival cases can complete preparation but cannot start an
advance visa filing. Cases outside the route-guidance workflow retain their
separate rules.

An operator release cannot override a material contradiction, pending detail
stage or active source dispute. Low-confidence display follows the configured
hold policy, which production must keep enabled. Corrections and saved-case
requirement updates are recorded for review.
