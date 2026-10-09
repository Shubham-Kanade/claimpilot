import { expect, test, type Browser, type TestInfo } from "@playwright/test";

import { E2E_SANDBOX, PERSONA, randomSandbox } from "./env";
import { findClaim, listClaims, seedPile, submitClaim } from "./pile";
import { CLAIM, CLAIMS, claimCard, resetDemo, stayOnBatchPage, waitForPersona } from "./support";

/**
 * Per-visitor demo sandboxes: two visitors acting as the same demo employee (DEMO-ASHA) must
 * never see or delete each other's batches, claims, approvals or stats. Visitor A is the suite's
 * own sandbox (seeded through the API); visitor B is a second browser with a different random
 * sandbox id in localStorage, exactly what a second judge opening the Space would be.
 */

async function visitorB(browser: Browser, info: TestInfo, persona: string) {
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
  const sandbox = randomSandbox();
  await context.addInitScript(
    ([sandboxId, personaId]) => {
      window.localStorage.setItem("claimpilot.sandbox", sandboxId);
      if (!window.localStorage.getItem("claimpilot.persona")) {
        window.localStorage.setItem("claimpilot.persona", personaId);
      }
    },
    [sandbox, persona] as const,
  );
  return { context, sandbox };
}

test.beforeEach(async () => {
  await resetDemo();
  await seedPile(); // visitor A: Asha, seven claims
  // something waiting in A's approvals queue, so an empty queue for B proves isolation
  await submitClaim(await findClaim(CLAIM.dinner, (c) => c.findings.length === 0));
});

test("a second visitor sees none of the first visitor's data", async ({ browser }, testInfo) => {
  const mine = await listClaims();
  expect(mine).toHaveLength(CLAIMS);
  const { context, sandbox } = await visitorB(browser, testInfo, PERSONA.asha);
  expect(sandbox).not.toBe(E2E_SANDBOX);
  const page = await context.newPage();

  // same employee, other sandbox: nothing uploaded yet
  await page.goto("/claims");
  await waitForPersona(page);
  await expect(page.getByText("No claims yet")).toBeVisible();
  await expect(page.getByTestId("claim-card")).toHaveCount(0);

  // a direct link to the other visitor's claim is "not found", not a peek at their data
  await page.goto(`/claims/${mine[0].id}`);
  await expect(page.getByRole("heading", { level: 1, name: "Claim not found" })).toBeVisible();

  // the approver of this sandbox has nothing to approve, though A's queue has a submitted claim
  await page.evaluate(() => window.localStorage.setItem("claimpilot.persona", "DEMO-RAVI"));
  await page.goto("/approvals");
  await expect(page.getByRole("heading", { level: 1, name: "Approvals" })).toBeVisible();
  await expect(page.getByText("All caught up")).toBeVisible();
  await expect(page.getByTestId("approval-row")).toHaveCount(0);
  await context.close();
});

test("Start over only clears the visitor's own sandbox", async ({ browser }, testInfo) => {
  const before = (await listClaims()).map((c) => c.id).sort();
  const { context } = await visitorB(browser, testInfo, PERSONA.asha);
  const page = await context.newPage();

  // B has to have something to clear first: run the samples in B's sandbox
  await page.goto("/");
  await waitForPersona(page);
  await page.getByRole("button", { name: "Try with sample receipts" }).click();
  await expect(page.getByRole("heading", { level: 1, name: `${CLAIMS} claims ready` })).toBeVisible(
    { timeout: 90_000 },
  );
  await stayOnBatchPage(page);

  await page.goto("/claims");
  await expect(page.getByTestId("claim-card").filter({ visible: true })).toHaveCount(CLAIMS);
  await page.getByRole("button", { name: "Start over" }).click();
  const dialog = page.getByRole("dialog", { name: "Start over?" });
  await expect(dialog).toContainText("Nobody else's data is affected");
  await dialog.getByRole("button", { name: "Delete and start over" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "Started over. Removed 15 receipts and 7 claims." }),
  ).toBeVisible();

  // visitor A still has every claim they had
  expect((await listClaims()).map((c) => c.id).sort()).toEqual(before);
  await context.close();
});

test("the sample button in a second sandbox makes its own claims, not duplicates of the first", async ({
  browser,
}, testInfo) => {
  const before = (await listClaims()).map((c) => c.id).sort();
  const { context } = await visitorB(browser, testInfo, PERSONA.asha);
  const page = await context.newPage();
  await page.goto("/");
  await waitForPersona(page);
  await page.getByRole("button", { name: "Try with sample receipts" }).click();
  await expect(page.getByRole("heading", { level: 1, name: `${CLAIMS} claims ready` })).toBeVisible(
    { timeout: 90_000 },
  );
  await stayOnBatchPage(page);
  // every receipt was read and checked on its own: the only duplicate is the pile's own copy
  await expect(page.locator("li[data-phase='checked']")).toHaveCount(15);
  await expect(page.locator("li[data-phase='failed']")).toHaveCount(0);

  await page.goto("/claims");
  const cards = page.getByTestId("claim-card").filter({ visible: true });
  await expect(cards).toHaveCount(CLAIMS);
  await expect(claimCard(page, CLAIM.dinner)).toHaveCount(2);
  // the first copy of the dinner is clean here (it would be flagged if A's upload counted)
  await expect(claimCard(page, CLAIM.dinner, "No flags")).toHaveCount(1);
  await expect(claimCard(page, CLAIM.dinner, "1 high risk")).toHaveCount(1);

  // and visitor A is untouched: same seven claims, same ids
  expect((await listClaims()).map((c) => c.id).sort()).toEqual(before);
  await context.close();
});
