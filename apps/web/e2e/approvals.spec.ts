import { expect, test } from "@playwright/test";

import { answerClaim, findClaim, seedPile, submitClaim } from "./pile";
import { actAs, CLAIM, claimCard, PERSONA, resetDemo, waitForPersona } from "./support";

test.beforeEach(async () => {
  await resetDemo();
});

test("employees are told, politely, that approvals are for approvers", async ({ page }) => {
  await actAs(page, PERSONA.asha);
  await page.goto("/approvals");
  await expect(page.getByRole("heading", { name: "Approvers only" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Approvals" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Switch to Ravi Iyer" })).toBeVisible();
});

test("switch to the approver, review the riskiest claim, reject it with a reason, approve another", async ({
  page,
}) => {
  // Asha answers the trip's one question and submits the trip and the clean dinner (no UI: the
  // employee side is covered by questions-and-submit.spec.ts)
  await seedPile();
  const trip = await answerClaim(await findClaim(CLAIM.trip));
  await submitClaim(trip);
  await submitClaim(await findClaim(CLAIM.dinner, (c) => c.findings.length === 0));

  await actAs(page, PERSONA.asha);
  await page.goto("/");
  await waitForPersona(page);

  // the persona switcher: pick the approver
  await page.getByRole("combobox", { name: "Acting as" }).selectOption(PERSONA.ravi);
  await page.getByRole("link", { name: "Approvals" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Approvals" })).toBeVisible();

  // submitted claims, riskiest first: the trip with the alcohol bill, then the clean dinner
  const rows = page.getByTestId("approval-row").filter({ visible: true });
  await expect(rows).toHaveCount(2);
  await expect(page.getByRole("tab", { name: /^Submitted 2/ })).toBeVisible();
  await expect(rows.nth(0)).toContainText(CLAIM.trip);
  await expect(rows.nth(0)).toContainText("1 high risk");
  await expect(rows.nth(0)).toContainText("FIN-2026-");
  // the row says why before it is opened
  await expect(rows.nth(0).getByTestId("top-flag")).toContainText("This bill includes alcohol");
  await expect(rows.nth(1)).toContainText(CLAIM.dinner);
  await expect(rows.nth(1)).toContainText("No flags");

  // expand it: the same evidence the employee saw, with the quoted policy clauses
  await rows.nth(0).locator("button[aria-expanded]").first().click();
  await expect(page.getByText("Policy clause 6.1").first()).toBeVisible();
  await expect(page.getByText("Policy clause 5.1").first()).toBeVisible();
  await expect(page.getByText("Evidence · 6 receipts")).toBeVisible();
  // an approver never edits the employee's answers
  await expect(page.getByLabel("Your reply")).toHaveCount(0);

  // rejecting needs a reason
  const reject = page.getByRole("button", { name: "Reject" });
  const comment = page.getByLabel(/^Comment/);
  await expect(reject).toBeDisabled();
  await expect(
    page.getByText("To reject, say why. Finance keeps the reason on record."),
  ).toBeVisible();
  await comment.fill("   ");
  await expect(reject).toBeDisabled(); // blanks are not a reason
  await comment.fill("Alcohol has to come out of the bill: please resubmit without it");
  await expect(reject).toBeEnabled();
  await reject.click();
  await expect(page.getByRole("status").filter({ hasText: "Rejected" })).toContainText(CLAIM.trip);
  await expect(rows).toHaveCount(1);

  // approve the other one without a comment
  await rows.first().locator("button[aria-expanded]").first().click();
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Approved" })).toContainText(
    CLAIM.dinner,
  );
  await expect(page.getByText("All caught up")).toBeVisible();

  // the decisions show up under their tabs
  await page.getByRole("tab", { name: /^Rejected 1/ }).click();
  await expect(page.getByTestId("approval-row").filter({ visible: true })).toHaveCount(1);
  await expect(page.getByTestId("approval-row").filter({ visible: true })).toContainText(
    CLAIM.trip,
  );
  await page.getByRole("tab", { name: /^Approved 1/ }).click();
  await expect(page.getByTestId("approval-row").filter({ visible: true })).toContainText(
    CLAIM.dinner,
  );

  // and Asha sees the outcome on her own claims
  await page.getByRole("combobox", { name: "Acting as" }).selectOption(PERSONA.asha);
  await page.goto("/claims");
  await expect(claimCard(page, CLAIM.trip)).toContainText("Rejected");
  await expect(claimCard(page, CLAIM.dinner, "Approved")).toHaveCount(1);
});

test("the persona choice survives a reload, and each persona sees only their own claims", async ({
  page,
}) => {
  await seedPile(); // Asha's claims
  await actAs(page, PERSONA.asha);
  await page.goto("/claims");
  await expect(claimCard(page, CLAIM.trip)).toHaveCount(1);

  const picker = page.getByRole("combobox", { name: "Acting as" });
  await picker.selectOption(PERSONA.advika);
  // never the previous persona's claims, not even for a moment
  await expect(page.getByText("No claims yet")).toBeVisible();
  await expect(claimCard(page, CLAIM.trip)).toHaveCount(0);

  await page.reload();
  await expect(picker).toHaveValue(PERSONA.advika);
  await expect(page.getByText("No claims yet")).toBeVisible();
});
