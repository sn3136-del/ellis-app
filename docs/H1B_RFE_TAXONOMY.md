# H-1B RFE/Denial Ground Taxonomy — as of 2026-08-09

Curated research grounding Ellis's objection-anticipation engine
(backend/app/h1b/counsel.py). Each ground carries USCIS's own wording, the
curing/preventive evidence, and the deterministic case signals a rules engine
checks. Re-verify amounts and rule status before relying on anything here in
a filing season; every section names its sources and confidence.

**Regulatory baseline:** H-1B Modernization Final Rule effective 2025-01-17
(amended 8 CFR 214.2(h)); Weighted Selection Final Rule published 2025-12-29,
effective 2026-02-27 (FY2027 cap). Context: FY2025 RFE rate ~23%, FY2026
projected 23-27% (highest since 2019). DOL "Project Firewall" (launched
2025-09-19) runs parallel enforcement: 175+ investigations, $15M back wages,
4 employers debarred as willful violators (2026). The $100K Proclamation
10973 fee was vacated 2026-06-08 (D. Mass.); First Circuit denied stay
2026-07-24 — fee currently not payable, appeal pending, proclamation
self-expires 2026-09-20. Deference to prior I-129 approvals for extensions is
now codified (absent material change, material error, or new adverse
information) — "material change" is the wedge officers use.

## 1. Specialty occupation (largest category: ~38-42% of RFEs)

**USCIS wording:** "The evidence does not establish that the proffered
position qualifies as a specialty occupation" under 8 CFR
214.2(h)(4)(iii)(A) — i.e., none of the four criteria met: (1) a bachelor's
or higher in a directly related specific specialty is normally the minimum
for entry into the occupation; (2) degree requirement common in industry /
position so complex or unique; (3) employer normally requires the degree;
(4) duties so specialized and complex that knowledge is usually associated
with such a degree. Post-2025 codified standard: "normally" is not "always";
"directly related" means "a logical connection between the degree field(s)
and the duties." A range of acceptable degree fields is fine only if each
listed field is directly related to the duties — "any engineering degree" or
"business administration or any related field" without duty-nexus
explanation draws the RFE.

**Curing/preventive evidence:** duty-by-duty breakdown with % time
allocations mapped to specific degree coursework; OOH excerpt for the SOC
code; internal job postings + degree requirements for same role; comparable
postings from similar-size same-industry employers requiring identical
degrees; org chart showing where role sits; expert opinion letter
(professor/industry) tying each duty to the degree field; product/project
technical documentation.

**Deterministic risk signals:** SOC codes with contested specialty status
(Computer Systems Analyst 15-1211, generic "Software Developer" used for
QA/support work, Business Analyst, Project Manager, Market Research Analyst,
Management Analyst, Financial Analyst at small firms); job title generic vs
duties (title contains "Analyst/Consultant/Specialist/Coordinator" without
technical qualifier); LCA lists multiple unrelated acceptable degree fields;
degree-field list includes "business administration" or "any related field";
duties copied from OOH/O*NET verbatim; occupation's OOH entry says "some
employers accept associate degree or experience."

**Wage Level I status:** AAO holdings (Matter of B-C-, Matter of
G-J-S-USA, 2018) that Level I does not preclude specialty occupation remain
good law and were not disturbed by the 2025 rule — but 2026 practice has
revived wage-level RFEs (~12% of RFEs are "wage level adequacy"): officers
argue Level I ("basic understanding, close supervision") contradicts claims
that duties are "specialized and complex" (criterion 4). Cure: never rely on
criterion 4 with a Level I wage; explain Level I as entry into a profession
that still requires the degree; show supervision structure consistent with
Level I. Signal: wage level = I elevates risk on every specialty-occupation
prong AND collapses lottery odds under the weighted rule (see section 7).

**Sources:** uscis.gov/working-in-the-united-states/h-1b-specialty-occupations;
federalregister.gov 2024-12-18 modernization rule; hklaw.com (modernization
analysis); cholawllc.com (directly-related rule); h1bdatahub.com RFE guide
2026; immi-usa.com RFE reasons; blog.cyrusmehta.com (AAO wage-level
decisions). Confidence: high (rule text + multiple 2025-26 firm analyses).

## 2. Beneficiary qualifications (~18% of RFEs)

