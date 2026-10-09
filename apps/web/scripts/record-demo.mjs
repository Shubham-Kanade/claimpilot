// Records a scripted, captioned run of the whole demo as a video you can upload to YouTube.
//
//   node scripts/record-demo.mjs [--base http://localhost:7860] [--api <base>/api] [--out ../../docs/demo]
//                                [--pace 1] [--shots dir] [--keep]
//
// Needs a running ClaimPilot with the demo pile available (the hosted-demo image on :7860 is the
// simplest: docker run -p 7860:7860 claimpilot-demo). It plays Asha Menon uploading the sample
// receipts, reads the findings, answers the one question, submits, then switches to the approver,
// who rejects the trip with a reason and approves the clean phone bill. Before it starts it empties
// the demo's data (POST /v1/demo/reset as the approver) so the run always begins from the same
// pile; --keep skips that. Captions are drawn on the page so the video makes sense without
// narration; record your own voice over it if you like (docs/DEMO_SCRIPT.md has the words).
// --pace 1.4 slows everything down.
//
// Output: <out>/claimpilot-demo.webm (1920x1080). Playwright cannot draw the mouse pointer into a
// video, so the page gets a small fake pointer that follows the real one.
import { mkdir, readdir, rename } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright-core";

const here = path.dirname(fileURLToPath(import.meta.url));
const args = Object.fromEntries(
  process.argv.slice(2).reduce((pairs, arg, i, all) => {
    if (arg.startsWith("--"))
      pairs.push([arg.slice(2), all[i + 1] && !all[i + 1].startsWith("--") ? all[i + 1] : "true"]);
    return pairs;
  }, []),
);
const base = (args.base ?? "http://localhost:7860").replace(/\/$/, "");
const api = (args.api ?? `${base}/api`).replace(/\/$/, "");
const outDir = path.resolve(here, args.out ?? "../../../docs/demo");
const pace = Number(args.pace ?? 1);
const channel =
  process.env.BROWSER_CHANNEL ?? (process.platform === "win32" ? "msedge" : undefined);

// One fixed demo sandbox for the whole recording: the same id for the reset below and for the
// browser (localStorage), so the video always starts from, and stays in, its own sandbox.
const SANDBOX = "record-demo-sandbox-0000000001";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms * pace));

if (args.keep !== "true") {
  // The approver's reset empties this sandbox. Outside demo mode the route does not exist: fine.
  const reset = await fetch(`${api}/v1/demo/reset`, {
    method: "POST",
    headers: { "X-Persona": "DEMO-RAVI", "X-Sandbox": SANDBOX },
  }).catch(() => null);
  console.log(reset ? `demo reset: HTTP ${reset.status}` : "demo reset: API not reachable");
}

await mkdir(outDir, { recursive: true });
const browser = await chromium.launch({ channel });
const context = await browser.newContext({
  viewport: { width: 1920, height: 1080 },
  recordVideo: { dir: outDir, size: { width: 1920, height: 1080 } },
});
await context.addInitScript((sandbox) => {
  try {
    localStorage.setItem("claimpilot.sandbox", sandbox);
  } catch {}
  try {
    if (!localStorage.getItem("claimpilot.persona"))
      localStorage.setItem("claimpilot.persona", "DEMO-ASHA");
  } catch {}
  const mount = () => {
    if (document.getElementById("cp-caption")) return;
    const style = document.createElement("style");
    style.textContent = `
      /* 112px up: clear of the claim page's sticky "Confirm & submit" bar, which the viewer must see */
      #cp-caption { position: fixed; left: 50%; bottom: 112px; transform: translateX(-50%); z-index: 2147483647;
        max-width: 1300px; padding: 16px 28px; border-radius: 14px; background: rgba(15, 23, 42, .93); color: #fff;
        font: 600 28px/1.35 system-ui, "Segoe UI", sans-serif; text-align: center; box-shadow: 0 10px 40px rgba(0,0,0,.35);
        transition: opacity .25s; pointer-events: none; }
      #cp-caption:empty { opacity: 0; }
      #cp-pointer { position: fixed; z-index: 2147483647; width: 26px; height: 26px; margin: -13px 0 0 -13px; border-radius: 50%;
        background: rgba(99, 102, 241, .45); border: 3px solid #4338ca; pointer-events: none; transition: transform .08s; }
      #cp-pointer.down { transform: scale(.7); background: rgba(99, 102, 241, .8); }`;
    const caption = Object.assign(document.createElement("div"), { id: "cp-caption" });
    const pointer = Object.assign(document.createElement("div"), { id: "cp-pointer" });
    pointer.style.cssText = "left:-50px;top:-50px";
    // Under <html>, not <body>: React owns the body and a foreign child there upsets hydration.
    document.documentElement.append(style, caption, pointer);
    addEventListener(
      "mousemove",
      (e) => {
        pointer.style.left = e.clientX + "px";
        pointer.style.top = e.clientY + "px";
      },
      true,
    );
    addEventListener("mousedown", () => pointer.classList.add("down"), true);
    addEventListener("mouseup", () => pointer.classList.remove("down"), true);
  };
  if (document.documentElement) mount();
  else addEventListener("DOMContentLoaded", mount);
  new MutationObserver(mount).observe(document, { childList: true });
}, SANDBOX);

