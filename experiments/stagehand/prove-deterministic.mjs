// Can Stagehand still do plain, deterministic browser actions — no model in
// the loop? Ellis's runtime depends on exactly this: click / type / select /
// upload against a recorded selector, with nothing inferred.
import { makeStagehand } from "./ellis-client.mjs";
import http from "node:http";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const HTML = `<!doctype html><html><body>
  <input id="name" type="text" placeholder="Full name">
  <select id="country"><option value="">--</option>
    <option value="CHN">China</option><option value="THA">Thailand</option></select>
  <input id="agree" type="checkbox">
  <input id="doc" type="file">
  <input type="radio" name="sex" id="sx_f" value="F"><label for="sx_f">FEMALE</label>
  <input type="radio" name="sex" id="sx_m" value="M"><label for="sx_m">MALE</label>
  <button id="go">Continue</button>
  <div id="log"></div>
  <script>
    document.getElementById('go').addEventListener('click',()=>{
      document.getElementById('log').textContent='clicked';});
    document.getElementById('doc').addEventListener('change',e=>{
      document.getElementById('log').textContent='file:'+e.target.files[0].name;});
  </script>
</body></html>`;

const srv = http.createServer((_q,res)=>{res.writeHead(200,{"content-type":"text/html"});res.end(HTML);}).listen(0);
const url = `http://127.0.0.1:${srv.address().port}/`;

const tmp = path.join(os.tmpdir(), "ellis-passport.txt");
fs.writeFileSync(tmp, "not a real passport");

const sh = makeStagehand();
await sh.init();
const [page] = await sh.context.pages();
await page.goto(url);

const results = {};
const t0 = Date.now();

await page.locator("#name").fill("XIANGWEI CAO");
results.fill = await page.evaluate("document.getElementById('name').value");

await page.locator("#country").selectOption("THA");
results.selectOption = await page.evaluate("document.getElementById('country').value");

await page.locator("#sx_m").click();
results.radioClick = await page.evaluate("document.getElementById('sx_m').checked");

await page.locator("#agree").click();
results.checkboxClick = await page.evaluate("document.getElementById('agree').checked");

await page.locator("#doc").setInputFiles(tmp);
results.setInputFiles = await page.evaluate("document.getElementById('log').textContent");

await page.locator("#go").click();
results.buttonClick = await page.evaluate("document.getElementById('log').textContent");

const ms = Date.now() - t0;
console.log(JSON.stringify(results, null, 2));
console.log(`\nsix deterministic actions in ${ms}ms, ZERO model calls`);
console.log("model calls made:", sh.metrics ? JSON.stringify(sh.metrics) : "n/a");

await sh.close(); srv.close(); fs.unlinkSync(tmp);
