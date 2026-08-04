// A three-step wizard shaped like TDAC: one URL, controls swap per step,
// a final step whose button SUBMITS. Two variants: an open Continue, and a
// Continue disabled until the step is filled (which is what TDAC does).
import http from "node:http";
import { walkWizard } from "./wizard-walk.mjs";

const page = (disabled) => `<!doctype html><html><body>
<div id="app"></div>
<script>
  const STEPS = [
    {name:"Personal Information", fields:[["familyName","Family Name"],["firstName","First Name"],["occupation","Occupation"]], btn:"Continue"},
    {name:"Trip & Accommodation", fields:[["arrDate","Arrival Date"],["flightNo","Flight No"],["accAddress","Address"]], btn:"Continue"},
    {name:"Health Declaration",  fields:[["fever","Fever"],["country14","Countries visited"]], btn:"Submit"},
  ];
  let i = 0;
  function render() {
    const s = STEPS[i];
    document.getElementById('app').innerHTML =
      '<h2>' + s.name + '</h2>' +
      s.fields.map(([n,l]) => '<label>'+l+'</label><input formcontrolname="'+n+'" placeholder="'+l+'">').join('') +
      '<button id="go" ' + (${disabled} && i < 2 ? 'disabled' : '') + '>' + s.btn + '</button>';
    document.getElementById('go').onclick = () => {
      if (STEPS[i].btn === 'Submit') { document.body.innerHTML = '<h1>FILED</h1>'; return; }
      i++; render();
    };
  }
  render();
</script></body></html>`;

for (const [label, disabled] of [["Continue always enabled", false],
                                 ["Continue disabled until filled (TDAC-like)", true]]) {
  const srv = http.createServer((_q,res)=>{res.writeHead(200,{"content-type":"text/html"});res.end(page(disabled));}).listen(0);
  const url = `http://127.0.0.1:${srv.address().port}/`;
  console.log(`\n=== ${label} ===`);
  const steps = await walkWizard(url);
  for (const s of steps) {
    const btns = (s.buttons||[]).map(b=>b.text+(b.disabled?"(disabled)":"")).join(", ");
    console.log(`  step ${s.step}: ${s.controls} controls | buttons: ${btns}`);
    if (s.stopped) console.log(`     STOP: ${s.stopped}`);
  }
  srv.close();
}
