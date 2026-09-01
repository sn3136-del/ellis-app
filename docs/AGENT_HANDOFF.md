# Ellis: complete handoff for the next coding agent

Written 2026-08-30, at the end of a long build and audit session. Read this whole file
before touching anything. It is the context that is not derivable from the code.

---

## 1. What Ellis is

Ellis is a visa information database built for Trip.com (they call it the T-Station /
T站 information base). It answers "do I need a visa, which one, how much, how long"
for a passport x destination x purpose x travel-document combination, backed by
official government sources only.

- Production: **https://ellis-visa.com** (Hetzner box `49.13.220.169`, Caddy in front,
  FastAPI backend as systemd unit `ellis-backend`, SQLite at `/var/lib/ellis/ellis.db`)
- Repo: this one. Branch `h1b-edition` is the working branch. The repo is PUBLIC by
  owner decision, including a tracked `backend/.env` whose leaked keys were all rotated
  dead on 2026-08-09. Never stage a live secret into tracked files.
- The customer-facing app is a React SPA (`src/renderer/`), hash-routed:
  `#database/...` is the customer surface, `#ops` is the quality console.

**The four deliverables** (their priority order): P0 quality-control backend (the ops
console; the standard makes it the precondition for acceptance), P1 query tool,
P2 display page, P3 AI Q&A.

## 2. The governing documents

Three PDFs from Trip.com govern everything. The owner has copies in `~/Downloads`:

1. `T站信息库建设 — 需求文档（中英文对照版） (2).pdf` (requirements, bilingual)
2. `T站信息库建设 — 需求文档；T-Site VISA Information Repository Construction — Requirements Specification.pdf`
   (the 25-field data dictionary; field calibers)
3. `T站签证信息库建设项目验收交付标准...Acceptance & Delivery Standard (3).pdf`
   (§1-§7: stages, defect grading, quality rules, metrics, deliverables)

Key facts from them you must not re-derive wrongly:

- A **station (站点)** is an ORIGIN market (a passport), not a destination. Phase 1 =
  18 stations: HKG TWN JPN KOR USA THA SGP MYS GBR RUS AUS IDN PHL FRA VNM ESP IND CAN.
  The two TEST stations are Hong Kong and the US, defined as "that passport to the
  whole world". Only those two are at depth (~140 destinations); the other 16 average
  ~14. Closing that is Stage 4 and the largest remaining body of work.
- The dictionary defines **25 fields**; exactly **20 are marked required** (必须要有)
  and 5 are provide-if-available (如有则提供): `processing_min_days`, `processing_unit`,
  `consulate_district`, `entry_requirements`, `special_conditions`. Our
  `REQUIRED_FIELDS` in `backend/app/visa_snapshot/tstation.py` matches exactly.
- Field 5 (`visa_requirement`) is an enum of exactly THREE values (免签 / 落地签 /
  需提前办理) with eight 细化子类 subcategories nested under them, ETA电子授权 under
  需提前办理. We currently ship a fourth primary, `Conditional`, on ~146 records.
  Deliberate but non-conformant; open question for Trip.com (see §8).
- §4.2.1: official sources only; **AI inference/speculation to fill data is
  prohibited**; where no single official source exists, cross-validate several and
  **bind each source to its own URL**.
- §6.1 metrics and the §6.2 rule: failure of ANY single quantitative metric fails the
  acceptance. §6.1 defines completeness as filled/required CELLS; §4.2.2 defines it as
  records with all fields filled / records. The two are ~48 points apart on the same
  data. This contradiction is unresolved and only Trip.com can settle it.

## 3. Owner's standing rules (violating these has caused rework)

- Never mention Claude/AI-agent names anywhere customer facing.
- No em dashes, no semicolons anywhere on the website, in any language. Minimal
  hyphens. Clear conversational English; short sentences; short paragraphs.
- Never `Other` as an application method. Never a bare `missing` shown to a reader:
  every blank states its reason (`Not publicly available` / `Does not apply to this
  route`). Never "not yet verified" as a label; go verify instead.
