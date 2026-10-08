import { CalendarDays, FileStack, MapPin } from "lucide-react";
import Link from "next/link";

import { ModeBadge, RouteChip, SeverityBadge, StatusChip } from "@/components/ui/badges";
import type { ClaimView } from "@/lib/api/types";
import { countBySeverity } from "@/lib/claims/summary";
import { formatDateRange, formatINR, pluralize } from "@/lib/format";
import { SEVERITY_ORDER } from "@/lib/labels";

import { TopFlag } from "./TopFlag";

/** A claim in a list: title, mode, dates, city, total, status, flags by severity and routing. */
export function ClaimCard({ claim }: { claim: ClaimView }) {
  const counts = countBySeverity(claim.findings);
  const flagged = SEVERITY_ORDER.filter((severity) => counts[severity] > 0);

  return (
    <article
      className="group shadow-card hover:shadow-raised relative flex h-full flex-col gap-3 rounded-2xl border border-slate-200 bg-white p-4 transition hover:border-indigo-300 has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-indigo-700 sm:p-5"
      data-testid="claim-card"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-base leading-snug font-semibold text-slate-900 sm:text-lg">
            <Link
              href={`/claims/${claim.id}`}
              className="outline-none after:absolute after:inset-0 after:rounded-2xl"
            >
              {claim.title}
            </Link>
          </h2>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-slate-600">
            <span className="inline-flex items-center gap-1">
              <CalendarDays className="size-3.5" aria-hidden="true" />
              {formatDateRange(claim.start_date, claim.end_date)}
            </span>
            {claim.city ? (
              <span className="inline-flex items-center gap-1">
                <MapPin className="size-3.5" aria-hidden="true" />
                {claim.city}
              </span>
            ) : null}
          </p>
        </div>
        <p className="shrink-0 text-lg font-semibold text-slate-900 tabular-nums sm:text-xl">
          {formatINR(claim.total)}
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <ModeBadge mode={claim.mode} />
        <StatusChip status={claim.status} />
      </div>

      <TopFlag findings={claim.findings} />

      <div className="mt-auto flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 pt-3">
        <div className="flex flex-wrap items-center gap-1.5">
          {flagged.length === 0 ? (
            <span className="text-sm text-slate-600">No flags</span>
          ) : (
            flagged.map((severity) => (
              <SeverityBadge
                key={severity}
                severity={severity}
                count={counts[severity]}
                size="sm"
              />
            ))
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1 text-xs text-slate-600">
            <FileStack className="size-3.5" aria-hidden="true" />
            {pluralize(claim.document_ids.length, "receipt")}
          </span>
          <RouteChip route={claim.route} size="sm" />
        </div>
      </div>
    </article>
  );
}
