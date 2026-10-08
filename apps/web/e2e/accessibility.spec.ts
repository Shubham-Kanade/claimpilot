import { expect, test } from "@playwright/test";

import { answerClaim, findClaim, seedPile, submitClaim } from "./pile";
import {
  actAs,
  CLAIM,
  CLAIMS,
  expectNoA11yViolations,
  openClaim,
  openReceipt,
  PERSONA,
  resetDemo,
  stayOnBatchPage,
  uploadSamples,
  waitForPersona,
} from "./support";

// axe-core (WCAG 2.x A/AA + best practices) must report ZERO violations on every screen, in the
// states a visitor really sees, at the phone width and at the desktop width (both projects run
// this file), against the real demo data.

test.describe("screens that only read", () => {
  test.beforeAll(async () => {
    await resetDemo();
    await seedPile();
  });
  test.beforeEach(async ({ page }) => {
    await actAs(page, PERSONA.asha);
  });

  test("upload screen, with the sample pile open", async ({ page }) => {
    await page.goto("/");
    await waitForPersona(page);
    await expect(page.getByText("LLM mode: replay")).toBeVisible();
    await expect(page.getByText("Receipts processed")).toBeVisible();
    await expectNoA11yViolations(page, "upload");
    await page.getByText("What is in the sample pile?").click();
    await expect(page.getByTestId("sample-pile").getByRole("listitem")).toHaveCount(15);
    await expectNoA11yViolations(page, "upload (pile list open)");
  });

  test("upload screen with files selected and the skipped-files notice", async ({ page }) => {
    await page.goto("/");
    await waitForPersona(page);
    const png = Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
      "base64",
    );
    await page.getByLabel("Choose receipts to upload").setInputFiles([
      { name: "one.png", mimeType: "image/png", buffer: png },
      { name: "notes.docx", mimeType: "application/msword", buffer: Buffer.from("x") },
    ]);
    await expect(page.getByRole("button", { name: "Process 1 receipt" })).toBeVisible();
    await expect(page.getByText(/Skipped 1 file/)).toBeVisible();
    await expectNoA11yViolations(page, "upload with files");
  });

  test("claims list: the seven claims, and a filter without matches", async ({ page }) => {
    await page.goto("/claims");
    await expect(page.getByTestId("claim-card").filter({ visible: true })).toHaveCount(CLAIMS);
    await expectNoA11yViolations(page, "claims list");

    await page
      .getByRole("group", { name: "Filter claims by status" })
      .getByRole("button", { name: /^Approved/ })
      .click();
    await expect(page.getByText("No claims with this status")).toBeVisible();
    await expectNoA11yViolations(page, "claims list (filter without matches)");
  });

  test("claim review: the trip, with its question, the alcohol bill and a receipt open", async ({
    page,
  }) => {
    await openClaim(page, CLAIM.trip);
    await expect(page.getByText("Policy clause 6.1").first()).toBeVisible();
    await expect(page.getByLabel("Your reply")).toBeVisible();
    await expectNoA11yViolations(page, "claim review (trip, needs input)");
    await openReceipt(page, "05-hotel-folio-mumbai.pdf");
    await expectNoA11yViolations(page, "claim review (trip, PDF receipt open)");
  });

  test("claim review: the duplicate, the edited total and the injected note", async ({ page }) => {
    await openClaim(page, CLAIM.dinner, "1 high risk");
    await expect(
      page
        .getByText(/almost identical to/)
        .filter({ visible: true })
        .first(),
    ).toBeVisible();
    await expectNoA11yViolations(page, "claim review (duplicate)");

    await openClaim(page, CLAIM.conveyance);
    await expect(
      page
        .getByText(/doesn't match the bill's own items/)
        .filter({ visible: true })
        .first(),
    ).toBeVisible();
    await expectNoA11yViolations(page, "claim review (edited total)");

    await openClaim(page, CLAIM.meals);
    await expect(
      page
        .getByText(/text addressed to an AI reviewer/)
        .filter({ visible: true })
        .first(),
    ).toBeVisible();
    await expectNoA11yViolations(page, "claim review (prompt injection)");
  });

  test("claim review: the UPI question and a clean claim with its photo open", async ({ page }) => {
    await openClaim(page, CLAIM.misc);
    await expect(page.getByLabel("Your reply")).toBeVisible();
    await expectNoA11yViolations(page, "claim review (UPI question)");

    await openClaim(page, CLAIM.dinner, "No flags");
    const photo = await openReceipt(page, "01-client-dinner-saffron-terrace.jpg");
    await expect(photo.getByRole("img", { name: /Original receipt/ })).toBeVisible();
    await expectNoA11yViolations(page, "claim review (clean, photo open)");
  });

  test("impact, and the approvals page for someone who is not an approver", async ({ page }) => {
    await page.goto("/impact");
    await expect(page.getByText("Average processing time")).toBeVisible();
    await expectNoA11yViolations(page, "impact");

    await page.goto("/approvals");
    await expect(page.getByRole("heading", { name: "Approvers only" })).toBeVisible();
    await expectNoA11yViolations(page, "approvals (not an approver)");
  });
});

