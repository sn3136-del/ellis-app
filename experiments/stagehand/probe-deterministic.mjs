import { makeStagehand } from "./ellis-client.mjs";
const sh = makeStagehand();
await sh.init();
const [page] = await sh.context.pages();
const names = new Set();
for (let o = page; o && o !== Object.prototype; o = Object.getPrototypeOf(o))
  Object.getOwnPropertyNames(o).forEach(n => names.add(n));
const want = ["click","fill","type","press","check","uncheck","selectOption",
              "setInputFiles","locator","waitForSelector","getByRole","getByLabel",
              "evaluate","goto","content","keyboard","mouse","frames","$","$$"];
console.log("PAGE deterministic surface:");
for (const w of want) console.log(`  ${w.padEnd(16)} ${typeof page[w]}`);
console.log("\nlocator() surface:");
try {
  const loc = page.locator("body");
  const ln = new Set();
  for (let o = loc; o && o !== Object.prototype; o = Object.getPrototypeOf(o))
    Object.getOwnPropertyNames(o).forEach(n => ln.add(n));
  console.log("  " + ["click","fill","selectOption","setInputFiles","check","isVisible",
    "textContent","count","first","nth"].map(n => `${n}:${typeof loc[n]}`).join("  "));
} catch (e) { console.log("  locator err:", e.message); }
await sh.close();
