"use client";

import { BadgeCheck, CircleX, Send } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";

import { RouteChip, StatusChip } from "@/components/ui/badges";
import { Button, buttonClasses } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { InlineError } from "@/components/ui/feedback";
import type { ClaimView } from "@/lib/api/types";
import { countBySeverity, unansweredQuestions } from "@/lib/claims/summary";
import { cn } from "@/lib/cn";
import { formatINR, pluralize } from "@/lib/format";
import { useSubmitClaim } from "@/lib/hooks/queries";
import { newIdempotencyKey } from "@/lib/idempotency";
import { SEVERITY_INFO, SEVERITY_ORDER } from "@/lib/labels";

/** "1 high risk, 2 warnings" or "None". */
export function describeFlags(claim: ClaimView): string {
  const counts = countBySeverity(claim.findings);
  const parts = SEVERITY_ORDER.filter((s) => counts[s] > 0).map(
    (s) =>
      `${counts[s]} ${counts[s] === 1 ? SEVERITY_INFO[s].label.toLowerCase() : SEVERITY_INFO[s].heading.toLowerCase()}`,
  );
  return parts.length > 0 ? parts.join(", ") : "None";
}

/** Why a claim goes to a person, using only what the API tells us. */
export function routeReasons(claim: ClaimView): string[] {
  if (claim.route !== "finance_review") return [];
  const counts = countBySeverity(claim.findings);
  const reasons: string[] = [];
  if (counts.high > 0) reasons.push(pluralize(counts.high, "high-risk flag"));
  if (counts.warn > 0) reasons.push(pluralize(counts.warn, "warning"));
  const open = unansweredQuestions(claim).length;
  if (open > 0) reasons.push(pluralize(open, "open question"));
  if (reasons.length === 0) reasons.push("the claim total");
  return reasons;
}

function ConfirmContent({ claim, error }: { claim: ClaimView; error: unknown }) {
  const reasons = routeReasons(claim);
  return (
    <div className="space-y-4">
      <dl className="divide-y divide-slate-100 rounded-xl border border-slate-200 text-sm">
        <div className="flex items-center justify-between gap-4 px-4 py-3">
          <dt className="text-slate-600">Claim</dt>
          <dd className="text-right font-semibold text-slate-900">{claim.title}</dd>
        </div>
        <div className="flex items-center justify-between gap-4 px-4 py-3">
          <dt className="text-slate-600">Total</dt>
          <dd className="text-lg font-semibold text-slate-900 tabular-nums">
            {formatINR(claim.total)}
          </dd>
        </div>
        <div className="flex items-center justify-between gap-4 px-4 py-3">
          <dt className="text-slate-600">Receipts</dt>
          <dd className="font-semibold text-slate-900">{claim.document_ids.length}</dd>
        </div>
        <div className="flex items-center justify-between gap-4 px-4 py-3">
          <dt className="text-slate-600">Flags</dt>
          <dd className="text-right font-semibold text-slate-900">{describeFlags(claim)}</dd>
        </div>
        <div className="flex items-center justify-between gap-4 px-4 py-3">
          <dt className="text-slate-600">Route</dt>
          <dd>
            <RouteChip route={claim.route} />
          </dd>
        </div>
      </dl>
      <p className="text-sm leading-relaxed text-slate-700">
        {reasons.length > 0
          ? `This claim goes to finance for review because of ${reasons.join(" and ")}. `
          : "This claim is low risk (small, clean and complete), so the approver can approve it in one click. "}
        Once submitted it can&apos;t be changed.
      </p>
      {error ? <InlineError error={error} /> : null}
    </div>
  );
}

/**
 * The sticky bar at the bottom of the claim: status, total, route, and the ONLY way to submit.
 * "Confirm & submit" is enabled only for the owner of a `ready` claim and opens a confirmation
 * dialog; submitting sends `{confirmed: true}` with an Idempotency-Key generated once per claim
 * view and reused on every retry, so a double click or a retry can never create two submissions.
 */