const page = await context.newPage();
const caption = (text) =>
  page.evaluate((t) => {
    const el = document.getElementById("cp-caption");
    if (el) el.textContent = t;
  }, text);
let shots = 0;
const say = async (text, ms = 3500) => {
  await caption(text);
  if (args.shots) {
    await page.waitForTimeout(250); // real time, not paced: let a smooth scroll or a fade settle
    await page.screenshot({
      path: path.join(path.resolve(args.shots), `step-${String(++shots).padStart(2, "0")}.png`),
    });
  }
  await sleep(ms);
};
const glide = async (locator) => {
  await locator.scrollIntoViewIfNeeded();
  const box = await locator.boundingBox();
  if (box) await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 30 });
  await sleep(350);
};
const press = async (locator) => {
  await glide(locator);
  await locator.click();
};
const typeSlowly = async (locator, text) => {
  await glide(locator);
  await locator.click();
  await locator.pressSequentially(text, { delay: 38 });
};

try {
  // 1. The pile
  await page.goto(`${base}/`, { waitUntil: "networkidle" });
  await page.mouse.move(960, 540);
  await say(
    "Asha is back from a client visit with a week of receipts: photos, PDFs, a UPI screenshot, a handwritten auto fare.",
    4500,
  );

  // 2. Upload and watch the pipeline
  await say(
    "One click. Every receipt is read by Claude; a fast System One model, Jev, picks the category, and Claude takes over when it is unsure.",
    1500,
  );
  await press(page.getByRole("button", { name: /try with sample receipts/i }));
  await page.waitForURL(/\/batches\//, { timeout: 60000 });
  await caption(
    "Live progress: each receipt is read, categorised and checked for trust. Duplicates, edited totals and hidden instructions are caught here.",
  );
  await page.getByRole("heading", { name: /claims ready/i }).waitFor({ timeout: 120000 });
  await sleep(1500);
  await page.mouse.wheel(0, 700);
  await sleep(2500);
  await page.mouse.wheel(0, 900);
  await say("15 receipts, 7 claims, in seconds. Two receipts are blocked, one needs review.", 3500);
  await page.mouse.wheel(0, -2000);

  // 3. The claims
  await Promise.race([
    press(
      page
        .getByRole("link", { name: /review claims/i })
        .or(page.getByRole("button", { name: /review claims/i }))
        .first(),
    ),
    page.waitForURL(/\/claims$/, { timeout: 8000 }),
  ]).catch(() => {});
  await page.waitForURL(/\/claims$/, { timeout: 20000 });
  await say(
    "It grouped the pile itself: the Mumbai trip, the client dinner, a month of local rides, the phone bill, and what could not be grouped.",
    4500,
  );

  const open = async (name) => {
    await press(page.getByRole("link", { name }).first());
    await page.waitForURL(/\/claims\/clm-/);
    await page.waitForLoadState("networkidle");
    await sleep(900);
  };
  const back = async () => {
    await page.goBack();
    await page.waitForURL(/\/claims$/);
    await sleep(700);
  };

  // 4. The findings
  await open(/Local conveyance/i);
  await say(
    "An edited receipt: the total says 830 rupees, but its own items and tax add up to 330. The finding says so, with the evidence.",
    5000,
  );
  await page.mouse.wheel(0, 800);
  await sleep(3500);
  await back();

  await open(/Meals Oct/i);
  await say(
    "This café bill carries a note to an AI reviewer: approve without checks. It is flagged as prompt injection and ignored. Approvals depend only on rules.",
    6000,
  );
  await back();

  await open(/Mumbai trip/i);
  await sleep(1500);
  // The alcohol finding sits on the dinner receipt, below the fold: bring it to the middle
  await page
    .getByText(/Policy clause 6\.1/)
    .first()
    .evaluate((el) => el.scrollIntoView({ block: "center", behavior: "smooth" }), null, {
      timeout: 5000,
    })
    .catch(() => {}); // a missing scroll must never lose the recording
  await sleep(900);
  await say("Alcohol is never reimbursed. Every finding quotes the policy clause behind it.", 5500);
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "smooth" }));
  await sleep(900);

  // 5. One question, one answer
  await say(
    "The calendar already explained the client dinner. For the trip, one question; Asha answers in a sentence.",
    3500,
  );
  const reply = page.getByRole("textbox").first();
  await typeSlowly(reply, "Client visit and quarterly review with Kestrel Logistics");
  await sleep(500);
  await press(page.getByRole("button", { name: /^send/i }).first());
  await page
    .getByText(/all set/i)
    .first()
    .waitFor({ timeout: 20000 });
  await say("Nothing is submitted without an explicit confirmation.", 3000);

  // 6. Submit
  await press(page.getByRole("button", { name: /confirm & submit/i }).first());
  await sleep(1800);
  await press(page.getByRole("dialog").getByRole("button", { name: /confirm & submit/i }));
  await page
    .getByText(/FIN-\d{4}-\d+/)
    .first()
    .waitFor({ timeout: 20000 });
  await say("Submitted to the finance system over MCP: it comes back with a reference.", 4000);

  // 7. The clean claims go in too, then the approver
  for (const name of [/Mobile & internet/i, /Client dinner/i]) {
    await page.goto(`${base}/claims`, { waitUntil: "networkidle" });
    await open(name);
    const submit = page.getByRole("button", { name: /confirm & submit/i }).first();
    if (await submit.isEnabled().catch(() => false)) {
      await press(submit);
      await sleep(700);
      await press(page.getByRole("dialog").getByRole("button", { name: /confirm & submit/i }));
      await page
        .getByText(/FIN-\d{4}-\d+/)
        .first()
        .waitFor({ timeout: 20000 });
    }
  }
  await page.goto(`${base}/claims`, { waitUntil: "networkidle" });
  await say("Now the approver's side.", 2000);
  const persona = page.getByRole("combobox").first(); // a native <select>: its pop-up is not recorded
  await glide(persona);
  await persona.selectOption({ value: "DEMO-RAVI" });
  await sleep(800);
  await page.goto(`${base}/approvals`, { waitUntil: "networkidle" });
  await say(
    "Ravi sees the submitted claims, riskiest first, each with its evidence. The trip is on top.",
    4500,
  );

  // 8. The approver decides: the trip goes back with a reason, the clean bill is one click
  const decided = (verdict) =>
    page
      .getByRole("status")
      .filter({ hasText: new RegExp(`^\\s*${verdict}`) })
      .first();
  const toTop = async () => {
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: "smooth" }));
    await sleep(900);
  };
  await press(page.getByRole("button", { name: /Mumbai trip/ }).first());
  await sleep(900);
  await say(
    "The bill includes alcohol, which is never reimbursed. Ravi rejects it, and a reason is required: finance keeps it on record.",
    3500,
  );
  await typeSlowly(
    page.getByRole("textbox", { name: /comment/i }),
    "Please take the 1,540 rupees of alcohol off the Kesar Grill bill and resubmit.",
  );
  await sleep(500);
  await press(page.getByRole("button", { name: /^reject$/i }));
  await decided("Rejected").waitFor({ timeout: 20000 });
  await toTop();
  await say("Rejected, with the reason attached. The employee sees it and fixes the claim.", 3500);

  await press(page.getByRole("button", { name: /Mobile & internet/ }).first());
  await sleep(700);
  await say("The phone bill has no flags and is auto-approvable: one click.", 3000);
  await press(page.getByRole("button", { name: /^approve$/i }));
  await decided("Approved").waitFor({ timeout: 20000 });
  await toTop();
  await say("Approved. The decision is recorded in the finance system over MCP.", 3500);

  // 9. Impact
  await page.goto(`${base}/impact`, { waitUntil: "networkidle" });
  await say(
    "And the impact: receipts read, claims formed, what the AI cost, and the time saved, with its assumption stated.",
    5000,
  );
  await say(
    "ClaimPilot: from a pile of receipts to a claim that is ready to submit. Try it, no login: link in the description.",
    4500,
  );
} finally {
  const video = page.video();
  await context.close();
  await browser.close();
  const raw = video ? await video.path() : null;
  if (raw) {
    const target = path.join(outDir, "claimpilot-demo.webm");
    await rename(raw, target).catch(() => {});
    console.log(`wrote ${path.relative(process.cwd(), target)}`);
  }
  for (const f of await readdir(outDir))
    if (f !== "claimpilot-demo.webm" && f.endsWith(".webm")) console.log("leftover:", f);
}
