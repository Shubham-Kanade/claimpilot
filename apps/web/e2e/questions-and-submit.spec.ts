import { expect, test } from "@playwright/test";

import { seedPile } from "./pile";
import { actAs, CLAIM, claimCard, openClaim, PERSONA, resetDemo } from "./support";

/** Answering what ClaimPilot could not work out itself, and submitting with a confirmation. */
test.beforeEach(async ({ page }) => {
  await resetDemo();
  await seedPile();
  await actAs(page, PERSONA.asha);
});

test("the trip: one question, one reply, an explicit confirmation, a finance reference", async ({
  page,
}) => {
  await openClaim(page, CLAIM.trip);

  // the claim needs input, so submitting is locked
  const submit = page.getByRole("button", { name: "Confirm & submit" });
  await expect(submit).toBeDisabled();
  await expect(page.getByLabel("Your reply")).toBeVisible();

  // one reply answers the one open question
  await page
    .getByLabel("Your reply")
    .fill("Client visit to review the quarterly roadmap with Kestrel Logistics");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Got it, here is what I understood:")).toBeVisible();
  await expect(page.getByText("All set")).toBeVisible();
  await expect(page.getByText("Ready to submit").first()).toBeVisible();
  await expect(page.getByText("Answer the open question to enable submit.")).toHaveCount(0);

  // nothing has been submitted yet: the button only opens a summary to confirm
  await expect(submit).toBeEnabled();
  await submit.click();
  const dialog = page.getByRole("dialog", { name: "Submit this claim to finance?" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText(CLAIM.trip);
  await expect(dialog).toContainText("₹12,678.31");
  await expect(dialog).toContainText("1 high risk, 1 warning");
  await expect(dialog).toContainText("Finance review");

  // Escape cancels without submitting, and focus returns to the button
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(submit).toBeFocused();
  await expect(page.getByText(/FIN-2026-\d+/)).toHaveCount(0);

  // confirm for real
  await submit.click();
  await dialog.getByRole("button", { name: "Confirm & submit" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: /Submitted · FIN-2026-\d{6}/ }),
  ).toBeVisible();
  await expect(page.getByText("This claim has been submitted.")).toBeVisible();
  await expect(page.getByLabel("Your reply")).toHaveCount(0);

  // it shows as submitted in the list
  await page.goto("/claims");
  await expect(claimCard(page, CLAIM.trip)).toContainText("Submitted");
});

test("the UPI payment: answer in your own words and the claim is ready", async ({ page }) => {
  await openClaim(page, CLAIM.misc);
  await expect(page.getByRole("button", { name: "Confirm & submit" })).toBeDisabled();

  // quick replies fill the box; the person still decides what to send
  const reply = page.getByLabel("Your reply");
  await page.getByRole("button", { name: "Yes, it was a business expense" }).click();
  await expect(reply).toHaveValue(/business expense/);
  await reply.fill("It was an auto fare to the client office, no receipt was available");
  await reply.press("Control+Enter");

  await expect(page.getByText("Got it, here is what I understood:")).toBeVisible();
  await expect(page.getByText("All set")).toBeVisible();
  await expect(page.getByRole("button", { name: "Confirm & submit" })).toBeEnabled();
  // the answer is kept with the claim, and can still be edited
  await expect(page.getByText("auto fare to the client office").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Edit" }).first()).toBeVisible();
});

test("a clean claim goes straight through: confirm and a reference, no questions asked", async ({
  page,
  isMobile,
}) => {
  await openClaim(page, CLAIM.dinner, "No flags");
  await expect(page.getByLabel("Your reply")).toHaveCount(0); // nothing to ask
  const submit = page.getByRole("button", { name: "Confirm & submit" });
  await expect(submit).toBeEnabled();
  // (the phone's slim bar leaves the hint out: the dialog says the same)
  if (!isMobile) await expect(page.getByText("Nothing is sent until you confirm.")).toBeVisible();
  await submit.click();
  const dialog = page.getByRole("dialog", { name: "Submit this claim to finance?" });
  await expect(dialog).toContainText("Flags");
  await expect(dialog).toContainText("None");
  await expect(dialog).toContainText("Low risk");
  await expect(dialog).toContainText("₹8,400.00");
  await dialog.getByRole("button", { name: "Confirm & submit" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: /Submitted · FIN-2026-\d{6}/ }),
  ).toBeVisible();

  // the other dinner, the duplicate, is untouched
  await page.goto("/claims");
  await expect(claimCard(page, CLAIM.dinner, "1 high risk")).toContainText("Ready to submit");
  await expect(claimCard(page, CLAIM.dinner, "Submitted")).toHaveCount(1);
});
