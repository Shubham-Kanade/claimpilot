import { Check, CircleAlert, FileImage, FileText, Loader2 } from "lucide-react";
import Link from "next/link";

import { ConfidenceBadge, EngineBadge, TrustBadge } from "@/components/ui/badges";
import { Chip } from "@/components/ui/Chip";
import { Skeleton } from "@/components/ui/feedback";
import {
  phaseOf,
  type BatchState,
  type DocPhase,
  type DocProgress,
} from "@/lib/batch/batchReducer";
import { cn } from "@/lib/cn";
import { formatINR, formatUSD } from "@/lib/format";
import { categoryLabel, docTypeLabel } from "@/lib/labels";

const STEPS: readonly { phase: Exclude<DocPhase, "failed">; label: string }[] = [
  { phase: "waiting", label: "Waiting" },
  { phase: "reading", label: "Reading" },
  { phase: "read", label: "Read" },
  { phase: "checked", label: "Checked" },
];

const PHASE_TEXT: Record<DocPhase, string> = {
  waiting: "Waiting in the queue",
  reading: "Reading the receipt…",
  read: "Read · checking policy and trust…",
  checked: "Checked",
  failed: "Couldn't read this file",
};

function Stepper({ phase }: { phase: DocPhase }) {
  const current = STEPS.findIndex((s) => s.phase === phase);
  return (
    <ol className="mt-3 flex items-center gap-1.5" aria-label="Progress">
      {STEPS.map((step, index) => {
        const done = phase === "failed" ? false : index < current || phase === "checked";
        const active = index === current && phase !== "checked";
        return (
          <li
            key={step.phase}
            aria-current={active ? "step" : undefined}
            className="flex min-w-0 flex-1 flex-col gap-1"
          >
            <span
              className={cn(
                "h-1.5 rounded-full transition-colors duration-500",
                phase === "failed" && "bg-rose-300",
                phase !== "failed" && done && "bg-teal-600",
                phase !== "failed" && active && "bg-indigo-600",
                phase !== "failed" && !done && !active && "bg-slate-200",
              )}
            />
            <span
              className={cn(
                "truncate text-[11px] font-medium",
                done || active ? "text-slate-800" : "text-slate-500",
              )}
            >
              {step.label}
              <span className="sr-only">{done ? " (done)" : active ? " (in progress)" : ""}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function PhaseBadge({ phase }: { phase: DocPhase }) {
  switch (phase) {
    case "waiting":
      return <Chip size="sm">Waiting</Chip>;
    case "reading":
      return (
        <Chip
          size="sm"
          tone="brand"
          icon={<Loader2 className="animate-spin motion-reduce:animate-none" />}
        >
          Reading
        </Chip>
      );
    case "read":
      return (
        <Chip
          size="sm"
          tone="accent"
          icon={<Loader2 className="animate-spin motion-reduce:animate-none" />}
        >
          Checking
        </Chip>
      );
    case "checked":
      return (
        <Chip size="sm" tone="success" icon={<Check />}>
          Done
        </Chip>
      );
    case "failed":
      return (
        <Chip size="sm" tone="danger" icon={<CircleAlert />}>
          Failed
        </Chip>
      );
  }
}

function isPdf(filename: string): boolean {
  return /\.pdf$/i.test(filename);
}

/**
 * One upload moving through waiting -> reading -> read -> checked. The card has a fixed minimum
 * height so streaming updates never shift the layout; each new stage fades in.
 */
export function DocumentProgressCard({
  state,
  doc,
  demo = false,
  replay = false,
}: {
  state: BatchState;
  doc: DocProgress;
  /** The public demo only reads its recorded samples, so "retake the photo" would mislead. */
  demo?: boolean;
  /** The answers are replayed from recordings: a cost shown is the recorded one, not spent now. */
  replay?: boolean;
}) {
  const phase = phaseOf(state, doc);
  const Icon = isPdf(doc.filename) ? FileText : FileImage;

  return (
    <li
      data-phase={phase}
      className={cn(
        "shadow-card min-h-[14.5rem] min-w-0 rounded-2xl border bg-white p-4 transition-colors duration-500",
        phase === "failed" ? "border-rose-300 bg-rose-50/40" : "border-slate-200",
      )}
    >
      <div className="flex items-start gap-3">
        <span
          aria-hidden="true"
          className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-slate-100 text-slate-600"
        >
          <Icon className="size-5" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-slate-900" title={doc.filename}>
            {doc.filename}
          </p>
          <p className="text-xs text-slate-600">{PHASE_TEXT[phase]}</p>
        </div>
        <PhaseBadge phase={phase} />
      </div>

      <Stepper phase={phase} />

      <div key={phase} className="animate-fade-in mt-4 min-h-[6.5rem]">
        {phase === "waiting" || phase === "reading" ? (
          <div className="space-y-2" aria-hidden="true">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-4 w-1/3" />
            <Skeleton className="h-6 w-1/2" />
          </div>
        ) : null}

        {phase === "read" || phase === "checked" ? (
          <div className="space-y-2.5">
            <div className="flex items-baseline justify-between gap-3">
              <p className="min-w-0 truncate text-sm font-semibold text-slate-900">
                {doc.merchant ?? "Merchant not found"}
              </p>
              <p className="shrink-0 text-base font-semibold text-slate-900 tabular-nums">
                {doc.total !== null ? formatINR(doc.total) : "No total"}
              </p>
            </div>
            <p className="text-xs text-slate-600">
              {doc.docType ? docTypeLabel(doc.docType) : "Document"}
              {doc.cached ? " · cached (no LLM cost)" : null}
              {doc.costUsd !== null && !doc.cached
                ? ` · ${formatUSD(doc.costUsd)}${replay ? " (recorded)" : ""}`
                : null}
            </p>
            <div className="flex flex-wrap items-center gap-1.5">
              {doc.category ? (
                <Chip tone="brand" size="sm">
                  {categoryLabel(doc.category)}
                </Chip>
              ) : null}
              {doc.categoryConfidence !== null ? (
                <ConfidenceBadge value={doc.categoryConfidence} what="category confidence" />
              ) : null}
              {doc.engine ? <EngineBadge engine={doc.engine} /> : null}
            </div>
            {phase === "checked" ? (
              <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-2.5">
                <TrustBadge verdict={doc.verdict} score={doc.trustScore} showMeter />
                <span className="text-xs font-medium text-slate-700">
                  {doc.findings === null
                    ? null
                    : doc.findings === 0
                      ? "No flags"
                      : `${doc.findings} ${doc.findings === 1 ? "flag" : "flags"}`}
                </span>
              </div>
            ) : null}
          </div>
        ) : null}

        {phase === "failed" ? (
          <div className="space-y-2 text-sm">
            <p className="font-medium text-rose-900">
              {doc.error ?? "This file could not be read."}
            </p>
            {demo ? (
              <p className="text-slate-700">
                Go{" "}
                <Link
                  href="/"
                  className="font-semibold text-indigo-700 underline underline-offset-2"
                >
                  back to upload
                </Link>{" "}
                and choose &ldquo;Try with sample receipts&rdquo;. The other receipts are not
                affected.
              </p>
            ) : (
              <p className="text-slate-700">
                Retake the photo in good light with the whole receipt in frame, then{" "}
                <Link
                  href="/"
                  className="font-semibold text-indigo-700 underline underline-offset-2"
                >
                  upload it again
                </Link>
                . The other receipts are not affected.
              </p>
            )}
          </div>
        ) : null}
      </div>
    </li>
  );
}
