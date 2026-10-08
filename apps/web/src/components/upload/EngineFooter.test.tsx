import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { META, META_DEMO } from "@/test/fixtures";

import { EnginePanel } from "./EngineFooter";

describe("EnginePanel", () => {
  it("shows the LLM mode, the decision engine, the runtime and every route's model", () => {
    render(<EnginePanel meta={META} />);
    expect(screen.getByText("LLM mode: replay")).toBeInTheDocument();
    expect(screen.getByText(/Decisions by: Jev \(System One\)/)).toBeInTheDocument();
    expect(screen.getByText("Runtime: distributed")).toBeInTheDocument();
    expect(screen.queryByText("Public demo")).not.toBeInTheDocument();
    expect(screen.getByText("Reads each receipt")).toBeInTheDocument();
    expect(screen.getByText("Chats with you")).toBeInTheDocument();
    expect(screen.getByText("Decides when Jev is unsure")).toBeInTheDocument();
    expect(screen.getByText(/overridden/)).toBeInTheDocument();
  });

  it("says so in the public demo", () => {
    render(<EnginePanel meta={META_DEMO} />);
    expect(screen.getByText("Public demo")).toBeInTheDocument();
    expect(screen.getByText("Runtime: embedded")).toBeInTheDocument();
  });

  it("names other decision engines and unknown routes sensibly", () => {
    render(
      <EnginePanel
        meta={{
          ...META,
          decision_engine: "llm",
          routes: [
            {
              route: "brand_new_route",
              model_key: "x",
              model_id: "model-x",
              effort: null,
              overridden: false,
            },
          ],
        }}
      />,
    );
    expect(screen.getByText(/Decisions by: LLM \(System Two\)/)).toBeInTheDocument();
    expect(screen.getByText("Brand new route")).toBeInTheDocument();
  });

  it("explains who decides in words that match the configuration (honest about the engines)", () => {
    const { container, rerender } = render(<EnginePanel meta={META} />);
    // Jev configured: System One decides the category, the language model reads and chats.
    expect(container).toHaveTextContent(
      "System One (Jev) makes fast, typed decisions such as the expense category.",
    );
    expect(container).toHaveTextContent("Every card shows which engine did what.");

    // The hosted demo has no Jev: the language model decides, and the page must not claim otherwise.
    rerender(<EnginePanel meta={{ ...META, decision_engine: "llm" }} />);
    expect(container).toHaveTextContent("decides the expense category and talks to you");
    expect(container).toHaveTextContent("Jev (System One) is not switched on");
    expect(container).not.toHaveTextContent("makes fast, typed decisions");

    rerender(<EnginePanel meta={{ ...META, decision_engine: "fake" }} />);
    expect(container).toHaveTextContent("the expense category comes from the fake engine");
  });
});
