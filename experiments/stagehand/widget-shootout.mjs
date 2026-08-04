// The decisive test: the four widgets that beat Ellis's hand-written recon.
//
// Ellis's own observer (_EXTRACT_JS) records what an element LOOKS like. It
// typed a mat-select, an autocomplete and a readonly datepicker all as
// "select", and gave three radios one group-wide selector. Each of those cost
// a live applicant's run tonight. The question here is whether observe() can
// say what each one IS and HOW to operate it.
import { makeStagehand } from "./ellis-client.mjs";
import http from "node:http";

const FIXTURES = {
  // Malaysia MDAC. Recon: type "text", placeholder DD/MM/YYYY. Truth: readonly,
  // driven by a picker. Ellis typed at it 5x/~9s each and killed the run.
  "malaysia_dob_readonly_picker": `
    <label for="dob">Date of Birth :</label>
    <input id="dob" type="text" placeholder="DD/MM/YYYY" readonly>
    <div id="cal" style="display:none;position:absolute;width:300px;height:220px;
         background:#eee;z-index:99">calendar</div>
    <script>
      const el=document.getElementById('dob'),c=document.getElementById('cal');
      for(const e of ['focus','click']) el.addEventListener(e,()=>c.style.display='block');
    </script>`,

  // Thailand TDAC nationality. This one WORKS today (~3.4s). The control.
  "thailand_nationality_autocomplete": `
    <div class="mat-mdc-form-field"><label>Nationality/Citizenship</label>
      <input id="nat" role="combobox" aria-autocomplete="list" placeholder="Select or enter">
    </div>
    <div class="cdk-overlay-container"><div class="mat-mdc-autocomplete-panel" role="listbox" hidden>
      <mat-option role="option">CHN : CHINESE</mat-option>
      <mat-option role="option">CHL : CHILEAN</mat-option>
    </div></div>
    <script>
      const i=document.getElementById('nat'),p=document.querySelector('.mat-mdc-autocomplete-panel');
      i.addEventListener('focus',()=>p.hidden=false);
    </script>`,

  // Thailand TDAC date of birth. Same ARIA as the nationality box, but reads
  // ZERO options at run time. Root cause still unknown after two refuted
  // hypotheses — this is the widget the whole night was spent on.
  "thailand_dob_three_boxes": `
    <label>Date of Birth</label>
    <input id="mat-input-18" role="combobox" aria-autocomplete="list" placeholder="yyyy">
    <input id="mat-input-19" role="combobox" aria-autocomplete="list" placeholder="mm">
    <input id="mat-input-20" role="combobox" aria-autocomplete="list" placeholder="dd">`,

  // Thailand TDAC gender. Recon gave all three the SAME selector, because every
  // radio in a group shares its name. Ellis asked for MALE and clicked FEMALE.
  "thailand_gender_shared_name": `
    <label>Gender</label>
    <mat-radio-group id="mat-radio-group-0">
      <input type="radio" name="mat-radio-group-0" id="g1" value="cXhw5LLgYCIM0sCqCyP+2A==">
      <label for="g1">FEMALE</label>
      <input type="radio" name="mat-radio-group-0" id="g2" value="ar2hFYafLjtx0JUbCesHKg==">
      <label for="g2">MALE</label>
      <input type="radio" name="mat-radio-group-0" id="g3" value="suqCBmB0Tsa1hv4wD1xkCw==">
      <label for="g3">UNDEFINED</label>
    </mat-radio-group>`,
};

const ASKS = {
  malaysia_dob_readonly_picker: "the date of birth field",
  thailand_nationality_autocomplete: "the nationality field",
  thailand_dob_three_boxes: "every box that makes up the date of birth",
  thailand_gender_shared_name: "each gender option that can be selected",
};

const state = { html: "" };
const srv = http.createServer((_q, res) => {
  res.writeHead(200, { "content-type": "text/html" });
  res.end(`<!doctype html><html><body>${state.html}</body></html>`);
}).listen(0);
const url = `http://127.0.0.1:${srv.address().port}/`;

const sh = makeStagehand();
await sh.init();
const [page] = await sh.context.pages();

for (const [name, html] of Object.entries(FIXTURES)) {
  state.html = html;
  await page.goto(url + "?" + Date.now());
  const t0 = Date.now();
  let found;
  try {
    found = await sh.observe(ASKS[name]);
  } catch (e) {
    found = [{ error: e.message.slice(0, 120) }];
  }
  const ms = Date.now() - t0;
  console.log(`\n=== ${name}  (${ms}ms) ===`);
  for (const f of found) {
    console.log(`  method=${String(f.method).padEnd(12)} selector=${f.selector}`);
    if (f.arguments?.length) console.log(`     args: ${JSON.stringify(f.arguments)}`);
    if (f.description) console.log(`     "${f.description.slice(0, 110)}"`);
    if (f.error) console.log(`     ERROR ${f.error}`);
  }
}

await sh.close();
srv.close();
