import { expect, test, type Browser, type Page, type TestInfo } from "@playwright/test";

import { answerClaim, findClaim, seedPile, submitClaim } from "./pile";
import { CLAIM, E2E_SANDBOX, PERSONA, resetDemo, waitForPersona } from "./support";

/**
 * The browser console must stay clean. In particular React's "Minified React error #418"
 * (hydration mismatch: the first client render differs from the server HTML) must never appear:
 * it silently throws the server-rendered page away and renders it again, and it only shows up
 * in the production build, in a fresh browser, whenever the API answers before React has
 * hydrated the whole page. Every screen is loaded from scratch (a full page load, so it hydrates)
 * by a first-time visitor (nothing saved) and by a returning approver.
 */

interface Problems {
  pageErrors: string[];
  consoleErrors: string[];
}

function watch(page: Page): Problems {
  const problems: Problems = { pageErrors: [], consoleErrors: [] };
  page.on("pageerror", (error) => problems.pageErrors.push(error.message.slice(0, 400)));
  page.on("console", (message) => {
    if (message.type() === "error") problems.consoleErrors.push(message.text().slice(0, 400));
  });
  return problems;
}

/** A full page load, then wait until the app is hydrated and has found out who is acting. */
async function visit(page: Page, path: string): Promise<void> {
  await page.goto(path);
  await waitForPersona(page);
  // let the first round of API answers land (and re-render) before judging the console
  await page.waitForTimeout(1500);
}

/** A brand-new browser context (empty storage and cache) with this project's device settings. */
async function freshContext(browser: Browser, info: TestInfo, saved?: { persona: string }) {
  const {
    baseURL,
    viewport,
    userAgent,
    isMobile,
    hasTouch,
    deviceScaleFactor,
    locale,
    timezoneId,
  } = info.project.use;
  const context = await browser.newContext({
    baseURL,
    viewport,
    userAgent,
    isMobile,
    hasTouch,
    deviceScaleFactor,
    locale,
    timezoneId,
    reducedMotion: "reduce",
  });
  await context.addInitScript((sandbox) => {
    window.localStorage.setItem("claimpilot.sandbox", sandbox);
  }, E2E_SANDBOX);
  if (saved) {
    await context.addInitScript((id) => {
      window.localStorage.setItem("claimpilot.persona", id);
      window.sessionStorage.setItem("claimpilot.demo-banner-dismissed", "1");
    }, saved.persona);
  }
  return context;
}

test.describe("a clean console on every screen", () => {
  let claimId = "";
  let batchId = "";

  test.beforeAll(async () => {
    await resetDemo();
    const claims = await seedPile();
    claimId = claims[0].id;
    batchId = claims[0].batch_id;
    // something in the approver's queue, so that screen has content to hydrate too
    await submitClaim(await answerClaim(await findClaim(CLAIM.trip)));
  });

  test("a first-time visitor (nothing saved yet) loads every screen without errors", async ({
    browser,
  }, testInfo) => {
    for (const path of [
      "/",
      "/claims",
      `/claims/${claimId}`,
      `/batches/${batchId}`,
      "/impact",
      "/approvals",
    ]) {
      // a new browser context per screen: nothing saved, nothing cached, a real first visit
      const context = await freshContext(browser, testInfo);
      const page = await context.newPage();
      const problems = watch(page);
      await visit(page, path);
      expect(problems, `console problems on ${path}`).toEqual({
        pageErrors: [],
        consoleErrors: [],
      });
      await context.close();
    }
  });

  test("a returning approver (persona saved, notice dismissed) loads every screen without errors", async ({
    browser,
  }, testInfo) => {
    const context = await freshContext(browser, testInfo, { persona: PERSONA.ravi });
    const page = await context.newPage();
    const problems = watch(page);
    for (const path of ["/", "/claims", "/approvals", "/impact"]) {
      await visit(page, path);
      expect(problems, `console problems on ${path}`).toEqual({
        pageErrors: [],
        consoleErrors: [],
      });
    }
    // and a row opened inside the queue
    await visit(page, "/approvals");
    await page.getByTestId("approval-row").first().locator("button[aria-expanded]").first().click();
    await expect(page.getByRole("button", { name: "Approve" })).toBeVisible();
    await page.waitForTimeout(500);
    expect(problems).toEqual({ pageErrors: [], consoleErrors: [] });
    await context.close();
  });

  test("client-side navigation between the screens is clean too", async ({ browser }, testInfo) => {
    const context = await freshContext(browser, testInfo, { persona: PERSONA.asha });
    const page = await context.newPage();
    const problems = watch(page);
    await visit(page, "/");
    await page.getByRole("link", { name: "My claims" }).click();
    await expect(page).toHaveURL(/\/claims$/);
    await expect(page.getByTestId("claim-card").first()).toBeVisible();
    await page.getByTestId("claim-card").first().getByRole("link").first().click();
    await expect(page).toHaveURL(/\/claims\/clm-/);
    await expect(page.getByTestId("document-card").first()).toBeVisible();
    await page.getByRole("link", { name: "Impact" }).click();
    await expect(page.getByText("Average processing time")).toBeVisible();
    await page.waitForTimeout(500);
    expect(problems).toEqual({ pageErrors: [], consoleErrors: [] });
    await context.close();
  });
});
