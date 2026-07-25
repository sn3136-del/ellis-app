# Global route coverage

Every lawful `passport nationality → lawful residence → destination →
tourism/short-stay` combination resolves to exactly one **defined, honest**
outcome. Nothing is invented; nothing unresolved is reported as supported.

## Layers (normalized — no redundant per-combination copies)

| Layer | Table / file | What varies here |
|---|---|---|
| Pair policy | `global_route_pair_policies` | nationality × travel document × destination |
| Portal family | `portal_families` (seeds: `data/reference/portal_families.json`) | one row per real platform, shared by many routes |
| Family adapter | `portal_family_adapters` | exactly one adapter per family |
| Jurisdiction | `consular_jurisdiction_rules` | **the only place lawful residence changes the answer** |
| Destination data | `snapshot_passport_validity_rules`, `visa_fee_versions` | destination-scoped rules and fees |

`global_routes.resolver.resolve_route()` assembles a full route record for any
tuple from these layers. It is deterministic and calls no model.

## Provenance tiers

| `source` | `verification_status` | Counts as coverage? |
|---|---|---|
| `reference_dataset` | `provisional` | **No** — reported separately, never released |
| `official_research` | `verified` | Yes, once a workflow or adapter is released |
| `none` | `unresolved` | **No** — explicit honest gap |

The reference baseline (`data/reference/passport_index_baseline.csv`, MIT,
sha256-pinned in `baseline_provenance.json`) gives every pair a defined
outcome. It is **non-official** and is superseded by any official research.
Provisional rows are structurally barred from `route_matrix_entries`, because
that table has no provenance column and every existing consumer treats a
researched disposition there as verified official research.

## Outcomes

`VISA_EXEMPT`, `ENTRY_PREPARATION`, `ELECTRONIC_AUTHORIZATION`, `EVISA`,
`VISA_ON_ARRIVAL`, `EMBASSY_OR_CONSULATE_APPLICATION`,
`AUTHORIZED_VISA_CENTER`, `MAIL_APPLICATION`, `APPOINTMENT_REQUIRED`,
`NO_AVAILABLE_TOURIST_ROUTE`, `REQUIRES_MANUAL_JURISDICTION_SELECTION`,
`UNRESOLVED`.

Only `ELECTRONIC_AUTHORIZATION` and `EVISA` need a portal-submission adapter.
Everything else is a non-portal workflow (preparation, checklist, jurisdiction,
appointment or mail instructions) and never generates a fake portal.

## Automatic release (no routine admin approval)

`release_gates.evaluate_gates()` computes 16 objective gates from stored
evidence. All pass → the existing `AutoReleasePolicyEngine` releases the
reversible sandbox binding plus each irreversible capability whose structural
gate passes. Any gate fails → **fail closed**, and the precise missing
capability is recorded on `FamilyAdapterLink.last_error` and shown on the
dashboard.

Gates: official portal identity · destination/jurisdiction correct · no
mock/synthetic driver · safe navigation succeeded · required fields mapped ·
selectors verified in a second session · account flow mapped · upload flow
mapped · applicant confirmation gates preserved · CAPTCHA/OTP handoffs
preserved · payment confirmation preserved · submission confirmation
preserved · no irreversible action in testing · structured provider errors ·
security scan · regression tests.

Applicant intervention (OTP, CAPTCHA, passkeys, identity checks, biometrics,
declarations, signatures, exact payment confirmation, final review, final
submission) is never automated and never delegated to an administrator.

## Commands

```bash
python -m app.global_routes build-all [--max-live-builds N]
python -m app.global_routes build-destination TUR
python -m app.global_routes build-family turkey-evisa
python -m app.global_routes retry-failed
python -m app.global_routes revalidate
python -m app.global_routes verify-live --families a,b,c
python -m app.global_routes coverage
python -m app.global_routes unsupported --limit 200
python -m app.global_routes export
python -m app.global_routes stop
python -m app.global_routes resume
```

Runs and tasks are checkpointed; `stop` is safe at any point and `resume`
continues from the checkpoint. Task `dedup_key` prevents duplicate builds
platform-wide; transient failures are retryable, permanent ones are not.

## Dashboard

`GET /admin/global/coverage` (admin) returns the whole picture in one payload:
totals, mapped, released, per-outcome buckets, verification/provenance splits,
portal families by verification, adapters released/building/failed with exact
failure reasons, build-task counts, last verification. `GET
/admin/global/unsupported` samples both unresolved and legally-unavailable
routes. The Admin Console "Global coverage" tab renders it.

Applicants see only `GET /global/route-outcome` — outcome, verification, the
official channel (**only when its identity is verified**), their own required
confirmations, and the standard disclaimer. No operator language.
