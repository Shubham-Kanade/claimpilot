import { expect, test } from "@playwright/test";

import {
  actAs,
  CLAIM,
  CLAIMS,
  claimCard,
  DOCUMENTS,
  PERSONA,
  resetDemo,
  stayOnBatchPage,
  uploadSamples,
  waitForPersona,
} from "./support";

test.beforeEach(async ({ page }) => {
  await resetDemo();
  await actAs(page, PERSONA.asha);
});

test("the sample pile: live progress for 15 receipts, then 7 claims ready to review", async ({
  page,
}) => {
  // Remember every stage a receipt card passes through (the recorded replays are fast, so polling
  // the page would miss them).
  await page.addInitScript(() => {
    const seen = new Set<string>();
    (window as unknown as { __phases: Set<string> }).__phases = seen;
    new MutationObserver((records) => {
      for (const record of records) {
        const phase = (record.target as HTMLElement).getAttribute?.("data-phase");
        if (phase) seen.add(phase);
      }
    }).observe(document, {
      subtree: true,
      attributes: true,
      attributeFilter: ["data-phase"],
      childList: true,
    });
  });

  await page.goto("/");
  await waitForPersona(page);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("From a pile of receipts");

  // the recorded profile can only read the samples: the drop zone says so
  await expect(
    page.getByText(
      "In this demo only the sample receipts can be read: use Try with sample receipts.",
    ),
  ).toBeVisible();

  // what the pile is, in words (it is fine to say what it contains)
  await expect(
    page.getByText(
      /15 synthetic receipts from one week of Asha Menon's expenses, including a duplicate, an edited total, a note aimed at an AI reviewer and an alcohol bill/,
    ),
  ).toBeVisible();
  await page.getByText("What is in the sample pile?").click();
  await expect(page.getByTestId("sample-pile").getByRole("listitem")).toHaveCount(DOCUMENTS);

  // the engine transparency footer (GET /v1/meta): honest about the hosted demo
  await expect(page.getByText("LLM mode: replay")).toBeVisible();
  await expect(page.getByText("Public demo")).toBeVisible();
  await expect(page.getByText("claude-haiku-5-5").first()).toBeVisible();
  await expect(page.getByText(/Decisions by: LLM \(System Two\)/)).toBeVisible();

  await uploadSamples(page);

  // live progress: overall bar, elapsed time, running cost, one card per receipt
  await expect(page.getByRole("progressbar", { name: "Overall progress" })).toBeVisible();
  await expect(page.getByText("LLM cost (recorded)")).toBeVisible();
  await expect(page.getByText("Elapsed")).toBeVisible();
  await expect(page.getByRole("list", { name: "Receipts" }).locator("> li")).toHaveCount(DOCUMENTS);

  // done: "7 claims ready", every receipt read and checked, and nothing submitted
  await expect(page.getByRole("heading", { level: 1, name: `${CLAIMS} claims ready` })).toBeVisible(
    { timeout: 90_000 },
  );
  await stayOnBatchPage(page);
  await expect(page.getByText("All 15 receipts read, checked and grouped.")).toBeVisible();
  await expect(page.getByRole("progressbar", { name: "Overall progress" })).toHaveAttribute(
    "aria-valuenow",
    "100",
  );
  await expect(page.locator("li[data-phase='checked']")).toHaveCount(DOCUMENTS);
  await expect(page.locator("li[data-phase='failed']")).toHaveCount(0);

  const phases = await page.evaluate(() =>
    Array.from((window as unknown as { __phases: Set<string> }).__phases),
  );
  expect(phases).toContain("checked");

  // what was read, per receipt (merchant, amount) and what the trust checks said
  const card = (file: string) => page.locator("li[data-phase]").filter({ hasText: file });
  await expect(card("01-client-dinner-saffron-terrace.jpg")).toContainText("SAFFRON TERRACE");
  await expect(card("01-client-dinner-saffron-terrace.jpg")).toContainText("₹8,400.00");
  await expect(card("05-hotel-folio-mumbai.pdf")).toContainText("Lotus Bay Residency");
  await expect(card("05-hotel-folio-mumbai.pdf")).toContainText("₹6,090.00");
  await expect(card("09-auto-slip-pune.jpg")).toContainText("₹260.00"); // handwriting, Hindi
  await expect(card("10-mobile-bill-october.pdf")).toContainText("₹1,059.64");
  await expect(card("11-upi-payment-120.png")).toContainText(/Low \d+%/); // unsure of the category
  await expect(card("12-client-dinner-saffron-terrace-copy.jpg")).toContainText("Needs review");
  await expect(card("13-cab-pune-shivajinagar-to-baner.jpg")).toContainText("Blocked");
  await expect(card("14-cafe-bill-banyan-pune.jpg")).toContainText("Blocked");
  // the alcohol bill is a policy finding, not a trust one: the document itself looks genuine
  await expect(card("15-dinner-mumbai-10-oct.jpg")).toContainText("Looks genuine");

  // on to the claims (the page also opens them by itself a few seconds after it finishes)
  await page
    .getByRole("link", { name: "Review claims" })
    .click({ timeout: 5_000 })
    .catch(() => undefined);
  await expect(page).toHaveURL(/\/claims$/);
  await expect(page.getByTestId("claim-card").filter({ visible: true })).toHaveCount(CLAIMS);
  for (const title of [CLAIM.trip, CLAIM.conveyance, CLAIM.meals, CLAIM.misc, CLAIM.mobile]) {
    await expect(claimCard(page, title)).toHaveCount(1);
  }
  // the duplicate got a claim of its own, so there are two dinners of the same day and total
  await expect(claimCard(page, CLAIM.dinner)).toHaveCount(2);
});

test("a reload mid-way is safe: the batch page resumes where it was", async ({ page }) => {
  await uploadSamples(page);
  await page.reload();
  await waitForPersona(page);
  await expect(page.getByRole("heading", { level: 1, name: `${CLAIMS} claims ready` })).toBeVisible(
    { timeout: 90_000 },
  );
  await expect(page.locator("li[data-phase='checked']")).toHaveCount(DOCUMENTS);
});

test("the sample button reads the same pile again from a clean start", async ({ page }) => {
  await uploadSamples(page);
  await expect(page.getByRole("heading", { level: 1, name: `${CLAIMS} claims ready` })).toBeVisible(
    { timeout: 90_000 },
  );
  await stayOnBatchPage(page);

  // pressing it again clears the earlier uploads first: still 7 claims, not 14
  await uploadSamples(page);
  await expect(page.getByRole("heading", { level: 1, name: `${CLAIMS} claims ready` })).toBeVisible(
    { timeout: 90_000 },
  );
  await stayOnBatchPage(page);
  await page.goto("/claims");
  await expect(page.getByTestId("claim-card").filter({ visible: true })).toHaveCount(CLAIMS);
});
