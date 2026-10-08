import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { ClaimView } from "@/lib/api/types";
import { useClaim } from "@/lib/hooks/queries";
import { makeClaim, makeQuestion } from "@/test/fixtures";
import { renderApp } from "@/test/render";
import { problem, server, url } from "@/test/server";

import { AssistantPanel } from "./AssistantPanel";

const ATTENDEES = makeQuestion({
  id: "q-attendees-1",
  kind: "attendees",
  text: "Who attended the client dinner on 12 Jul (₹830)? Please give names and company.",
  document_ids: ["doc-000001"],
});
const PURPOSE = makeQuestion({
  id: "q-business_purpose-2",
  kind: "business_purpose",
  text: "What was the business purpose of the client dinner on 12 Jul (₹830)?",
});

const PROMPT_TWO =
  "To finish “Client dinner 12 Jul 2026” I need a few details:\n1. Who attended the client dinner on 12 Jul (₹830)? Please give names and company.\n2. What was the business purpose of the client dinner on 12 Jul (₹830)?\nYou can answer in one message.";

/** Renders the panel against a tiny in-memory server so replies really change the claim. */
function setup(initial: ClaimView, options: { canAnswer?: boolean; prompt?: string | null } = {}) {
  let claim = initial;
  const requests: { reply: unknown[]; answers: unknown[] } = { reply: [], answers: [] };
  const prompt = options.prompt === undefined ? PROMPT_TWO : options.prompt;

  server.use(
    http.get(url(`/v1/claims/${claim.id}`), () => HttpResponse.json(claim)),
    http.get(url(`/v1/claims/${claim.id}/prompt`), () =>
      HttpResponse.json({
        prompt,
        open_question_ids: (claim.open_questions ?? []).filter((q) => !q.answer).map((q) => q.id),
      }),
    ),
    http.post(url(`/v1/claims/${claim.id}/reply`), async ({ request }) => {
      const body = (await request.json()) as { text: string };
      requests.reply.push(body);
      const [first, second] = body.text.split(";").map((part) => part.trim());
      const understood: Record<string, string> = {};
      const open = (claim.open_questions ?? []).filter((q) => !q.answer);
      if (first && open[0]) understood[open[0].id] = first;
      if (second && open[1]) understood[open[1].id] = second;
      claim = {
        ...claim,
        open_questions: (claim.open_questions ?? []).map((q) =>
          understood[q.id] ? { ...q, answer: understood[q.id] } : q,
        ),
      };
      const stillOpen = (claim.open_questions ?? []).filter((q) => !q.answer);
      claim = { ...claim, status: stillOpen.length === 0 ? "ready" : "needs_info" };
      return HttpResponse.json({
        claim,
        understood,
        follow_up:
          stillOpen.length === 0
            ? null
            : `To finish “${claim.title}” I need one detail:\n1. ${stillOpen[0].text}`,
      });
    }),
    http.post(url(`/v1/claims/${claim.id}/answers`), async ({ request }) => {
      const body = (await request.json()) as { answers: Record<string, string> };
      requests.answers.push(body);
      claim = {
        ...claim,
        open_questions: (claim.open_questions ?? []).map((q) =>
          body.answers[q.id] ? { ...q, answer: body.answers[q.id] } : q,
        ),
      };
      return HttpResponse.json(claim);
    }),
  );

  function Harness() {
    const query = useClaim(claim.id);
    return query.data ? (
      <AssistantPanel claim={query.data} canAnswer={options.canAnswer ?? true} />
    ) : null;
  }
  renderApp(<Harness />);
  return { requests };
}

const dinner = (overrides: Partial<ClaimView> = {}) =>
  makeClaim({
    id: "clm-dinner",
    title: "Client dinner 12 Jul 2026",
    mode: "event",
    status: "needs_info",
    open_questions: [ATTENDEES, PURPOSE],
    ...overrides,
  });