- Validity: either the date, "determined by the embassy", or "not publicly
  available", always source backed. Same spirit for processing time.
- Every verified fact must cite a government-domain URL. This is ENFORCED IN CODE
  (see §4); do not weaken the gate, extend `GOV_SUFFIXES` only for genuinely
  official non-gov TLDs after opening the site yourself.
- The admin token (`admin-token`) ships in the public JS bundle **by owner decision**
  so Trip.com testers get the full console with zero setup. Do not "fix" this, and
  do not raise it again; it is waived.
- The owner dislikes full test-suite runs mid-conversation (8 min); run targeted
  `-k` selections; run the full suite only before large deploys.

## 4. Architecture and the load-bearing invariants

### Answer pipeline (read this before editing anything in `visa_snapshot/`)

```
route dict -> kimi_primary.get_route_guidance()
  -> cache (KimiRouteGuidanceCache, key = cache_key(route))
  -> on miss: live Kimi call (model kimi-k2.7-code-highspeed via MOONSHOT_API_KEY)
  -> _result(): builds out{} incl. DERIVED fields apply_steps, workflow_plan
  -> apply_verified_overrides(): human-verified facts win; re-derives apply_steps
     and workflow_plan from the merged guidance (DO NOT remove this re-derivation)
  -> apply_portal_fallback()
tstation.records_for_route(): guidance -> one 25-field record per visa product
main.py /database/records | /database/lookup | export.xlsx serve those records
```

**Invariant 1: the verdict governs everything derived from it.** The deepest bug class
found in audit: an answer is a verified verdict layered over an unverified model
answer, and cleanups that only run on a hand-maintained field list let contradictions
survive (a visa-free route asking for a "payment card"; "no travel authorisation"
next to "requires an approved ETIAS"; a five-step How-to-apply under "No visa
needed"). The fixes live in THREE places and all must stay:
- `verified_overrides._drop_application_leftovers` (+ `_documents_without_an_application`,
  `_exceptions_without_a_required_authorisation`)
- `tstation._strip_visa_only_fields` calls the same reconciliation on the path EVERY
  record takes (engine-only visa-free routes included)
- `kimi_primary.apply_verified_overrides` re-derives `apply_steps`/`workflow_plan`
- the frontend (`TravelDatabase.jsx`) must NOT rebuild apply steps from raw guidance
  when `application_channel` is in `NO_APPLICATION_CHANNELS`
A shipped test checks OUTPUT for self-contradiction: `test_no_record_contradicts_its_own_verdict`.

**Invariant 2: the cache key.** `kimi_primary.cache_key()` =
`NAT|RESIDENCE|DEST|purpose|<jurisdiction>|<policy-month>|v6[+|via:...][+|doc:...]`.
- Stopovers and non-ordinary documents append; plain routes keep their key so the
  warm cache stays valid. NEVER key on free text (departure city would mint an entry
  per spelling). The departure city resolves to a consular DISTRICT via
  `consular_districts.resolve()` which fills the jurisdiction slot; destinations with
  no district table resolve to `default` = today's behavior. District TABLES are not
  yet populated (`data/reference/consular_jurisdictions.json` has `entries: []`);
  a research workflow for Japan-in-China missions died on a script bug (`m is not
  defined` in the verify stage) and is resumable:
  `Workflow scriptPath .../japan-china-consular-districts-wf_a52bd715-7e0.js`,
  `resumeFromRunId: wf_a52bd715-7e0` (research results are cached).

**Invariant 3: overrides.** `data/database_seed/verified_overrides.json` (~1,439
entries) is the single store of human-verified facts.
- Loader: `verified_overrides.py`. Route key = nat|dest|purpose[|doc], the LAST entry
  wins, so **merge into an existing entry, never append a duplicate** (a shipped test
  enforces this, and `ordinary_passport` collides with the doc-less key on purpose).
- `OVERRIDABLE` whitelists fields; anything else is silently dropped (this silently
  ate `consulate_district` data once; it now maps via `consular_jurisdiction`).
  `source_url` and `corroborating_sources` ARE overridable, each URL individually
  gated by `authority.is_government_host`.
- `_REVIEWER_VOICE` guard: text that reads like the verifier arguing with a claim
  ("CORRECTION TO THE SUBMITTED CLAIM", "I am correcting it") is dropped from
  customer-text fields at load; a shipped test scans the whole seed file.
- Headline fee must equal some product price or the consistency test fails.
- Every entry: `route`, `verified_at`, `verified_by`, gov `source_url`, `note`
  (say what was checked and why), `fields`.

**Invariant 4: the three kinds of blank.** `field_status()` returns
filled / not-published (verified absent, via `unpublished_fields`) / not-applicable
(visa-free routes) / missing (unresearched) / optional-empty / pending-review.
Completeness (the "fillable" reading) = (filled+pending) / (filled+pending+missing).
The console COMPLETE tile prints the fraction and the exclusions; the literal §6.1
number lives in the requirement ledger.

**Invariant 5: subcategory nests under its primary.** `_NESTED_UNDER` +
`_subcategory_for` + `_nested_detail` in `tstation.py`. The product NAME chooses the
kind, the VERDICT restricts the choice set, and the product-less path is constrained
too. 74 contradictions ("Visa-free / Paper Visa" beside "No visa needed") came from
name-only derivation. Shipped test: `test_every_subcategory_nests_under_its_primary`.

### Other components

- **Freshness monitor** (`freshness.py`): re-reads official pages; agreement
  restamps, disagreement with a HUMAN override files a `DatabaseIssueReport` with a
  full `proposal` (page_says / record_holds / quote) rather than auto-overwriting.
  Nationality-specific fields cannot be touched by a generic page.
- **Issue loop**: open -> acknowledged -> corrected -> reviewed -> published (+
  dismissed). API (`main.py /database/issues/{id}`) enforces order, no skips, reviewer
  must differ from resolver. Publishing EXPIRES the route's cache row (so readers
  never see an answer an operator just ruled on) - publishing many at once therefore
  shrinks the served dataset until routes re-warm; that is by design, re-warm gently.
  **The console only has buttons for corrected/dismissed; reviewed/published/
  acknowledged exist only via API. Open item.**
