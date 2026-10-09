import AxeBuilder from "@axe-core/playwright";
import { expect, type Locator, type Page } from "@playwright/test";

import { API, BASE_URL, E2E_SANDBOX, PERSONA, resetEverything } from "./env";

export { API, BASE_URL, E2E_SANDBOX, PERSONA };

/**
 * The claims the demo pile turns into (data/synth/demo/README.md, "What should happen"):
 * 15 documents, 7 claims. Two claims are titled "Client dinner 6 Oct 2026": the first bill's
 * (clean, auto-approved) and the duplicate's own (finance review): tell them apart with `DINNER`.
 */
export const CLAIM = {
  dinner: "Client dinner 6 Oct 2026",
  trip: "Mumbai trip 9–10 Oct 2026",
  conveyance: "Local conveyance Oct 2026",
  meals: "Meals Oct 2026",
  misc: "Miscellaneous Oct 2026",
  mobile: "Mobile & internet Oct 2026",
} as const;

/** The 15 documents of the pile, by file name (what the live-progress cards show). */
export const DOCUMENTS = 15;
export const CLAIMS = 7;

/** Start every test from nothing: "Start over" for every persona. */
export async function resetDemo(): Promise<void> {
  await resetEverything();
}

/** Act as a persona from the first page load (the app saves the choice in localStorage). */
export async function actAs(page: Page, personaId: string): Promise<void> {
  await page.addInitScript(
    ([id, sandbox]) => {
      // Every visitor has a sandbox: the suite's own, so API seeding and the browser agree.
      window.localStorage.setItem("claimpilot.sandbox", sandbox);
      // Only the very first load: a persona picked later in the test must survive reloads.
      if (!window.localStorage.getItem("claimpilot.persona")) {
        window.localStorage.setItem("claimpilot.persona", id);
      }
    },
    [personaId, E2E_SANDBOX] as const,
  );
}

/** Wait until the app knows who is acting (the header's persona picker is filled in). */
export async function waitForPersona(page: Page): Promise<void> {
  await expect(page.getByRole("combobox", { name: "Acting as" })).toHaveValue(/.+/);
}

/** Click "Try with sample receipts" and wait for the live-processing screen; returns the batch id. */
export async function uploadSamples(page: Page): Promise<string> {
  await page.goto("/");
  await waitForPersona(page);
  const button = page.getByRole("button", { name: "Try with sample receipts" });
  await expect(button).toBeEnabled();
  await button.click();
  await expect(page).toHaveURL(/\/batches\/[0-9a-f]{8,}/);
  return new URL(page.url()).pathname.split("/").pop() ?? "";
}

/**
 * A batch that finishes while you watch opens its claims by itself after a short countdown. Tests
 * that want to look at the finished batch page press "Stay here" (when the countdown is running).
 */
export async function stayOnBatchPage(page: Page): Promise<void> {
  const stay = page.getByRole("button", { name: "Stay here" });
  if (await stay.isVisible()) await stay.click();
}

/** Upload the sample pack and wait until every receipt has been read, checked and grouped. */
export async function uploadSamplesAndWait(page: Page): Promise<void> {
  await uploadSamples(page);
  await expect(page.getByRole("heading", { level: 1, name: `${CLAIMS} claims ready` })).toBeVisible(
    { timeout: 90_000 },
  );
  await stayOnBatchPage(page);
}

/** A claim's card on /claims, found by its title and (when titles repeat) other words on it. */
export function claimCard(page: Page, title: string, others?: string | RegExp): Locator {
  let card = page.getByTestId("claim-card").filter({ visible: true, hasText: title });
  if (others) card = card.filter({ hasText: others });
  return card;
}

/** Open a claim from the "My claims" list by its title (plus `others` when titles repeat). */
export async function openClaim(
  page: Page,
  title: string,
  others?: string | RegExp,
): Promise<void> {
  await page.goto("/claims");
  const card = claimCard(page, title, others);
  await expect(card).toHaveCount(1);
  await card.getByRole("link", { name: title, exact: true }).click();
  await expect(page).toHaveURL(/\/claims\/clm-/);
  await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();
}

/** Expand one receipt of the open claim by (part of) its merchant or file name. */
export async function openReceipt(page: Page, name: string | RegExp): Promise<Locator> {
  const card = page.getByTestId("document-card").filter({ hasText: name });
  await expect(card).toHaveCount(1);
  // Receipts with a serious flag open by themselves; the others start collapsed.
  const toggle = card.locator("button[aria-expanded]").first();
  if ((await toggle.getAttribute("aria-expanded")) === "false") await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  return card;
}

/** WCAG 2.x A/AA (plus axe best practices) must report zero violations on the page as shown. */
export async function expectNoA11yViolations(page: Page, label: string): Promise<void> {
  // Colours are read from computed styles: let running transitions and animations finish first,
  // otherwise a button that has just been enabled is measured halfway between two colours.
  await page.evaluate(() => {
    const finite = document
      .getAnimations()
      .filter((animation) => animation.effect?.getComputedTiming().iterations !== Infinity);
    const settled = Promise.all(finite.map((animation) => animation.finished.catch(() => null)));
    return Promise.race([settled, new Promise((resolve) => setTimeout(resolve, 2000))]);
  });
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"])
    .analyze();
  const summary = results.violations.map((violation) => ({
    rule: violation.id,
    impact: violation.impact,
    help: violation.help,
    nodes: violation.nodes.slice(0, 4).map((node) => ({
      target: node.target.join(" "),
      summary: node.failureSummary?.split("\n").slice(0, 3).join(" | "),
    })),
  }));
  expect(summary, `axe violations on: ${label}`).toEqual([]);
}

/** The page must not scroll sideways (a classic small-screen bug). */
export async function expectNoHorizontalOverflow(page: Page, label: string): Promise<void> {
  const overflow = await page.evaluate(() => {
    const root = document.documentElement;
    return { scrollWidth: root.scrollWidth, clientWidth: root.clientWidth };
  });
  expect(overflow.scrollWidth, `horizontal overflow on: ${label}`).toBeLessThanOrEqual(
    overflow.clientWidth,
  );
}
