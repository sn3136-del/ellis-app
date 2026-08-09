# H1B Edition: Two-Party Petition Architecture

Edition scope: Trip.com (Chinese parent) sponsoring Chinese workers for H1B roles in
the USA. Petitioner is Trip.com's US entity; beneficiaries are Chinese nationals who
finish at a US consulate in China. Branch: `h1b-edition`, kept thin per the
vietnam-edition precedent.

This document pins the names and interfaces. Implementers follow it exactly; a name
change here is a design change, not a drive-by edit.

## Owner decisions (2026-08-09)

- **Automation level = the tourist tool's, for both parties.** Ellis fills every
  field of ETA-9035 (petitioner), the H-1B registration (petitioner), the I-129
  (petitioner), and the DS-160 prep (beneficiary), computes OEWS wage level, and
  drives the flow to the edge of every irreversible step, then the executor submits
  after authorization exactly like `background-portal-execution`. This is NOT an
  unattended bot: matching the tourist tool means login-walled auth, fee payment,
  and the penalty-of-perjury signature/submit stay personal acts in the acting
  party's secure Browserbase window. Legal walls that make this mandatory, not
  optional: myUSCIS ToU (no third party may operate an account or e-sign for
  another), Login.gov Rules of Use (no automated authentication), and the personal
  penalty-of-perjury attestations on LCA Section J and I-129 Part 7. There is no
  filing API on either side; status-only read APIs exist (USCIS Torch).
- **Legal posture = full filler + auto-submission + a prominent "consult an
  immigration attorney" disclaimer**, surfaced at case creation, before every
  signature ceremony, and on any eligibility-adjacent output. Ellis discloses as a
  non-attorney preparer (I-129 Part 8 / LCA Section K). No attorney-review gate is
  built; the disclaimer is the chosen mitigation. (Preparer criminal exposure under
  18 U.S.C. 1001 makes the existing data-provenance + user-review-before-sign flows
  compliance features, not just UX — keep them.)
- **First real target = H-1B extension/amendment** (year-round, no lottery). The
  FY2027 cap is exhausted; new cap registrations cannot file until ~March 2027. The
  cap/registration path is still built (P2/full), but the first genuine
  end-to-end run is an extension/amendment for a current Trip.com US worker.

Disclaimer text lives in one place (`h1b/disclaimer.py:ATTORNEY_DISCLAIMER`, with
zh-CN/zh-Hant catalog keys) and is referenced by every surface; never inline it.

## Why H1B cannot ride the tourist pipeline

Verified walls (2026-08-09 machinery survey):

- `visa_snapshot/routekey.py:49` raises for `travel_purpose != 'tourism'`;
  `registry.normalize_category` knows only tourist categories.
- `visa_snapshot/api.py:580` (`/intake/{id}/continue`) hard-codes `visa_type='tourist'`.
- `RoutePairPolicy.pair_key` (`gp1|nat|doc|dest`) has no purpose axis; one portal
  family per pair; `FamilyAdapterLink` is one adapter per family.
- `CaseMachine` is one 40-state single-filing pipeline; `WorkflowExecution` is unique
  per case; `portal_queue` allows one active run per case.

H1B is three-plus sequential filings on three portals by two different humans. The
sanctioned precedents are `renewal.py` (linked sibling case per government filing,
documents carried by sha, results propagated) and `entry_preparation.py` +
`worker.release_due_entry_filings` (condition-gated filing release). We compose
those; we do not relax any single-flow invariant.

## Case topology

One parent case, one child case per government filing.

- Parent: `VisaApplication` with `visa_type='h1b'`, destination USA. Both parties see
  it; it holds the beneficiary answers (compatibility with intake/passport/OCR
  machinery) and the umbrella checklist.
- Children, one per filing, created lazily when the predecessor step verifies:
  - `visa_type='h1b_lca'` (DOL FLAG, ETA-9035, acting party petitioner)
  - `visa_type='h1b_registration'` (myUSCIS, seasonal window, petitioner)
  - `visa_type='h1b_i129'` (myUSCIS, petitioner)
  - `visa_type='h1b_ds160'` (CEAC prep + consular leg in China, beneficiary)
- `visa_applications.parent_case_id` String(32) default '' (additive, `_ensure_columns`).
- Every child runs the standard 40-state workflow with its own adapter binding,
  authorization, review version, signatures, payment, and confirmation.

### Pipeline table

`H1bCaseStep` (`h1b_case_steps`, new table in `backend/app/h1b/models.py`, module
imported in `db.create_all`):

