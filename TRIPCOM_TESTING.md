# Ellis — Schengen appointment lane (evaluation)

Run on macOS with Node 18+ and Python 3.12+:

```
git clone -b h1b-edition https://github.com/sn3136-del/ellis-app.git
cd ellis-app
npm run ellis:web
```

First run asks for the unlock passphrase (the owner sends it with this
link), then builds its own environments (a minute or two). Open
http://localhost:5199 — the menu shows the Schengen visa lane.

Open it, pick a city (Kanton has dated slots; Shanghai registers on
waiting lists), read the calendar, pick a date and time, press
Schedule, upload the passport photo page, answer the remaining
questions, confirm the statements, type the picture text, and press
Register. The booked card shows the date and time, the consulate's
street address, and a screenshot of the official confirmation page.
Every value entered on the government site is the applicant's own
answer; Ellis never solves the picture check and never picks a date.

Provider keys (Moonshot, Browserbase, Google OCR) travel encrypted in
`backend/secrets.enc` and run on the owner's accounts. On first run the
start command asks for the unlock passphrase — the owner sends it to
you together with this link. Nothing else to configure.

`npm run ellis:stop` shuts everything down.
