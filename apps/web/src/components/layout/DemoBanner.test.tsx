import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { README_URL } from "@/lib/config";
import { META, META_DEMO } from "@/test/fixtures";
import { renderApp } from "@/test/render";
import { url } from "@/test/server";

import { DemoBanner } from "./DemoBanner";

const demoMeta = () => [http.get(url("/v1/meta"), () => HttpResponse.json(META_DEMO))];

describe("DemoBanner", () => {
  it("says what the demo is, in plain words, with a link to the README", async () => {
    renderApp(<DemoBanner />, { handlers: demoMeta() });
    const banner = await screen.findByTestId("demo-banner");
    expect(banner).toHaveTextContent(
      "Demo: the sample receipts are replayed from recordings, nothing here is real data. To read your own receipts run ClaimPilot locally with your own API key.",
    );
    const link = screen.getByRole("link", { name: "See the README" });
    expect(link).toHaveAttribute("href", README_URL);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  it("in the hybrid profile (live + record) says who reads uploads, the shared budget and what not to upload", async () => {
    renderApp(<DemoBanner />, {
      handlers: [
        http.get(url("/v1/meta"), () =>
          HttpResponse.json({
            ...META_DEMO,
            llm_mode: "live",
            llm_record: true,
            daily_llm_budget_usd: 1.5,
          }),
        ),
      ],
    });
    const banner = await screen.findByTestId("demo-banner");
    expect(banner).toHaveTextContent(
      "Demo: the sample receipts come from recordings. Receipts you upload yourself are read by Anthropic's Claude API, with a shared daily budget of $1.50 for everyone. Upload only synthetic or non-personal receipts, no real company data.",
    );
    expect(banner).not.toHaveTextContent("replayed from recordings");
    expect(screen.getByRole("link", { name: "See the README" })).toBeInTheDocument();
  });

  it("when fully live does not claim the samples are recordings", async () => {
    renderApp(<DemoBanner />, {
      handlers: [
        http.get(url("/v1/meta"), () =>
          HttpResponse.json({ ...META_DEMO, llm_mode: "live", llm_record: false }),
        ),
      ],
    });
    const banner = await screen.findByTestId("demo-banner");
    expect(banner).toHaveTextContent(
      "Receipts you upload yourself are read by Anthropic's Claude API",
    );
    expect(banner).toHaveTextContent("shared daily budget of $1.00");
    expect(banner).not.toHaveTextContent("come from recordings");
  });

  it("keeps the README link in one constant (no URL hard-coded in the UI)", () => {
    expect(README_URL).toMatch(/^https:\/\/.+/);
  });

  it("is not shown outside the demo", async () => {
    renderApp(<DemoBanner />, {
      handlers: [http.get(url("/v1/meta"), () => HttpResponse.json(META))],
    });
    await waitFor(() => expect(document.body).toBeInTheDocument());
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByTestId("demo-banner")).not.toBeInTheDocument();
  });

  it("can be dismissed for the rest of the session", async () => {
    const user = userEvent.setup();
    const { unmount } = renderApp(<DemoBanner />, { handlers: demoMeta() });
    await user.click(await screen.findByRole("button", { name: "Dismiss demo notice" }));
    expect(screen.queryByTestId("demo-banner")).not.toBeInTheDocument();
    expect(window.sessionStorage.getItem("claimpilot.demo-banner-dismissed")).toBe("1");

    // a new page load in the same session (a fresh render) does not bring it back
    unmount();
    renderApp(<DemoBanner />, { handlers: demoMeta() });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByTestId("demo-banner")).not.toBeInTheDocument();
  });

  it("survives storage being blocked (it just comes back next time)", async () => {
    const user = userEvent.setup();
    renderApp(<DemoBanner />, { handlers: demoMeta() });
    const banner = await screen.findByTestId("demo-banner");
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = () => {
      throw new Error("denied");
    };
    try {
      await user.click(screen.getByRole("button", { name: "Dismiss demo notice" }));
    } finally {
      Storage.prototype.setItem = original;
    }
    expect(banner).toBeInTheDocument();
  });
});
