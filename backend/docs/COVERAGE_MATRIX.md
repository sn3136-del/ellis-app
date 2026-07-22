# Country Adapter Coverage Matrix

Global support means **individually implemented, versioned, tested, and
monitored adapters** — not a generic agent. Honest status:

| Adapter | Country | Visa | Status | Production enabled | Tested against |
|---|---|---|---|---|---|
| `mockland-tourist-v1` | Mockland | tourist | mock | ❌ | in-process mock portal |
| `vietnam-evisa-tourist-v1` | Vietnam | tourist (e-Visa) | tested | ❌ | in-process mock portal |

- **mock** — demonstration only; not a real portal.
- **tested** — real portal *configuration* (domains, field/document mappings,
  fee-discovery, human checkpoints, confirmation extraction) implemented and
  validated, driver exercised against the mock. **Not** run against the live
  portal; `production_enabled=False`. The validator refuses to enable it without
  `production_approved`.

No adapter is production-approved. `demo_e2e.py` and the test suite run the full
flow against the mock only. No live payment or real government submission runs
anywhere in this build.

## Prioritized adapter backlog (official online e-visa portals first)

Preference order favors portals with an official applicant/agent online workflow
and no CAPTCHA-bypass requirement:

1. **Vietnam e-Visa** — `evisa.gov.vn` *(configuration implemented; needs live
   Playwright driver + legal review to activate)*
2. India e-Visa — `indianvisaonline.gov.in` (where restored for the nationality)
3. Turkey e-Visa — `evisa.gov.tr`
4. Egypt e-Visa — `visa2egypt.gov.eg`
5. Kenya eTA — `etakenya.go.ke`
6. Sri Lanka ETA — `eta.gov.lk`
7. Cambodia e-Visa — `evisa.gov.kh`
8. Saudi Arabia e-Visa — `visa.visitsaudi.com`

Embassy/consular portals (US CEAC, Schengen VACs, UK) require in-person
biometrics and frequently prohibit automation; those remain applicant-driven at
the counter and are lower priority for automated adapters.

## Activation checklist per adapter

1. Legal review of the portal's terms for authorized-agent automation.
2. Implement the Playwright driver against the live DOM (replace the mock driver).
3. Verify selectors on the live portal (manual, authorized).
4. Add real-adapter fixture tests (recorded non-sensitive fixtures where permitted).
5. `production_approval_status = 'production_approved'`.
6. `production_enabled = True` (the contract validator then permits it).
7. Configure conservative rate limits; enable monitoring + alerting.
