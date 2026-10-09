import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { getStartedOverNotice } from "@/lib/demo/notice";
import { ASHA, META, META_DEMO, RAVI } from "@/test/fixtures";
import { router } from "@/test/navigation";
import { renderApp } from "@/test/render";
import { problem, url } from "@/test/server";

import { describeReset, StartOver } from "./StartOver";

const demo = () => http.get(url("/v1/meta"), () => HttpResponse.json(META_DEMO));
const reset = (counts = { batches: 2, documents: 11, claims: 10 }) =>
  http.post(url("/v1/demo/reset"), () => HttpResponse.json(counts));

describe("StartOver", () => {
  it("is hidden when this is not the public demo", async () => {
    renderApp(<StartOver />, {
      handlers: [http.get(url("/v1/meta"), () => HttpResponse.json(META))],
    });
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect(screen.queryByRole("button", { name: "Start over" })).not.toBeInTheDocument();
  });

  it("always asks before deleting, and cancelling deletes nothing", async () => {
    const user = userEvent.setup();
    let calls = 0;
    renderApp(<StartOver />, {
      handlers: [
        demo(),
        http.post(url("/v1/demo/reset"), () => ((calls += 1), HttpResponse.json({}))),
      ],
    });
    await user.click(await screen.findByRole("button", { name: "Start over" }));
    const dialog = screen.getByRole("dialog", { name: "Start over?" });
    expect(dialog).toHaveAccessibleDescription(
      "This deletes the receipts and claims you uploaded in this demo session so you can run the samples again. Nobody else's data is affected.",
    );
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calls).toBe(0);
  });

  it("deletes the persona's data, remembers the confirmation and goes to the upload screen", async () => {
    const user = userEvent.setup();
    let persona: string | null = null;
    renderApp(<StartOver />, {
      handlers: [
        demo(),
        http.post(url("/v1/demo/reset"), ({ request }) => {
          persona = request.headers.get("X-Persona");
          return HttpResponse.json({ batches: 2, documents: 11, claims: 10 });
        }),
      ],
    });
    await user.click(await screen.findByRole("button", { name: "Start over" }));
    await user.click(screen.getByRole("button", { name: "Delete and start over" }));
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/"));
    expect(persona).toBe(ASHA.id);
    expect(getStartedOverNotice()).toBe("Started over. Removed 11 receipts and 10 claims.");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("can stay on the page (link variant on the upload screen)", async () => {
    const user = userEvent.setup();
    renderApp(<StartOver variant="link" redirectTo={null} />, { handlers: [demo(), reset()] });
    await user.click(await screen.findByRole("button", { name: "Start over" }));
    await user.click(screen.getByRole("button", { name: "Delete and start over" }));
    await waitFor(() => expect(getStartedOverNotice()).toMatch(/^Started over\./));
    expect(router.push).not.toHaveBeenCalled();
  });

  it("tells an approver it clears the whole demo session, including the approvals queue, and nobody else's data", async () => {
    const user = userEvent.setup();
    renderApp(<StartOver />, { persona: RAVI.id, handlers: [demo(), reset()] });
    await user.click(await screen.findByRole("button", { name: "Start over" }));
    await waitFor(() =>
      expect(screen.getByRole("dialog")).toHaveAccessibleDescription(
        "As an approver this clears everything in this demo session, including the approvals queue. Nobody else's data is affected.",
      ),
    );
  });

  it("shows a friendly message and keeps the dialog open when the reset fails", async () => {
    const user = userEvent.setup();
    renderApp(<StartOver />, {
      handlers: [
        demo(),
        http.post(url("/v1/demo/reset"), () =>
          problem(404, "demo_disabled", "Start over is for the demo only"),
        ),
      ],
    });
    await user.click(await screen.findByRole("button", { name: "Start over" }));
    await user.click(screen.getByRole("button", { name: "Delete and start over" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Start over isn't available");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(router.push).not.toHaveBeenCalled();
  });

  it("refreshes what was cached for the persona after a reset", async () => {
    const user = userEvent.setup();
    const { client } = renderApp(<StartOver />, { handlers: [demo(), reset()] });
    client.setQueryData(["p", ASHA.id, "claims"], [{ id: "old" }]);
    client.setQueryData(["p", ASHA.id, "claim", "old"], { id: "old" });
    await user.click(await screen.findByRole("button", { name: "Start over" }));
    await user.click(screen.getByRole("button", { name: "Delete and start over" }));
    await waitFor(() => expect(router.push).toHaveBeenCalled());
    expect(client.getQueryData(["p", ASHA.id, "claim", "old"])).toBeUndefined(); // deleted data is dropped
    expect(client.getQueryState(["p", ASHA.id, "claims"])?.isInvalidated).toBe(true); // lists refetch
  });
});

describe("describeReset", () => {
  it("counts receipts and claims in words", () => {
    expect(describeReset({ batches: 1, documents: 1, claims: 1 })).toBe(
      "Removed 1 receipt and 1 claim.",
    );
    expect(describeReset({ batches: 0, documents: 0, claims: 0 })).toBe(
      "Removed 0 receipts and 0 claims.",
    );
  });
});
