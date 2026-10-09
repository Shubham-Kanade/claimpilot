import { expect, test } from "@playwright/test";

import { callApi, seedPile, uploadUnknownFile } from "./pile";
import {
  actAs,
  expectNoA11yViolations,
  expectNoHorizontalOverflow,
  PERSONA,
  resetDemo,
  waitForPersona,
} from "./support";

/** AI ops (/operations): how the AI calls are going, system-wide, plus this session's failures. */
test.describe("AI ops", () => {
  let profile = "replay";

  test.beforeAll(async () => {
    await resetDemo();
    await seedPile();
    profile = (await callApi<{ llm_mode: string }>("GET", "/v1/meta")).llm_mode;
    // a receipt that is not a sample: the recorded profile cannot read it, which is a failure to see
    if (profile === "replay") await uploadUnknownFile();
  });

  test.beforeEach(async ({ page }) => {
    await actAs(page, PERSONA.asha);
  });

  test("is in the header for everyone and shows the KPI tiles and the route table", async ({
    page,
  }) => {
    await page.goto("/");
    await waitForPersona(page);
    await page.getByRole("link", { name: "AI ops" }).click();
    await expect(page).toHaveURL(/\/operations$/);
    await expect(page.getByRole("heading", { level: 1, name: "AI operations" })).toBeVisible();
    await expect(
      page.getByText("How the AI calls are going: system-wide, all visitors."),
    ).toBeVisible();

    for (const tile of ["calls", "live", "errors", "cost", "cache"]) {
      await expect(page.getByTestId(`kpi-${tile}`)).toBeVisible();
    }
    // the pile was read through the API: the AI was called (recorded or live) at least 15 times
    const calls = await page.getByTestId("kpi-calls").innerText();
    expect(Number(calls.replace(/\D/g, "").slice(0, 4))).toBeGreaterThan(14);
    await expect(page.getByTestId("kpi-cost")).toContainText("not spent");

    const table = page.getByRole("table", { name: /AI calls per route and model/ });
    await expect(
      table.getByRole("columnheader", { name: "Recorded cost (as recorded)" }),
    ).toBeVisible();
    await expect(table.getByRole("row", { name: /^Extraction haiku/ })).toBeVisible();
    await expectNoA11yViolations(page, "AI ops");
  });

  test("the window selector asks for 1 hour, 24 hours or 7 days", async ({ page }) => {
    await page.goto("/operations");
    await waitForPersona(page);
    const group = page.getByRole("group", { name: "Time window" });
    await expect(group.getByRole("button", { name: "24 h" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    const asked = page.waitForRequest((r) => r.url().includes("/v1/ops/llm?hours=168"));
    await group.getByRole("button", { name: "7 days" }).click();
    await asked;
    await expect(group.getByRole("button", { name: "7 days" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(page.getByTestId("kpi-calls")).toBeVisible();
  });

  test("a failure of this session can be traced", async ({ page }) => {
    test.skip(
      profile !== "replay",
      "the failure comes from a file the recorded profile cannot read",
    );
    await page.goto("/operations");
    await waitForPersona(page);
    const failures = page.getByRole("list", { name: "Recent failures" });
    const item = failures.getByRole("listitem").first();
    await expect(item).toContainText("Not recorded");
    await expect(item).toContainText("only the sample receipts can be read");
    await item.getByRole("button", { name: /Show trace/ }).click();
    // the trace id is the backend's own (not the batch id): the field is filled with it
    await expect(page.getByLabel("Trace id")).toHaveValue(/^[0-9a-zA-Z_-]{8,64}$/);
    const trace = page.getByRole("table", { name: /calls of one trace/i });
    await expect(trace).toBeVisible();
    await expect(trace.getByRole("row", { name: /Extraction/ })).toContainText("Recorded");
    await expectNoA11yViolations(page, "AI ops with a trace");
  });

  test("fits at this width, with tables that scroll in a region the keyboard reaches", async ({
    page,
  }) => {
    await page.goto("/operations");
    await waitForPersona(page);
    await expect(page.getByTestId("kpi-calls")).toBeVisible();
    await expectNoHorizontalOverflow(page, "AI ops");
    const region = page.getByRole("group", { name: /AI calls by route and model/ });
    await region.focus();
    await expect(region).toBeFocused();
  });
});
