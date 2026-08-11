# Ellis automation roadmap — the honest ceiling and everything left to do

Consolidated 2026-08-10 from the H1B + tourist + appointment research sweeps.
This is the definitive "what remains" list and the definitive answer to what
"100% AI-agent automatic" can and cannot mean.

## The automation ceiling (verified fact, not caution)

Across BOTH editions, a small set of acts are legally irreducible to a human.
These were checked against primary sources repeatedly; they are settled:

- **Login.** Login.gov (FLAG, myUSCIS) and the consular portals ban automated
  authentication. The human signs in; Ellis works inside that session.
- **Signature.** LCA Section J and I-129 Part 7 are penalty-of-perjury
  attestations; since 2026-07-10 a bad signature can get a petition *denied*,
  not just rejected. myUSCIS e-signature is the human acting in their own
  account; a paper/PDF upload needs wet ink.
- **Payment.** Fees are paid by the human (pay.gov / MRV / portal).
- **Submission.** The final "submit" is a personal act (myUSCIS ToU forbids a
  third party operating the account).
- **CAPTCHA / biometrics.** Solving a CAPTCHA or giving fingerprints is the
  applicant's act. Australia's ETA even requires a live in-app selfie.

So "100% AI-agent automatic, unattended" is not achievable for a lawful
government filing — and any product claiming it is either breaking a ToS
(risking the user's case) or quietly using a human. What IS achievable is
**~95% of the labor automated, with the human's remaining work reduced to
login + review + sign/pay/submit** — often a couple of minutes. That is the
target, and it is what the whole architecture is built for.

The one thing that removes the MOST friction is not a tool: it is the user's
real accounts + one attended login session per portal. After that first
teach, mapping memory makes every later build of that portal near-instant.
"Instantaneous adapter building" means instant on the *second* case for a
portal, never the first.

## Already built and committed (branch h1b-edition)

Two-party H1B pipeline; official I-129 (02/27/26) + ETA-9035 fill; mail-ready
paper packet; Ask Ellis (Kimi); counsel/RFE engine; **wage-level computed from
DOL OFLC data**; SOC/NAICS from NIOCCS/O*NET/Census; employer console; adapter
learning loop (mapping memory + gate advisor); compact-snapshot recon; USCIS
Torch case-status. Tourist editions untouched on their own branches.

## In flight (integrations batch)

Steel.dev browser provider (CAPTCHA/stealth off); Federal Register staleness
alarm + USCIS Employer Data Hub; sanctioned agent-channel families (Canada
APR, Sri Lanka Third Party, VFS registered-agent portal); corroboration
providers (Sherpa/Regula/E-Verify/credential-eval, honest-degrade); LCA Public
Access File + posting notice.

## Remaining build queue

### A. Build-now — no user input needed (I can build these next)
- **DOL FLAG adapters**: ETA-9035E (LCA) and **ETA-9141 (PWD)** deterministic
  prefill; human does login + submit. Prevailing-wage errors are a top RFE
  driver, so structured prefill is high value.
- **Vietnam e-Visa domain fix**: point any adapter at evisa.gov.vn /
  thithucdientu.gov.vn (old host is dead), plus Cambodia/Egypt portal-fill.
- **Canada eTA representative** flow (sanctioned APR path; note the no-eTA-fee
  rule if monetized).
- **US appointment "seconds for the user" pre-stage**: fill DS-160, save
  profile, assemble docs, guide fee payment, so the human's step is minimal;
  plus the **paid Expedited/emergency** request assembly.
- **US wait-time display**: ingest the State Dept Quarterly Report (citable,
  low-frequency) and deep-link the official live tool with attribution — never
  scrape the Cloudflare-walled page.
- **RFE response assembly**: build the response packet, index exhibits to each
  RFE issue, AI-draft the narrative in the builder (attorney reviews; human
  uploads).

### B. Build-abstraction — seam now, lights up when the user enrolls
- **USCIS Org-Account bulk spreadsheet generator** (up to 2,500 beneficiaries)
  from HRIS/beneficiary data, validated to the template; human drives upload.
- **HRIS import** (Merge/Finch unified API) so Trip.com HR never retypes
  title/salary/worksite/start-date.
- **Entity verification** (Middesk / D&B) feeding ability-to-pay + FDNS risk.
- **G-28 attorney-of-record** prep for org-account Legal Teams (two human
  e-signatures irreducible).
- **Cap-exemption wizard** (8 CFR 214.2(h)(19)) — near-deterministic for
  higher-ed / nonprofit-research given entity data.
- **Chinese-degree verification** (CSSD/CHSI attended-observation adapter) +
  WES/ECE referral/tracking.
- **Address validation** (Smarty/Loqate) + **photo-compliance** (Regula Face /
  ICAO 9303) for tourist forms. (Name transliteration is already solved by the
  MRZ read — no vendor needed.)
- **VFS eVisa agent-submission portals** (agents.vfsevisa.com) — highest
  automation yield for Schengen-adjacent eVisa, gated on the agent account.

### C. Business decisions — Trip.com must obtain (not buildable by me)
- **VFS registered-agent account** (unlocks the real appointment channel) and,
  per member state, **Schengen commercial-intermediary accreditation**.
- **US group-appointment** coordinator status for genuine tour groups (the one
  sanctioned US batch mechanism).
- Sherpa / VisaHQ **white-label fulfillment** contract as a fallback for routes
  Ellis declines.
- The real FLAG + myUSCIS **organizational accounts** and a first real case.

## What "no government e-visa API exists" means for strategy

Confirmed across India, Vietnam, Turkey, Australia, NZ, Kenya, Cambodia,
Egypt: there is NO government-authorized submission API to integrate. So
deterministic portal-fill + human-submit IS the sanctioned path everywhere —
there is no shortcut being left on the table, and Ellis's architecture already
is the answer. India explicitly disowns intermediaries; never present Ellis as
one there.
