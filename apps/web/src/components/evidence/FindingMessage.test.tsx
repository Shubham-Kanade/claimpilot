import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { makeDocumentView, makeFinding } from "@/test/fixtures";
import { renderApp } from "@/test/render";
import { problem, server, url } from "@/test/server";

import { FindingList, FindingMessage } from "./FindingList";

const EARLIER = "e9cf5624e1434f30b53b0e3bdd528ee0";
const duplicate = makeFinding({
  code: "duplicate_image",
  severity: "high",
  fields: [],
  actual: EARLIER,
  message: `This picture is almost identical to document ${EARLIER}, uploaded earlier (a re-saved, resized or re-photographed copy).`,
});

describe("a finding that points at another receipt", () => {
  it("names that receipt by its file, never by a 32-character id", async () => {
    server.use(
      http.get(url(`/v1/documents/${EARLIER}`), () =>
        HttpResponse.json(
          makeDocumentView({ id: EARLIER, filename: "01-client-dinner-saffron-terrace.jpg" }),
        ),
      ),
    );
    renderApp(<FindingList findings={[duplicate]} />);
    const item = await screen.findByRole("listitem");
    expect(await within(item).findByText("01-client-dinner-saffron-terrace.jpg")).toBeVisible();
    expect(item).toHaveTextContent(
      "This picture is almost identical to 01-client-dinner-saffron-terrace.jpg, uploaded earlier",
    );
    expect(item).not.toHaveTextContent(EARLIER);
  });

  it("says 'another receipt' while it is unknown, and when it cannot be looked up", async () => {
    server.use(
      http.get(url(`/v1/documents/${EARLIER}`), () =>
        problem(404, "document_not_found", "No such document"),
      ),
    );
    renderApp(<FindingList findings={[duplicate]} />);
    const item = await screen.findByRole("listitem");
    expect(item).toHaveTextContent("almost identical to another receipt, uploaded earlier");
    expect(item).not.toHaveTextContent(EARLIER);
  });

  it("asks for nothing when the message has no receipt in it", () => {
    // no providers and no handlers: a lookup here would throw or fail the test
    renderApp(<FindingMessage message="The printed total does not match." />);
    expect(screen.getByText("The printed total does not match.")).toBeInTheDocument();
  });
});