test.describe("screens that change things", () => {
  test.beforeEach(async () => {
    await resetDemo();
  });

  test("live processing, then the finished batch, with the persona-switch toast showing", async ({
    page,
  }) => {
    await actAs(page, PERSONA.advika); // so that the sample button switches to Asha, with a toast
    await uploadSamples(page);
    // the toast stays ten seconds: look at it first, while it is still there
    await expect(page.getByTestId("toast")).toBeVisible();
    await expect(page.getByRole("progressbar", { name: "Overall progress" })).toBeVisible();
    await expectNoA11yViolations(page, "batch (processing, with the toast)");
    await expect(
      page.getByRole("heading", { level: 1, name: `${CLAIMS} claims ready` }),
    ).toBeVisible({ timeout: 90_000 });
    await stayOnBatchPage(page);
    await expectNoA11yViolations(page, "batch (done)");
  });

  test("a failed receipt on the batch page", async ({ page }) => {
    await actAs(page, PERSONA.asha);
    await page.goto("/");
    await waitForPersona(page);
    const png = Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
      "base64",
    );
    await page
      .getByLabel("Choose receipts to upload")
      .setInputFiles([{ name: "my-own-lunch.png", mimeType: "image/png", buffer: png }]);
    await page.getByRole("button", { name: "Process 1 receipt" }).click();
    await expect(page.locator("li[data-phase='failed']")).toHaveCount(1, { timeout: 90_000 });
    await stayOnBatchPage(page);
    await expectNoA11yViolations(page, "batch (a receipt that could not be read)");
  });

  test("claims list: nothing uploaded yet, and the Start over dialog", async ({ page }) => {
    await actAs(page, PERSONA.asha);
    await page.goto("/claims");
    await expect(page.getByText("No claims yet")).toBeVisible();
    await expectNoA11yViolations(page, "claims list (empty)");
    await page.getByRole("button", { name: "Start over" }).click();
    await expect(page.getByRole("dialog", { name: "Start over?" })).toBeVisible();
    await expectNoA11yViolations(page, "start over dialog");
  });

  test("claim review: the confirmation dialog and the submitted claim", async ({ page }) => {
    await seedPile();
    await actAs(page, PERSONA.asha);
    await openClaim(page, CLAIM.mobile);
    await page.getByRole("button", { name: "Confirm & submit" }).click();
    await expect(page.getByRole("dialog", { name: "Submit this claim to finance?" })).toBeVisible();
    await expectNoA11yViolations(page, "confirm dialog");
    await page.getByRole("dialog").getByRole("button", { name: "Confirm & submit" }).click();
    await expect(page.getByText(/Submitted · FIN-2026-\d{6}/)).toBeVisible();
    await expectNoA11yViolations(page, "claim review (submitted)");
  });

  test("claim review: after replying to the question", async ({ page }) => {
    await seedPile();
    await actAs(page, PERSONA.asha);
    await openClaim(page, CLAIM.trip);
    await page
      .getByLabel("Your reply")
      .fill("Client visit to review the quarterly roadmap with Kestrel Logistics");
    await page.getByRole("button", { name: "Send" }).click();
    await expect(page.getByText("Got it, here is what I understood:")).toBeVisible();
    await expect(page.getByText("All set")).toBeVisible();
    await expectNoA11yViolations(page, "claim review (answered)");
  });

  test("approvals: the empty queue, the queue, a row open, and a decision", async ({ page }) => {
    await actAs(page, PERSONA.ravi);
    await page.goto("/approvals");
    await expect(page.getByText("All caught up")).toBeVisible();
    await expectNoA11yViolations(page, "approvals (empty queue)");

    await seedPile();
    await submitClaim(await answerClaim(await findClaim(CLAIM.trip)));
    await submitClaim(await findClaim(CLAIM.mobile));
    await page.reload();
    const rows = page.getByTestId("approval-row").filter({ visible: true });
    await expect(rows).toHaveCount(2);
    await expectNoA11yViolations(page, "approvals (queue)");

    await rows.first().locator("button[aria-expanded]").first().click();
    await expect(page.getByRole("button", { name: "Approve" })).toBeVisible();
    await expectNoA11yViolations(page, "approvals (row expanded)");

    await page.getByRole("button", { name: "Approve" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Approved" })).toBeVisible();
    await expectNoA11yViolations(page, "approvals (after a decision)");
    await page.getByRole("tab", { name: /^Approved/ }).click();
    await expect(rows).toHaveCount(1);
    await expectNoA11yViolations(page, "approvals (approved tab)");
  });
});
