# Maximal automation spec — "as close to 100% as the law allows"

The goal, stated precisely: for every route, Ellis automates every step up to
each irreducible human act, and reduces that act to a SINGLE TAP in a secure
window that is already pre-driven to the exact point of the act. The human
never types a field, never navigates, never hunts for a button. They log in,
glance at a review, and tap.

The irreducible acts (see AUTOMATION_ROADMAP.md) are: login, signature,
payment, submission, CAPTCHA, in-person biometrics. Everything else is Ellis.

## The measure: "taps to done" per route

We make this concrete and testable. For each route, count the human taps.

| Route | Human acts that legally remain | Target taps |
|---|---|---|
| H1B LCA (FLAG) | login+OTP, review, submit | 3 |
| H1B I-129 (myUSCIS) | login+OTP, e-sign, pay, submit | 4 |
| H1B registration | login+OTP, pay, submit | 3 |
| Tourist e-visa (portal) | login (if any), pay, submit, CAPTCHA if shown | 2-4 |
| US appointment | login, pick date, pay MRV, confirm | 2-4 |
| Schengen via VFS agent portal | (Ellis-as-agent) confirm/pay | 1-2 |

Anything above these numbers is Ellis leaving work on the table; anything
below is Ellis doing a human's legal act, which it must never do.

## The "filing cockpit" (the product surface that delivers it)

One screen per filing/appointment, showing three things and nothing else:
1. **Everything Ellis has prepared** — every field filled, from case data,
   each traceable to its source; the computed wage level; the assembled
   documents; the drafted narrative.
2. **What's still missing** — stated honestly, each with the one input needed.
3. **The single action** — "Open secure window and finish" — which launches
   the applicant's own Browserbase/Steel session, ALREADY driven (login page
   presented, every field Ellis can fill already filled behind the login once
   the human authenticates) to the exact point of the human's act.

The cockpit is the same shape for tourist and H1B; only the pre-stage differs.

## Build items that push each route to its tap-target (no user input)

### H1B petitioner side
- **FLAG LCA (ETA-9035E) fill**: every field from the wage/SOC/worksite data,
  prevailing wage from the computed OFLC level, attestations as declarations.
- **ETA-9141 PWD prefill**: the whole prevailing-wage request from job+SOC.
- **myUSCIS I-129 / registration fill logic**: reuse the verified field maps;
  the adapter goes live only after the attended session, but the fill is built.
- **pay.gov fee engine**: compute the exact fee stack for THIS petition
  (base + ACWIA + fraud + asylum + premium) and present the exact amount the
  human will pay — never pay it.
- **Org-account bulk spreadsheet generator**: the 2,500-beneficiary template
  filled from HRIS/case data, validated, ready for the human's upload.
- **RFE response assembler**: packet + exhibit index keyed to each RFE issue +
  AI-drafted narrative (attorney reviews, human uploads).

### Beneficiary / tourist side
- **DS-160 + consular pre-stage**: fill everything, prep the MRV fee, save the
  profile, so the consular step is login + pay + confirm.
- **Appointment cockpit**: show legitimately-available dates, let the user pick,
  pre-drive the official booking to the confirm point (US: human confirms;
  Schengen via VFS agent portal: Ellis-as-agent completes where accredited).
- **Expedite/emergency request assembly** for the US paid-expedite path.

## What stays a personal act, enforced in code (never "automated to 100%")

The cockpit's single button opens the secure window; it does NOT auto-click
login, sign, pay, submit, or a CAPTCHA. Those remain the human's tap. This is
enforced by the same execution-class + handoff machinery already in place:
the runtime physically has no path to perform them, and every such point is a
typed handoff, not an automated step. "As close to 100%" is the pre-stage
being total; the last tap being human is the law, and the product's integrity.
