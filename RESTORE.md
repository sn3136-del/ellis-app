# Bringing Ellis back

The savepoint to return to is the annotated git tag **`known-good-2026-07-28b`**
(commit `bef31f0`), on this machine and on the private remote
`git@github.com:sn3136-del/ellis-app.git`.

Ask for it in plain words — *"bring back the version of Ellis I told you to
back up"* — or run it yourself:

```bash
./scripts/restore-known-good.sh                      # the tag above
./scripts/restore-known-good.sh known-good-2026-07-27 # an older savepoint
```

## What comes back

| | Restored | Where it lives |
|---|---|---|
| All app code (backend, renderer, Electron, scripts, reference data) | yes | git |
| The released **Vietnam e-Visa route** — 53-node flow, runtime binding, 170 route policies | yes | `backend/route_bundles/vietnam-evisa-v1.json` |
| The verified **US$25** official fee | yes | same bundle |
| The **official dropdown lists** read from evisa.gov.vn | yes | same bundle |

Verified by disaster-recovery drill: cloning from GitHub into an empty database
and importing the bundle yields a route that resolves, with its flow, dropdowns
and fee intact.

## What does NOT come back — and what to do about it

**1. Provider credentials (`backend/.env`).** Deliberately never committed, in
any form. After a restore, recreate it from `backend/.env.example` with the
Kimi (`MOONSHOT_API_KEY`), Browserbase and Google Document AI keys. Without it
Ellis starts but cannot read passports or drive a portal — it fails closed and
says so honestly rather than pretending.

Keep those keys in a password manager. They are the one part of Ellis that no
git tag protects.

**2. Applicant cases and documents.** They live in
`~/Library/Application Support/Ellis/ellis.db` (plus uploaded files), outside
the repo because they hold real passport data. A restore gives you a working
Ellis, not your previous applications. To keep those, back up that directory
separately and encrypted.

## Savepoints

| Tag | What it is |
|---|---|
| `known-good-2026-07-28b` | **current** — everything below, plus the Vietnam route committed as a restorable file |
| `known-good-2026-07-28` | pre-run questions with the portal's real dropdowns, one signature, one-step pay, honest outage reporting |
| `known-good-2026-07-27` | the first end-to-end China→Vietnam flow (fill, declare, advance) |
