# Sanctioned Representative and Agent Channels

Some governments and one large contractor publish an *official* way for somebody other
than the traveller to complete a filing. Those channels are portal families like any
other, seeded in `data/reference/portal_families.json`, and this document is the record
of what each one actually sanctions.

Edition-neutral. Nothing here keys off a government family or a visa purpose; the
tourist product is the immediate beneficiary and the H1B edition inherits the same
discipline.

Three rules govern every entry below.

1. **A sanctioned channel is a channel, not a permission slip.** The government
   sanctions a *representative relationship*. It does not sanction automating the
   representative's own acts. Ellis fills; a human declares, pays, signs and submits.
2. **Being sanctioned does not make it releasable.** Every channel here carries a
   business or evidence prerequisite that no amount of code closes. Each family is
   seeded so that the prerequisite is structurally enforced, not merely documented.
3. **No third party is ever quoted as the government.** VFS Global is a contractor.
   Its pages are a second opinion about appointments, never the authority on a visa
   rule.

## How the seeds enforce this

| Family | `account_required` | `destinations` | `supported_outcomes` | `entry_gate` | Sync verdict |
| --- | --- | --- | --- | --- | --- |
| `canada-eta-representative` | `false` | `["CAN"]` | `[]` | absent | `verified_official_domain` |
| `srilanka-eta-thirdparty` | `false` | `["LKA"]` | `[]` | absent | `verified_official_domain` |
| `vfs-registered-agent-appointments` | `true` | `[]` | `[]` | absent | `seed_unverified` |

`supported_outcomes` is empty on all three **on purpose**. `families.pick_family()`
skips any family whose `supported_outcomes` does not contain the pair's outcome, so
`assign_families_to_pairs()` can never bind a tourist route to an agent channel by
accident. A traveller filing for themselves keeps resolving to `canada-ircc` or
`srilanka-eta`; the agent channel is selected deliberately by the caller that knows a
representative relationship exists, or not at all. Emptiness is the mechanism, not a
placeholder for data we have not gotten round to.

`entry_gate` is absent on all three because no live gate probe has been run against any
of them. That matches the H1B families: a gate is authored from observed evidence in an
attended step, never guessed. Absent is the honest form.

---

## Canada eTA, representative filing

**Family:** `canada-eta-representative` -> `eta.onlineservices-servicesenligne.apps.cic.gc.ca`

**What is officially sanctioned.** IRCC permits an authorized representative to
complete and submit an eTA application for a client. The channel is built into the
public eTA form itself: its first eligibility question is *"Are you applying on
someone's behalf?"*, and answering yes opens the representative declaration.
(`canada-ircc`'s curated entry gate answers that same question **No** — that is the
self-filing channel, and the two must not be conflated.) IRCC additionally runs an
account-based Authorized Paid Representatives Portal. It is deliberately **not** seeded:
no live evidence of its eTA path has been taken, and an unobserved hostname is never
claimed as a portal.

Filing is **per applicant**. There is no bulk upload and **no API**.

**What Ellis may automate.** Deterministic fill of the eTA form from data the applicant
already gave us — passport fields read at intake, travel details, the representative's
own identifying details entered by that representative. Nothing more.

**What stays a personal act.**

- The representative's declaration that they are who they say they are, and their
  professional-body membership number.
- Payment of the eTA fee.
- The submit click.
- Any authentication, if the Authorized Paid Representatives Portal is ever adopted.

**Business prerequisite.** The declared representative must be authorized under IRPA
s.91: a lawyer or paralegal in good standing, a regulated Canadian immigration
consultant (RCIC), or an **unpaid** family member or friend. And the eTA-specific fee
rule: *a travel professional may not charge a fee specifically for submitting an eTA*.
That rule is a release blocker for any paid-agent framing of this channel, not a UI
footnote. A product that bills for eTA submission is not made lawful by this family
existing.

**Re-verification triggers.**

- The representative question's selector or option values change (`canada-ircc`'s gate
  breaking is the early warning; the two share the form).
- IRCC changes who may act as a representative, or the eTA fee rule.
- Before the representative branch is probed live for the first time — its gate must be
  authored from that probe, never backfilled from the self-filing branch.

---

## Sri Lanka ETA, Third Party application

**Family:** `srilanka-eta-thirdparty` -> `eta.gov.lk`

**What is officially sanctioned.** The state ETA system offers a **Third Party**
application type alongside Individual and Group: an agent, an airline or a relative may
apply on the traveller's behalf.

**Status: unstable. Re-verify before any release.** The privatized `srilankaevisa.lk`
operation was reverted to the state-run `eta.gov.lk` in October 2025, and the
before-arrival ETA mandate is **suspended**. Checked 2026-08-11: `srilankaevisa.lk` does
not resolve in DNS; `https://eta.gov.lk/` redirects to `https://eta.gov.lk/slvisa/` and
serves the state *Online Visa Application* page.

Note precisely what the sync verdict means here. `eta.gov.lk` is on the government
suffix allowlist, so the deterministic domain check verifies **identity** — this really
is the Sri Lankan state's own host. It says nothing about **policy stability**, and the
policy is in flux. Identity verified is not the same as safe to use, and the family's
`notes` carry that distinction into the database rather than leaving it in a document
nobody reads at runtime.

