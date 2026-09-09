# Data Caliber Manual — T-Station Visa Information Base

Current implementation rules, revised 2026-09-09. This document describes the
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

High requires explicit human verdict verification, complete required fields,
and no unresolved dispute or contradiction. AI verification cannot confer High.
Medium requires recorded verdict evidence or an effective automated verdict
check and may have field gaps. A URL alone cannot establish Medium, including
for a productless exemption. Low includes missing verdict evidence, invalid or
absent provenance dates or notes, nonofficial-only evidence, and unresolved
disputes or contradictions.

A fresh automated check can support Medium when historical human provenance
is incomplete; it cannot retrospectively establish human verification.
At grading time, imported manual notes are checked for substantive text,
official source and valid nonfuture date. The grader does not reinterpret
every multilingual note's semantic relevance. The operator's factual judgment
remains auditable through source and per-field provenance.

Source labels describe evidence, while completeness and link coverage are
separate measurements. Each record's held and review_required flags describe
the entire canonical route, including its other products.

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
