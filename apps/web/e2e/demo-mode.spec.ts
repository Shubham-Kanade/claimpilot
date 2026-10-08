import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";

import { findClaim, SAMPLES_DIR, seedPile, submitClaim } from "./pile";
import {
  actAs,
  CLAIM,
  CLAIMS,
  DOCUMENTS,
  PERSONA,
  resetDemo,
  stayOnBatchPage,
  waitForPersona,
} from "./support";

// The hosted demo: recorded answers, personas instead of logins, "Start over" instead of a
// database. These tests are what a visitor of the public Space goes through.

test.beforeEach(async () => {
  await resetDemo();
});

test("a first-time visitor acts as Asha Menon, with the demo personas listed first", async ({
  page,
}) => {
  await page.goto("/");
  await waitForPersona(page);
  const picker = page.getByRole("combobox", { name: "Acting as" });
  await expect(picker).toHaveValue(PERSONA.asha);

  // the three demo personas first, the synthetic-dataset employees after them
  const groups = await picker
    .locator("optgroup")
    .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("label")));
  expect(groups).toEqual(["Demo personas", "More synthetic employees"]);
  await expect(picker.locator("optgroup").first().locator("option")).toHaveText([
    "Asha Menon · L3 · Pune",
    "Ravi Iyer · L5 · approver",
    "Meera Shah · L2 · Mumbai",
  ]);
  await expect(picker.locator("optgroup").nth(1).locator("option").first()).toContainText(
    "Advika Hayer",
  );
});

test("a slim demo notice explains the recordings and can be dismissed for the session", async ({
  page,
}) => {
  await page.goto("/");
  const banner = page.getByTestId("demo-banner");
  await expect(banner).toBeVisible();
  await expect(banner).toContainText(
    "Demo: the sample receipts are replayed from recordings, nothing here is real data. To read your own receipts run ClaimPilot locally with your own API key.",
  );
  await expect(banner.getByRole("link", { name: "See the README" })).toHaveAttribute(
    "href",
    /github\.com/,
  );

  await banner.getByRole("button", { name: "Dismiss demo notice" }).click();
  await expect(banner).toBeHidden();
  await page.reload();
  await waitForPersona(page);
  await expect(page.getByTestId("demo-banner")).toHaveCount(0);
});

test("starting as someone else, the sample button switches to Asha (with a toast) and reads her pile", async ({
  page,
}) => {
  await actAs(page, PERSONA.advika);
  await page.goto("/");
  await waitForPersona(page);
  const picker = page.getByRole("combobox", { name: "Acting as" });
  await expect(picker).toHaveValue(PERSONA.advika);

  await page.getByRole("button", { name: "Try with sample receipts" }).click();

  // the recordings include Asha's calendar, so the pile only replays for her: say so, and switch
  const toast = page.getByTestId("toast");
  await expect(toast).toContainText("The sample receipts are Asha Menon's, so we switched to her");
  await expect(picker).toHaveValue(PERSONA.asha);
  // it can be dismissed (it also goes by itself after ten seconds, so do it straight away)
  await toast.getByRole("button", { name: "Dismiss notification" }).click();
  await expect(page.getByTestId("toast")).toHaveCount(0);
  await expect(page).toHaveURL(/\/batches\/[0-9a-f]{8,}/);
  await expect(page.getByRole("heading", { level: 1, name: `${CLAIMS} claims ready` })).toBeVisible(
    { timeout: 90_000 },
  );
  await stayOnBatchPage(page);
  await expect(page.locator("li[data-phase='checked']")).toHaveCount(DOCUMENTS);
  await expect(page.locator("li[data-phase='failed']")).toHaveCount(0);

  // the claims are Asha's, and Advika has none
  await page.goto("/claims");
  await expect(page.getByTestId("claim-card").filter({ visible: true })).toHaveCount(CLAIMS);
  await picker.selectOption(PERSONA.advika);
  await expect(page.getByText("No claims yet")).toBeVisible();
});

