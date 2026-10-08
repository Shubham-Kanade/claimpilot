"use client";

import { ChevronDown, CircleCheck, CircleX } from "lucide-react";
import { useId, useState } from "react";

import { ClaimEvidence } from "@/components/evidence/ClaimEvidence";
import { ModeBadge, RouteChip, SeverityBadge, StatusChip } from "@/components/ui/badges";
import { Button } from "@/components/ui/Button";
import { InlineError } from "@/components/ui/feedback";
import type { ClaimView, Employee } from "@/lib/api/types";
import { cn } from "@/lib/cn";
import { countBySeverity } from "@/lib/claims/summary";
import { formatDateRange, formatINR, pluralize } from "@/lib/format";
import { useDecision } from "@/lib/hooks/queries";
import { SEVERITY_ORDER } from "@/lib/labels";
import { TopFlag } from "@/components/claims/TopFlag";

export const COMMENT_MAX = 500;

/** Rejecting needs a reason (the finance system demands one); approving does not. */
export function canReject(comment: string): boolean {
  return comment.trim().length > 0;
}

function DecisionPanel({
  claim,
  onDecided,
}: {
  claim: ClaimView;
  onDecided?: (claim: ClaimView, approved: boolean) => void;
}) {
  const decision = useDecision();
  const commentId = useId();
  const helpId = useId();
  const [comment, setComment] = useState("");

  function decide(approved: boolean) {
    decision.mutate(
      { claimId: claim.id, approved, comment: comment.trim() },
      { onSuccess: (updated) => onDecided?.(updated, approved) },
    );
  }

  const rejectable = canReject(comment);
  const busyApprove = decision.isPending && decision.variables?.approved === true;
  const busyReject = decision.isPending && decision.variables?.approved === false;

  return (
    <div className="rounded-2xl border border-indigo-200 bg-indigo-50/60 p-4 sm:p-5">
      <h3 className="text-base font-semibold text-slate-900">Your decision</h3>
      <p className="mt-0.5 text-sm text-slate-700">
        Review the evidence below, then approve or reject. The decision goes to the finance system.
      </p>
      <div className="mt-3 space-y-1.5">
        <label htmlFor={commentId} className="text-sm font-medium text-slate-800">
          Comment{" "}
          <span className="font-normal text-slate-600">
            (optional to approve, required to reject)
          </span>
        </label>
        <textarea
          id={commentId}
          value={comment}
          onChange={(event) => setComment(event.target.value)}
          maxLength={COMMENT_MAX}
          rows={2}
          aria-describedby={helpId}
          className="w-full resize-y rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-base sm:text-sm"
        />
        <p id={helpId} className="flex justify-between gap-3 text-xs text-slate-600">
          <span>
            {rejectable
              ? "Ready to send with your decision."
              : "To reject, say why. Finance keeps the reason on record."}
          </span>
          <span className="tabular-nums">
            {comment.length}/{COMMENT_MAX}
          </span>
        </p>
      </div>
      {decision.isError ? <InlineError className="mt-3" error={decision.error} /> : null}
      <div className="mt-3 flex flex-wrap gap-2.5">
        <Button
          variant="success"
          onClick={() => decide(true)}
          loading={busyApprove}
          disabled={decision.isPending}
          icon={<CircleCheck className="size-4" aria-hidden="true" />}
        >
          Approve
        </Button>
        <Button
          variant="danger"
          onClick={() => decide(false)}
          loading={busyReject}
          disabled={decision.isPending || !rejectable}
          icon={<CircleX className="size-4" aria-hidden="true" />}
        >
          Reject
        </Button>
      </div>
    </div>
  );
}

/**
 * A claim in the approver's queue. The row shows who, what, how much and how risky; expanding it
 * shows the same evidence as the employee's review screen (read-only) plus the decision form.
 */
export function ApprovalRow({
  claim,
  employee,
  defaultOpen = false,
  decidable = claim.status === "submitted",
  onDecided,
}: {
  claim: ClaimView;
  employee?: Employee | null;
  defaultOpen?: boolean;
  /** Only submitted claims can be decided. */
  decidable?: boolean;
  onDecided?: (claim: ClaimView, approved: boolean) => void;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const panelId = useId();
  const counts = countBySeverity(claim.findings);
  const flagged = SEVERITY_ORDER.filter((s) => counts[s] > 0);

  return (
    <article
      data-testid="approval-row"
      className="shadow-card rounded-2xl border border-slate-200 bg-white"
    >
      <h2>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((v) => !v)}
          className="flex w-full items-start gap-3 rounded-2xl p-4 text-left hover:bg-slate-50 sm:p-5"
        >
          <span className="min-w-0 flex-1 space-y-2">
            <span className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
              <span className="min-w-0 text-base font-semibold text-slate-900 sm:text-lg">
                {claim.title}
              </span>
              <span className="text-lg font-semibold text-slate-900 tabular-nums sm:text-xl">
                {formatINR(claim.total)}
              </span>
            </span>
            <span className="block text-sm font-normal text-slate-600">
              {employee ? `${employee.name} · ${employee.grade} · ` : ""}
              {formatDateRange(claim.start_date, claim.end_date)}
              {claim.city ? ` · ${claim.city}` : ""} ·{" "}
              {pluralize(claim.document_ids.length, "receipt")}
              {claim.submission_reference ? ` · ${claim.submission_reference}` : ""}
            </span>
            <span className="flex flex-wrap items-center gap-1.5">
              <ModeBadge mode={claim.mode} size="sm" />
              <StatusChip status={claim.status} size="sm" />
              <RouteChip route={claim.route} size="sm" />
              {flagged.map((severity) => (
                <SeverityBadge
                  key={severity}
                  severity={severity}
                  count={counts[severity]}
                  size="sm"
                />
              ))}
              {flagged.length === 0 ? (
                <span className="text-xs text-slate-600">No flags</span>
              ) : null}
            </span>
            <TopFlag findings={claim.findings} inline />
          </span>
          <ChevronDown
            className={cn(
              "mt-1 size-5 shrink-0 text-slate-500 transition-transform motion-reduce:transition-none",
              open && "rotate-180",
            )}
            aria-hidden="true"
          />
        </button>
      </h2>

      {open ? (
        <div id={panelId} className="space-y-5 border-t border-slate-100 p-4 sm:p-5">
          {decidable ? <DecisionPanel claim={claim} onDecided={onDecided} /> : null}
          <ClaimEvidence claim={claim} />
        </div>
      ) : null}
    </article>
  );
}
