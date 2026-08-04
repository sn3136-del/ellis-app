// Stagehand wired to the SAME model Ellis already uses (Moonshot/Kimi), so this
// experiment introduces no new vendor and no new credential.
//
// Doctrine carried over from Ellis, enforced here rather than assumed:
//   - CAPTCHA auto-solve is OFF. A challenge asks whether a human is present and
//     the honest answer is to fetch one; Ellis never answers it itself.
//   - The autonomous agent() surface is not used. Ellis owns its own loop.
//   - This file is BUILD-TIME tooling. Nothing here runs during an applicant's run.
import { Stagehand, CustomOpenAIClient } from "@browserbasehq/stagehand";
import OpenAI from "openai";
import { execFileSync } from "node:child_process";

const PY = "/Users/sammynawaly/Documents/ellis-app/backend/.venv/bin/python";

/** Read Ellis's own settings rather than duplicating credentials here. */
export function ellisSettings() {
  const out = execFileSync(PY, ["-c", `
import json
from app.config import settings
s = settings()
print(json.dumps({"base": s.kimi_base_url, "model": s.kimi_model,
                  "key": s.moonshot_api_key}))`],
    { cwd: "/Users/sammynawaly/Documents/ellis-app/backend", encoding: "utf8" });
  return JSON.parse(out.trim().split("\n").pop());
}

// Moonshot is OpenAI-compatible but not identical: kimi-k3 accepts exactly one
// top_p (0.95) and rejects the value Stagehand sends. Normalise at the
// transport so neither side has to know about the other.
function moonshotFetch(base) {
  return async (url, init = {}) => {
    if (init.body && typeof init.body === "string") {
      try {
        const body = JSON.parse(init.body);
        if ("top_p" in body) body.top_p = 0.95;
        init = { ...init, body: JSON.stringify(body) };
      } catch { /* not JSON: pass through untouched */ }
    }
    return base(url, init);
  };
}

export function makeStagehand({ headless = true, env = "LOCAL" } = {}) {
  const s = ellisSettings();
  const llmClient = new CustomOpenAIClient({
    modelName: s.model,
    client: new OpenAI({
      apiKey: s.key, baseURL: s.base,
      fetch: moonshotFetch(globalThis.fetch),
    }),
  });
  return new Stagehand({
    env,
    llmClient,
    verbose: 0,
    // Explicitly off — see the doctrine note above.
    selfHeal: false,
    localBrowserLaunchOptions: { headless },
  });
}