**USCIS wording:** "The evidence does not establish that the beneficiary is
qualified to perform services in a specialty occupation" under 8 CFR
214.2(h)(4)(iii)(C): must hold (1) US bachelor's+ from accredited
institution in the specialty, (2) foreign degree equivalent to a US
bachelor's in the specialty, (3) unrestricted state license, or (4)
education/training/progressive experience equivalent (3-for-1: three years
of experience = one year of university education, 8 CFR
214.2(h)(4)(iii)(D)). Sub-species: (a) equivalency doubted; (b) degree field
not among acceptable fields listed for the position (self-inflicted:
petitioner's own stated requirements exclude the beneficiary's major).

**China-specific:** Chinese completion requires BOTH 学位证 (degree
certificate — confers the bachelor's degree) AND 毕业证 (graduation
certificate — proves program completion). Submitting only 毕业证 means no
degree conferred = equivalency RFE or denial. 3-year 大专 (dazhuan/associate)
is not a bachelor's; needs +9 years progressive experience via 3-for-1 or a
top-up degree. Self-study/成人教育 (adult education) degrees draw authenticity
scrutiny. Verification channels USCIS/evaluators recognize: CHESICC (学信网)
for 毕业证, CDGDC for 学位证.

**Curing/preventive evidence:** NACES-member credential evaluation stating
US bachelor's equivalency in the specific field; both Chinese certificates +
certified translations + CHESICC/CDGDC verification reports; transcripts
showing coursework in the required field; for field-mismatch: course-by-
course evaluation showing major coursework substantially in the required
specialty, or combined education+experience evaluation by an evaluator
authorized to grant college credit; experience letters showing progressive
responsibility.

