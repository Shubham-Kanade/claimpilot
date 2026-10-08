import { expect, test, type Page } from "@playwright/test";

import { answerClaim, findClaim, seedPile, submitClaim } from "./pile";
import {
  actAs,
  CLAIM,
  CLAIMS,
  expectNoHorizontalOverflow,
  openClaim,
  openReceipt,
  PERSONA,
  resetDemo,
  stayOnBatchPage,
  uploadSamples,
  waitForPersona,
} from "./support";

// These run in both projects: "mobile" is 360x740 with touch, "desktop" is 1280x800.

test.beforeEach(async () => {
  await resetDemo();
});

test("no screen scrolls sideways at this width", async ({ page }) => {
  await actAs(page, PERSONA.asha);
  await page.goto("/");
  await waitForPersona(page);
  await expectNoHorizontalOverflow(page, "upload");
  await page.getByText("What is in the sample pile?").click();
  await expectNoHorizontalOverflow(page, "upload (pile list open)");

  await uploadSamples(page);
  await expect(page.getByRole("heading", { level: 1, name: `${CLAIMS} claims ready` })).toBeVisible(
    { timeout: 90_000 },
  );
  await stayOnBatchPage(page);
  await expectNoHorizontalOverflow(page, "batch progress");

  await page.goto("/claims");
  await expect(page.getByTestId("claim-card").first()).toBeVisible();
  await expectNoHorizontalOverflow(page, "claims list");

  // the claim with the most on it: six receipts, the alcohol bill open, a PDF
  await openClaim(page, CLAIM.trip);
  await expect(page.getByText("Policy clause 6.1").first()).toBeVisible();
  await expectNoHorizontalOverflow(page, "claim review (trip)");
  await openReceipt(page, "05-hotel-folio-mumbai.pdf");
  await expectNoHorizontalOverflow(page, "claim review (trip, PDF open)");

  await openClaim(page, CLAIM.dinner, "1 high risk");
  await expectNoHorizontalOverflow(page, "claim review (duplicate)");
  await openClaim(page, CLAIM.conveyance);
  await expectNoHorizontalOverflow(page, "claim review (edited total)");

  await page.goto("/impact");
  await expect(page.getByText("Receipts processed")).toBeVisible();
  await expectNoHorizontalOverflow(page, "impact");
});

test("approvals fit too", async ({ page }) => {
  await seedPile();
  await submitClaim(await answerClaim(await findClaim(CLAIM.trip)));
  await actAs(page, PERSONA.ravi);
  await page.goto("/approvals");
  const row = page.getByTestId("approval-row").first();
  await expect(row).toBeVisible();
  await expectNoHorizontalOverflow(page, "approvals list");
  await row.locator("button[aria-expanded]").first().click();
  await expect(page.getByRole("button", { name: "Approve" })).toBeVisible();
  await expectNoHorizontalOverflow(page, "approvals row expanded");
});

test("the camera button appears on phones only; the drop zone works at any size", async ({
  page,
  isMobile,
}) => {
  await actAs(page, PERSONA.asha);
  await page.goto("/");
  await waitForPersona(page);
  const camera = page.getByRole("button", { name: "Take photo" });
  if (isMobile) await expect(camera).toBeVisible();
  else await expect(camera).toBeHidden();
  await expect(page.getByLabel("Choose receipts to upload")).toBeAttached();
  await expect(page.getByText(/up to 30 files · 15 MB each/)).toBeVisible();
});

/**
 * Load every receipt of the open claim, scroll to the very bottom and check that the last one
 * ends above the sticky submit bar (the page keeps room for it, whatever its height).
 */
async function expectLastReceiptAboveBar(page: Page, receipts: number) {
  const submit = page.getByRole("button", { name: "Confirm & submit" });
  await expect(submit).toBeVisible();
  const viewport = page.viewportSize()!;
  const box = (await submit.boundingBox())!;
  expect(box.y + box.height).toBeLessThanOrEqual(viewport.height);

  // let all the receipts (and the open one's photo) load: only a loaded card has the test id
  const cards = page.getByTestId("document-card");
  await expect(cards).toHaveCount(receipts);
  const last = cards.last();
  await expect
    .poll(
      async () => {
        await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
        const lastBox = (await last.boundingBox())!;
        const barBox = (await submit.boundingBox())!;
        return lastBox.y + lastBox.height <= barBox.y + 2;
      },
      { message: "the last receipt is hidden behind the submit bar" },
    )
    .toBe(true);
}

test("the sticky submit bar stays on screen and never hides the claim's content", async ({
  page,
}) => {
  await seedPile();
  await actAs(page, PERSONA.asha);
  await openClaim(page, CLAIM.trip); // six receipts, and the bar carries a hint (not ready yet)
  await expectLastReceiptAboveBar(page, 6);
  await openClaim(page, CLAIM.dinner, "No flags"); // ready: the bar shows the active button
  await expectLastReceiptAboveBar(page, 1);
});

test("at 390px the bar leaves the last receipt readable and the picker still names the person", async ({
  page,
  isMobile,
}) => {
  test.skip(!isMobile, "phone widths only");
  await page.setViewportSize({ width: 390, height: 844 });
  await seedPile();
  await actAs(page, PERSONA.asha);

  // the picker is narrow (it truncates the visible text), but it is still Asha, and it says so
  await page.goto("/");
  await waitForPersona(page);
  const picker = page.getByRole("combobox", { name: "Acting as" });
  await expect(picker.locator("option:checked")).toHaveText(/^Asha Menon · L3/);
  expect((await picker.boundingBox())!.width).toBeGreaterThanOrEqual(150);
  await expectNoHorizontalOverflow(page, "upload at 390px");

  await openClaim(page, CLAIM.trip);
  await expectLastReceiptAboveBar(page, 6);
  await expectNoHorizontalOverflow(page, "claim review at 390px");
  await openClaim(page, CLAIM.dinner, "No flags");
  await expectLastReceiptAboveBar(page, 1);
});

test("controls are comfortable to tap (44px high)", async ({ page, isMobile }) => {
  test.skip(!isMobile, "tap targets matter on the phone layout");
  await actAs(page, PERSONA.asha);
  await page.goto("/");
  await waitForPersona(page);
  for (const locator of [
    page.getByRole("combobox", { name: "Acting as" }),
    page.getByRole("button", { name: "Try with sample receipts" }),
    page.getByRole("link", { name: "My claims" }),
    page.getByText("What is in the sample pile?"),
  ]) {
    const box = (await locator.boundingBox())!;
    expect(box.height).toBeGreaterThanOrEqual(39);
  }
});

test("a phone shows the toast without covering what it is about", async ({ page, isMobile }) => {
  test.skip(!isMobile, "the toast placement matters on the phone layout");
  await actAs(page, PERSONA.advika);
  await page.goto("/");
  await waitForPersona(page);
  await page.getByRole("button", { name: "Try with sample receipts" }).click();
  const toast = page.getByTestId("toast");
  await expect(toast).toBeVisible();
  const viewport = page.viewportSize()!;
  const box = (await toast.boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(viewport.width);
  expect(box.y + box.height).toBeLessThanOrEqual(viewport.height);
  await expect(toast.getByRole("button", { name: "Dismiss notification" })).toBeVisible();
  const dismiss = (await toast
    .getByRole("button", { name: "Dismiss notification" })
    .boundingBox())!;
  expect(dismiss.height).toBeGreaterThanOrEqual(32);
});