test("an approver pressing the sample button is switched to Asha, and nobody else's data is wiped", async ({
  page,
}) => {
  await seedPile(PERSONA.asha);
  await actAs(page, PERSONA.ravi);
  await page.goto("/");
  await waitForPersona(page);
  await page.getByRole("button", { name: "Try with sample receipts" }).click();
  await expect(page.getByTestId("toast")).toContainText("so we switched to her");
  await expect(page.getByRole("combobox", { name: "Acting as" })).toHaveValue(PERSONA.asha);
  await expect(page.getByRole("heading", { level: 1, name: `${CLAIMS} claims ready` })).toBeVisible(
    { timeout: 90_000 },
  );
  await stayOnBatchPage(page);
  await page.goto("/claims");
  // Asha's own earlier upload was cleared first: still seven claims, not fourteen
  await expect(page.getByTestId("claim-card").filter({ visible: true })).toHaveCount(CLAIMS);
});

test("Start over empties the list, after asking first", async ({ page }) => {
  await seedPile();
  await actAs(page, PERSONA.asha);
  await page.goto("/claims");
  await expect(page.getByTestId("claim-card").filter({ visible: true })).toHaveCount(CLAIMS);

  // asks first, and Cancel (focused first) or Escape leaves everything as it was
  await page.getByRole("button", { name: "Start over" }).click();
  const dialog = page.getByRole("dialog", { name: "Start over?" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("deletes your uploaded receipts and claims");
  await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(page.getByTestId("claim-card").filter({ visible: true })).toHaveCount(CLAIMS);

  // then it deletes, lands on the upload screen and says what was removed
  await page.getByRole("button", { name: "Start over" }).click();
  await dialog.getByRole("button", { name: "Delete and start over" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(
    page.getByRole("status").filter({ hasText: "Started over. Removed 15 receipts and 7 claims." }),
  ).toBeVisible();
  await page.getByRole("link", { name: "My claims" }).click();
  await expect(page.getByText("No claims yet")).toBeVisible();
});

test("the approver's Start over empties the whole queue, after a warning", async ({ page }) => {
  // two submitted claims of Asha's are waiting for Ravi
  await seedPile();
  await submitClaim(await findClaim(CLAIM.dinner, (c) => c.findings.length === 0));
  await submitClaim(await findClaim(CLAIM.mobile));

  await actAs(page, PERSONA.ravi);
  await page.goto("/approvals");
  await expect(page.getByTestId("approval-row")).toHaveCount(2);

  await page.getByRole("button", { name: "Start over" }).click();
  const dialog = page.getByRole("dialog", { name: "Start over?" });
  await expect(dialog).toContainText("clears everyone's uploaded receipts and claims");
  await dialog.getByRole("button", { name: "Delete and start over" }).click();
  await expect(page).toHaveURL(/\/$/);

  await page.getByRole("link", { name: "Approvals" }).click();
  await expect(page.getByText("All caught up")).toBeVisible();
});

test("a receipt that is not one of the samples is explained in plain words, the rest carry on", async ({
  page,
}) => {
  await actAs(page, PERSONA.asha);
  await page.goto("/");
  await waitForPersona(page);
  const png = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
    "base64",
  );
  await page.getByLabel("Choose receipts to upload").setInputFiles([
    { name: "my-own-lunch.png", mimeType: "image/png", buffer: png },
    {
      name: "07-cab-pune-to-kestrel-office.jpg",
      mimeType: "image/jpeg",
      buffer: readFileSync(path.join(SAMPLES_DIR, "07-cab-pune-to-kestrel-office.jpg")),
    },
  ]);
  await page.getByRole("button", { name: "Process 2 receipts" }).click();
  await expect(page).toHaveURL(/\/batches\/[0-9a-f]{8,}/);
  await expect(page.getByRole("heading", { level: 1, name: /claims? ready/ })).toBeVisible({
    timeout: 90_000,
  });
  await stayOnBatchPage(page);

  // the server's own words are shown as they are, plus where to go from here
  const failed = page.locator("li[data-phase='failed']");
  await expect(failed).toHaveCount(1);
  await expect(failed).toContainText("my-own-lunch.png");
  await expect(failed).toContainText(
    "This demo reads only its recorded sample receipts. To read your own, run ClaimPilot with your own API key (see the README).",
  );
  await expect(failed.getByRole("link", { name: "back to upload" })).toBeVisible();
  await expect(page.locator("li[data-phase='checked']")).toHaveCount(1);
});
