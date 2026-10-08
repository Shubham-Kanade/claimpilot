import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { renderApp } from "@/test/render";
import { problem, server, url } from "@/test/server";

import { overlayStyle, ReceiptViewer, viewerCaption } from "./ReceiptViewer";

const FILE = "hotel-folio-lotus-bay.png";
const box = { page: 0, x: 0.1, y: 0.25, w: 0.5, h: 0.05 };

function serveFile(contentType = "image/png") {
  server.use(
    http.get(
      url("/v1/documents/doc-000001/file"),
      () =>
        new HttpResponse(new Uint8Array([137, 80, 78, 71]), {
          headers: { "Content-Type": contentType },
        }),
    ),
  );
}

function renderViewer(props: Partial<React.ComponentProps<typeof ReceiptViewer>> = {}) {
  return renderApp(
    <ReceiptViewer
      documentId="doc-000001"
      filename={FILE}
      boxes={{}}
      selectedField={null}
      {...props}
    />,
  );
}

describe("ReceiptViewer", () => {
  it("shows the ORIGINAL file, fetched with the persona header and rendered from a blob URL", async () => {
    let persona: string | null = null;
    server.use(
      http.get(url("/v1/documents/doc-000001/file"), ({ request }) => {
        persona = request.headers.get("X-Persona");
        return new HttpResponse(new Uint8Array([1]), { headers: { "Content-Type": "image/png" } });
      }),
    );
    renderViewer();
    const image = await screen.findByRole("img", { name: `Original receipt: ${FILE}` });
    expect(image.getAttribute("src")).toMatch(/^blob:/);
    expect(image).toHaveAttribute("loading", "lazy");
    expect(persona).toBe("DEMO-ASHA");
    expect(screen.getByRole("link", { name: "Open original" })).toHaveAttribute("target", "_blank");
  });

  it("highlights where the selected field is printed (box -> percentage overlay)", async () => {
    serveFile();
    renderViewer({ boxes: { total: box }, selectedField: "total" });
    const highlight = await screen.findByTestId("verify-highlight");
    expect(highlight).toHaveStyle({ left: "10%", top: "25%", width: "50%", height: "5%" });
    expect(highlight).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByText("Highlighted on the receipt: Total.")).toBeInTheDocument();
  });

  it("degrades gracefully when the selected field has no box: no overlay, clear explanation", async () => {
    serveFile();
    renderViewer({ boxes: { merchant_name: box }, selectedField: "total" });
    await screen.findByRole("img");
    expect(screen.queryByTestId("verify-highlight")).not.toBeInTheDocument();
    expect(
      screen.getByText(/Total is selected, but its position on the receipt isn't available/),
    ).toBeInTheDocument();
  });

  it("works with no boxes at all: still a zoomable image and an honest caption", async () => {
    serveFile();
    renderViewer({ boxes: {}, selectedField: null });
    await screen.findByRole("img");
    expect(
      screen.getByText(/Field locations aren't available for this receipt/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Zoom in" })).toBeEnabled();
  });

  it("invites a selection when locations exist but nothing is selected", async () => {
    serveFile();
    renderViewer({ boxes: { total: box }, selectedField: null });
    await screen.findByRole("img");
    expect(screen.getByText("Select a field to see where it is printed.")).toBeInTheDocument();
  });

  it("never crashes on a malformed or off-page box", async () => {
    serveFile();
    renderViewer({
      boxes: { total: { page: 0, x: Number.NaN, y: 0, w: 1, h: 1 } },
      selectedField: "total",
    });
    await screen.findByRole("img");
    expect(screen.queryByTestId("verify-highlight")).not.toBeInTheDocument();
  });

  it("does not draw a page-0 overlay for a box on another page", async () => {
    serveFile();
    renderViewer({ boxes: { total: { ...box, page: 2 } }, selectedField: "total" });
    await screen.findByRole("img");
    expect(screen.queryByTestId("verify-highlight")).not.toBeInTheDocument();
  });

  it("zooms in steps, resets to fit, and disables the buttons at the limits", async () => {
    const user = userEvent.setup();
    serveFile();
    renderViewer();
    await screen.findByRole("img");
    const out = screen.getByRole("button", { name: "Zoom out" });
    const inn = screen.getByRole("button", { name: "Zoom in" });
    const fit = screen.getByRole("button", { name: "Fit to width" });
    expect(out).toBeDisabled();
    expect(fit).toBeDisabled();
    await user.click(inn);
    expect(screen.getByText("150%")).toBeInTheDocument();
    expect(screen.getByRole("img").parentElement).toHaveStyle({ width: "150%" });
    for (let i = 0; i < 5; i += 1) await user.click(inn); // 150% ... 400%
    expect(screen.getByText("400%")).toBeInTheDocument();
    expect(inn).toBeDisabled();
    await user.click(out);
    expect(screen.getByText("350%")).toBeInTheDocument();
    await user.click(fit);
    expect(screen.getByText("100%")).toBeInTheDocument();
  });

  it("scrolls the highlight into the middle of the viewer", async () => {
    serveFile();
    const scrollTo = vi.fn();
    Element.prototype.scrollTo = scrollTo as unknown as typeof Element.prototype.scrollTo;
    renderViewer({ boxes: { total: box }, selectedField: "total" });
    await screen.findByTestId("verify-highlight");
    await waitFor(() => expect(scrollTo).toHaveBeenCalled());
    expect(scrollTo.mock.calls[0][0]).toMatchObject({
      behavior: expect.stringMatching(/auto|smooth/),
    });
    // @ts-expect-error restore jsdom's lack of Element.scrollTo
    delete Element.prototype.scrollTo;
  });

  it("shows PDFs in the browser's own viewer, without zoom or highlight, and says so", async () => {
    serveFile("application/pdf");
    const { container } = renderViewer({
      filename: "invoice.pdf",
      boxes: { total: box },
      selectedField: "total",
    });
    await waitFor(() => expect(container.querySelector("object")).not.toBeNull());
    const object = container.querySelector("object")!;
    expect(object).toHaveAttribute("type", "application/pdf");
    expect(object).toHaveAttribute("aria-label", "Original receipt: invoice.pdf");
    expect(screen.getByRole("link", { name: "Open the PDF" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Zoom in" })).not.toBeInTheDocument();
    expect(screen.queryByTestId("verify-highlight")).not.toBeInTheDocument();
    expect(
      screen.getByText(/PDF preview\. Field highlights are shown on image receipts/),
    ).toBeInTheDocument();
  });

  it("does not embed a PDF in a browser that cannot show one (phones): a big button instead", async () => {
    Object.defineProperty(navigator, "pdfViewerEnabled", { value: false, configurable: true });
    try {
      serveFile("application/pdf");
      const { container } = renderViewer({ filename: "invoice.pdf" });
      const open = await screen.findByRole("link", { name: "Open the PDF" });
      expect(container.querySelector("object")).toBeNull();
      expect(open).toHaveAttribute("target", "_blank");
      expect(open.getAttribute("href")).toMatch(/^blob:/);
      expect(screen.getByText(/this browser cannot show a PDF inside the page/)).toBeVisible();
      expect(screen.getByRole("group", { name: "Original receipt: invoice.pdf" })).toBeVisible();
    } finally {
      // @ts-expect-error back to jsdom's default: the flag does not exist
      delete navigator.pdfViewerEnabled;
    }
  });

  it("explains when the original is no longer available", async () => {
    server.use(
      http.get(url("/v1/documents/doc-000001/file"), () =>
        problem(404, "file_unavailable", "The file is no longer available"),
      ),
    );
    renderViewer();
    expect(await screen.findByRole("alert")).toHaveTextContent("Original file unavailable");
  });
});

describe("viewer helpers", () => {
  it("turns a normalised box into CSS percentages", () => {
    expect(overlayStyle({ page: 0, x: 0.1, y: 0.2, w: 0.3, h: 0.04 })).toEqual({
      left: "10%",
      top: "20%",
      width: "30%",
      height: "4%",
    });
  });

  it("writes the caption for every situation", () => {
    expect(
      viewerCaption({ selectedField: null, located: false, anyLocations: true, isPdf: false }),
    ).toMatch(/Select a field/);
    expect(
      viewerCaption({ selectedField: null, located: false, anyLocations: false, isPdf: false }),
    ).toMatch(/aren't available/);
    expect(
      viewerCaption({ selectedField: "total", located: true, anyLocations: true, isPdf: false }),
    ).toBe("Highlighted on the receipt: Total.");
    expect(
      viewerCaption({ selectedField: "total", located: false, anyLocations: true, isPdf: false }),
    ).toMatch(/isn't available/);
    expect(
      viewerCaption({ selectedField: "total", located: false, anyLocations: true, isPdf: true }),
    ).toMatch(/PDF preview/);
  });
});
