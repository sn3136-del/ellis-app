# Visa appointment wait times — human-placed snapshots

Read by `backend/app/appt_availability.py`. Override this directory with
`$ELLIS_VISA_WAIT_TIMES_DIR`.

## Why this is manual

Researched 2026-08-11: the U.S. Department of State publishes visa appointment
wait times only as a human-readable interactive tool (Global Visa Wait Times)
and a narrative Quarterly Report on Visa Wait Times. There is no documented
JSON, CSV, or RSS feed, and `travel.state.gov` refuses non-browser requests
(HTTP 403). Rather than scrape a site that is declining to be read, Ellis reads
a snapshot a person placed here. With nothing placed, availability is reported
as explicitly unavailable — never a guessed date.

**A wait time is not availability.** Real appointment availability is visible
only to a signed-in human on the official scheduling site. Ellis never searches
for, polls for, holds, or books a slot: automated slot search gets the
TRAVELER's appointment cancelled and their visa revoked (roughly 2,000
cancellations in India in 2025). `usvisascheduling.com` is additionally behind
Cloudflare bot protection and must never be requested.

## File naming

`<YYYY-MM-DD>.json` — the date the figures were published. The newest file wins.
A snapshot older than 45 days is served with `stale: true`.

## Shape

```json
{
  "as_of": "<YYYY-MM-DD>",
  "source_url": "https://travel.state.gov/content/travel/en/us-visas/visa-information-resources/global-visa-wait-times.html",
  "collected_by": "<who transcribed it>",
  "posts": [
    {
      "post": "<post name>",
      "post_code": "<3-letter post code>",
      "country": "<country>",
      "visitor": "<whole days>",
      "student_exchange": "<whole days>",
      "petition_worker": "<whole days>",
      "crew_transit": "<whole days>"
    }
  ]
}
```

A long form is also accepted: one row per post + category, with
`{"post": ..., "category": ..., "wait_days": ...}`. The Department of State's
own category names (`"Visitor Visa (B1/B2)"`, `"Petition-Based Temporary
Workers (H, L, O, P, Q)"`, …) are normalized automatically.

## Rules the ingester enforces

- `source_url` must be a Department of State host. A snapshot attributed to a
  scheduling system is **refused outright** — that would mean someone read a
  protected calendar.
- `wait_days` is a whole number of days. An unpublished figure (`"N/A"`, empty)
  is omitted with a warning; it is never zero-filled.
- An undated snapshot is refused: a wait time that cannot be judged fresh or
  stale is not usable.

`appt_availability.ingest_plan()` returns this same guidance at runtime, so an
unavailable payload tells the operator exactly what to place.
