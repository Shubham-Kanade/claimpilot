"use client";

import { FileStack, Upload } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { buttonClasses } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/feedback";
import type { ClaimStatus, ClaimView } from "@/lib/api/types";
import { cn } from "@/lib/cn";
import { pluralize } from "@/lib/format";
import { useClaims } from "@/lib/hooks/queries";
import { STATUS_INFO } from "@/lib/labels";
import { sortForReview } from "@/lib/claims/summary";
import { usePersona } from "@/lib/persona/PersonaProvider";

import { StartOver } from "@/components/demo/StartOver";

import { ClaimCard } from "./ClaimCard";

export type ClaimFilter = "all" | ClaimStatus;

const FILTERS: readonly { id: ClaimFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "needs_info", label: STATUS_INFO.needs_info.label },
  { id: "ready", label: STATUS_INFO.ready.label },
  { id: "submitted", label: STATUS_INFO.submitted.label },
  { id: "approved", label: STATUS_INFO.approved.label },
  { id: "rejected", label: STATUS_INFO.rejected.label },
];

export function countByStatus(claims: readonly ClaimView[]): Record<ClaimFilter, number> {
  const counts: Record<string, number> = { all: claims.length };
  for (const claim of claims) counts[claim.status] = (counts[claim.status] ?? 0) + 1;
  return counts as Record<ClaimFilter, number>;
}

export function filterClaims(claims: readonly ClaimView[], filter: ClaimFilter): ClaimView[] {
  // Drafts have no filter of their own: they are shown under "All" and "Needs your input".
  if (filter === "all") return [...claims];
  if (filter === "needs_info") {
    return claims.filter((c) => c.status === "needs_info" || c.status === "draft");
  }
  return claims.filter((c) => c.status === filter);
}

function FilterBar({
  counts,
  value,
  onChange,
}: {
  counts: Record<ClaimFilter, number>;
  value: ClaimFilter;
  onChange: (filter: ClaimFilter) => void;
}) {
  return (
    <div
      role="group"
      aria-label="Filter claims by status"
      className="-mx-4 flex gap-2 overflow-x-auto px-4 pb-1 sm:mx-0 sm:flex-wrap sm:px-0"
    >
      {FILTERS.map((filter) => {
        const selected = filter.id === value;
        const count =
          filter.id === "needs_info"
            ? (counts.needs_info ?? 0) + (counts.draft ?? 0)
            : (counts[filter.id] ?? 0);
        return (
          <button
            key={filter.id}
            type="button"
            aria-pressed={selected}
            onClick={() => onChange(filter.id)}
            className={cn(
              "inline-flex h-10 shrink-0 items-center gap-2 rounded-full px-4 text-sm font-semibold ring-1 transition-colors ring-inset",
              selected
                ? "bg-indigo-600 text-white ring-indigo-600"
                : "bg-white text-slate-700 ring-slate-300 hover:bg-slate-50",
            )}
          >
            {filter.label}{" "}
            <span
              className={cn(
                "rounded-full px-1.5 text-xs tabular-nums",
                selected ? "bg-white text-indigo-700" : "bg-slate-100 text-slate-700",
              )}
            >
              {count}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function ClaimSkeletons() {
  return (
    <div
      className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3"
      aria-busy="true"
      aria-label="Loading claims"
    >
      {Array.from({ length: 6 }, (_, i) => (
        <Card key={i} className="space-y-3">
          <Skeleton className="h-6 w-3/4" />
          <Skeleton className="h-4 w-1/2" />
          <Skeleton className="h-6 w-1/3" />
          <Skeleton className="h-px w-full" />
          <Skeleton className="h-5 w-2/3" />
        </Card>
      ))}
    </div>
  );
}

/** /claims: every claim the acting persona can see, filterable by status. */
export function ClaimsScreen() {
  const { status, error: personaError, reload } = usePersona();
  const claimsQuery = useClaims();
  const [filter, setFilter] = useState<ClaimFilter>("all");

  const claims = claimsQuery.data;
  const counts = claims ? countByStatus(claims) : null;
  const visible = claims ? sortForReview(filterClaims(claims, filter)) : [];

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 px-4 py-8 sm:px-6 sm:py-12 lg:px-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900 sm:text-3xl">
            My claims
          </h1>
          <p className="mt-1 text-sm text-slate-600 sm:text-base">
            {claims
              ? claims.length === 0
                ? "Nothing here yet."
                : `${pluralize(claims.length, "claim")}. The ones that need you come first. Open one to review and submit.`
              : "Your receipts, grouped into claims."}
          </p>
        </div>
        <div className="flex flex-wrap items-start gap-3">
          <StartOver />
          <Link href="/" className={buttonClasses("secondary")}>
            <Upload className="size-4" aria-hidden="true" />
            Upload more
          </Link>
        </div>
      </div>

      {status === "error" ? (
        <ErrorState error={personaError} onRetry={reload} />
      ) : claimsQuery.isError ? (
        <ErrorState error={claimsQuery.error} onRetry={() => void claimsQuery.refetch()} />
      ) : !claims || !counts ? (
        <ClaimSkeletons />
      ) : claims.length === 0 ? (
        <EmptyState
          icon={<FileStack className="size-6" />}
          title="No claims yet"
          action={
            <Link href="/" className={buttonClasses("primary")}>
              Upload receipts
            </Link>
          }
        >
          Drop a pile of receipts and ClaimPilot will read them, check them and group them into
          claims for you.
        </EmptyState>
      ) : (
        <>
          <FilterBar counts={counts} value={filter} onChange={setFilter} />
          <p role="status" aria-live="polite" className="sr-only">
            Showing {pluralize(visible.length, "claim")}
          </p>
          {visible.length === 0 ? (
            <EmptyState title="No claims with this status">
              Try another filter to see your other claims.
            </EmptyState>
          ) : (
            <ul className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {visible.map((claim) => (
                <li key={claim.id} className="min-w-0">
                  <ClaimCard claim={claim} />
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
