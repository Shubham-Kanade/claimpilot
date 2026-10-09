"use client";

import { Coins, FileCheck2, Timer, Zap } from "lucide-react";
import type { ReactNode } from "react";

import { Skeleton, ErrorState } from "@/components/ui/feedback";
import { Card } from "@/components/ui/Card";
import { StatusChip } from "@/components/ui/badges";
import type { ClaimStatus, Stats } from "@/lib/api/types";
import { cn } from "@/lib/cn";
import { useMeta, useStats } from "@/lib/hooks/queries";
import { costProfile, type CostProfile } from "@/lib/labels";
import { formatMinutes, formatSeconds, formatUSD, pluralize } from "@/lib/format";

const STATUS_ORDER: readonly ClaimStatus[] = [
  "draft",
  "needs_info",
  "ready",
  "submitted",
  "approved",
  "rejected",
];

const COST_NOTES: Record<CostProfile, string> = {
  recorded: ", as recorded with the replayed answers. Nothing is spent when you try the demo.",
  "recorded-and-live":
    ", partly recorded with the replayed sample answers, partly spent live on receipts you uploaded.",
  live: ".",
};

/** Share of claims that are low risk (one click for the approver), as a whole percentage. */
export function autoApproveShare(stats: Pick<Stats, "claims" | "auto_approvable_claims">): number {
  return stats.claims === 0 ? 0 : Math.round((stats.auto_approvable_claims / stats.claims) * 100);
}

function Tile({
  icon,
  label,
  value,
  note,
  className,
}: {
  icon: ReactNode;
  label: string;
  value: ReactNode;
  note?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-1", className)}>
      <div className="flex items-center gap-2 text-sm font-medium text-slate-600">
        <span aria-hidden="true" className="text-indigo-600 [&>svg]:size-4">
          {icon}
        </span>
        {label}
      </div>
      <div className="text-2xl font-semibold tracking-tight text-slate-900 tabular-nums sm:text-3xl">
        {value}
      </div>
      {note ? <p className="text-xs leading-relaxed text-slate-600">{note}</p> : null}
    </div>
  );
}

/** The impact numbers for a given Stats object (pure; `StatsWidget` fetches and renders this). */
export function StatsPanel({
  stats,
  variant,
  profile = "live",
}: {
  stats: Stats;
  variant: "compact" | "full";
  /** Where the costs come from (GET /v1/meta): recorded with replayed answers, live, or both. */
  profile?: CostProfile;
}) {
  const share = autoApproveShare(stats);
  const compact = variant === "compact";
  const minutesSaved = stats.estimated_minutes_saved;

  return (
    <div className="space-y-6">
      <div
        className={cn(
          "grid gap-x-8 gap-y-6",
          compact ? "grid-cols-2 lg:grid-cols-4" : "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3",
        )}
      >
        <Tile
          icon={<FileCheck2 />}
          label="Receipts processed"
          value={stats.documents_processed}
          note={
            stats.documents_failed > 0
              ? `${pluralize(stats.documents_failed, "file")} could not be read`
              : "Every file read successfully"
          }
        />
        <Tile
          icon={<Timer />}
          label="Time saved (estimated)"
          value={formatMinutes(minutesSaved)}
          note={`Estimate: assumes ${stats.assumed_manual_minutes_per_document} min of manual work per receipt.`}
        />
        <Tile
          icon={<Coins />}
          label={
            profile === "recorded"
              ? "LLM cost per receipt (as recorded)"
              : profile === "recorded-and-live"
                ? "LLM cost per receipt (as recorded or live)"
                : "LLM cost per receipt"
          }
          value={
            stats.llm_cost_per_document_usd === null
              ? "—"
              : formatUSD(stats.llm_cost_per_document_usd)
          }
          note={`${formatUSD(stats.llm_cost_usd)} in total over ${pluralize(stats.llm_calls, "LLM call")}${COST_NOTES[profile]}`}
        />
        <Tile
          icon={<Zap />}
          label="Low-risk claims"
          value={`${share}%`}
          note={`${stats.auto_approvable_claims} of ${pluralize(stats.claims, "claim")} are small, clean and complete: one click to approve.`}
        />
        {!compact ? (
          <Tile
            icon={<Timer />}
            label="Average processing time"
            value={stats.avg_batch_seconds === null ? "—" : formatSeconds(stats.avg_batch_seconds)}
            note="From upload to claims ready, per batch."
          />
        ) : null}
      </div>

      {!compact ? (
        <div>
          <h2 className="text-sm font-semibold text-slate-900">Claims by status</h2>
          <ul className="mt-3 flex flex-wrap gap-2">
            {STATUS_ORDER.map((status) => (
              <li key={status} className="flex items-center gap-1.5">
                <StatusChip status={status} />
                <span className="text-sm font-semibold text-slate-800 tabular-nums">
                  {stats.claims_by_status[status] ?? 0}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

/** Impact meter, connected to GET /v1/stats. */
export function StatsWidget({ variant = "compact" }: { variant?: "compact" | "full" }) {
  const query = useStats();
  const profile = costProfile(useMeta().data);

  if (query.isPending) {
    return (
      <Card aria-busy="true" aria-label="Loading impact numbers">
        <div className="grid grid-cols-2 gap-6 lg:grid-cols-4">
          {Array.from({ length: 4 }, (_, i) => (
            <div key={i} className="space-y-2">
              <Skeleton className="h-4 w-28" />
              <Skeleton className="h-8 w-20" />
              <Skeleton className="h-3 w-full" />
            </div>
          ))}
        </div>
      </Card>
    );
  }
  if (query.isError) {
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  }
  return (
    <Card>
      <StatsPanel stats={query.data} variant={variant} profile={profile} />
    </Card>
  );
}
