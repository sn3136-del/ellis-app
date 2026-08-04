// Observing EVERY step of a multi-step wizard — the single biggest coverage
// gap in Ellis today. Thailand's TDAC is a three-step wizard and its adapter
// has ZERO nodes for steps 2 and 3, because recon never navigates past step 1.
//
// Safety rules, enforced here and not merely intended:
//   1. NEVER press a final submit. A build must not file anything.
//   2. NEVER type applicant data into a government form to get past a step.
//      If a step will not advance without data, that is reported honestly and
//      the walk stops — attended observation (the applicant drives, Ellis
//      watches) is the sanctioned way to see what is behind it.
//   3. An advance counts only when the CONTROLS change. A URL that changed
//      while the form did not is not a new step.
//   4. Hard step cap.
import { makeStagehand } from "./ellis-client.mjs";

const MAX_STEPS = 6;

// Words that mean "this files the application". Never clicked by the walk.
const SUBMIT_WORDS =
  /\b(submit|confirm|pay|finish|complete|declare|agree and|send|apply now)\b/i;

/** A stable fingerprint of the controls on screen. Two steps of an SPA wizard
 *  share a URL; they do not share this. */
async function controlFingerprint(page) {
  return page.evaluate(`(() => {
    const els = document.querySelectorAll(
      'input:not([type=hidden]), select, textarea, [role="combobox"], mat-select');
    return Array.from(els).map((e) =>
      [e.tagName, e.getAttribute('formcontrolname') || e.name || e.id || '',
       e.getAttribute('placeholder') || ''].join('|')).join('\\n');
  })()`);
}

/** The control that advances the wizard, or null. Refuses anything that reads
 *  like a final submission. */
async function findAdvance(sh, page) {
  const candidates = await page.evaluate(`(() => {
    const out = [];
    for (const b of document.querySelectorAll('button, input[type=submit], a[role=button]')) {
      const t = (b.innerText || b.value || '').replace(/\\s+/g, ' ').trim();
      if (!t) continue;
      const r = b.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) continue;
      out.push({ text: t.slice(0, 40), disabled: !!b.disabled });
    }
    return out;
  })()`);
  return candidates;
}

export async function walkWizard(url, { maxSteps = MAX_STEPS } = {}) {
  const sh = makeStagehand();
  await sh.init();
  const [page] = await sh.context.pages();
  await page.goto(url);

  const steps = [];
  let previous = null;

  for (let i = 0; i < maxSteps; i++) {
    const fingerprint = await controlFingerprint(page);
    if (fingerprint === previous) {
      steps.push({ step: i + 1, stopped: "the form did not change — not a new step" });
      break;
    }
    previous = fingerprint;

    const controls = fingerprint ? fingerprint.split("\n").length : 0;
    const buttons = await findAdvance(sh, page);
    steps.push({ step: i + 1, controls, buttons });

    // Rule 1: refuse to click anything that files the application.
    const advance = buttons.find(
      (b) => !SUBMIT_WORDS.test(b.text) && /continue|next|proceed|forward/i.test(b.text));
    const submitish = buttons.find((b) => SUBMIT_WORDS.test(b.text));
    if (!advance) {
      steps[steps.length - 1].stopped = submitish
        ? `reached the submission step ("${submitish.text}") — a build never presses it`
        : "no advance control on this step";
      break;
    }
    // Rule 2: a disabled advance means the portal wants data first, and a
    // build does not put data on a government form.
    if (advance.disabled) {
      steps[steps.length - 1].stopped =
        `"${advance.text}" is disabled until the step is filled — needs attended observation`;
      break;
    }

    await sh.act(`click the "${advance.text}" button`);
    await new Promise((r) => setTimeout(r, 1200));
  }

  await sh.close();
  return steps;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const url = process.argv[2];
  if (!url) { console.error("usage: node wizard-walk.mjs <url>"); process.exit(2); }
  console.log(JSON.stringify(await walkWizard(url), null, 2));
}