- `id`, `org_id` idx, `application_id` (parent) idx
- `step_key` in `('lca', 'registration', 'i129', 'ds160_consular')`
- `child_case_id` String(32) default ''
- `acting_party` in `('petitioner', 'beneficiary')`
- `depends_on` JSON (list of step_keys)
- `status` in `('blocked', 'ready', 'in_progress', 'awaiting_government', 'verified', 'failed')`
- receipt columns: `lca_number`, `beneficiary_confirmation_number`, `uscis_receipt_number`,
  all String default ''
- UniqueConstraint(application_id, step_key)

Sequencing rule: a step becomes `ready` only when every `depends_on` step is
`verified`. `verified` requires the predecessor child case to hold a
`SubmissionConfirmation` plus government-host `AdapterOutcomeEvidence`
(`main.py:_adapter_verified_result` discipline) or, for `awaiting_government`
outcomes (LCA certification, lottery selection, I-129 adjudication), a read-from-
portal evidence row. Never inferred, never timer-faked. The worker sweep
(`release_due_h1b_steps`, modeled on `release_due_entry_filings`) enqueues the next
filing when its step turns `ready` and statutory timing allows (registration window
per `gov_calendar`).

Lottery reality: `registration` -> `i129` is gated on a selection notice read from
myUSCIS. Non-selection is a terminal-honest outcome for the season, surfaced as
such, with the parent case parked, never silently retried.

## Two-party model

New tables (zero-ceremony migration):

- `CaseParty` (`case_parties`): `id`, `org_id` idx, `application_id` idx (parent
  case), `role` in `('beneficiary', 'petitioner')`, `party_kind`
  (`'person'|'organization'`), `user_id` String(64) default '', `display_name`,
  `email`, `phone`, `employer_profile_id` String(32) default '', `answers` JSON
  default dict, `status` (`'active'|'invited'|'revoked'`),
  UniqueConstraint(application_id, role).
- `EmployerProfile` (`employer_profiles`), org-level, reused across beneficiaries:
  legal_name, trade_name, `fein` (digits only), naics_code, US address fields,
  year_established, total_employees, gross/net income cents, `h1b_dependent`,
  `willful_violator` (nullable booleans, never defaulted), signatory
  name/title/email/phone, `parent_company_name`, `parent_company_country` (the
  Trip.com China relationship is I-129 evidence).

Additive party columns, String(32) default '' where '' means legacy beneficiary:
`portal_accounts.party_id`, `native_signatures.party_id` + `signer_role` String(24)
default 'applicant', `application_review_versions.party_id`,
`applicant_standing_authorizations.party_id`, `stored_documents.party_id`,
`checklist_submissions.party_id`, `portal_runs.acting_party_id`,
`human_handoffs.party_id`.

Answer partitioning (mandatory, the flat answers dict is party-blind):

- Beneficiary answers stay in `parent.answers`.
- Petitioner answers live in `CaseParty.answers` + `EmployerProfile`.
- Each child filing's workflow receives an explicitly assembled dict built by a
  per-step builder (`h1b/assembly.py:answers_for_step`) that whitelists shared facts
  (beneficiary identity into I-129/registration; wage/worksite into LCA). Never a
  wholesale merge of both parties.

Authorization: per-party ceremonies on the existing machinery. The petitioner's
standing authorization + signatures cover LCA/registration/I-129; the beneficiary's
cover the consular leg. `final_review.verify_ready_to_submit` becomes party-aware:
the ACTING party of the filing must hold the valid authorization and the signed,
unchanged review. Every attestation on a government form stays a
`legally_personal_declaration` handoff performed by that party personally. Server
enforcement lives in `_signal_or_gate_error` and the signature/payment endpoints,
never only in UI hiding.

Registration in `privacy._CASE_CHILD_MODELS`: `CaseParty`, `H1bCaseStep` (and any
new blob-like table) join the erasure cascade; employer vault refs destroyed like
`PortalAccount` refs.

## Journey registration (renewal-style, no Kimi route decision)

- Creation endpoint `POST /h1b/cases` (backend/app/h1b/api.py): creates the parent
  case (`visa_type='h1b'`), the beneficiary `CaseParty` from the caller, optionally
  binds an `EmployerProfile` + petitioner party, writes
  `CaseRouteGuidance(continuation_kind='h1b_petition')` with deterministic curated
  guidance (statutory facts with official source URLs, no model in the loop), and
  derives the checklist. Beneficiary passport can be carried from a prior intake by
  sha (renewal precedent).
- `checklist_intake.NEXT_STAGE_BY_KIND` gains `'h1b_petition': 'petition_preparation'`.
- `KIND_BY_DISPOSITION` untouched (we never enter through Kimi disposition).
- Route facts (fees, windows) are `RouteRule`/`VisaFee` rows keyed
  (destination='United States', visa_type='h1b_*'), admin-reviewed, with official
  sources; numbers land from the 2026 research pass, marked pending until then.