export function SubmitBar({ claim, isOwner }: { claim: ClaimView; isOwner: boolean }) {
  const submit = useSubmitClaim(claim.id);
  const [open, setOpen] = useState(false);
  const idempotencyKey = useRef<string | null>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);

  const open_questions = unansweredQuestions(claim).length;
  const ready = claim.status === "ready" && isOwner;

  function confirm() {
    idempotencyKey.current ??= newIdempotencyKey();
    submit.mutate(idempotencyKey.current, {
      onSuccess: () => setOpen(false),
    });
  }

  // The bar is slim on phones (a row: total + button, one line of help) and roomier on desktop.
  let action;
  let hint: string | null = null;
  if (claim.status === "submitted" || claim.status === "approved" || claim.status === "rejected") {
    action = (
      <div className="flex flex-wrap items-center justify-end gap-2 sm:gap-3">
        <p
          role="status"
          className="inline-flex items-center gap-2 rounded-xl bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-900 ring-1 ring-emerald-200 ring-inset sm:px-3.5"
        >
          {claim.status === "rejected" ? (
            <CircleX className="size-4" aria-hidden="true" />
          ) : (
            <BadgeCheck className="size-4" aria-hidden="true" />
          )}
          {claim.status === "approved"
            ? "Approved"
            : claim.status === "rejected"
              ? "Rejected"
              : "Submitted"}
          {claim.submission_reference ? (
            <>
              {" · "}
              <span className="font-mono">{claim.submission_reference}</span>
            </>
          ) : null}
        </p>
        <Link href="/claims" className={buttonClasses("secondary")}>
          All claims
        </Link>
      </div>
    );
  } else {
    action = (
      <Button
        disabled={!ready}
        onClick={() => setOpen(true)}
        icon={<Send className="size-4" aria-hidden="true" />}
        aria-describedby="submit-hint"
        className="sm:h-12 sm:px-6 sm:text-base"
      >
        Confirm &amp; submit
      </Button>
    );
    hint = !isOwner
      ? "Only the claim's owner can submit it."
      : ready
        ? "You'll see a summary first. Nothing is sent until you confirm."
        : open_questions > 0
          ? `Answer ${open_questions === 1 ? "the open question" : `the ${open_questions} open questions`} to enable submit.`
          : "This claim isn't ready to submit yet.";
  }

  return (
    <>
      <div className="fixed inset-x-0 bottom-0 z-30 border-t border-slate-200 bg-white/95 pb-[env(safe-area-inset-bottom)] shadow-[0_-6px_16px_-8px_rgba(15,23,42,0.18)] backdrop-blur">
        <div className="mx-auto max-w-6xl px-4 py-2.5 sm:px-6 sm:py-3 lg:px-8">
          <div className="flex items-center justify-between gap-3 sm:gap-6">
            <div className="flex min-w-0 items-center gap-4">
              <div>
                <p className="text-xs font-medium text-slate-600">Claim total</p>
                <p className="text-lg leading-tight font-semibold text-slate-900 tabular-nums sm:text-xl">
                  {formatINR(claim.total)}
                </p>
              </div>
              {/* the status and route are also in the claim header: bar space is scarce on phones */}
              <div className="hidden flex-wrap items-center gap-1.5 sm:flex">
                <StatusChip status={claim.status} />
                <RouteChip route={claim.route} />
              </div>
            </div>
            {action}
          </div>
          {hint ? (
            <p
              id="submit-hint"
              className={cn(
                "mt-1.5 text-xs text-slate-600 sm:text-right",
                ready && "hidden sm:block", // an enabled button needs no help on a phone
              )}
            >
              {hint}
            </p>
          ) : null}
        </div>
      </div>

      <Dialog
        open={open}
        onClose={() => !submit.isPending && setOpen(false)}
        title="Submit this claim to finance?"
        description="Check the summary. Submitting is final."
        dismissible={!submit.isPending}
        initialFocusRef={cancelRef}
        footer={
          <>
            <Button
              ref={cancelRef}
              variant="secondary"
              onClick={() => setOpen(false)}
              disabled={submit.isPending}
            >
              Cancel
            </Button>
            <Button onClick={confirm} loading={submit.isPending}>
              {submit.isError ? "Try again" : "Confirm & submit"}
            </Button>
          </>
        }
      >
        <ConfirmContent claim={claim} error={submit.error} />
      </Dialog>
    </>
  );
}