**Deterministic risk signals:** foreign degree with no credential evaluation
attached; Chinese degree with only one of the two certificates; 3-year
degree/diploma; beneficiary's major string not in the position's stated
acceptable-fields list (exact-match check: e.g., degree "Electronic
Commerce" vs required "Computer Science"); equivalency built on experience
(3-for-1) rather than pure education; evaluation older than the petition and
generic (not field-specific); degree from unaccredited or non-Ministry-of-
Education-recognized institution.

**Sources:** eres.com; evaluationworld.com (China); aaeevaluations.com;
thedegreepeople.com; documentevaluation.com. Confidence: high on doctrine,
medium on 2026-specific China enforcement intensity (evaluator sources, not
USCIS-published).

## 3. Bona fide job offer / third-party placement (~22% of RFEs)

**USCIS wording (post-2025):** the old "employer-employee relationship"
control test is gone from the regulation; replaced by (a) revised "United
States employer" definition (legal presence, amenable to service of process,
bona fide job offer to employ the beneficiary "as of the requested start
date"), and (b) codified authority to request "contracts, work orders, or
similar evidence" to establish a bona fide position in a specialty
occupation. Itinerary requirement eliminated for all H classifications.
Critical new doctrine: for third-party placement, the specialty-occupation
analysis runs against the END-CLIENT's requirements for the work actually
performed there, not the staffing company's job description. RFE wording:
"the evidence does not establish that a bona fide position in a specialty
occupation is available as of the start date."

**Curing/preventive evidence:** MSA + SOW/work order covering the validity
period, naming the beneficiary or role with duties; end-client letter
describing duties, required degree, duration, worksite; evidence of
non-speculative work at filing (project plans, funded contracts); LCA
listing every worksite; for in-house employers: product docs, org chart,
payroll.

**Deterministic risk signals:** worksite address differs from petitioner
address (third-party placement flag); petitioner NAICS = IT
staffing/consulting (5415xx with placement model); end-client letter absent;
SOW expires before requested validity end; multiple client sites; petitioner
is H-1B dependent; requested validity period exceeds contract coverage
(expect approval shortened to contract period); "benching" history (Project
Firewall's top finding). For extensions: work-location change outside the
LCA's MSA is a material change requiring an amended petition BEFORE the move
(Matter of Simeio Solutions, still enforced) — an unamended move is both a
denial ground and an FDNS/DOL finding.

**Sources:** immpolicytracking.org; fragomen.com; cilawgroup.com;
americanimmigrationcouncil.org; h1bdatahub.com. Confidence: high.

## 4. Ability to pay / employer viability

**USCIS wording:** not a freestanding H-1B statutory requirement (unlike
I-140), but reached through: "the evidence does not establish that a bona
fide job offer exists" / "that the petitioner has the ability to pay the
proffered wage" / "that the petitioner is a bona fide, operating business."
The LCA obligation (pay required wage from day 1, no benching) gives the
hook.

**Curing/preventive evidence:** federal tax returns, audited financials or
bank statements, payroll records (W-3/941s), FEIN, business licenses, lease,
client contracts/funding evidence (term sheets, wire records for startups),
org chart with headcount, photos of premises, website. Payroll must be cash
wage meeting the LCA wage — equity cannot substitute.

**Deterministic risk signals:** employer entity age under 2 years; employee
count below ~10-25; annual revenue below proffered wage x H-1B headcount;
net income negative with no capital evidence; residential address as
business address; no online footprint (site-visit trigger per FDNS targeting
criteria); beneficiary-owner at 50%+ (allowed since 2025 rule but capped at
18-month validity for initial + first extension); wage at exactly the
prevailing wage floor at a tiny company.

**Sources:** rnlawgroup.com; tryalma.com; immi-usa.com; dewit.law.
Confidence: medium-high (practice-based; H-1B ability-to-pay is adjudicator
practice, not codified).

## 5. Maintenance of status (extensions/amendments; ~5% of RFEs, high stakes)

**USCIS wording:** "Submit evidence that the beneficiary has maintained
valid H-1B status" / late filing: extension "must be filed before the
expiration of the current period of stay"; late filing excusable only for
"extraordinary circumstances beyond the control of the petitioner or
beneficiary" (8 CFR 214.1(c)(4)); a status gap risks unlawful presence and a
consular-processing-only approval (I-129 approved, I-94 extension denied).

**Curing/preventive evidence:** all pay stubs for the current validity
period (officers sample the last 2-6 months; gaps = benching inference);
W-2s; current I-94, prior approval notices; leave documentation (FMLA,
maternity) explaining pay gaps; for late filing: contemporaneous evidence of
the extraordinary circumstance. The codified deference rule helps only if no
material change — flag any change in role, wage, worksite, or hours.

**Deterministic risk signals:** filing date after I-94 expiry (near-certain
denial of the extension-of-stay component); pay-stub gap over 30 days within
the validity period; W-2 wages below LCA required wage x employment
fraction; employer changed since last approval without an intervening
transfer petition; over 240 days since I-94 expiry with petition pending
(work-authorization lapse); job title/SOC/worksite on new filing differing
from prior approval (kills deference, invites full re-adjudication).

**Sources:** uscis.gov H-1B FAQs; h1btrack.com; immi-usa.com;
lighthousehq.com. Confidence: high.

## 6. Export control / deemed export (Part 6, I-129)

**USCIS wording:** not an RFE ground per se — Part 6 is a petitioner
attestation under penalty of perjury: petitioner has reviewed EAR (15 CFR
770-774) and ITAR (22 CFR 120-130) and either (a) no license is required to
release controlled technology to the beneficiary, or (b) a license is
required and access will be PREVENTED until licensed. Unanswered or
inconsistent Part 6 = rejection or RFE; a false attestation = fraud referral
plus BIS/DDTC exposure.

**Curing/preventive evidence:** documented export-control classification
review (ECCN determination) predating filing; technology control plan (TCP)
if box 2 is checked; for universities: fundamental-research exclusion memo.

**Deterministic risk signals:** beneficiary nationality = China (Country
Group D:1/D:5 — deemed-export license far more likely; BIS has
denial-by-policy postures on semiconductors, AI, aerospace); employer
industry in EAR-sensitive sectors (semiconductors, EDA tools, telecom,
encryption beyond mass-market, aerospace, biotech, quantum, advanced
computing/AI chips); employer or parent on the BIS Entity List or Chinese
military-civil-fusion lists (check the parent name — a US subsidiary of a
listed Chinese parent has near-certain complications); job duties mention
source-code access to controlled tech; Part 6 box 2 checked with no TCP on
file. For a Chinese parent's US subsidiary: the combination (Chinese
national beneficiary + Chinese parent + sensitive tech) also feeds
FDNS/national-security vetting (section 8).

**Sources:** nafsa.org; morganlewis.com; fordham.edu;
researchservices.cornell.edu. Confidence: high on mechanics, medium on 2026
China-specific enforcement intensity.

## 7. Weighted selection era (FY2027 cap, effective 2026-02-27)

**Mechanics:** entries per registration by OEWS wage level of the offered
wage: Level IV = 4, III = 3, II = 2, I = 1 (below-Level-I permitted with
alternative wage-source documentation, 1 entry). Registration must state
offered wage, SOC code, wage level, worksite(s). FY2027: registration
2026-03-04 to 03-19; petitions 2026-04-01 to 06-30.

**New denial/revocation ground:** "process integrity" — USCIS will deny or
revoke where registration data appears engineered to inflate selection odds.
Petition-stage consistency is adjudicated: LCA + I-129 wage, SOC, and
worksite must match the registration; an actual offered wage lower than the
registered wage level = denial; a SOC swap between registration and petition
= denial/revocation; a worksite moved to a lower-wage MSA after registration
= flag.

**Deterministic risk signals:** petition wage below the wage floor of the
registered OEWS level for the SOC/MSA; SOC on LCA differing from the
registration; worksite MSA differing from the registration; wage set exactly
at a level boundary (gaming appearance); Level IV claimed for a title
mapping to entry-level duties; Level I registration (selection-probability
collapse — a business-risk signal). Extension/amendment petitions are
unaffected by weighting but inherit the wage-consistency scrutiny culture.

**Sources:** federalregister.gov 2025-23853; gtlaw.com; ogletree.com;
fisherphillips.com. Confidence: high.

## 8. Fraud / FDNS site-visit exposure (incl. foreign-parent petitioners)

**USCIS wording:** the modernization rule codified inspection authority (any
worksite incl. third-party); "refusal or failure to fully cooperate in an
inspection or verification may result in denial or revocation."
NOID/revocation wording: "information obtained during a site visit indicates
the beneficiary is not employed in the capacity specified in the petition."

**What officers verify:** beneficiary physically at the LCA worksite doing
the petition's duties; payroll wage = LCA; supervisor and beneficiary
interviews consistent with the I-129; signage/premises exist; for
third-party sites, the end-client confirms the project.

**Published targeting criteria:** H-1B dependent employers; third-party
placement employers; employers "whose business cannot be verified through
publicly available information." Chinese-parent US-subsidiary compounding:
newly formed subsidiary, parent funds payroll (bona-fide-employer and
ability-to-pay questions), officers/directors overlap with the parent, US
office small or virtual, beneficiary previously employed by the parent
abroad (looks L-1-shaped — expect "why H-1B" scrutiny plus section 6 export
flags). Project Firewall adds DOL-side exposure: back wages, CMPs, debarment
(willful violator = barred from new H-1Bs 2-3 years).

**Curing/preventive:** front-desk site-visit protocol; public folder with
LCA, I-129 copy, pay records; consistent duty descriptions across
I-129/LCA/offer letter/client SOW; verifiable web presence; documented
intercompany services agreement if the parent funds the subsidiary.

**Deterministic risk signals:** parent company incorporated abroad (esp.
China); subsidiary age under 2 years; H-1B share of US headcount at or above
dependency thresholds; worksite differing from petitioner premises; no
company website or unverifiable address; payroll funded by intercompany
transfers; prior RFE/denial/revocation history for the FEIN; beneficiary's
prior employer = the foreign parent.

**Sources:** rnlawgroup.com; murthy.com; pryorcashman.com; hklaw.com
(Project Firewall); epi.org; visaverge.com (2026 debarments). Confidence:
high on mechanics/targeting; medium on foreign-parent-specific triggers
(synthesized from FDNS criteria + practitioner guidance, not a published
USCIS list).

## Cross-cutting notes for the rules engine

- Extensions get codified deference: diff the current filing vs the prior
  approval on {SOC, title, wage, wage level, worksite MSA, hours, employer
  FEIN}; any diff = fresh-adjudication risk.
- Highest-weight composite signals (multiple grounds fire at once): {wage
  level I}, {third-party worksite}, {employer age under 2y AND parent
  abroad}, {Chinese national AND sensitive-tech industry}, {degree-field
  string mismatch}, {pay-stub gap}.
- The AAO non-precedent decisions database is the primary-source stream for
  2025-26 dismissal language; the most-litigated ground remains specialty
  occupation criterion 1. Confidence: medium (doctrine above comes from rule
  text and firm analyses, not individually extracted AAO decisions).
