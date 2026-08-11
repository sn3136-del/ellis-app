# US + Schengen appointment booking — the compliant design

Mechanic-level basis for the Trip.com appointment feature, from the 2026-08-10
deep dive. The recurring rule: an authorized human may operate the official
site; software may prepare and organize everything around that, but automated
slot-search/booking (bots/scripts/extensions) is forbidden and gets the
APPLICANT's appointment and visa cancelled. So Ellis pre-stages to the edge
and the authorized human takes the booking action.

## US B1/B2 (China)

- **System:** usvisascheduling.com — CGI Federal's Atlas360 platform (through
  2032), for Beijing / Shanghai / Guangzhou / Shenyang / Wuhan. NOT
  ustraveldocs (legacy), NOT ais.usvisa-info (GDIT/India).
- **MRV fee:** $185, paid via China CITIC Bank (Smart Counter / ATM / online
  debit / cash) with a UnionPay card. Ellis reminds and tracks; the human pays.
- **The sanctioned batch path — official GROUP APPOINTMENTS.** Built for "10 or
  more travelling together"; "tour groups" are a named qualifying example and
  "families and relatives" are explicitly excluded. A human GROUP COORDINATOR
  submits a group request (per member: passport #, 10-digit DS-160
  confirmation, MRV receipt), the consulate approves, then the coordinator
  books each member's slot. Verbatim: "Our agents cannot schedule group
  appointments" and "the applicant must complete his or her own Form DS-160."
  This is the US analog to the VFS accredited-agent portal — a purpose-built,
  non-automation channel a Trip.com tour group qualifies for.
- **Lifecycle (tagged):** triage waiver/EVUS eligibility [Ellis] → DS-160 per
  applicant at ceac.state.gov [Ellis fills, applicant e-signs] → profile /
  group-coordinator profile [mixed] → pay MRV [personal] → schedule / submit
  group request [human coordinator; NEVER automated slot search] → choose
  document-return location [Ellis pre-selects] → attend biometrics + interview
  [personal] → collect passport [agent/courier permitted] → EVUS before travel
  on a 10-yr visa [Ellis assists].
- **How CIBT/Fragomen/Newland Chase do it legitimately:** authorized human
  staff operating accounts manually + software that prepares data and does
  human-facing calendar monitoring. No bots. "Where permitted" is load-bearing;
  some posts bar even manual third-party scheduling — degrade to
  prepare-everything, hand the human the last click.

## Schengen (China applicant)

- **Path:** Art. 45 accredited commercial intermediary + the ESP
  registered-agent channel (VFS RegisteredLogin, TLScontact, BLS). Trip.com's
  accreditation (per member state) is the enabling business asset.
- **The 59-month rule is the crux.** For a REPEAT applicant whose fingerprints
  are in VIS < 59 months (Art. 13(3) reuse), applying to a member state that
  permits submission-without-appearance, the whole journey — form prep,
  appointment via the ESP agent portal, fee, document lodging, passport return
  — sits inside the accredited-agent envelope with NO applicant biometric act.
  That is the near-one-tap route.
- **The immovable gate:** first-time or >59-month applicants MUST appear in
  person for fingerprints (Art. 45 bars the intermediary from collecting them;
  Art. 43 bars ESP/VIS access). Non-delegable.
- **Lifecycle (tagged):** determine member state + ESP/VAC [Ellis] → complete
  national/France-Visas form + assemble docs [Ellis] → applicant signs the
  mandate authorizing the intermediary [personal] → determine VIS biometric
  status [Ellis] → book via ESP registered-agent channel [accredited agent] →
  pay ESP + visa fee [agent; Ellis never enters card data] → lodge at VAC
  [accredited agent] → biometrics [personal + non-delegable if first/>59mo] →
  interview if requested [consulate] → decision + sticker [consulate-only] →
  passport return [agent courier].

## What Ellis builds (the appointment cockpit)

One surface, both routes, honest about the human's remaining act:
1. **Eligibility triage** — waiver/EVUS (US), VIS 59-month status (Schengen):
   decides whether an in-person act is even required.
2. **Pre-stage everything** — DS-160 / national form filled, MRV/visa fee
   computed and the payment channel surfaced, documents assembled and checked,
   document-return preference set, the mandate/authorization drafted.
3. **The booking action, correctly bounded** — US: assemble the group-request
   roster and hand the human coordinator a one-action submit; NEVER an
   automated slot search. Schengen (accredited): drive the ESP registered-agent
   portal to the confirm point where accreditation permits. Availability shown
   from legitimate sources only; never scrape a Cloudflare-walled calendar.
4. **Monitoring** — legitimate wait-time data + case-status; alert the human
   when action is due. Slot "sniping" is out, permanently.

## Hard lines (enforced, never crossed)

No CAPTCHA solving, no automated slot search, no card/bank credential entry, no
biometric delegation, no scraping a bot-protected calendar. The applicant's
appointment and visa are what get cancelled for a violation — so these lines
protect the traveler, not just Ellis.