## Checklist (party-tagged, deterministic deriver)

`h1b/checklist.py:derive_h1b_checklist` follows `renewal.derive_renewal_checklist`:
emits `{id, label, kind, required, satisfied_by, party, note}`; `party` rides
verbatim through `CaseRouteGuidance.checklist` and `apply_checklist_status`.

Beneficiary items (zh-CN labels alongside en): passport (existing MRZ pipeline,
sole identity source), degree certificate 学位证 AND graduation certificate 毕业证
(distinct items, China reality), transcripts, credential evaluation report
(third-party, attach-and-verify), resume, prior I-797s (required=False for
first-timers), current visa (auto-detected `prior_visa`), I-94 (only when in the
US; for the China flow default off).

Petitioner items: support letter, job description, FEIN evidence (CP-575),
employer financials, corporate-relationship evidence (Trip.com parent-subsidiary),
certified LCA (`certified_lca`, starts required=False and flips to required when
the LCA step verifies, via the deriver re-run on `current_checklist` healing).

Registry updates (all four synced places, ordered BEFORE the `'form'` /
`'statement'` keyword traps in `_DOC_TYPE_KEYWORDS`):
`doc_classifier.CANONICAL_TYPES` + `_KEYWORDS`, `intake_flow._DOC_TYPE_KEYWORDS`,
`MANUAL_DOC_TYPES` (backend + `src/renderer/src/lib/intake.js` + `i18n.js`
`doctype.*` in en/zh-CN/zh-Hant). New types: `degree_certificate`,
`graduation_certificate`, `transcript`, `resume_cv`, `prior_i797`, `i94_record`,
`employer_support_letter`, `job_description`, `fein_evidence`,
`employer_financials`, `corporate_relationship_evidence`, `certified_lca`,
`credential_evaluation`.

Two deterministic extractors (regex on `recognized_text` into `extracted_fields`,
hooked at `main.py add_document` item_types context): I-797 receipt numbers
(`[A-Z]{3}\d{10}`, WAC/EAC/LIN/SRC/MSC/IOE) and I-94 number + admit-until through
`app/dates.py`. Everything except the passport stays attach-and-verify; grounded
prefill via `document_answers.SOURCES` (FEIN from CP-575, wage/SOC from certified
LCA, degree name from 学位证 + evaluation report).

## Portal families and adapters

- `visa_snapshot/authority.py`: add `dol.gov` to GOV_SUFFIXES (`uscis.gov` already
  passes); `login.gov` and `pay.gov` handled as redirect/payment hosts in manifest
  `allowed_redirect_hosts`, not navigable form hosts.
- `data/reference/portal_families.json`: seed `usa-dol-flag`
  (hostnames ['flag.dol.gov'], operator 'U.S. Department of Labor OFLC',
  account_required true, destinations ['USA']) and `usa-uscis-myaccount`
  (hostnames ['my.uscis.gov'], operator 'USCIS', account_required true), modeled on
  `usa-ceac`. Entry gates authored from `gate_probe.py` evidence on the public
  pre-login pages; declared_handoffs carry otp for the login walls.
