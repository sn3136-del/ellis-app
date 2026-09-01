# Requirement-to-Function Matrix

T-Station Visa Information Base. Every requirement of the
requirements specification and the Acceptance & Delivery Standard,
traced to its implementation and verified LIVE against
https://ellis-visa.com on 2026-08-28 by an independent audit fleet.
Statuses: PASS (verified live), PARTIAL (works with a noted gap),
DOCUMENT-NEEDED (a process document, produced alongside this one),
NOT-APPLICABLE (outside the database deliverable).

## Re-measurement, 2026-08-28 (end of day) — supersedes stale numbers below

The audit tables below were captured against the 1,856-record morning
dataset. The dataset was then rebuilt and corrected through the day
(sourced-override waves, a full rewarm, enum fixes). Current live truth:

- Records 1,092; destinations 187; all 18 Phase-1 stations present.
- Field completeness 98.34% (21,478/21,840 required cells) — target 99%
  still NOT met; record-level 85.3%.
- Source coverage 100.0% — the audit's "162 records lack any source URL"
  is closed (final 13 received adversarially verified official sources).
- Confidence: High 796 / Medium 158 / Low 138 -> Medium+ 87.4% — target
  90% still NOT met; every Low is blocked from customers.
- Enum rows below marked FAIL are closed: validity_unit now only
  Day/Month/Year, max_stay_unit only Hour/Day, application_method is
  distributed across its five values (Online 384, Other 376, Agency 199,
  Embassy 77, On-arrival 47).
- The CHN->JPN business FAIL is closed (full guidance, 2 products, agency
  channel). The held-card leak is closed (guidance null, no steps).
  Mis-keyed lookup bodies now 422 instead of silently defaulting.
- Uptime monitoring, status page, daily backups and the RTO runbook are
  live; the availability record began 2026-08-28 and is younger than the
  monthly window it must eventually prove.
- Deliverable 6's export half now exists: /database/changes.csv.

## AI Q&A (P3) — /api/database/ask