- **Change log** (`change_log.py`): 28 watched fields, add/modify recorded with
  field diffs. **`delete` is never written by any caller** (the cache-expiry path
  writes no entry). Open item.
- **Q&A log**: `DatabaseAskLog`, `GET /database/asks`. Read path only; **no write
  path for a reviewer's verdict**, no console tab. Open item.
- **Acceptance archive**: `python3 scripts/generate_acceptance_archive.py`
  regenerates 41 §5.3 documents from live measurement. Verdict columns/signatures
  intentionally blank (joint artifacts).
- **Availability probe**: cron on the server, 1/min, `/var/lib/ellis/uptime/*.csv`,
  served at `/api/health/uptime`. Watchdog restarts the backend on failure.

## 5. Operations runbook

- SSH: `ssh -i ~/.ssh/ellis_hetzner root@49.13.220.169`. App at `/opt/ellis`,
  venv `/opt/ellis/backend/.venv`, service `ellis-backend`
  (`EnvironmentFile=/opt/ellis/backend/.env`).
- **Frontend build**: `npx vite build --config vite.web.config.mjs` -> outputs to
  `src/renderer/dist` (NOT `npm run build`). `dist/` is gitignored. Deploy:
  `rsync -az --delete -e "ssh -i ~/.ssh/ellis_hetzner" src/renderer/dist/ root@49.13.220.169:/opt/ellis/src/renderer/dist/`
- **Backend deploy**: rsync changed files under `backend/app/...` and
  `data/database_seed/verified_overrides.json` to the same paths under `/opt/ellis/`,
  then `systemctl restart ellis-backend && sleep 8 && systemctl is-active ellis-backend`.
  Overrides hot-reload on file mtime (no restart needed for the JSON alone).
