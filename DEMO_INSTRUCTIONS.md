# Ellis demo for Trip.com

Ellis prepares and files tourist visa and arrival card applications end to end.
This demo build is ready to run: all provider credentials are included, and the
destination picker is limited to the three demo routes.

- **Germany** (China passport): consular route. Ellis asks the required
  questions, fills the official Schengen form, adds your photo and signature,
  and produces one combined application packet PDF with instructions for the
  consulate visit.
- **Vietnam**: official e-visa portal, filled live by Ellis while you watch.
- **Singapore**: SG Arrival Card (visa free for Chinese passports), filled live
  on the official ICA portal while you watch.

## Requirements

- A Mac (macOS 13 or newer)
- Node.js 20 or newer: https://nodejs.org
- Python 3.12 or newer: https://python.org
- Internet access

## Run it

1. Download the repository (green "Code" button, then "Download ZIP", or
   `git clone`), and open a terminal in the project folder.
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