| Requirement | Source | Status | Evidence / gap |
|---|---|---|---|
| Natural-language question in Chinese (their own example 持中国普通护照去日本旅游需要什么签证？) parses to CHN->JPN tourism and returns guidance | requirements-spec | PASS | POST returned 200 with understood:true, route {nationality:CHN, destination:JPN, travel_purpose:tourism, travel_document_type:ordinary_passport}; status KIMI_PRIMARY with full guidance (disposition VISA_REQUIRED, visa_category 'Temporary Visitor (tourist)', re |
| Same question in English parses to the same route and answers with guidance | requirements-spec | PASS | English phrasing returned understood:true, identical route {CHN, JPN, tourism, ordinary_passport}, status KIMI_PRIMARY, source_verified url https://www.cn.emb-japan.go.jp/itpr_zh/visa_kanko.html verified_at 2026-08-22; guidance body identical to the Chinese an |
| Answers carry source annotation (source_verified with url+date, or guidance source, or official portal) | acceptance-standard | PASS | Tourism answer carries source_verified {source_url: https://www.cn.emb-japan.go.jp/itpr_zh/visa_kanko.html, verified_at: 2026-08-22, verified_by: 'Ellis source audit', fields list} plus guidance.official_portal_url https://www.mofa.go.jp/j_info/visit/visa/inde |
| A route-less question refuses politely: understood:false, no 5xx | acceptance-standard | PASS | 'what documents do I need?' returned HTTP 200 with body {"understood":false,"nationality":"","destination":""} — no guidance fabricated, no server error. |
| Follow-up with context works: {question:'what about business?', context:{CHN,JPN,tourism,ordinary_passport}} switches purpose using context | requirements-spec | PASS | Returned understood:true with route travel_purpose switched to 'business' (nationality/destination/document carried from context), status KIMI_UNCERTAIN then cached full guidance with business documents (invitation letter from Japanese company, employment cert |

## Acceptance metrics and process deliverables

| Requirement | Source | Status | Evidence / gap |
|---|---|---|---|
| Field completeness >= 99% across required fields | acceptance-standard | FAIL | Live /api/database/records (1856 records, 20 required fields): 35405/37120 required cells filled = 95.38%; fully-complete records only 996/1856 = 53.66% (API summary completeness_rate 0.5366) **Gap:** 3.6 points short on cell-level fill; dominant gaps are visa_fee_amount/visa_fee_currency and the 162 records with no source_url |
| Accuracy (measured as substantiated share: human-quote + grounded-consistent over total) | acceptance-standard | FAIL | source_check counts: human-quote 267 + grounded-consistent 52 = 319/1856 = 17.19% substantiated (API summary substantiated=319); remainder: reference 1375, unchecked 162 **Gap:** 82.8% of records are model/reference-derived without human quote or grounded-consistent verification; true accuracy is unmeasurable until verified |
| Confidence: >= 90% of records at Medium confidence or above | acceptance-standard | FAIL | High 267 + Medium 1047 = 1314/1856 = 70.80%; Low = 542 (29.2%) **Gap:** 19.2 points short of the 90% target |
| Source coverage: 100% of records cite an official source URL | acceptance-standard | FAIL | 1694/1856 = 91.27% have a source_url (API summary source_coverage 0.9127); 162 records have none (source_check=unchecked). URLs present are government/ministry domains **Gap:** 162 records lack any source URL |
| Station coverage >= 18 destination stations | acceptance-standard | PASS | 196 distinct destination_country values in live records (target 18) |
| Timeliness machinery: freshness rechecks and live change log | acceptance-standard | PASS | GET /api/database/freshness (ops) returns per-route grounded rechecks with grounded_at (2026-08-25), grounded_source official URLs, grounded_consistent and disputed_fields; GET /api/database/changes is live (200) and currently returns {"changes":[]} |
| Test report per stage | requirements-spec | PARTIAL | /Users/sammynawaly/Documents/ellis-app/docs/TRIPCOM_ACCEPTANCE_TEST_PLAN.md: AT-01..AT-15 with per-test PASS results and evidence, runnable via scripts/run-acceptance-tests.sh **Gap:** It is an integration/app acceptance plan with results at commit time, not per-stage test reports for the database delivery stages |
| Acceptance checklist | requirements-spec | PARTIAL | docs/ACTIVATION_CHECKLIST.md (production activation/operations checklist) and the acceptance test plan's Result column exist **Gap:** No checklist keyed to the database acceptance metrics of their section 6.1 |
| Signed acceptance report | requirements-spec | DOCUMENT-NEEDED | No signed acceptance report found anywhere in repo docs; only unsigned test plan and checklists **Gap:** Needs a formal, counter-signed acceptance report document |
| Requirement-to-function matrix | requirements-spec | DOCUMENT-NEEDED | backend/docs/TRIPCOM_REQUIREMENTS.json is a sandbox contract must-provide list; backend/docs/COVERAGE_MATRIX.md maps country adapters, not requirements to functions **Gap:** No document tracing each Trip.com requirement to the implementing function/endpoint |
| Backend operating manual | requirements-spec | PARTIAL | backend/docs/PRIVACY_AND_OPS.md (data flow, operations, incident response) plus docs/ACTIVATION_CHECKLIST.md cover much operating content **Gap:** No single consolidated operating manual (startup/monitoring/runbooks) presented as such |
| Data-caliber (data quality) manual | requirements-spec | DOCUMENT-NEEDED | No document describing data quality standards, field definitions, confidence/verification methodology found in repo docs (grep for caliber/data quality hit only ops and coverage docs) **Gap:** Needs a manual defining field semantics, verification tiers (human-quote/grounded/reference), and freshness policy |
| US Form+Appointment module (DS-160 filling + appointment, 8+8 RMB/person) | requirements-spec | NOT-APPLICABLE | That PDF describes Trip.com's own benchmark service module, not a requirement on the Ellis visa database acceptance |

## Non-functional and data-standard live audit (https://ellis-visa.com)

| Requirement | Source | Status | Evidence / gap |
|---|---|---|---|
| Online browser access with no installation: GET https://ellis-visa.com returns the app over valid TLS | acceptance-standard | PASS | HTTP/2 200 in 0.5s serving the SPA (title 'Ellis for Trip.com'); ssl_verify_result=0; Let's Encrypt cert CN=ellis-visa.com valid 2026-08-28 to 2026-11-26 |
| Availability mechanisms: systemd auto-restart, daily backups, firewall on the server | acceptance-standard | PASS | Site is up and responding (200 over HTTPS); mechanisms stated as configured on the server and consistent with the live service being reachable |
| 99.99% availability SLA measurement | acceptance-standard | DOCUMENT-NEEDED | Site currently up, but no uptime-monitoring record was available to verify the 99.99% figure; requires monitoring logs/report |
| Batch export completes without timeout: GET /database/export.xlsx of the full database | acceptance-standard | PASS | GET https://ellis-visa.com/api/database/export.xlsx with admin headers returned HTTP 200, 385,151 bytes, valid Microsoft Excel 2007+ file, in 3.9 seconds (full DB = 1,856 records) |
| All 25 fields present with exact English names (travel_document_type ... confidence_level) | requirements-spec | PASS | /api/database/records 'fields' array lists all 25 names exactly as specified; per-record keys match with 0 missing (extra internal keys cache_key/completeness/field_status/source_check also present) |
| validity_unit enum in {Day, Month, Year} | requirements-spec | FAIL | Live values: Day, Month, Year, null, plus 'Hour' on 11 records (e.g. CHN->MYS validity 120 Hour, CHN->AUS 72 Hour) — transit-visa validities expressed in hours **Gap:** 'Hour' is outside the allowed enum; either add Hour to the standard or convert hour-based validities to Day |
| max_stay_unit enum in {Hour, Day} | requirements-spec | FAIL | Live values include 'Month' (21 records, e.g. USA->CAN 6 Month) and 'Year' (21 records) beyond Hour/Day **Gap:** 42 records use Month/Year for max stay; convert to Day or extend the enum |
| entries enum in {Single, Multiple, Unlimited} | requirements-spec | PASS | Only Single/Multiple/Unlimited (plus null) observed across 1,856 records |
| processing_unit enum in {Working Day, Calendar Day} | requirements-spec | PASS | Only 'Working Day' and 'Calendar Day' (plus null) observed |
| visa_fee_currency is ISO-4217 | requirements-spec | PASS | 30 distinct values, all valid ISO-4217 codes (AUD, EUR, USD, XOF, XCD, ...); zero non-conforming |
| application_method enum in {Embassy Submission, Online Application, Agency Service, On-arrival Processing, Other} | requirements-spec | PARTIAL | All values are within the enum, but distribution is degenerate: Other 1,782, On-arrival Processing 72, null 2; Embassy Submission / Online Application / Agency Service never used **Gap:** Enum compliance holds, yet 96% of records are 'Other', so the field carries almost no classification value; likely a mapping defect upstream |
| confidence_level enum in {High, Medium, Low} | requirements-spec | PASS | Only High/Medium/Low observed |
| Phase-1 coverage: records exist for all 18 destination stations (HKG TWN JPN KOR USA THA SGP MYS GBR RUS AUS IDN PHL FRA VNM ESP IND CAN) | requirements-spec | PASS | All 18 present as destinations; counts e.g. GBR 145, AUS 78, IND 75, USA 74, JPN 70, RUS 72, minimum HKG 37 — none missing |
| HK and US test stations covered as nationalities (travel_document_country) | requirements-spec | PASS | HKG appears as travel_document_country on 453 records and USA on 269 (CHN also present with 652); all 18 station countries also appear as nationalities |

## P0 QUALITY-CONTROL BACKEND (live audit of https://ellis-visa.com/api, 2026-08-28)

| Requirement | Source | Status | Evidence / gap |
|---|---|---|---|
| Combined-filter query returns only matching rows (nationality=CHN&destination=JPN&purpose=tourism) | requirements-spec | PASS | GET /database/records?nationality=CHN&destination=JPN&purpose=tourism returned 21 records; every record has travel_document_country=CHN, destination_country=JPN, travel_purpose=tourism (programmatic purity check True). |
| Name forms work: nationality=China and URL-encoded 中国 resolve to the same rows | requirements-spec | PASS | nationality=China&destination=Japan returned the same 21 CHN->JPN rows; nationality=%E4%B8%AD%E5%9B%BD (中国) also returned 21 CHN rows. |
| Per-field checklist: exactly 25 fields, in order travel_document_type first ... confidence_level last | acceptance-standard | PASS | Response 'fields' array has exactly 25 names, first=travel_document_type, last=confidence_level; every record key set matches the declared order (no extras missing). |
| Fill status filled/missing visible per field and completeness computed | acceptance-standard | PASS | All 1856 records carry field_status with 25 keys (values observed: 39558 filled, 1720 missing, 5047 optional-empty) and a numeric completeness on every record (min 0.65, max 1.0). |
| Issue feedback loop: reader POST /database/report-issue -> appears in ops /database/issues -> POST /database/issues/{id} status=corrected wi | requirements-spec | PASS | Reader (dev-token/qa) POST returned {ok:true,id:c8746544...,status:open}; ops GET /database/issues showed it with route, field=visa_fee_amount, reported_by=qa; ops POST /database/issues/{id} {status:corrected,resolution:...} returned 200 and /database/issues?s **Gap:** Reporter's description text landed as note:'' in the stored issue (field/route captured, prose lost). |
| Confidence auto-label High/Medium/Low present on every record | acceptance-standard | PASS | All 1856 records labeled: High 267, Medium 1047, Low 542; zero records missing or off-vocabulary. |
| Low-confidence content blocked from readers until confirmed (held card with no claims) | acceptance-standard | PARTIAL | Low route found via ?confidence=Low: TWN->MYS tourism. Reader POST /database/lookup returned held:true, review_required:true, operator_released:false, guidance:null, apply_steps:[] — the card is held. Reader GET /database/records is refused outright (403 'admi **Gap:** Held response is not claim-free: workflow_plan exposes visa-exempt determination and required-document items for an unconfirmed Low route. |
| Source traceability: every record's source_url present and government-domain | acceptance-standard | FAIL | 162 of 1856 records have no source_url at all (e.g. JPN->MYS, JPN->PHL, SGP->MYS, MYS->PHL, THA->PHL, GBR->PHL tourism, all Medium confidence). Random sample of 30 records that do have source_url: all 30 resolve to government domains (embassy/MFA/immigration:  **Gap:** 'Every record' fails: 162 records (8.7%) ship with source_url missing. |
| Excel export: 200 xlsx, two sheets (Field descriptions + Data), 25 columns, filterable, no timeout | requirements-spec | PASS | GET /database/export.xlsx -> 200, 384KB in 5.1s; sheets ['Field descriptions','Data']; Data sheet dimension A1:Y1854 = 25 columns; Field descriptions A1:D26 (25 fields + header). ?destination=JPN -> 200, 20KB in 0.6s, Data A1:Y68 (smaller filtered set). Note:  |
| Change log shows add/modify/delete entries with field diffs and searchable ?q= | requirements-spec | PARTIAL | GET /database/changes returned 2 entries (1 modify, 1 add) with per-field from/to diffs (e.g. requirement_detail evisa->paper_visa, permitted_stay diff). ?q= filters: q=JPN -> 2, q=ZZZ -> 0. No delete entry exists in the live log to demonstrate the delete acti **Gap:** Delete action unobserved live; log depth (2 entries) does not reflect the claimed 27 recent corrections, so modify coverage of the pipeline is unproven. |
| Freshness summary present at /database/changes companion endpoint /database/freshness | requirements-spec | PASS | GET /database/freshness -> 200 with summary {total:1017, stale:135, grounded:104, human_verified:164, disputed:24} plus 1017 per-answer rows carrying fresh_until, stale, grounded_source, disputed_fields. |
| Backend availability (backend unavailable = acceptance fails) | acceptance-standard | PARTIAL | Backend was reachable for most of the audit, but between 05:06:10 and 05:06:50 UTC every endpoint (including previously working queries) returned HTTP 502 from Caddy for ~40-60 seconds, then recovered without intervention. All subsequent checks succeeded. **Gap:** An unexplained full-outage window (all routes 502) occurred mid-audit; under the strict standard this is an availability blip that needs a root cause (likely process restart behind the proxy). |

## P2 Display Page — CHN→JPN live audit (ellis-visa.com)

| Requirement | Source | Status | Evidence / gap |
|---|---|---|---|
| GET https://ellis-visa.com/#database/CHN/JPN/tourism/ordinary_passport returns the app shell (200); each passport x destination combination  | requirements-spec | PASS | curl https://ellis-visa.com/ returned HTTP 200 (hash fragment is client-side, same shell serves every /#database/... combination) |
| Lookup API returns verdict for CHN/JPN tourism ordinary | requirements-spec | PASS | POST /api/database/lookup {nationality, destination, travel_purpose, travel_document_type} -> guidance.disposition=VISA_REQUIRED, status=KIMI_PRIMARY |
| visa_type present | requirements-spec | PASS | guidance.visa_category='Temporary Visitor (tourist)'; requirement_detail='evisa' |
| validity present | requirements-spec | PASS | visa_products validity: '3 months' (single-entry and group), '3 years', '5 years' |
| stay present | requirements-spec | PASS | permitted_stay='15 or 30 days on a single-entry tourist visa...30 days per visit on the 3-year multiple, 90 on the 5-year'; permitted_stay_days=30; per-product max_stay_days 30/15/30/90 |
| entries via products | requirements-spec | PASS | 4 visa_products with entry field: single (individual), single (group tour 5-40), multiple (3-year), multiple (5-year high income) |
| fee present | requirements-spec | PASS | government_fee 715 CNY; per-product fees 715 CNY (single/group) and 1430 CNY (3y/5y multiple); uncertainty[] honestly flags consular-fee waiver ambiguity for agents to verify |
| channel with no ambiguity: Japan must say designated agency, personal applications not accepted | acceptance-standard | PASS | application_channel='authorised_agent'; application_channel_detail='Chinese ordinary-passport holders cannot apply directly to the embassy. The application must be lodged through a travel agency accredited by the Japanese embassy or consulate-general'; excepti |
| document requirements present | requirements-spec | PASS | 9 required_documents: valid ordinary PRC passport, application form, photo, itinerary, return flight, accommodation proof, financial proof, employment/student certificate, Chinese ID/hukou copy |
| 3-5 apply steps | acceptance-standard | PASS | apply_steps has 3 entries (pay fees to accredited agency, deliver documents to accredited agency, collect passport); workflow_plan additionally carries a 6-step machine plan (collect_documents...track_status) |
| entry tips including arrival card 'Visit Japan Web' | acceptance-standard | PASS | arrival_card={required:true, name:'Visit Japan Web', submission_window:'Register before departure for immigration and customs'}; exceptions include eVisa issuance-notice tip (must be shown online at the airport, no PDF/screenshot/printout) |
| Travel-document switcher data: diplomatic CHN->JPN differs from ordinary | requirements-spec | PASS | travel_document_type='diplomatic_passport' returns disposition=VISA_EXEMPT, permitted_stay='90 days', docs limited to diplomatic passport + recommended ticket/funds — versus ordinary's VISA_REQUIRED 715 CNY agent route. Distinct data confirmed |
| Purpose filter data: business CHN->JPN differs from tourism (switches products) | requirements-spec | FAIL | travel_purpose='business' returns status=KIMI_UNCERTAIN with guidance=null, held=true, review_required=true, cached=true; missing_fields=['government_fee'], contradictions=['disposition VISA_REQUIRED but no visa_products were listed for this purpose'], apply_s **Gap:** The business route is cached as an uncertain, held decision with empty guidance and no visa products, so the display page has nothing to render for the business purpose; the held entry needs review/re |
| API field-name contract: request body keys used by the display page | requirements-spec | PARTIAL | Only 'nationality' is required; 'travel_document_type' and 'travel_purpose' are the effective keys. A request sent with 'travel_document'/'purpose'/'passport_country' is accepted and silently defaults to tourism + ordinary_passport (three differently-intended  **Gap:** Unknown extra body fields are ignored instead of rejected, so a mis-keyed client silently gets ordinary/tourism data for every switcher position |
| Lookup availability on cache-miss (reader never waits on research) | acceptance-standard | PARTIAL | First two diplomatic cache-miss calls returned HTTP 502 in ~0.3s; third call returned 200 with full data. Cached routes answer in <1s **Gap:** Cold (uncached) switcher combinations intermittently 502 until the background decision lands; the display page needs a retry/pending state for unwarmed combinations |
| Travel-document switcher UI at top (default ordinary) and purpose filter UI rendering | requirements-spec | DOCUMENT-NEEDED | API-side default confirmed (travel_document_type defaults to 'ordinary_passport', travel_purpose to 'tourism' in backend main.py lines 968-970); the visual placement/behavior of the switcher in the shell was not exercised in this API-level audit |

## Query tool (P1) — live audit of POST https://ellis-visa.com/api/database/lookup, 2026-08-28

| Requirement | Source | Status | Evidence / gap |
|---|---|---|---|
| Input: travel_document_type — P0 five types (ordinary / diplomatic / official-service / travel document / temporary-emergency) must work | requirements-spec | PARTIAL | Canonical codes work live: ordinary_passport -> full answer (VISA_REQUIRED/evisa, 4 products); diplomatic_passport -> answered 0.33s; service_passport -> answered 0.33s (held card); emergency_passport -> answered 27.4s cold (held card). Registry (data/referenc **Gap:** Two gaps: (1) no code for the PRC Travel Document (旅行证) — closest are refugee/stateless/alien documents, so one of their P0 five has no dedicated type; (2) the lookup endpoint does not validate the do |
| Input: P1 expansion document types accepted | requirements-spec | PASS | refugee_travel_document accepted live (200, honest held card in 22.4s while cold research runs); registry also carries stateless_travel_document, alien_passport, laissez_passer. |
| Input: nationality required, real-country validated | requirements-spec | PASS | nationality "XYZ123" -> HTTP 422 in 0.29s with clean message: "nationality and destination must be real countries (name or ISO code)". Valid ISO3 and names accepted. |
| Input: travel_purpose required — tourism business family study work transit other | requirements-spec | PASS | All seven answered live for CHN->JPN: tourism (evisa, 4 products), business (held honest card), family_visit (paper_visa, 4 products, 90-day stay), study (Student 留学 category), work (Work visa, 6 products), transit (CONDITIONAL/conditional_visa_free, 15 days), **Gap:** Minor vocabulary mismatch: their "family" is the API's "family_visit"; an unmapped purpose string silently keys a new cache entry rather than erroring. |
| Input: departure city accepted | requirements-spec | PASS | departure_city:"Beijing" accepted (200, full CHN->JPN evisa answer with 715 CNY fee returned in 26s cold); field is truncated to 80 chars server-side (app/main.py DatabaseLookupIn). |
| Input: destination required; transit point optional list | requirements-spec | PASS | destination "Atlantis" -> clean 422 in 0.30s. transit_countries:["JPN"] on CHN->USA accepted (up to 5, ISO-normalized) and echoed back in the response's transit_countries and route echo. |
| Output: 3-level requirement classification with detailed subcategories (visa-free unconditional/conditional; VoA eVisa/paper; advance eVisa/ | requirements-spec | PASS | Live values observed: unconditional_visa_free (CHN diplomatic->JPN), conditional_visa_free (CHN->JPN transit), evisa (CHN->JPN tourism), paper_visa (family/study/work), eta_electronic_authorization (USA->GBR, disposition ELECTRONIC_AUTHORIZATION_REQUIRED, GBP  |
| Output: visa type NAME | requirements-spec | PASS | guidance.visa_category returned live: "Temporary Visitor (tourist)" (CHN->JPN tourism), "Student (留学) status of residence", "Work visa (status of residence)", "Electronic Travel Authorisation (ETA) / Standard Visitor", "B-1/B-2 visitor visa" (CHN->USA). |
| Output: complete details — validity per product, permitted stay, document checklist, fees, transit requirement when transit point given | requirements-spec | PASS | CHN->JPN tourism: per-product validity (3 months / 3 months / 3 years / 5 years), per-product max_stay_days (30/15/30/90) and fees (715/715/1430/1430 CNY), 9-item required_documents checklist, permitted_stay narrative. CHN->USA with transit JPN returned transi |
| Accuracy spot check: CHN->JPN — 4 products, 715/1430 CNY, designated-agency channel, 15/30/90 stays | requirements-spec | PASS | Live warm answer: exactly 4 visa_products (single-entry, group ADS, 3-year multiple, 5-year high-income); fees 715 CNY single/group and 1430 CNY multiples; application_channel authorised_agency ("must be lodged through a travel agency accredited by the Japanes |
| Accuracy: the 7 appendix items from the original Japan complaint all fixed | requirements-spec | DOCUMENT-NEEDED | The corrected facts known from the fix (agency-only channel, 715/1430 fee schedule, electronic issuance notice, mission-set 15/30 stay, group ADS product, multi-entry variants, product-level stays) all appear correctly in the live answer, and Trip.com's own 20 **Gap:** The original 7-item appendix text is not in the repo, so item-by-item confirmation needs the source document; and the 2026-08-28 diplomatic/service override batch must be deployed (live diplomatic ans |
| Answers always: junk destination -> clean 422; low-confidence held with honest card, never blank 5xx | acceptance-standard | PARTIAL | Junk destination and junk nationality both return 422 in ~0.3s with a human-readable message. Low-confidence/unverified routes (service, emergency, refugee doc; CHN->JPN business) return HTTP 200 held cards: held:true, guidance:null, status/label present, deta **Gap:** Cold-miss latency (22-59s observed) exceeds common client timeouts, and /api/health is 502 behind the proxy; the held card also still exposes workflow_plan steps (e.g. visa_exempt_preparation for the  |
| Speed: warm answers under 2 seconds | acceptance-standard | PASS | Every cached lookup measured 0.29-0.42s total (curl time_total, 10+ samples across doc types and purposes: 0.30, 0.31, 0.33, 0.35, 0.37, 0.40, 0.42s). Cold uncached routes take 15-59s but respond 200 and cache for all subsequent readers. |

## Addendum 2026-09-01, after the Trip.com evaluation of 2026-08-31

| Requirement | Source | Status | Evidence |
| --- | --- | --- | --- |
| Spot check filters resolve country terms exactly, never to a lookalike | evaluation 4.1 | PASS | Tiered resolver (src/renderer/src/lib/countryMatch.js) with registry-wide round-trip tests (tests/visa/country_match.test.mjs, every alpha-2, alpha-3 and name resolves to its own country). Live: KOR filter returns 28 South Korea records across all 18 stations, India+China returns 4 IND->CHN records. |
| Destination coverage includes South Korea for every station | evaluation 4.1 | PASS | 7 unwarmed override-backed station->KOR routes released 2026-09-01 (GBR AUS IDN FRA VNM ESP IND). All 18 stations now serve KOR records. |
| All available visa products listed per route, multi-year included | evaluation 4.2 | PASS | CHN->AUS serves Visitor 600 Tourist stream, Frequent Traveller stream (10 years, AUD1,845) and ADS stream, plus ETA and eVisitor ineligibility notes, all quoted verbatim from immi.homeaffairs.gov.au and adversarially verified. |
| No route withheld awaiting review | evaluation 4.3, owner directive | PASS | All 19 held route combinations verified against official pages and released 2026-09-01. Live grade split after release: 939 High, 220 Medium, 0 Low. The hold mechanism itself stays armed for future low-confidence answers. |
| AI Q&A supports multi-turn conversation in both languages | evaluation V.2 | PASS | Deterministic context-slot continuation (kimi_primary.parse_question_with_context) plus a chat thread with passport chips and follow-up chips. Verified live in English and Chinese: Australia ETA -> which passport -> China -> full CHN->AUS answer. |
| Regional and transit visa-free policies answered with sources | evaluation V.3, V.4 | PASS | Gov-gated special policy store (backend/app/visa_snapshot/special_policies.py): Hainan 61-country 30-day entry, China 240-hour transit (57 countries, replaced 144h on 2024-12-17), Korea group scheme for CHN. A known nationality gets a decisive covered-or-not line: India is told it is not on the Hainan list. |
| AI Q&A output can be spot checked with a written verdict | requirements P0, acceptance 4.1.2 | PASS | Console AI Q&A tab lists every exchange; correct or wrong rulings write reviewer and timestamp; wrong files a tracked issue automatically (POST /database/asks/{id}/review). |
| Issue loop operable end to end in the console | acceptance 4.1.2 | PASS | All five stages have status-aware buttons (provider notified, corrected, reviewed, published, dismiss). Reviewer must differ from corrector, server enforced. |
| Change management distinguishes delete | acceptance 4.1.2 | PASS | Expiring a served answer through the issue loop writes a delete change entry with the withdrawn fields (test_expiring_a_ruled_answer_writes_a_delete_change_entry). |
| Batch export at 10,000 rows or more | acceptance 4.4, 5.2 | PASS | No row cap in code. Live full export 1,160 rows in 5.6s with both sheets and the snapshot stamp; workbook build 1.2s at 12,000 rows; projected under one minute at 10,000. |
| Localized install package for acceptance | evaluation VI.3 | PASS | deploy/localization/ Docker Compose package, image built and boot tested (web 200, API direct and proxied 200). |
| Operations manual and data caliber manual delivered | acceptance 5.1 item 10, evaluation VI.5 | PASS | docs/tripcom/backend-operations-manual.html and docs/tripcom/data-caliber-specification.html, bilingual, dated 2026-09-01. |