**What Ellis may automate.** Deterministic fill of the Third Party form once its
structure has actually been observed. Today: nothing, because nothing has been observed.

**What stays a personal act.** Payment, submission, and any declaration the third-party
form asks the applying party to make about their relationship to the traveller.

**Business prerequisite.** None contractual — the Third Party type is open. The
prerequisite is evidentiary: a live observation of the branch, taken while the policy is
stable enough for that observation to still be true a week later.

**Re-verification triggers.**

- Any news of the ETA mandate being reinstated, re-suspended, or re-privatized.
- `eta.gov.lk` ceasing to redirect to `/slvisa/`, or `srilankaevisa.lk` resolving again.
- The Third Party option disappearing from, or changing shape in, the application-type
  chooser.
- Any fee change; the free-visa regimes announced on the portal's own alert banner move
  frequently.

---

## VFS Global registered travel-partner appointments

**Family:** `vfs-registered-agent-appointments` -> `row1.vfsglobal.com`, `row4.vfsglobal.com`

**What is officially sanctioned.** VFS Global operates a registered travel-partner
appointment channel, distinct from the public site in the `vfs-global` family. An
accredited agent signs in, adds its customers, and books a calendar slot for them:

- `row1.vfsglobal.com/GlobalAppointment/Account/RegisteredLogin`
- `row4.vfsglobal.com/GAR1Ph1Appt/Account/RegisteredLogin`

Hostnames checked 2026-08-11: both resolve on VFS Global's own `vfsglobal.com` apex,
behind the same Cloudflare front as `visa.vfsglobal.com`, and answer over HTTPS.

**This is a commercial operator, not a government.** `vfsglobal.com` is not on the
government allowlist, so the family stays `seed_unverified` until a destination
government's own page is observed linking to it (`families.mark_official_link_verified`).
That is the intended fail-closed outcome. Do not add `vfsglobal.com` to the allowlist to
make this go green.

**What Ellis may automate.** Deterministic fill of customer details into the agent's
booking form, and reading back the slot that a human selected and confirmed — once the
account exists and the flow has been observed.

**What stays a personal act.**

- The registered-agent login. Those credentials belong to a named staff member, they are
  never automated and never stored against an applicant.
- Any payment.
- Selecting and confirming the appointment slot, which is the irreversible act.

**Business prerequisite — not a code gap.** No registered-agent account is held.
Obtaining one is a contractual step for the operating company. Until it exists there is
nothing to build against, and the seed makes that structural rather than advisory:

- `destinations` is deliberately **empty**, because which client governments an agent may
  book for is a property of *that account*, not of the portal. `release_gates`'
  destination check (`build_request.destination in family.destinations`) can therefore
  never pass, so no build can escape while the account does not exist.
- `account_required` is `true`, so the release gate additionally demands a mapped
  `credentials` handoff.
- `supported_outcomes` is empty, so no tourist pair can be bound to it.

VFS expires a registration after **100 inactive days**. That makes "the account still
works" a recurring pre-flight check, not a one-time setup step.

**Re-verification triggers.**

- 100 days of inactivity on the agent account, or any login failure.
- Either `row*` host changing path, moving, or being consolidated.
- A destination government adding or dropping VFS as its contracted center — the
  official-link evidence is per government and expires with that contract.
- Any change to VFS's registered-partner terms about automated access.

---

## The wider truth: there is no e-visa submission API

Ellis fills portals because there is nothing else to fill. Across the destinations we
have surveyed, **no government offers a public API for submitting a visa or e-visa
application**:

India, Vietnam, Turkey, Australia, New Zealand, Kenya, Cambodia, Egypt.

Every one of them is a web portal for humans. Read-only status APIs exist in places
(USCIS Torch, for example) and are a different thing entirely — they report on a filing
that already exists.

This is not a limitation to route around. It is the finding that makes the design
correct: **deterministic portal fill plus a human submit IS the sanctioned path.** There
is no back door being declined here, because there is no back door. Any vendor claiming
an e-visa submission API is either reselling manual labour or scraping, and neither is
something to integrate.

### India is a special case: never present Ellis as an intermediary

The Indian e-visa authority explicitly disowns intermediaries. Its own site warns
applicants that the government has not authorized agents or third-party websites to
apply on their behalf, and that such sites are a fraud risk.

So for India, and anywhere else a government makes the same statement:

- Ellis is a tool **the applicant uses**. The applicant is the applicant.
- Never describe Ellis, the operating company, or a partner as an agent, intermediary,
  representative or authorized service for that destination — not in the UI, not in
  generated documents, not in marketing copy.
- Do not seed an agent-channel family for such a destination. The absence of one is the
  correct state, and adding it would be claiming a channel the government says does not
  exist.

The three channels documented above are the exceptions that a government or contractor
actually publishes. Absent that publication, the answer is not "probably fine" — it is
that the applicant files for themselves, with Ellis preparing the filing.