- **API keys are server-side only** (`MOONSHOT_API_KEY` etc. in the server .env).
  Testers need nothing. `ELLIS_ALLOW_LIVE` does NOT exist in app code.
- Tests: `cd backend && .venv/bin/python -m pytest -q tests/` (~2,633 pass, ~8 min;
  `test_live_providers` is flaky-network). Prefer targeted `-k`.
- Backups: `/var/backups/ellis/` (daily db + incident copies).

### Hard-won operational rules (each cost real damage today)

1. **Availability budget**: ≥99.99%/month = 4.3 min downtime. Every deploy restart
   and every heavy script counts. THREE outages today were self-inflicted (deploy
   restarts + a re-warm loop that held a SQLite session across model calls and 502'd
   the site). Batch deploys; in long scripts open the DB session only around the
   write and sleep between routes; `nice -n 15`; never run a fan-out of live
   lookups against production.
2. **Probe pollution**: any `/database/lookup` for a route not in the curated set
   MINTS A RECORD. Audits and tests against prod have polluted the dataset four
   times. Purge by exact `cache_key` list, never by route-diff (a route-diff purge
   deleted 380 rows when 67 were probes). Always back up rows to
   `/var/backups/ellis/` before deleting.
3. **Restores must carry all columns**. A restore that wrote only cache_key/route/
   guidance silently blanked `info_validity` (from `fresh_until`), reset
   `generated_at` (which feeds `collected_at`, making data claim it was fresher
   than it was), and dropped `verification` (confidence fell). The model has 9+
   columns; copy them all.
4. **Status codes lie about links**. Albania's ministry serves its 404 with
   HTTP 200 behind Imperva; gov.uk/mofa.go.jp/boca.gov.tw/travel.state.gov 403
   every fetcher and work fine for humans. Judge links by rendered BODY in a real
   browser; treat 403-with-browser-render as healthy; never swap a canonical
   source for a lesser page just because curl fails.
5. **Repeat-bug discipline** (memory doctrine): a repeated symptom means the last
   fix was scoped to one path. Enumerate every emitter, fix the shared path, prove
   it live. (Example: verdict reconciliation had to move from the override branch
   to the universal path, then ALSO be killed in the frontend fallback.)

## 6. Current state (measured 2026-08-30, live)

~1,130 records, 187 destinations. Completeness (fillable) ~99.1%; literal §6.1
~91.4%; record-level §4.2.2 ~44%. Source coverage 100% (gov-gated). Confidence
Medium+ ~98.6%, ~16 held (each held = `source_check: reference`, page never read
for that record). 0 misnested subcategories, 0 self-contradictions, 0 stale ETIAS
text, 29 records bind a second official source. Availability this month ~99.90%
(3 failed probes, all ours). 0 issues past the 48h window; ~37 monitor disputes
awaiting human ruling (that is the loop working, not a backlog).

**Acceptance stance**: does NOT pass §6.2 today. (a) availability needs a clean
calendar month, no code can fix it, stop deploying against the measured box;
(b) completeness passes only on our denominator, fails literal, needs Trip.com's
ruling; (c) accuracy has never been measured by either party (§4.2.2 assigns the
sampling to Party A). Requirement-by-requirement ledger (63 rows, kept current):
`docs/tripcom/requirement-ledger.html`, published at
https://claude.ai/code/artifact/6a9f532d-1c44-495b-a450-75e3e72e2911

**Honest accuracy guess** (not a measurement): ~95-97% of cells overall; 98-99% on
the high-traffic routes (CHN->JPN/THA/USA/SGP/KOR, HKG->*, USA->*, Schengen set)
which were repeatedly adversarially verified. Residual risk is in prose fields and
the long tail, not headline verdicts/fees.

## 7. What was fixed today (so you do not re-litigate or regress it)

- Blank `/ops` (TDZ: useMemo above its state decl). Deep links now follow
  `hashchange` (was mount-only) with a live-handler ref; a request sequence guard
  stops a slower lookup overwriting a newer one (real race, seen in prod).
- Held pages keep the document/purpose switchers (shared `switchers` element) and
  the hold message names the PURPOSE, not just the country pair.
