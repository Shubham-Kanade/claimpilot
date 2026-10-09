"use client";

import { CircleCheck, Inbox, ShieldCheck, X } from "lucide-react";
import { useState } from "react";

import { StartOver } from "@/components/demo/StartOver";
import { Button } from "@/components/ui/Button";
import { EmptyState, ErrorState, PageSkeleton, Skeleton } from "@/components/ui/feedback";
import { TabPanel, Tabs, type TabItem } from "@/components/ui/Tabs";
import type { ApprovalStatus, ClaimView } from "@/lib/api/types";
import { sortByRisk } from "@/lib/claims/summary";
import { useApprovals } from "@/lib/hooks/queries";
import { usePersona } from "@/lib/persona/PersonaProvider";

import { ApprovalRow } from "./ApprovalRow";

const ID_PREFIX = "approvals";

const EMPTY_TEXT: Record<ApprovalStatus, { title: string; body: string }> = {
  submitted: {
    title: "All caught up",
    body: "No submitted claims are waiting for a decision right now.",
  },
  approved: { title: "Nothing approved yet", body: "Claims you approve will be listed here." },
  rejected: { title: "Nothing rejected", body: "Claims you reject will be listed here." },
};

/** /approvals: the approver's queue. Everyone else gets a polite explanation. */
export function ApprovalsScreen() {
  const {
    isApprover,
    meLoading,
    status,
    error,
    reload,
    employees,
    approverIds,
    persona,
    setPersonaId,
  } = usePersona();
  const [tab, setTab] = useState<ApprovalStatus>("submitted");
  const [decided, setDecided] = useState<{ claim: ClaimView; approved: boolean } | null>(null);

  const submitted = useApprovals("submitted", isApprover);
  const approved = useApprovals("approved", isApprover);
  const rejected = useApprovals("rejected", isApprover);
  const queries = { submitted, approved, rejected };
  const active = queries[tab];

  if (status === "error") {
    return (
      <div className="mx-auto w-full max-w-3xl px-4 py-12">
        <ErrorState as="h1" error={error} onRetry={reload} />
      </div>
    );
  }
  if (status === "loading" || meLoading) {
    return (
      <div className="mx-auto w-full max-w-6xl px-4 py-10">
        <PageSkeleton rows={2} />
      </div>
    );
  }

  if (!isApprover) {
    const approvers = employees.filter((e) => approverIds.has(e.id));
    return (
      <div className="mx-auto w-full max-w-3xl px-4 py-12 sm:py-20">
        <EmptyState
          as="h1"
          icon={<ShieldCheck className="size-6" />}
          title="Approvers only"
          action={approvers.map((approver) => (
            <Button key={approver.id} variant="primary" onClick={() => setPersonaId(approver.id)}>
              Switch to {approver.name}
            </Button>
          ))}
        >
          {persona ? `${persona.name} can't review other people's claims. ` : ""}
          The approvals queue is for approvers. Use the persona menu at the top to act as one.
        </EmptyState>
      </div>
    );
  }

  const tabs: TabItem<ApprovalStatus>[] = [
    { id: "submitted", label: "Submitted", count: submitted.data?.length },
    { id: "approved", label: "Approved", count: approved.data?.length },
    { id: "rejected", label: "Rejected", count: rejected.data?.length },
  ];

  const claims = active.data ? (tab === "submitted" ? sortByRisk(active.data) : active.data) : null;

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 px-4 py-8 sm:px-6 sm:py-12 lg:px-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900 sm:text-3xl">
            Approvals
          </h1>
          <p className="mt-1 text-sm text-slate-600 sm:text-base">
            Submitted claims, riskiest first. Open one to see the receipts, what was flagged and
            why.
          </p>
        </div>
        {/* Public demo only. An approver's reset empties the whole demo session (not other visitors'), and says so first. */}
        <StartOver />
      </div>

      {decided ? (
        <div
          role="status"
          className="flex items-start justify-between gap-3 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-950 ring-1 ring-emerald-200 ring-inset"
        >
          <span className="flex items-start gap-2">
            <CircleCheck className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <span>
              <strong className="font-semibold">
                {decided.approved ? "Approved" : "Rejected"}
              </strong>
              : {decided.claim.title}
              {decided.claim.submission_reference ? ` (${decided.claim.submission_reference})` : ""}
            </span>
          </span>
          <button
            type="button"
            onClick={() => setDecided(null)}
            aria-label="Dismiss"
            className="rounded-md p-1 hover:bg-emerald-100"
          >
            <X className="size-4" aria-hidden="true" />
          </button>
        </div>
      ) : null}

      <Tabs tabs={tabs} value={tab} onChange={setTab} label="Claim status" idPrefix={ID_PREFIX} />

      <TabPanel idPrefix={ID_PREFIX} id={tab} className="space-y-3 outline-none">
        {active.isError ? (
          <ErrorState error={active.error} onRetry={() => void active.refetch()} />
        ) : !claims ? (
          <div className="space-y-3" aria-busy="true" aria-label="Loading claims">
            {Array.from({ length: 3 }, (_, i) => (
              <Skeleton key={i} className="h-28 w-full rounded-2xl" />
            ))}
          </div>
        ) : claims.length === 0 ? (
          <EmptyState icon={<Inbox className="size-6" />} title={EMPTY_TEXT[tab].title}>
            {EMPTY_TEXT[tab].body}
          </EmptyState>
        ) : (
          claims.map((claim) => (
            <ApprovalRow
              key={claim.id}
              claim={claim}
              employee={employees.find((e) => e.id === claim.employee_id)}
              onDecided={(updated, wasApproved) => {
                setDecided({ claim: updated, approved: wasApproved });
              }}
            />
          ))
        )}
      </TabPanel>
    </div>
  );
}