describe("AssistantPanel", () => {
  it("asks the ONE combined question as a numbered list, with a single reply box", async () => {
    setup(dinner());
    const log = await screen.findByRole("log", { name: "Conversation with ClaimPilot" });
    expect(await within(log).findByText(/I need a few details/)).toBeInTheDocument();
    const items = within(log).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Who attended the client dinner on 12 Jul (₹830)?");
    expect(items[1]).toHaveTextContent("What was the business purpose");
    expect(within(log).getByText("You can answer in one message.")).toBeInTheDocument();
    expect(screen.getAllByRole("textbox")).toHaveLength(1);
    expect(screen.getByLabelText("Your reply")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("tells people they can answer several questions in one numbered message", async () => {
    const user = userEvent.setup();
    setup(dinner());
    await screen.findByText(/I need a few details/);
    const tip = screen.getByText("Tip:").parentElement!;
    expect(tip).toHaveTextContent("Tip: reply in one message, e.g. 1. ... 2. ...");
    await user.click(screen.getByRole("button", { name: "Numbered reply" }));
    const box = screen.getByLabelText("Your reply") as HTMLTextAreaElement;
    expect(box.value).toBe("1. \n2. ");
    // pressing it again never clobbers what was typed
    await user.type(box, "Neha");
    await user.click(screen.getByRole("button", { name: "Numbered reply" }));
    expect((screen.getByLabelText("Your reply") as HTMLTextAreaElement).value).toContain("Neha");
  });

  it("shows no numbering tip when only one question is open", async () => {
    setup(dinner({ open_questions: [PURPOSE] }), {
      prompt:
        "To finish “Client dinner 12 Jul 2026” I need one detail:\n1. What was the business purpose of the client dinner on 12 Jul (₹830)?",
    });
    await screen.findByText(/I need one detail/);
    expect(screen.queryByText("Tip:")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Numbered reply" })).not.toBeInTheDocument();
  });

  it("sends one reply for several questions and shows what it understood as confirmed answers", async () => {
    const user = userEvent.setup();
    const { requests } = setup(dinner());
    await screen.findByText(/I need a few details/);
    await user.type(screen.getByLabelText("Your reply"), "Neha Rao from Kestrel; contract renewal");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Got it, here is what I understood:")).toBeInTheDocument();
    expect(requests.reply).toEqual([{ text: "Neha Rao from Kestrel; contract renewal" }]);
    expect(screen.getByText("Neha Rao from Kestrel", { selector: "span" })).toBeInTheDocument();
    // everything is answered now: the assistant says so, and the composer is gone
    expect(await screen.findByText("All set")).toBeInTheDocument();
    expect(screen.queryByLabelText("Your reply")).not.toBeInTheDocument();
    // both answers are listed under "Confirmed details", each with its source
    const confirmed = screen.getByRole("heading", { name: "Confirmed details" }).parentElement!;
    expect(within(confirmed).getAllByText("Your answer")).toHaveLength(2);
    expect(within(confirmed).getByText("contract renewal")).toBeInTheDocument();
  });

  it("asks once more, still in a single message, for whatever is left", async () => {
    const user = userEvent.setup();
    setup(dinner());
    await screen.findByText(/I need a few details/);
    await user.type(screen.getByLabelText("Your reply"), "Neha Rao from Kestrel");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText(/I need one detail/)).toBeInTheDocument();
    const log = screen.getByRole("log");
    expect(
      within(log)
        .getAllByRole("listitem")
        .some((li) => /business purpose/.test(li.textContent ?? "")),
    ).toBe(true);
    // the box is cleared and ready for the next answer
    expect(screen.getByLabelText("Your reply")).toHaveValue("");
    // the earlier exchange stays visible
    expect(within(log).getByText("Neha Rao from Kestrel", { selector: "p" })).toBeInTheDocument();
  });

  it("sends with Ctrl+Enter and offers one-tap fills that never send by themselves", async () => {
    const user = userEvent.setup();
    const claim = makeClaim({
      id: "clm-misc",
      title: "Miscellaneous Jul 2026",
      status: "needs_info",
      open_questions: [
        makeQuestion({
          id: "q-confirm_personal-1",
          kind: "confirm_personal",
          text: "The ₹430 handwritten bill from “Gupta Provision Store” looks personal. Is it a business expense?",
        }),
      ],
    });
    const { requests } = setup(claim, {
      prompt:
        "To finish “Miscellaneous Jul 2026” I need one detail:\n1. The ₹430 handwritten bill looks personal. Is it a business expense?",
    });
    await screen.findByText(/I need one detail/);
    await user.click(screen.getByRole("button", { name: "Yes, it was a business expense" }));
    expect(screen.getByLabelText("Your reply")).toHaveValue("Yes, it was a business expense: ");
    expect(requests.reply).toHaveLength(0);
    await user.type(
      screen.getByLabelText("Your reply"),
      "office pantry{Control>}{Enter}{/Control}",
    );
    await waitFor(() => expect(requests.reply).toHaveLength(1));
    expect(requests.reply[0]).toEqual({ text: "Yes, it was a business expense: office pantry" });
  });

  it("shows answers that came from the calendar or the receipt, and lets you edit them", async () => {
    const user = userEvent.setup();
    const claim = dinner({
      status: "ready",
      open_questions: [
        {
          ...ATTENDEES,
          answer:
            "from calendar: Dinner with Kestrel Logistics (attendees: Neha Rao (Kestrel Logistics))",
        },
        { ...PURPOSE, answer: "from receipt: Contract renewal" },
      ],
    });
    const { requests } = setup(claim);
    expect(await screen.findByText("All set")).toBeInTheDocument();
    expect(screen.getByText("From your calendar")).toBeInTheDocument();
    expect(screen.getByText("From the receipt")).toBeInTheDocument();
    expect(
      screen.getByText(/Dinner with Kestrel Logistics \(attendees: Neha Rao/),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Edit answer: Business purpose" }));
    const box = screen.getByRole("textbox", { name: /Edit your answer/ });
    expect(box).toHaveValue("Contract renewal");
    await user.clear(box);
    await user.type(box, "Annual review");
    await user.click(screen.getByRole("button", { name: "Save answer" }));
    await waitFor(() =>
      expect(requests.answers).toEqual([{ answers: { "q-business_purpose-2": "Annual review" } }]),
    );
    expect(await screen.findByText("Annual review")).toBeInTheDocument();
    expect(screen.getAllByText("Your answer").length).toBe(1);
  });

  it("can cancel an edit without saving", async () => {
    const user = userEvent.setup();
    const { requests } = setup(
      dinner({ status: "ready", open_questions: [{ ...PURPOSE, answer: "Client visit" }] }),
      { prompt: null },
    );
    await screen.findByText("Client visit");
    await user.click(screen.getByRole("button", { name: /Edit answer/ }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("textbox", { name: /Edit your answer/ })).not.toBeInTheDocument();
    expect(requests.answers).toHaveLength(0);
  });

  it("says a submitted claim can't be changed and offers no editing or composer", async () => {
    setup(
      dinner({
        status: "submitted",
        submission_reference: "FIN-2026-000009",
        open_questions: [{ ...PURPOSE, answer: "Client visit" }],
      }),
      { prompt: null },
    );
    expect(await screen.findByText("This claim has been submitted.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Your reply")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Edit answer/ })).not.toBeInTheDocument();
  });

  it("is read-only for anyone but the owner", async () => {
    setup(dinner(), { canAnswer: false });
    expect(
      await screen.findByText(/Only the employee who owns this claim can answer/),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Your reply")).not.toBeInTheDocument();
  });

  it("shows a friendly message when the reply is refused", async () => {
    const user = userEvent.setup();
    setup(dinner());
    server.use(
      http.post(url("/v1/claims/clm-dinner/reply"), () =>
        problem(409, "claim_locked", "a submitted claim can no longer be changed"),
      ),
    );
    await screen.findByText(/I need a few details/);
    await user.type(screen.getByLabelText("Your reply"), "anything");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This claim is already submitted");
    expect(screen.getByLabelText("Your reply")).toHaveValue("anything"); // nothing is lost
  });

  it("shows an error when the question itself cannot be loaded", async () => {
    setup(dinner());
    server.use(
      http.get(url("/v1/claims/clm-dinner/prompt"), () => problem(500, "http_500", "boom")),
    );
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});
