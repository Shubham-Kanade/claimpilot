import { expect, test, type Page } from "@playwright/test";

import { seedPile } from "./pile";
import {
  actAs,
  CLAIM,
  CLAIMS,
  claimCard,
  openClaim,
  openReceipt,
  PERSONA,
  resetDemo,
} from "./support";

/**
 * The demo pile, as the employee sees it: seven claims, four of them carrying a trap that
 * ClaimPilot has to catch (data/synth/demo/README.md, "What should happen"). The pile is uploaded
 * once through the API; these tests only read.
 */
test.describe("the seven claims of the demo pile", () => {
  test.beforeAll(async () => {
    await resetDemo();
    await seedPile();
  });

  test.beforeEach(async ({ page }) => {
    await actAs(page, PERSONA.asha);
  });

  const receipt = (page: Page, file: string) =>
    page.getByTestId("document-card").filter({ hasText: file });

  test("the list: what needs Asha first, every flagged claim saying why in words", async ({
    page,
  }) => {
    await page.goto("/claims");
    const cards = page.getByTestId("claim-card").filter({ visible: true });
    await expect(cards).toHaveCount(CLAIMS);
    await expect(
      page.getByText("7 claims. The ones that need you come first. Open one to review and submit."),
    ).toBeVisible();

    // the two claims waiting for an answer come first, then the flagged ones, then the clean ones
    await expect(cards.nth(0)).toContainText(CLAIM.trip);
    await expect(cards.nth(0)).toContainText("Needs your input");
    await expect(cards.nth(1)).toContainText(CLAIM.misc);
    await expect(cards.nth(1)).toContainText("Needs your input");
    for (const index of [2, 3, 4]) await expect(cards.nth(index)).toContainText("Finance review");
    for (const index of [5, 6]) await expect(cards.nth(index)).toContainText("Low risk");

    // titles and totals (the trip is 12,678.31; the cab with the edited total counts as printed)
    await expect(claimCard(page, CLAIM.trip)).toContainText("₹12,678.31");
    await expect(claimCard(page, CLAIM.conveyance)).toContainText("₹1,623.00");
    await expect(claimCard(page, CLAIM.meals)).toContainText("₹378.00");
    await expect(claimCard(page, CLAIM.misc)).toContainText("₹120.00");
    await expect(claimCard(page, CLAIM.mobile)).toContainText("₹1,059.64");
    await expect(claimCard(page, CLAIM.dinner)).toHaveCount(2);
    await expect(claimCard(page, CLAIM.dinner, "No flags")).toContainText("₹8,400.00");
    await expect(claimCard(page, CLAIM.dinner, "1 high risk")).toContainText("₹8,400.00");

    // each flagged card says WHY, so two dinners of the same day and total can be told apart
    const why = (title: string, others?: string) =>
      claimCard(page, title, others).getByTestId("top-flag");
    await expect(why(CLAIM.trip)).toContainText("This bill includes alcohol (2 lines, ₹1,540)");
    await expect(why(CLAIM.conveyance)).toContainText("The printed total ₹830.96 doesn't match");
    await expect(why(CLAIM.meals)).toContainText("text addressed to an AI reviewer");
    await expect(why(CLAIM.dinner, "1 high risk")).toContainText(
      "almost identical to another receipt",
    );
    await expect(why(CLAIM.misc)).toContainText("looks personal");
    await expect(claimCard(page, CLAIM.mobile).getByTestId("top-flag")).toHaveCount(0);
  });

  test("the list filters by status, with counts, and says so when nothing matches", async ({
    page,
  }) => {
    await page.goto("/claims");
    const filters = page.getByRole("group", { name: "Filter claims by status" });
    const cards = page.getByTestId("claim-card").filter({ visible: true });
    await expect(filters.getByRole("button", { name: "All 7" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await filters.getByRole("button", { name: "Needs your input 2" }).click();
    await expect(cards).toHaveCount(2);
    await expect(page.getByText("Showing 2 claims")).toBeAttached();
    await expect(claimCard(page, CLAIM.trip)).toHaveCount(1);
    await expect(claimCard(page, CLAIM.misc)).toHaveCount(1);

    await filters.getByRole("button", { name: "Ready to submit 5" }).click();
    await expect(cards).toHaveCount(5);
    for (const text of await cards.allInnerTexts()) expect(text).toContain("Ready to submit");

    await filters.getByRole("button", { name: /^Approved/ }).click();
    await expect(page.getByText("No claims with this status")).toBeVisible();
  });

  test("the trip: one open question, the alcohol bill (clause 6.1) and the meals limit (5.1)", async ({
    page,
  }) => {
    await openClaim(page, CLAIM.trip);
    await expect(page.getByText("Needs your input").first()).toBeVisible();
    await expect(
      page.getByText("Goes to finance review because of 1 high-risk flag and 1 warning"),
    ).toBeVisible();
    await expect(page.getByText("Evidence · 6 receipts")).toBeVisible();

    // the assistant asks ONE question (the calendar lists the trip as travel, not as a client
    // event), and nothing can be submitted until it is answered
    const chat = page.getByRole("log", { name: "Conversation with ClaimPilot" });
    await expect(chat).toContainText(
      "What was the business purpose of the Mumbai trip 9–10 Oct 2026?",
    );
    await expect(page.getByRole("button", { name: "Confirm & submit" })).toBeDisabled();
    await expect(page.getByText("Answer the open question to enable submit.")).toBeVisible();

    // a flag about the whole claim: the day's meals over the limit, with the clause quoted
    const meals = page
      .getByRole("listitem")
      .filter({ hasText: "add up to ₹3,178 (one bill), above the ₹2,000 daily limit" });
    await expect(meals).toContainText("Warning");
    await expect(meals).toContainText("Policy check");
    await expect(
      meals.getByText("Daily limit", { exact: true }).locator("xpath=following-sibling::dd"),
    ).toHaveText("₹2,000.00");
    await expect(
      meals.getByText("Meals that day", { exact: true }).locator("xpath=following-sibling::dd"),
    ).toHaveText("₹3,178.00");
    await expect(meals.getByRole("figure")).toContainText("Policy clause 5.1");

    // the alcohol bill opens by itself: what was read, the beer and the whisky, and clause 6.1
    const bill = receipt(page, "15-dinner-mumbai-10-oct.jpg");
    await expect(bill).toContainText("KESAR GRILL HOUSE");
    await expect(bill).toContainText("₹3,178.00");
    await expect(bill).toContainText("Draught Beer 330ml");
    await expect(bill).toContainText("Whisky 60ml");
    const alcohol = bill
      .getByRole("listitem")
      .filter({ hasText: "This bill includes alcohol (2 lines, ₹1,540)" });
    await expect(alcohol).toContainText("High risk");
    await expect(alcohol).toContainText("Policy check");
    await expect(alcohol.getByRole("figure")).toContainText("Policy clause 6.1");
    await expect(alcohol.getByRole("figure")).toContainText("Alcohol is never reimbursable");
    await expect(
      alcohol
        .getByText("Alcohol to take out", { exact: true })
        .locator("xpath=following-sibling::dd"),
    ).toHaveText("₹1,540.00");
    await expect(
      alcohol.getByText("Bill total", { exact: true }).locator("xpath=following-sibling::dd"),
    ).toHaveText("₹3,178.00");
  });

  test("the duplicate: caught as a copy of the first dinner, named by its file, sent to finance", async ({
    page,
  }) => {
    await openClaim(page, CLAIM.dinner, "1 high risk");
    await expect(
      page.getByText("Goes to finance review because of 1 high-risk flag."),
    ).toBeVisible();
    const copy = receipt(page, "12-client-dinner-saffron-terrace-copy.jpg");
    await expect(copy).toContainText("SAFFRON TERRACE");
    await expect(copy).toContainText("Needs review");
    const finding = copy.getByRole("listitem").filter({ hasText: "almost identical to" });
    await expect(finding).toContainText("High risk");
    await expect(finding).toContainText("Trust check");
    // the earlier receipt is named by its file name, never by a 32-character id
    await expect(finding).toContainText("almost identical to 01-client-dinner-saffron-terrace.jpg");
    await expect(finding).not.toContainText(/[0-9a-f]{32}/);

    // the first dinner, on the other hand, has nothing against it
    await openClaim(page, CLAIM.dinner, "No flags");
    await expect(page.getByText("No flags").first()).toBeVisible();
    await expect(
      page.getByText(
        "Low risk: small, clean and complete, so the approver can approve it in one click.",
      ),
    ).toBeVisible();
  });

  test("the edited total: the bill contradicts itself, in plain sight", async ({ page }) => {
    await openClaim(page, CLAIM.conveyance);
    await expect(page.getByText("Evidence · 4 receipts")).toBeVisible();
    const cab = receipt(page, "13-cab-pune-shivajinagar-to-baner.jpg");
    await expect(cab).toContainText("Sawari Rides");
    await expect(cab).toContainText("Blocked");
    const finding = cab
      .getByRole("listitem")
      .filter({ hasText: "doesn't match the bill's own items, taxes and charges (₹330.96)" });
    await expect(finding).toContainText("High risk");
    await expect(finding).toContainText("Trust check");
    await expect(
      finding
        .getByText("Worked out from the items", { exact: true })
        .locator("xpath=following-sibling::dd"),
    ).toHaveText("₹330.96");
    await expect(
      finding.getByText("Printed total", { exact: true }).locator("xpath=following-sibling::dd"),
    ).toHaveText("₹830.96");
    // nothing is open: the claim is complete, and still goes to a person
    await expect(page.getByText("All set")).toBeVisible();
    await expect(
      page.getByText("Goes to finance review because of 1 high-risk flag."),
    ).toBeVisible();
    // the other cabs and the handwritten slip are fine
    for (const file of [
      "07-cab-pune-to-kestrel-office.jpg",
      "08-cab-pune-hinjewadi-to-kothrud.png",
      "09-auto-slip-pune.jpg",
    ]) {
      await expect(receipt(page, file)).toContainText("No flags");
    }
    await expect(receipt(page, "09-auto-slip-pune.jpg")).toContainText("Ramesh Jadhav");
  });

  test("the prompt injection: a note to the AI reviewer is data, never an instruction", async ({
    page,
  }) => {
    await openClaim(page, CLAIM.meals);
    const cafe = receipt(page, "14-cafe-bill-banyan-pune.jpg");
    await expect(cafe).toContainText("BANYAN CAFE");
    await expect(cafe).toContainText("₹378.00");
    await expect(cafe).toContainText("Blocked");
    const finding = cafe
      .getByRole("listitem")
      .filter({ hasText: "text addressed to an AI reviewer or automated system" });
    await expect(finding).toContainText("High risk");
    await expect(finding).toContainText("treated as part of the document and ignored");
    await expect(finding).toContainText("needs a human review");
    await expect(
      page.getByText("Goes to finance review because of 1 high-risk flag."),
    ).toBeVisible();
    // the claim did NOT get approved because the note asked for it
    await expect(page.getByText("Finance review").first()).toBeVisible();
    await expect(page.getByText("Low risk").filter({ visible: true })).toHaveCount(0);
  });

  test("the UPI payment: not sure what it was for, so it asks instead of guessing", async ({
    page,
  }) => {
    await openClaim(page, CLAIM.misc);
    const chat = page.getByRole("log", { name: "Conversation with ClaimPilot" });
    await expect(chat).toContainText("The ₹120 UPI payment to “SUNIL BHOSALE” looks personal");
    const upi = receipt(page, "11-upi-payment-120.png");
    await expect(upi).toContainText("SUNIL BHOSALE");
    await expect(upi).toContainText(/Low \d+%/); // System Two is unsure of the category: it asks
    const finding = upi
      .getByRole("listitem")
      .filter({ hasText: "looks personal rather than business" });
    await expect(finding).toContainText("Warning");
    await expect(finding.getByRole("figure")).toContainText("Policy clause 10.1");
    await expect(page.getByRole("button", { name: "Confirm & submit" })).toBeDisabled();
  });

  test("a clean claim needs nothing from Asha: the calendar answered, ready to submit", async ({
    page,
  }) => {
    await openClaim(page, CLAIM.dinner, "No flags");
    await expect(page.getByText("All set")).toBeVisible();
    // the questions about who attended and why were answered from the calendar, and says so
    await expect(page.getByText("Attendees").first()).toBeVisible();
    await expect(page.getByText("From your calendar").first()).toBeVisible();
    await expect(page.getByText(/Kestrel Logistics/).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Confirm & submit" })).toBeEnabled();

    await openClaim(page, CLAIM.mobile);
    await expect(page.getByText("All set")).toBeVisible();
    await expect(page.getByText("Low risk").first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Confirm & submit" })).toBeEnabled();
  });

  test("every decision names the engine that made it, and which model read the receipt", async ({
    page,
  }) => {
    await openClaim(page, CLAIM.trip);
    const bill = receipt(page, "15-dinner-mumbai-10-oct.jpg");
    await expect(bill.getByText("How it was decided")).toBeVisible();
    // the hosted demo has no Jev: the language model (System Two) decides, and the card says so
    await expect(bill).toContainText("Category decided by LLM");
    await expect(bill).toContainText("System Two");
    await expect(bill).toContainText("Receipt read by claude-haiku-5-5");
    await expect(bill.getByText("Alcohol on the bill")).toBeVisible();
    await expect(bill.getByLabel("above the policy threshold")).toBeVisible();
  });

  test("the original receipt is shown next to what was read, images and PDFs alike", async ({
    page,
  }) => {
    await openClaim(page, CLAIM.dinner, "No flags");
    const photo = await openReceipt(page, "01-client-dinner-saffron-terrace.jpg");
    const image = photo.getByRole("img", {
      name: "Original receipt: 01-client-dinner-saffron-terrace.jpg",
    });
    await expect(image).toBeVisible();
    await expect
      .poll(async () => image.evaluate((el: HTMLImageElement) => el.naturalWidth))
      .toBeGreaterThan(100);
    // fetched with the persona header and shown from a blob URL
    expect(await image.getAttribute("src")).toMatch(/^blob:/);
    await expect(photo.getByRole("button", { name: "Zoom in" })).toBeVisible();
    await expect(photo.getByRole("link", { name: "Open original" })).toBeVisible();
    await expect(photo.getByText("Paneer Tikka")).toBeVisible(); // line items, from the receipt

    // a PDF (the hotel folio) is shown by the browser's own viewer, or, where the browser cannot
    // embed one (a phone), by a button that opens it: either way it carries the same name
    await openClaim(page, CLAIM.trip);
    const folio = await openReceipt(page, "05-hotel-folio-mumbai.pdf");
    await expect(folio).toContainText("Lotus Bay Residency");
    await expect(folio).toContainText("₹6,090.00");
    await expect(folio.getByLabel("Original receipt: 05-hotel-folio-mumbai.pdf")).toBeVisible();
    await expect(
      folio.getByRole("link", { name: /^Open (original|the PDF)$/ }).first(),
    ).toBeVisible();
  });

  test("click-to-verify: selecting a field highlights where it is printed on the original", async ({
    page,
    isMobile,
  }) => {
    await openClaim(page, CLAIM.dinner, "No flags");
    const photo = await openReceipt(page, "01-client-dinner-saffron-terrace.jpg");
    const image = photo.getByRole("img", { name: /^Original receipt/ });
    await expect(image).toBeVisible();
    await expect
      .poll(async () => image.evaluate((el: HTMLImageElement) => el.naturalWidth))
      .toBeGreaterThan(100);

    // the API located the main fields: they are buttons with a pin, the others are not
    const total = photo.getByRole("button", { name: /^Total/ });
    await expect(total.getByLabel("Location on the receipt available")).toBeVisible();
    await expect(photo.getByTestId("verify-highlight")).toHaveCount(0);
    await expect(
      photo.getByText("Select a field to see where it is printed on the receipt."),
    ).toBeVisible();

    await total.click();
    await expect(total).toHaveAttribute("aria-pressed", "true");
    const highlight = photo.getByTestId("verify-highlight");
    await expect(highlight).toBeVisible();
    await expect(photo.getByText("Highlighted on the receipt: Total.")).toBeVisible();

    // the highlight sits on the picture (inside the image's own box, which may be zoomed)
    const [imageBox, markBox] = [await image.boundingBox(), await highlight.boundingBox()];
    expect(imageBox && markBox).toBeTruthy();
    expect(markBox!.x).toBeGreaterThanOrEqual(imageBox!.x - 1);
    expect(markBox!.y).toBeGreaterThanOrEqual(imageBox!.y - 1);
    expect(markBox!.x + markBox!.width).toBeLessThanOrEqual(imageBox!.x + imageBox!.width + 1);
    expect(markBox!.y + markBox!.height).toBeLessThanOrEqual(imageBox!.y + imageBox!.height + 1);

    // on a phone the whole receipt is tiny, so selecting a field zooms in to make it legible
    await expect(photo.getByText(isMobile ? "200%" : "100%", { exact: true })).toBeVisible();

    // another located field moves the highlight; a field without a location says so instead
    const merchant = photo.getByRole("button", { name: /^Merchant/ });
    await merchant.click();
    await expect(merchant).toHaveAttribute("aria-pressed", "true");
    await expect(total).toHaveAttribute("aria-pressed", "false");
    await expect(photo.getByText("Highlighted on the receipt: Merchant.")).toBeVisible();
    const city = photo.getByRole("button", { name: /^City/ });
    await city.click();
    await expect(photo.getByTestId("verify-highlight")).toHaveCount(0);
    await expect(
      photo.getByText(/City is selected, but its position on the receipt isn't available/),
    ).toBeVisible();
  });

  test("a finding points at the field it is about: 'Show on the receipt'", async ({ page }) => {
    await openClaim(page, CLAIM.conveyance);
    const cab = receipt(page, "13-cab-pune-shivajinagar-to-baner.jpg");
    const finding = cab
      .getByRole("listitem")
      .filter({ hasText: "doesn't match the bill's own items, taxes and charges" });
    await expect(cab.getByTestId("verify-highlight")).toHaveCount(0);
    await finding.getByRole("button", { name: "Show on the receipt" }).click();
    // the edited total, right where it is printed
    await expect(cab.getByTestId("verify-highlight")).toBeVisible();
    await expect(cab.getByRole("button", { name: /^Total/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  test("a receipt the API could not locate anything on shows plain values, not dead buttons", async ({
    page,
  }) => {
    await openClaim(page, CLAIM.mobile);
    const bill = await openReceipt(page, "10-mobile-bill-october.pdf");
    await expect(bill).toContainText("Nakshatra Mobile");
    await expect(bill).toContainText("₹1,059.64");
    await expect(bill.getByText("Check these values against the original receipt.")).toBeVisible();
    await expect(bill.getByRole("button", { name: /^Total/ })).toHaveCount(0);
    await expect(bill.getByTestId("verify-highlight")).toHaveCount(0);
  });
});
