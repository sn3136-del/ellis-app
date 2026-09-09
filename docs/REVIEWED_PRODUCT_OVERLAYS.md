# Reviewed product overlays

The registered reviewed layers are `reviewed_schengen_overlay_2026_09_09.json` and `reviewed_australia_overlay_2026_09_09.json`, beside the core `verified_overrides.json`. They load after the core seed and before operator overrides. A missing optional layer is harmless. A present malformed or unreadable layer, missing/unreadable core seed, or malformed/unreadable operator store adds `source_verification_store_unavailable`; the shared invariant gate holds the answer. A valid repair removes only this component's marker. No private filesystem path enters the marker.

Each loaded table retains its own immutable error tuple. A request carries the status of its selected table through `find(route)` in request-local context; another request's repair or reload cannot clear that response's error. The cached file signature and table are published together. Operator edits check the snapshot loaded under their write lock, rather than a shared last-load status.

The marker is applied before normalization, override merging or finalization. While a source store is unavailable, the raw cached claims remain available for review, with no newly claimed provenance. An existing hold from another component is saved as `prior_unavailable`; retries do not nest it again, and recovery restores it. When only this loader's error has been repaired, its marker is removed before normal merging resumes. These controls do not bypass the shared held-answer gate or release source disputes.

The Schengen overlay has been regenerated and enabled in the local checkout after the scoped store, proof-binding and projection tests passed. This does not install it in production. Production installation still requires the exact baseline preflight below; a passing store-loader test alone does not verify policy contents. Australia has its own separately reviewed candidate and activation checkpoint.

The layer format is `{schema_version: 1, kind: "reviewed_overlay_conversion", entries: [...]}`. Each entry follows the existing seed schema. Conversion reports also retain `preflight`, `blocked` and review status for operations. A report's status is descriptive; the explicit filename registration determines whether its entries load.

## Schengen review and conversion

`reviewed_schengen_products_2026_09_09.json` contains the reviewed partial corrections and captured evidence. `scripts/prepare_reviewed_product_patch.py` checks exact nationality, destination, purpose, document, original product identity and hashes. It rejects foreign-government authority, absent literal quotations, a different nationality's required/exempt list, and fees belonging to another product or age/procedure tier. These are bounded EU/France/Spain evidence contracts, not a generic semantic validator for arbitrary policies.

`convert_reviewed_product_patch.convert_manifest(manifest, current_layers)` is a pure conversion: it does not write the seed, operator store, cache, issues or releases. `current_layers` maps each canonical cache key to:

- `raw_guidance`: the current database row's guidance;
- `merged_guidance`: the current live serving merge **before installing this new layer**, including existing operator overrides;
- `current_override_entries`: the current core-seed entries for that exact nationality/destination/purpose/document, in seed order.

Before calling the converter, the deployment guard must also compare the actual stored route identity with `baseline.raw_route` and the canonical key. The converter compares the raw guidance, merged guidance and scoped seed hashes to the reviewed baseline. Any drift aborts conversion. Re-export and examine changed fields before replacing a baseline; never refresh hashes merely to make preflight pass. Obtain live layers using the currently deployed code before copying the new serving layer, so the proposed layer does not incorrectly become its own precondition. Keep operator files unchanged. Installation must recheck these preconditions while writers are paused or under the deployment's equivalent guarded procedure.

The current Schengen conversion supplies 16 route entries. The combined ICT/local-contract and Talent Passport work route remains blocked. All 42 currently visible nested products are retained in the review; the original raw 41 products are also retained in the baseline, including the distinction between the raw and existing four-product Indonesian override. No new product or visa eligibility is inferred from an umbrella name.

New fields carry their own source, literal proof and AI attribution. Prior unrelated override fields keep their original provenance. Raw fields that were not reviewed are never swept into a new verified overlay. Each nested product has its own `field_provenance.disposition.subject` with exact nationality, destination, purpose, document, `product_type`, `disposition` and `requirement_detail`. The last two keys must be present even when a reviewed detail is explicitly unknown/null. Editing either permission value invalidates the old product proof; legacy explicit product proofs without this binding receive no credit. An invalid explicit product proof cannot inherit a parent citation. Only that visa requirement is credited; the product's other details retain separate evidence.

An explicit unknown/null has `status: "unknown"` and no verification date or source. Mixed lists carry `status: "partial"`, `verified_elements` and `retained_unverified_elements`. Existing disputes, release history and freshness timestamps are not changed by conversion or layer installation. These partial corrections do not certify every record field or satisfy a new six-hour source check.

## Source-read limits

The Schengen catalog records nine successful direct official-origin reads separately from six France-Visas observations returned by the web tool. Their reported crawl ages are retained; failed direct-origin HTTP 403 responses are not evidence or successful freshness checks. Spanish consulate general-policy evidence does not establish an applicant's filing jurisdiction, so the unresolved Spanish application portal remains null. Published long-stay fee alternatives remain conditional rather than one fabricated universal price.

Unknown or discretionary visa validity is no longer filled from permitted stay or product names. Explicitly unknown application methods are not inferred from electronic issuance. A lowered completeness percentage due to a real unknown must remain visible until its own applicable evidence is verified.

## Validity projection

The validity parser accepts a single explicit duration and keeps its calendar unit. It does not choose one alternative from a range or a discretionary grant, infer duration from the product name, or copy permitted stay into visa validity. Unknown or qualified validity keeps its exact text. This is separate from the permitted-stay parser; calendar-month stays are still not converted to invented day counts.