- Expected first real build: parks honestly at gate 5 ("no form page was mappable
  from public observation") because both portals are login-walled. The designed
  remedy is attended observation with the PETITIONER driving: consent (current
  version), personal login.gov/myUSCIS sign-in with OTP, they drive the real
  ETA-9035 / registration / I-129 wizards, Ellis records sanitized structure
  (`authorized_session` provenance), `finish()` rebuilds through specgen -> static
  -> contract -> live structural -> the 16 gates. Check
  `attended_observation.MAX_PAGES=40 / MAX_MINUTES=45` against the I-129 wizard
  length and raise if needed BEFORE the first session.
- `specgen.ELLIS_FIELDS` grows the petition vocabulary FIRST or every observed
  field is rejected as unknown: employer_legal_name, employer_dba, employer_fein,
  employer_naics, employer_contact_*, job_title, soc_code, soc_title, wage_offer,
  wage_offer_unit, prevailing_wage, pw_tracking_number, worksite_address_*,
  employment_start_date, employment_end_date, full_time_position,
  h1b_dependent_employer, willful_violator, beneficiary middle_name,
  birth_country, citizenship_country. Yes/No attestation keys join
  `KEY_QUESTIONS` + `runtime._BOOLEAN_KEYS`. All new date keys end in `_date`
  (the `to_portal` fail-closed contract). USCIS/FLAG date format 'MM/DD/YYYY';
  CEAC DS-160 prep 'DD-MON-YYYY' via `consular_forms.FORMS`.
- Released-flow resolution: `resolve_released_route` gains a step-keyed branch for
  `h1b_*` visa types ((visa_type, step_key) -> portal_family_id map), sidestepping
  the purpose-less `pair_key`; `personal_gate.deterministic_gate_completion` gets
  the same generalization. RouteReadiness gate rows per filing route.
- Tier honesty: gates auto-release to SANDBOX only. Production release is a human
  admin act over the evidence package, after a genuine legal/ToS review of
  DOL FLAG and myUSCIS automation (the deterministic policy review auto-permits
  gov domains and does NOT do that review). Until then every terminal claim is
  clamped and the UI refuses to show 'filed/paid/selected/approved' as real; the
  execution-class machinery already enforces this structurally.

## Sequencing honesty

New pause/handoff kinds, added simultaneously to `released_flow.APPLICANT_HANDOFFS`
(+ aliases), the spec schema, and `visaBackend.js` HANDOFF_UI/HANDOFF_COPY/
HANDOFF_SIGNAL, each carrying `party`:

- `petitioner_credentials`, `petitioner_otp` (aliases onto existing credential/otp
  machinery with party routing)
- `lca_certification_wait`, `lottery_result_wait`, `adjudication_wait` (typed
  awaiting_government pauses with scheduled evidence re-checks, never a question
  with no actor)

`workflow._pause(**extra)` carries `party`; `HumanHandoff` gains `party_id`.
CaseFlow filters pending by party; the other surface shows "waiting on the
employer" / "waiting on the worker". Emails target the acting party.

## Surfaces

- Persona: generalize `App.jsx detectAdminMode` -> `detectPersona()`
  ('applicant' | 'employer' | 'admin'), hash `#employer`, localStorage-persisted,
  same bundle in Electron and web. New `screens/EmployerConsole.jsx` beside
  VisaConsole: employer profile wizard (SetupWizard pattern), beneficiary batch
  list, per-filing progress, document checklist via existing DocCards/DocPreview,
  SignatureModal/FinalReviewModal ceremonies, LiveViewModal for FLAG/myUSCIS
  secure-window logins.
- `visaSession.js:newEmployerSession()` (same orgId as the case, distinct userId);
  org tenancy grants shared case access today; per-party signal authorization is
  enforced server-side (see two-party model).
- Org-scoped `GET /cases` (new) so the employer machine can discover cases without
  localStorage.
- Trip.com channel: `integrations/tripcom.py` webhooks gain case.status events for
  H1B step transitions; the HR console deep-links via `applicant_deep_link`
  pattern. zh-CN/zh-Hant catalogs get every new key in the same commit (parity is
  a sibling contract).
- Real remote employer auth = Clerk activation (`security.verify_clerk` is a 501
  stub). Until activated, dev-token personas carry it on localhost only.

## Contract pins (each ships with its change, not after)

1. Extend `test_personal_gate.py:test_every_required_applicant_fact_has_a_source`
   (or clone per visa_type) for every new gate-required fact; petitioner facts get
   `EMPLOYER_INTAKE_FIELDS` with the same required/default discipline. Never
   default an answer that becomes a statement on a government form.
2. Pipeline test: filing N+1 cannot enqueue without filing N's verified
   government evidence.
3. Journey payload pin for the h1b kinds (checklist keys the new UI renders).
4. Handoff vocabulary test across the 5 sync points, per party.
5. Widget tests for USWDS/React date pickers and FLAG markup in
   `test_select_widgets.py` / `test_required_marker.py`.
6. Doc-type classification fixtures per new type; cross-party mismatch stays
   advisory; per-party `complete_stage` gating test.
7. Seed parity: H1B families/policies/readiness ship as first-run seed data for the
   packaged app DB (the app<->DB sync gotcha).

## Phasing

- P1 Core: h1b models module + party columns + creation endpoint + checklist
  deriver + doc-type registries + NEXT_STAGE_BY_KIND + guaranteed-source
  extensions + tests.
- P2 Portals: authority allowlist + family seeds + entry gates + ELLIS_FIELDS
  vocabulary + step-keyed resolution + readiness rows; first real builds park at
  gate 5 by design.
- P3 Surfaces: employer persona + EmployerConsole + party-addressed pauses +
  GET /cases + zh-CN catalogs + Trip.com webhook events.
- P4 Live: petitioner attended-observation sessions on FLAG + myUSCIS (needs the
  user's real accounts), gate iteration, sandbox release, then the human
  production release after legal review.

External dependencies (user-provided, tracked in session task 8): FLAG employer
account, myUSCIS organizational account, US-entity employer facts (FEIN etc.),
credential evaluation partner for Chinese degrees, and eventually a real case
(extension/transfer/amendment/cap-exempt files year-round; new cap registrations
only in the annual window).
