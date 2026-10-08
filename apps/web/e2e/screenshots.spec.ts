import { expect, test, type Page, type TestInfo } from "@playwright/test";

import {
  actAs,
  CLAIM,
  claimCard,
  PERSONA,
  resetDemo,
  uploadSamples,
  uploadSamplesAndWait,
  waitForPersona,
} from "./support";

/**
 * Not part of the normal suite: run with `SCREENSHOTS=1 npm run e2e -- screenshots` to write a
 * PNG of every screen (and its main states) at 360px and 1280px into test-results/screens/.
 * They are looked at by a human (or Claude) to catch visual problems tests cannot see.
 */
test.skip(!process.env.SCREENSHOTS, "set SCREENSHOTS=1 to capture screens");

const SLICE_HEIGHT = 1000;

async function shot(page: Page, info: TestInfo, name: string, fullPage = true) {
  // let fonts, images and the last transitions settle
  await page.waitForTimeout(500);
  const base = `test-results/screens/${info.project.name}-${name}`;
  if (!fullPage) {
    await page.screenshot({ path: `${base}.png` });
    return;
  }
  const size = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    height: document.documentElement.scrollHeight,
  }));
  if (info.project.name !== "mobile" || size.height <= SLICE_HEIGHT * 1.3) {
    await page.screenshot({ path: `${base}.png`, fullPage: true });
    return;
  }
  // Phone screens are tall: save them as readable slices (-1, -2, ...) instead of one tiny image.
  const slices = Math.min(Math.ceil(size.height / SLICE_HEIGHT), 8);
  for (let i = 0; i < slices; i += 1) {
    await page.screenshot({
      path: `${base}-${i + 1}.png`,
      fullPage: true,
      clip: {
        x: 0,
        y: i * SLICE_HEIGHT,
        width: size.width,
        height: Math.min(SLICE_HEIGHT, size.height - i * SLICE_HEIGHT),
      },
    });
  }
}

/** Open the n-th claim on /claims whose title is `title` (the list is riskiest first). */
async function openNth(page: Page, title: string, n = 0) {
  await page.goto("/claims");
  const card = claimCard(page, title).nth(n);
  await expect(card).toBeVisible();
  await card.getByRole("link", { name: title, exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();
  // wait for the receipts of the claim to be drawn
  await expect(page.getByTestId("document-card").first()).toBeVisible();
}

test.beforeEach(async () => {
  await resetDemo();
});

test("capture the upload and live-progress screens", async ({ page }, info) => {
  test.setTimeout(240_000);
  // starting as somebody else: the sample button switches to Asha (with a toast)
  await actAs(page, PERSONA.advika);
  await page.goto("/");
  await waitForPersona(page);
  await expect(page.getByText("Receipts processed")).toBeVisible();
  await shot(page, info, "01-upload");

  await page.getByText("What is in the sample pile?").click();
  await shot(page, info, "02-upload-pile-open");

  const png = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
    "base64",
  );
  await page.getByLabel("Choose receipts to upload").setInputFiles([
    { name: "taxi-receipt.png", mimeType: "image/png", buffer: png },
    { name: "notes.docx", mimeType: "application/msword", buffer: Buffer.from("x") },
  ]);
  await expect(page.getByRole("button", { name: /Process 1 receipt/ })).toBeVisible();
  await shot(page, info, "03-upload-files-selected");

  await page.goto("/");
  await waitForPersona(page);
  await uploadSamples(page);
  await shot(page, info, "04-batch-processing", false);
  await expect(page.getByRole("heading", { level: 1, name: /claims ready/ })).toBeVisible({
    timeout: 90_000,
  });
  await shot(page, info, "05-batch-done");
});

test("capture the claims and every kind of flag", async ({ page }, info) => {
  test.setTimeout(300_000);
  await actAs(page, PERSONA.asha);
  await uploadSamplesAndWait(page);

  await page.goto("/claims");
  await expect(page.getByTestId("claim-card").first()).toBeVisible();
  await shot(page, info, "10-claims");

  await openNth(page, CLAIM.trip);
  await shot(page, info, "11-trip-needs-input");

  await openNth(page, CLAIM.dinner, 0);
  await shot(page, info, "12-dinner-first");
  await openNth(page, CLAIM.dinner, 1);
  await shot(page, info, "13-dinner-second");

  await openNth(page, CLAIM.conveyance);
  await shot(page, info, "14-conveyance");
  await openNth(page, CLAIM.meals);
  await shot(page, info, "15-meals-injection");
  await openNth(page, CLAIM.misc);
  await shot(page, info, "16-misc-upi");
  await openNth(page, CLAIM.mobile);
  await shot(page, info, "17-mobile");

  // click-to-verify: the clean dinner's photo with its total selected, then the edited total
  await openNth(page, CLAIM.dinner, 1);
  const dinner = page.getByTestId("document-card").first();
  await dinner.locator("button[aria-expanded]").first().click();
  await dinner.getByRole("button", { name: /^Total/ }).click();
  await expect(dinner.getByTestId("verify-highlight")).toBeVisible();
  await shot(page, info, "18-click-to-verify-total");
  await openNth(page, CLAIM.conveyance);
  const cab = page.getByTestId("document-card").filter({ hasText: "13-cab-pune" });
  await cab.getByRole("button", { name: "Show on the receipt" }).click();
  await expect(cab.getByTestId("verify-highlight")).toBeVisible();
  await shot(page, info, "19-show-edited-total");
});

test("capture reply, submit, approvals and impact", async ({ page }, info) => {
  test.setTimeout(300_000);
  await actAs(page, PERSONA.asha);
  await uploadSamplesAndWait(page);

  await openNth(page, CLAIM.trip);
  await page
    .getByLabel("Your reply")
    .fill("Client visit to review the quarterly roadmap with Kestrel Logistics");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("All set")).toBeVisible();
  await shot(page, info, "20-trip-answered");
  await page.getByRole("button", { name: "Confirm & submit" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await shot(page, info, "21-confirm-dialog", false);
  await page.getByRole("dialog").getByRole("button", { name: "Confirm & submit" }).click();
  await expect(page.getByText(/Submitted · FIN-2026-\d{6}/)).toBeVisible();
  await shot(page, info, "22-trip-submitted");

  await page.goto("/approvals");
  await expect(page.getByRole("heading", { name: "Approvers only" })).toBeVisible();
  await shot(page, info, "23-approvals-employee");
  await page.getByRole("combobox", { name: "Acting as" }).selectOption(PERSONA.ravi);
  await expect(page.getByTestId("approval-row").first()).toBeVisible();
  await shot(page, info, "24-approvals-queue");
  await page.getByTestId("approval-row").first().locator("button[aria-expanded]").first().click();
  await expect(page.getByRole("button", { name: "Approve" })).toBeVisible();
  await shot(page, info, "25-approvals-expanded");

  await page.goto("/impact");
  await expect(page.getByText("Average processing time")).toBeVisible();
  await shot(page, info, "26-impact");
});
