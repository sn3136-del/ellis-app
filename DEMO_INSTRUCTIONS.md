# Ellis demo for Trip.com

Ellis prepares and files tourist visa and arrival card applications end to end.
This demo build is ready to run: all provider credentials are included.

The demo ships as **two downloads** from the same repository:

1. **This build (the `main` branch ZIP)** covers:
   - **Germany** (China passport): consular route. Ellis asks the required
     questions, fills the official Schengen form, adds your photo and
     signature, and produces one combined application packet PDF with
     instructions for the consulate visit.
   - **Singapore**: SG Arrival Card (visa free for Chinese passports), filled
     live on the official ICA portal while you watch.
2. **The Vietnam build** (select the `vietnam-edition` branch on GitHub, then
   Code, then Download ZIP): the official Vietnam e-visa portal, filled live
   by Ellis while you watch.

Run one build at a time (stop one before starting the other); the steps below
are the same for both.

## Before you start (one-time, about 5 minutes)

You need a Mac (macOS 13 or newer) with internet access. Install these two
things first; both are normal Mac installers, no admin console work needed:

1. **Node.js 20 or newer**: go to https://nodejs.org and download the LTS
   macOS installer (.pkg), then run it.
2. **Python 3.12 or newer**: go to https://www.python.org/downloads/ and
   download the macOS installer (.pkg), then run it.

Nothing else is required: no Xcode, no git, no Docker, no accounts, no API
keys. If either tool is missing or too old, the launcher stops with a clear
message telling you which one to install.

## Run it

1. Download the repository ZIP (green "Code" button, then "Download ZIP"),
   double-click the ZIP to unpack it, and open Terminal in the unpacked
   folder (right-click the folder, then Services, then "New Terminal at
   Folder", or `cd` to it).
2. Run:

   ```bash
   npm run demo
   ```

   The first run installs dependencies (a few minutes). When it finishes,
   Ellis opens at **http://127.0.0.1:5199** in your browser.

3. Click "Start your application" and follow the flow: upload a passport photo
   page, answer the questions, pick a destination (Germany, Vietnam, or
   Singapore), and let Ellis work. During live portal filling you can watch
   the official page and scroll it, but not click; Ellis pauses and asks you
   whenever the portal needs something only you can answer.

To stop Ellis:

```bash
npm run ellis:stop
```

Run `npm run demo` again any time; setup steps are skipped once done.

## Notes

- Credentials for the AI, OCR, and browser providers are included in this
  private repository and billed to the owner. Please do not share the
  repository or copy the credential files.
- Ellis never solves CAPTCHAs, never invents an answer, and the final
  submission or payment click on a government portal always stays with the
  applicant.
- Everything runs locally on your machine except the provider calls (AI,
  OCR, secure browser sessions).

## Troubleshooting

- "port 8000 already in use": run `npm run ellis:stop`, then `npm run demo`.
- A blank live view during portal filling: click "Refresh view".
- Anything else: send the contents of
  `~/Library/Application Support/Ellis/logs/` to the repository owner.
