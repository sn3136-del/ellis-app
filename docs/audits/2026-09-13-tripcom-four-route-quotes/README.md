# Four Trip.com routes — deployed quotation and systemic-fix audit

**Status: deployed and live QC/API validation passed on 2026-09-13.** This report covers ordinary-passport tourism for Hong Kong → Vietnam, Indonesia → South Korea, Malaysia → Russia, and Thailand → Australia. It does not certify the entire Ellis database or future policy accuracy.

The shared fixes preserve nationality, visa-product, purpose, and application-scope boundaries when applying official facts. They include eligibility and product guards; external-source invalidation of the canonical QC cache; proof ownership tied to the current field value and product; recognition of the exact Jakarta embassy Google Sites pages linked by the embassy’s official MOFA notice; and residence-scope preservation through override loading and subsequent edits. The final residence fix is commit `779a881a3de06a77b0ae7fabda54c2b9b0bfbe50`. Missing residence no longer qualifies for Indonesian-residence filing instructions.

The final private four-route simulation produced **76 quote-bearing QC field cells across five product rows**: **68 populated factual cells plus 8 qualified-text quotation slots whose numeric value is intentionally blank (including 3 documented “Not applicable” cells within the 68 populated cells)**. These are displayed field cells, not 76 distinct quotations or sources; repeated unit/alias cells can use the same excerpt.

| Route | Reviewed product scope | Quote-bearing cells |
|---|---|---:|
| Hong Kong → Vietnam | Single-entry and multiple-entry eVisas; USD 25 / USD 50; granted dates and applicable entry conditions | 34 |
| Indonesia → South Korea | C-3-9 tourist visa; Jakarta filing scope for Indonesian residents; 3-month validity, 30-day stay, USD 40 national basic fee with local/service-fee qualifications | 15 |
| Malaysia → Russia | Unified eVisa, 120-day validity, single entry, 30-day stay; no unconditional visa-free classification | 15 |
| Thailand → Australia | Visitor subclass 600, Tourist stream, apply outside Australia; no ineligible ETA 601 assignment | 12 |
| **Total** | **Four routes / five products** | **76** |

All five live product rows remain Medium with `evidence_low=false`. No numeric values were invented to fill gaps, and no blanket grade upgrade was applied. Russia’s unresolved government-fee amount remains unresolved rather than being replaced with an agency-inclusive total or zero. Indonesia’s conflicting published processing/collection wording does not justify an invented numeric duration. Qualified conditions, geographical exceptions, and document requirements retain their scope; a partial composite is not represented as fully supported merely because one component has a quote.

For Indonesia → South Korea, real private operator-write and actual loader/application tests confirmed the default Indonesian-residence answer retains its 15 quoted fields. For US or unknown residence, the 11 local fields and their reviewed proof credit are excluded. The historical generic fallback remains unverified; this repair does not certify those other-residence products or their legacy durations.

Validation evidence:

- 107 targeted tests and 138 focused scope/override/quote tests passed. The final full backend suite passed **8,330 tests, with 7 skipped and no failures**, pinned to `779a881` in [backend-test-pin-y.json](backend-tests.json).
- The read-only code impact comparison covered **948 canonical routes with zero served changes and zero grade changes**. Database and operator-file hashes were unchanged: [code-impact-summary-y.json](code-impact.json). This is the code-only comparison; the four reviewed data packets intentionally change their own fields/proofs.
- The final combined private data simulation is [combined-private-dry-run4.log](/Users/sammynawaly/Documents/Codex/2026-09-09/files-mentioned-by-the-user-handoff20260909/work/resume20260912/trip-four-quotes/combined-private-dry-run4.log); exact candidate inputs are [four-route-packet-v4.json](reviewed-packet.json). It validates the intended projection before installation, not live deployment success.

Final deployment record:

- Runtime `runtime20260913x` installed the exact embassy-page authority and composite quote display changes at 21:51:43 UTC. Runtime `runtime20260913y` installed commit `779a881` scope preservation at 2026-09-13T22:09:03.957268+00:00. The API health check passed; only the API restarted, and no freshness service/timer/cron setting was changed.
- Four-route data committed at 2026-09-13T22:10:27.103105+00:00 with four auditable change-log entries. The reviewed manifest file SHA-256 is `fbc9878cdad7c5d06f72afba98f48c4e77aef058c0dd640fc44f0a2100c76f49`; the semantic packet hash is `8b46477a2b384c0b401d4f47553e67b23c0052c44ae838b8402f02e05267a9aa`. The installer preserved raw factual content, verification records and issue state; no grade or publication override was applied. An operator backup was saved before the transaction.
- Ten live read-only GET requests passed at 2026-09-13T22:10:42.153372+00:00: four record reads, five product-evidence reads and an additional Hong Kong → Vietnam read from a separate HTTP session. All five products are published, none is Low/held, field values and quote/source ownership match the reviewed packet, and Hong Kong results match across sessions. This check did not trigger paid research or a new source refresh.
- The existing freshness run/status remained unchanged, its timer remains active/enabled, and its next scheduled run remains 2026-09-14 00:26:27 UTC. This is scheduler preservation, not a claim that all historical records have been verified.

Final browser check and QC filter repair:

- The initial browser run exposed that a field wrapper defined inside the QC render remounted the country inputs during parent updates. Commit `bfa982d` moves that wrapper to a stable component, preserving typed text and the country auto-commit timer. Two regressions fail under the old component-identity behavior and pass with the repair. The full frontend suite passed **448 tests**; the web build and release secret scan passed.
- Frontend `publication20260913z` deployed at 2026-09-13T22:21:47.691480+00:00. The final live browser check passed: both Hong Kong → Vietnam product dropdowns render exact source quotations with their own clickable URLs, the selected filters remain applied, and no empty quote rows appear. QC list load measured 1.536 seconds; the two quote expansions measured 281 and 255 ms in this run.
- Simplified and Traditional Chinese labels preserved the original quotations, the dropdown fit a 390px viewport, and the centered freshness countdown ticked. Dynamic model translation was intercepted locally during this read-only quote-display test; these timings are not a claim about full route-text translation speed. No production POST/refresh/publication action was made by the browser test.

Remaining scope limits: the earlier 18-station pairwise sample contained two other unpublished answers; this four-route work does not claim to resolve or recount them. That pairwise sample is not the complete route inventory. The separate Canada → Vietnam passport proposal remains unapplied because the Canadian advisory wording conflicted with the destination embassy’s arrival-based rule. A wider database audit, unrelated routes, and automatic future-policy guarantees are outside the latest requested scope.