- Phone header: three equal pills, globe icon restored, short labels under 360px.
- "Where to apply" tile relabels to "Official source" when
  `application_channel` ∈ NO_APPLICATION_CHANNELS.
- ETIAS: not in operation, no launch date committed (EU site says notice will be
  given), fee EUR 20 when it starts. 212 entries corrected; never re-assert a date.
- Thailand: one fee schedule (1000/5000 THB) across document types; TDAC wording is
  Thai Immigration's own sentence ("3 days in advance of arrival"); CHN diplomatic/
  official = 30 days per the bilateral PDF (China is in the 30-day column);
  child_passport = 60 like ordinary.
- CHN->JPN tourism: 4 products (group 15d / individual single 30d incl. "embassy
  decides 15 or 30" / 3yr multiple 30d / 5yr multiple 90d), 715/1430 CNY consular
  fee with agency fee disclosed separately, agency-only channel. This is THE demo
  appendix route; guard it.
- Demo defect #2 closed: 17 verified convenience policies (Japan lead-applicant +
  family rules, three asset-free routes, gold-card shortcut; UK "no lead applicant,
  every traveller needs their own ETA"; US "invitation letter not used"). In `exceptions`
  via overrides, rendered under Good to know.
- USA->SYR corrected to VISA_ON_ARRIVAL (post-transition policy) with the
  do-not-travel advisory noted; CHN->USA family_visit stay corrected to blank
  (CBP decides at entry; no published stay).
- Cross-validation binding: `corroborating_sources` alongside the 25 fields
  (export shape untouched), per-entry gov gate, console renders each with quote.
- 3 genuinely dead links replaced (Albania stale slug, Benin truncated UUID,
  old Vietnam evisa host); the other ~80 "blocked" hosts are fine in browsers.

## 8. Open work, in priority order

1. **Station depth** (Stage 4, the big one): 16 stations from ~14 to ~140
   destinations each. Use the existing pattern: research workflow with adversarial
   verify -> overrides -> release. Never count provisional data as coverage.
2. **Trip.com decisions needed** (do not guess; the ledger §11 lists them):
   completeness denominator; `Conditional` as a 4th value vs folding into 需提前办理
   (data migration ready to write either way); `info_validity` semantics (policy
   expiry vs our warrant date, currently the latter, declared in the export);
   station coverage = present vs at-depth; who runs accuracy sampling.
3. **Required-input gating**: make `travel_document_type` and `travel_purpose` 422
   like nationality/destination (spec marks them 必填), and enforce departure
   server-side. BREAKS callers relying on defaults; tell Trip.com first.
4. **Consular district tables**: resume the Japan-missions workflow (see §4
   invariant 2), then Beijing/Shanghai/... rows make the departure city actually
   select a mission. Plumbing is live; only data is missing.
5. **Console gaps**: buttons for acknowledged/reviewed/published; a Q&A tab with a
   verdict write path (`DatabaseAskLog` has the columns); change-log `delete`
   entries when a cache row is expired by an issue.
6. **16 held records**: read each cited page, release or correct (the workflow
   `release-held-routes-wf_798e2954-b5e.js` is resumable; 6 verify agents died on a
   weekly rate limit, resets Sep 2).
7. **Long-tail sweep**: ~50 destinations never source-verified.
8. **Availability**: nothing to build. Deploy in batches, off-peak, and let a
   clean month accumulate.

## 9. Testing and verification norms this project holds itself to

- Verify in a real browser (the Browser/devtools, screenshots, network reads), not
  by asserting from code. Several "fixed" claims died on a browser check.
- Adversarial verification for data: every researched value gets an independent
  refutation pass before it ships; "not published by any government" is a valid,
  recordable outcome and better than a guess.
- When you fix a bug, write the shipped test that would have caught the CLASS, not
  the instance (see the self-contradiction, nesting, reviewer-voice, duplicate-
  override, headline-fee tests in `backend/tests/test_kimi_primary.py`).
- Report numbers only after measuring them live; state the denominator every time.
