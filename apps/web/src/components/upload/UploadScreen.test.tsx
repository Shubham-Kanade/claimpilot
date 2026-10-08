import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { setStartedOverNotice } from "@/lib/demo/notice";
import { getStoredPersona } from "@/lib/persona/store";
import { getToasts } from "@/lib/toast";
import { SAMPLE_RECEIPTS } from "@/lib/upload/samples";
import { ADVIKA, ASHA, META, META_DEMO, RAVI, makeStats } from "@/test/fixtures";
import { router } from "@/test/navigation";
import { renderApp } from "@/test/render";
import { problem, server, url } from "@/test/server";

import { UploadScreen } from "./UploadScreen";

const png = (name: string, size = 10) => {
  const file = new File(["x"], name, { type: "image/png", lastModified: name.length });
  Object.defineProperty(file, "size", { value: size });
  return file;
};

function created(batchId = "bat-000001") {
  return HttpResponse.json(
    {
      batch_id: batchId,
      status: "queued",
      documents: [],
      events_url: `/v1/batches/${batchId}/events`,
    },
    { status: 202 },
  );
}

async function ready() {
  const input = await screen.findByLabelText("Choose receipts to upload");
  await waitFor(() => expect(input).not.toBeDisabled());
  return input;
}

describe("UploadScreen", () => {
  it("explains the product, the three steps and which engines do what", async () => {
    renderApp(<UploadScreen />);
    expect(
      screen.getByRole("heading", {
        level: 1,
        name: /From a pile of receipts to a claim that's ready/,
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("Drop the whole pile")).toBeInTheDocument();
    expect(screen.getByText("Answer once")).toBeInTheDocument();
    expect(screen.getByText("Confirm and submit")).toBeInTheDocument();
    // transparency footer from GET /v1/meta
    expect(await screen.findByText("LLM mode: replay")).toBeInTheDocument();
    expect(screen.getByText(/Decisions by: Jev \(System One\)/)).toBeInTheDocument();
    expect(screen.getAllByText("claude-haiku-5-5").length).toBeGreaterThan(0);
    expect(screen.getByText(/overridden/)).toBeInTheDocument();
    expect(META.routes).toHaveLength(3);
  });

  it("shows the impact meter on the page", async () => {
    renderApp(<UploadScreen />, {
      handlers: [
        http.get(url("/v1/stats"), () =>
          HttpResponse.json(makeStats({ documents_processed: 123 })),
        ),
      ],
    });
    expect(await screen.findByText("123")).toBeInTheDocument();
    expect(screen.getByText("Time saved (estimated)")).toBeInTheDocument();
  });

  it("adds files as removable thumbnails and uploads them as one batch", async () => {
    const user = userEvent.setup({ applyAccept: false });
    let uploaded = 0;
    server.use(
      http.post(url("/v1/batches"), async ({ request }) => {
        uploaded = (await request.formData()).getAll("files").length;
        return created("bat-000007");
      }),
    );
    renderApp(<UploadScreen />);
    await user.upload(await ready(), [png("a.png"), png("b.png"), png("c.png")]);

    const list = screen.getByRole("list", { name: "Receipts to upload" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(3);
    expect(screen.getByText(/3 receipts ready/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Remove b.png" }));
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: "Process 2 receipts" }));
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/batches/bat-000007"));
    expect(uploaded).toBe(2);
  });

  it("clears the whole pile", async () => {
    const user = userEvent.setup({ applyAccept: false });
    renderApp(<UploadScreen />);
    await user.upload(await ready(), [png("a.png"), png("b.png")]);
    await user.click(screen.getByRole("button", { name: /Clear all/ }));
    expect(screen.queryByRole("list", { name: "Receipts to upload" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Process/ })).not.toBeInTheDocument();
  });

  it("tells people which files were skipped and why (type, size, duplicates)", async () => {
    const user = userEvent.setup({ applyAccept: false });
    renderApp(<UploadScreen />);
    const input = await ready();
    const notes = new File(["x"], "notes.docx", { type: "application/msword" });
    await user.upload(input, [png("ok.png"), notes, png("huge.png", 16 * 1024 * 1024)]);
    const status = screen
      .getAllByRole("status")
      .find((el) => /Skipped 2 files/.test(el.textContent ?? ""));
    expect(status).toBeDefined();
    expect(status).toHaveTextContent(
      "notes.docx: only JPEG, PNG, WebP and PDF files are accepted.",
    );
    expect(status).toHaveTextContent("huge.png: 16.0 MB is over the 15 MB limit per file.");
    expect(screen.getByRole("button", { name: "Remove ok.png" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove notes.docx" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove huge.png" })).not.toBeInTheDocument();
    expect(screen.getByText(/1 receipt ready/)).toBeInTheDocument();

    await user.upload(input, [png("ok.png")]);
    expect(
      screen.getAllByRole("status").some((el) => /already added/.test(el.textContent ?? "")),
    ).toBe(true);
  });

  it("stops at 30 receipts", async () => {
    const user = userEvent.setup({ applyAccept: false });
    renderApp(<UploadScreen />);
    const files = Array.from({ length: 32 }, (_, i) => png(`receipt-${i}.png`));
    await user.upload(await ready(), files);
    expect(screen.getByText(/30 receipts ready/)).toBeInTheDocument();
    expect(
      screen
        .getAllByRole("status")
        .some((el) => /30 receipts is the most per upload/.test(el.textContent ?? "")),
    ).toBe(true);
  });

  it("shows friendly copy for server-side upload problems and keeps the pile", async () => {
    const user = userEvent.setup({ applyAccept: false });
    server.use(
      http.post(url("/v1/batches"), () =>
        problem(413, "file_too_large", "scan.png is larger than 15 MB"),
      ),
    );
    renderApp(<UploadScreen />);
    await user.upload(await ready(), [png("scan.png")]);
    await user.click(screen.getByRole("button", { name: "Process 1 receipt" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("A file is too large");
    expect(alert).toHaveTextContent("scan.png is larger than 15 MB");
    expect(screen.getByRole("list", { name: "Receipts to upload" })).toBeInTheDocument();
    expect(router.push).not.toHaveBeenCalled();
  });

  it("uploads the sample pack with one click (no files of your own needed)", async () => {
    const user = userEvent.setup();
    const fetched: string[] = [];
    let parts = 0;
    server.use(
      http.get("http://localhost:3000/samples/:file", ({ params }) => {
        fetched.push(String(params.file));
        return new HttpResponse(new Uint8Array([1, 2, 3]), {
          headers: { "Content-Type": "image/png" },
        });
      }),
      http.post(url("/v1/batches"), async ({ request }) => {
        parts = (await request.formData()).getAll("files").length;
        return created("bat-000002");
      }),
    );
    renderApp(<UploadScreen />);
    const button = await screen.findByRole("button", { name: "Try with sample receipts" });
    await waitFor(() => expect(button).toBeEnabled());
    await user.click(button);
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/batches/bat-000002"));
    expect(fetched).toHaveLength(SAMPLE_RECEIPTS.length);
    expect(parts).toBe(SAMPLE_RECEIPTS.length);
  });

  it("says what the sample pile contains, and lists the files in upload order", async () => {
    const user = userEvent.setup();
    renderApp(<UploadScreen />);
    expect(
      screen.getByText(
        /15 synthetic receipts from one week of Asha Menon's expenses, including a duplicate, an edited total, a note aimed at an AI reviewer and an alcohol bill/,
      ),
    ).toBeInTheDocument();
    await user.click(screen.getByText("What is in the sample pile?"));
    const pile = screen.getByTestId("sample-pile");
    const items = within(pile).getAllByRole("listitem");
    expect(items).toHaveLength(SAMPLE_RECEIPTS.length);
    expect(items[0]).toHaveTextContent(SAMPLE_RECEIPTS[0].label);
    expect(items.at(-1)).toHaveTextContent(SAMPLE_RECEIPTS.at(-1)!.label);
    // outside the public demo nothing is switched, cleared or announced
    expect(screen.queryByText(/Each run starts clean/)).not.toBeInTheDocument();
  });

  it("reports when the samples cannot be loaded", async () => {
    const user = userEvent.setup();
    server.use(
      http.get(
        "http://localhost:3000/samples/:file",
        () => new HttpResponse(null, { status: 404 }),
      ),
    );
    renderApp(<UploadScreen />);
    const button = await screen.findByRole("button", { name: "Try with sample receipts" });
    await waitFor(() => expect(button).toBeEnabled());
    await user.click(button);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load the sample receipts");
    expect(router.push).not.toHaveBeenCalled();
  });

  it("shows a friendly error when the API is unreachable", async () => {
    renderApp(<UploadScreen />, {
      handlers: [http.get(url("/v1/employees"), () => problem(503, "http_503", "down"))],
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong on our side");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  });
});

describe("UploadScreen in the public demo", () => {
  const demoMeta = () => http.get(url("/v1/meta"), () => HttpResponse.json(META_DEMO));

  /**
   * Records the order of the calls so we can prove "reset, then samples, then upload", and who
   * each API call was made as (the X-Persona header).
   */
  function recordCalls(resetResponse?: () => Response) {
    const order: string[] = [];
    const actors: Record<string, string | null> = {};
    server.use(
      http.post(url("/v1/demo/reset"), ({ request }) => {
        order.push("reset");
        actors.reset = request.headers.get("X-Persona");
        return resetResponse
          ? resetResponse()
          : HttpResponse.json({ batches: 1, documents: 11, claims: 10 });
      }),
      http.get("http://localhost:3000/samples/:file", () => {
        order.push("sample");
        return new HttpResponse(new Uint8Array([1]), { headers: { "Content-Type": "image/png" } });
      }),
      http.post(url("/v1/batches"), ({ request }) => {
        order.push("upload");
        actors.upload = request.headers.get("X-Persona");
        return created("bat-000003");
      }),
    );
    return Object.assign(order, { actors });
  }

  async function clickSamples(user: ReturnType<typeof userEvent.setup>) {
    const button = await screen.findByRole("button", { name: "Try with sample receipts" });
    await waitFor(() => expect(button).toBeEnabled());
    await user.click(button);
  }

  it("clears the employee's earlier uploads first, so every sample trial starts clean", async () => {
    const user = userEvent.setup();
    const order = recordCalls();
    renderApp(<UploadScreen />, { handlers: [demoMeta()] });
    expect(await screen.findByText(/Each run starts clean/)).toBeInTheDocument();
    await clickSamples(user);
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/batches/bat-000003"));
    expect(order[0]).toBe("reset");
    expect(order.filter((step) => step === "reset")).toHaveLength(1);
    expect(order.indexOf("sample")).toBeGreaterThan(order.indexOf("reset"));
    expect(order.at(-1)).toBe("upload");
    // already Asha: nothing to switch, nothing to announce
    expect(order.actors).toEqual({ reset: ASHA.id, upload: ASHA.id });
    expect(getToasts()).toEqual([]);
  });

  it("switches to Asha first (with a toast), then clears her uploads and uploads as her", async () => {
    const user = userEvent.setup();
    const order = recordCalls();
    renderApp(<UploadScreen />, { persona: ADVIKA.id, handlers: [demoMeta()] });
    await clickSamples(user);
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/batches/bat-000003"));

    // the recordings include Asha's calendar: everything is done as her, not as Advika
    expect(getStoredPersona()).toBe(ASHA.id);
    expect(order.actors).toEqual({ reset: ASHA.id, upload: ASHA.id });
    expect(order[0]).toBe("reset");
    expect(order.at(-1)).toBe("upload");
    expect(getToasts().map((toast) => toast.text)).toEqual([
      "The sample receipts are Asha Menon's, so we switched to her",
    ]);
  });

  it("also switches an approver to Asha, so only Asha's own uploads are cleared", async () => {
    const user = userEvent.setup();
    const order = recordCalls();
    renderApp(<UploadScreen />, { persona: RAVI.id, handlers: [demoMeta()] });
    await clickSamples(user);
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/batches/bat-000003"));
    expect(getStoredPersona()).toBe(ASHA.id);
    expect(order.actors.reset).toBe(ASHA.id); // never Ravi's: an approver's reset empties everyone's
    expect(order.actors.upload).toBe(ASHA.id);
    expect(getToasts()).toHaveLength(1);
  });

  it("does not reset outside the demo", async () => {
    const user = userEvent.setup();
    const order = recordCalls();
    renderApp(<UploadScreen />);
    await clickSamples(user);
    await waitFor(() => expect(router.push).toHaveBeenCalled());
    expect(order).not.toContain("reset");
    expect(screen.queryByText(/Each run starts clean/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start over" })).not.toBeInTheDocument();
  });

  it("never wipes everyone's data silently for an approver when Asha is not in the directory", async () => {
    const user = userEvent.setup();
    const order = recordCalls();
    renderApp(<UploadScreen />, {
      persona: RAVI.id,
      handlers: [
        demoMeta(),
        http.get(url("/v1/employees"), () => HttpResponse.json([ADVIKA, RAVI])),
      ],
    });
    await screen.findByRole("button", { name: "Start over" });
    await waitFor(() =>
      expect(screen.queryByText(/Each run starts clean/)).not.toBeInTheDocument(),
    );
    await clickSamples(user);
    await waitFor(() => expect(router.push).toHaveBeenCalled());
    expect(order).not.toContain("reset");
    expect(order.actors.upload).toBe(RAVI.id);
    expect(getStoredPersona()).toBe(RAVI.id);
    expect(getToasts()).toEqual([]);
  });

  it("stops and explains when the reset fails", async () => {
    const user = userEvent.setup();
    const order = recordCalls(() => problem(500, "http_500", "boom"));
    renderApp(<UploadScreen />, { handlers: [demoMeta()] });
    await clickSamples(user);
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong on our side");
    expect([...order]).toEqual(["reset"]);
    expect(router.push).not.toHaveBeenCalled();
  });

  it("carries on when the server says it is not the demo after all (demo_disabled)", async () => {
    const user = userEvent.setup();
    const order = recordCalls(() =>
      problem(404, "demo_disabled", "Start over is for the demo only"),
    );
    renderApp(<UploadScreen />, { handlers: [demoMeta()] });
    await clickSamples(user);
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/batches/bat-000003"));
    expect(order[0]).toBe("reset");
    expect(order.at(-1)).toBe("upload");
  });

  it("keeps the switch to Asha even when the reset then fails (nothing is uploaded)", async () => {
    const user = userEvent.setup();
    recordCalls(() => problem(500, "http_500", "boom"));
    renderApp(<UploadScreen />, { persona: ADVIKA.id, handlers: [demoMeta()] });
    await clickSamples(user);
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong on our side");
    expect(getStoredPersona()).toBe(ASHA.id);
    expect(router.push).not.toHaveBeenCalled();
  });

  it("offers Start over here and confirms a finished one (dismissible)", async () => {
    const user = userEvent.setup();
    setStartedOverNotice("Started over. Removed 11 receipts and 10 claims.");
    renderApp(<UploadScreen />, { handlers: [demoMeta()] });
    expect(await screen.findByRole("button", { name: "Start over" })).toBeInTheDocument();
    const status = screen
      .getAllByRole("status")
      .find((el) => /Started over/.test(el.textContent ?? ""))!;
    expect(status).toHaveTextContent("Removed 11 receipts and 10 claims.");
    await user.click(within(status).getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText(/Started over\. Removed/)).not.toBeInTheDocument();
  });
});
