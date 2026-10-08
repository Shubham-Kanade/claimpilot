"use client";

import { Bot, CalendarCheck, CircleCheck, Pencil, Receipt, Send, UserRound } from "lucide-react";
import { useId, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from "react";

import { Button } from "@/components/ui/Button";
import { Chip } from "@/components/ui/Chip";
import { InlineError, Skeleton } from "@/components/ui/feedback";
import type { ClaimView, OpenQuestion, QuestionKind } from "@/lib/api/types";
import { cn } from "@/lib/cn";
import {
  answeredQuestions,
  parseAnswer,
  parsePrompt,
  unansweredQuestions,
  type AnswerSource,
} from "@/lib/claims/summary";
import { useAnswers, usePrompt, useReply } from "@/lib/hooks/queries";
import { QUESTION_KIND_LABELS } from "@/lib/labels";

const LOCKED = new Set(["submitted", "approved", "rejected"]);

/** One-tap replies that fill the box (never send by themselves). */
const QUICK_FILLS: Partial<Record<QuestionKind, readonly string[]>> = {
  confirm_personal: [
    "Yes, it was a business expense: ",
    "No, that was personal. Please leave it out.",
  ],
  self_declaration: ["Confirmed, it was for work: "],
  business_purpose: ["Client meeting: ", "Project work: ", "Training: "],
};

type Turn =
  | { id: number; role: "you"; text: string }
  | {
      id: number;
      role: "assistant";
      understood: readonly { question: OpenQuestion; answer: string }[];
    };

function Bubble({
  from,
  children,
  label,
}: {
  from: "assistant" | "you";
  children: ReactNode;
  label: string;
}) {
  const assistant = from === "assistant";
  return (
    <div className={cn("animate-fade-in flex gap-2.5", !assistant && "flex-row-reverse")}>
      <span
        aria-hidden="true"
        className={cn(
          "mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full",
          assistant ? "bg-teal-100 text-teal-800" : "bg-indigo-100 text-indigo-800",
        )}
      >
        {assistant ? <Bot className="size-4" /> : <UserRound className="size-4" />}
      </span>
      <div
        className={cn(
          "max-w-[88%] rounded-2xl px-4 py-3 text-sm leading-relaxed",
          assistant
            ? "rounded-tl-md bg-slate-100 text-slate-900"
            : "rounded-tr-md bg-indigo-600 text-white",
        )}
      >
        <span className="sr-only">{label}: </span>
        {children}
      </div>
    </div>
  );
}

/** The assistant's single combined message: an intro, the numbered questions, a closing hint. */
function QuestionMessage({ text }: { text: string }) {
  const parsed = parsePrompt(text);
  return (
    <div className="space-y-2">
      {parsed.intro ? <p className="font-medium">{parsed.intro}</p> : null}
      {parsed.items.length > 0 ? (
        <ol className="list-decimal space-y-1.5 pl-5 marker:font-semibold marker:text-teal-800">
          {parsed.items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ol>
      ) : null}
      {parsed.outro ? <p className="text-slate-700">{parsed.outro}</p> : null}
    </div>
  );
}

const SOURCE_BADGE: Record<
  AnswerSource,
  { label: string; icon: ReactNode; tone: "accent" | "info" | "neutral" }
> = {
  calendar: { label: "From your calendar", icon: <CalendarCheck />, tone: "accent" },
  receipt: { label: "From the receipt", icon: <Receipt />, tone: "info" },
  you: { label: "Your answer", icon: <UserRound />, tone: "neutral" },
};

function AnswerRow({
  question,
  editable,
  claimId,
  onSaved,
}: {
  question: OpenQuestion;
  editable: boolean;
  claimId: string;
  onSaved: () => void;
}) {
  const answers = useAnswers(claimId);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const fieldId = useId();
  const { text, source } = parseAnswer(question.answer ?? "");
  const badge = SOURCE_BADGE[source];

  function save(event: FormEvent) {
    event.preventDefault();
    if (!draft.trim()) return;
    answers.mutate(
      { [question.id]: draft.trim() },
      {
        onSuccess: () => {
          setEditing(false);
          onSaved();
        },
      },
    );
  }

  return (
    <li className="rounded-xl border border-slate-200 bg-white p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="flex items-center gap-1.5 text-sm font-semibold text-slate-900">
          <CircleCheck className="size-4 text-emerald-700" aria-hidden="true" />
          {QUESTION_KIND_LABELS[question.kind]}
        </p>
        <Chip tone={badge.tone} icon={badge.icon} size="sm">
          {badge.label}
        </Chip>
      </div>
      {editing ? (
        <form onSubmit={save} className="mt-2 space-y-2">
          <label htmlFor={fieldId} className="text-xs font-medium text-slate-700">
            Edit your answer: {question.text}
          </label>
          <textarea
            id={fieldId}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            rows={2}
            maxLength={2000}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
          />
          {answers.isError ? <InlineError error={answers.error} /> : null}
          <div className="flex gap-2">
            <Button type="submit" size="sm" loading={answers.isPending} disabled={!draft.trim()}>
              Save answer
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </form>
      ) : (
        <div className="mt-1.5 flex items-start justify-between gap-3">
          <p className="min-w-0 text-sm break-words text-slate-800">{text}</p>
          {editable ? (
            <button
              type="button"
              onClick={() => {
                setDraft(text);
                setEditing(true);
              }}
              aria-label={`Edit answer: ${QUESTION_KIND_LABELS[question.kind]}`}
              className="inline-flex shrink-0 items-center gap-1 rounded-lg px-2 py-1 text-xs font-semibold text-indigo-700 hover:bg-indigo-50"
            >
              <Pencil className="size-3.5" aria-hidden="true" />
              Edit
            </button>
          ) : null}
        </div>
      )}
    </li>
  );
}

/**
 * The assistant: asks the ONE combined question for everything still open, takes a single
 * free-text reply (POST /reply), shows what it understood as confirmed answers, and either asks
 * once more for what is left or says "All set". Earlier answers can be edited (POST /answers).
 */
export function AssistantPanel({ claim, canAnswer }: { claim: ClaimView; canAnswer: boolean }) {
  const locked = LOCKED.has(claim.status);
  const open = unansweredQuestions(claim);
  const answered = answeredQuestions(claim);
  const promptQuery = usePrompt(claim.id, !locked && open.length > 0);
  const reply = useReply(claim.id);
  const textId = useId();
  const textRef = useRef<HTMLTextAreaElement>(null);

  const [text, setText] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  // The assistant's last follow-up, which can differ from GET /prompt ("I couldn't match that...").
  const [followUp, setFollowUp] = useState<string | null | undefined>(undefined);
  const nextId = useRef(0);

  const currentMessage = followUp !== undefined ? followUp : (promptQuery.data?.prompt ?? null);
  const kinds = open.map((q) => q.kind);
  const quickFills = Array.from(new Set(kinds.flatMap((kind) => QUICK_FILLS[kind] ?? [])));

  function send(event?: FormEvent) {
    event?.preventDefault();
    const message = text.trim();
    if (!message || reply.isPending) return;
    reply.mutate(message, {
      onSuccess: (result) => {
        const byId = new Map((result.claim.open_questions ?? []).map((q) => [q.id, q]));
        const understood = Object.entries(result.understood).flatMap(([id, answer]) => {
          const question = byId.get(id);
          return question ? [{ question, answer }] : [];
        });
        setTurns((current) => [
          ...current,
          { id: nextId.current++, role: "you", text: message },
          { id: nextId.current++, role: "assistant", understood },
        ]);
        setFollowUp(result.follow_up);
        setText("");
      },
    });
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) send();
  }

  /** "1. \n2. " for the open questions: numbered replies are matched to questions without a model. */
  function insertNumbered() {
    const template = open.map((_, index) => `${index + 1}. `).join("\n");
    setText((current) => (current.trim() ? `${current.trimEnd()}\n${template}` : template));
    textRef.current?.focus();
  }

  function insertQuickFill(fill: string) {
    setText((current) =>
      current && !current.endsWith(" ") ? `${current} ${fill}` : `${current}${fill}`,
    );
    textRef.current?.focus();
  }

  return (
    <section
      aria-labelledby="assistant-heading"
      className="shadow-card rounded-2xl border border-slate-200 bg-white"
    >
      <div className="flex items-center gap-3 border-b border-slate-100 px-4 py-3.5 sm:px-5">
        <span
          aria-hidden="true"
          className="flex size-9 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-600 to-teal-600 text-white"
        >
          <Bot className="size-5" />
        </span>
        <div>
          <h2
            id="assistant-heading"
            className="text-base leading-tight font-semibold text-slate-900"
          >
            ClaimPilot assistant
          </h2>
          <p className="text-xs text-slate-600">Asks only what it can&apos;t work out itself</p>
        </div>
      </div>

      <div className="space-y-4 px-4 py-4 sm:px-5">
        {answered.length > 0 ? (
          <div>
            <h3 className="mb-2 text-xs font-semibold tracking-wide text-slate-600 uppercase">
              Confirmed details
            </h3>
            <ul className="space-y-2">
              {answered.map((question) => (
                <AnswerRow
                  key={question.id}
                  question={question}
                  claimId={claim.id}
                  editable={canAnswer && !locked}
                  onSaved={() => setFollowUp(undefined)}
                />
              ))}
            </ul>
          </div>
        ) : null}

        <div
          role="log"
          aria-label="Conversation with ClaimPilot"
          aria-live="polite"
          className="space-y-3"
        >
          {turns.map((turn) =>
            turn.role === "you" ? (
              <Bubble key={turn.id} from="you" label="You said">
                <p className="break-words whitespace-pre-wrap">{turn.text}</p>
              </Bubble>
            ) : (
              <Bubble key={turn.id} from="assistant" label="ClaimPilot">
                {turn.understood.length > 0 ? (
                  <div className="space-y-2">
                    <p className="font-medium">Got it, here is what I understood:</p>
                    <ul className="space-y-1.5">
                      {turn.understood.map(({ question, answer }) => (
                        <li key={question.id} className="flex items-start gap-1.5">
                          <CircleCheck
                            className="mt-0.5 size-4 shrink-0 text-emerald-700"
                            aria-hidden="true"
                          />
                          <span>
                            <strong className="font-semibold">
                              {QUESTION_KIND_LABELS[question.kind]}:
                            </strong>{" "}
                            {answer}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : (
                  <p>I couldn&apos;t match that to my questions.</p>
                )}
              </Bubble>
            ),
          )}

          {locked ? (
            <Bubble from="assistant" label="ClaimPilot">
              <p className="font-medium">This claim has been {claim.status}.</p>
              <p className="text-slate-700">
                Nothing more to ask. Submitted claims can&apos;t be changed.
              </p>
            </Bubble>
          ) : open.length === 0 ? (
            <Bubble from="assistant" label="ClaimPilot">
              <p className="flex items-center gap-1.5 font-semibold">
                <CircleCheck className="size-4 text-emerald-700" aria-hidden="true" />
                All set
              </p>
              <p className="text-slate-700">
                Everything I needed is in. Check the receipts and flags, then confirm to submit.
              </p>
            </Bubble>
          ) : currentMessage ? (
            <Bubble from="assistant" label="ClaimPilot asks">
              <QuestionMessage text={currentMessage} />
            </Bubble>
          ) : promptQuery.isError ? (
            <InlineError error={promptQuery.error} />
          ) : (
            <div className="space-y-2" aria-busy="true">
              <Skeleton className="h-4 w-4/5" />
              <Skeleton className="h-4 w-3/5" />
            </div>
          )}
        </div>

        {!locked && open.length > 0 ? (
          canAnswer ? (
            <form onSubmit={send} className="space-y-2.5">
              <label htmlFor={textId} className="text-sm font-medium text-slate-800">
                Your reply
              </label>
              <textarea
                id={textId}
                ref={textRef}
                value={text}
                onChange={(event) => setText(event.target.value)}
                onKeyDown={onKeyDown}
                rows={3}
                maxLength={2000}
                placeholder={
                  open.length > 1 ? "Answer all the questions in one message…" : "Type your answer…"
                }
                className="w-full resize-y rounded-xl border border-slate-300 px-3.5 py-3 text-base leading-relaxed shadow-sm placeholder:text-slate-500 sm:text-sm"
              />
              {open.length > 1 ? (
                <p className="text-xs leading-relaxed text-slate-700">
                  <strong className="font-semibold">Tip:</strong> reply in one message, e.g.{" "}
                  <code className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[11px]">
                    1. ... 2. ...
                  </code>
                </p>
              ) : null}
              {quickFills.length > 0 || open.length > 1 ? (
                <div className="flex flex-wrap gap-1.5" aria-label="Quick replies">
                  {open.length > 1 ? (
                    <button
                      type="button"
                      onClick={insertNumbered}
                      className="rounded-full bg-white px-3 py-1.5 text-xs font-medium text-indigo-700 ring-1 ring-indigo-200 ring-inset hover:bg-indigo-50"
                    >
                      Numbered reply
                    </button>
                  ) : null}
                  {quickFills.map((fill) => (
                    <button
                      key={fill}
                      type="button"
                      onClick={() => insertQuickFill(fill)}
                      className="rounded-full bg-white px-3 py-1.5 text-xs font-medium text-indigo-700 ring-1 ring-indigo-200 ring-inset hover:bg-indigo-50"
                    >
                      {fill.trim().replace(/[:]$/, "")}
                    </button>
                  ))}
                </div>
              ) : null}
              {reply.isError ? <InlineError error={reply.error} /> : null}
              <div className="flex items-center justify-between gap-3">
                <p className="text-xs text-slate-600">Ctrl+Enter sends.</p>
                <Button
                  type="submit"
                  loading={reply.isPending}
                  disabled={!text.trim()}
                  icon={<Send className="size-4" aria-hidden="true" />}
                >
                  Send
                </Button>
              </div>
            </form>
          ) : (
            <p className="rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-700 ring-1 ring-slate-200 ring-inset">
              Only the employee who owns this claim can answer these questions.
            </p>
          )
        ) : null}
      </div>
    </section>
  );
}
