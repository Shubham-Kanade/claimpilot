import { CalendarDays, MapPin, UserRound } from "lucide-react";

import { ModeBadge, RouteChip, SeverityBadge, StatusChip } from "@/components/ui/badges";
import type { ClaimView, Employee } from "@/lib/api/types";
import { countBySeverity } from "@/lib/claims/summary";
import { formatDateRange, formatINR } from "@/lib/format";
import { SEVERITY_ORDER } from "@/lib/labels";

import { routeReasons } from "./SubmitBar";

/** The claim at a glance: title, mode, dates, city, owner, status, route, total and flag counts. */
export function ClaimHeader({ claim, owner }: { claim: ClaimView; owner?: Employee | null }) {
  const counts = countBySeverity(claim.findings);
  const flagged = SEVERITY_ORDER.filter((s) => counts[s] > 0);
  const reasons = routeReasons(claim);

  return (
    <header className="shadow-card rounded-2xl border border-slate-200 bg-white p-4 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900 sm:text-3xl">
            {claim.title}
          </h1>
          <p className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-slate-600">
            <span className="inline-flex items-center gap-1.5">
              <CalendarDays className="size-4" aria-hidden="true" />
              {formatDateRange(claim.start_date, claim.end_date)}
            </span>
            {claim.city ? (
              <span className="inline-flex items-center gap-1.5">
                <MapPin className="size-4" aria-hidden="true" />
                {claim.city}
              </span>
            ) : null}
            {owner ? (
              <span className="inline-flex items-center gap-1.5">
                <UserRound className="size-4" aria-hidden="true" />
                {owner.name}
              </span>
            ) : null}
          </p>
        </div>
        <div className="text-right">
          <p className="text-xs font-medium text-slate-600">Total</p>
          <p className="text-2xl font-semibold text-slate-900 tabular-nums sm:text-3xl">
            {formatINR(claim.total)}
          </p>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <ModeBadge mode={claim.mode} />
        <StatusChip status={claim.status} />
        <RouteChip route={claim.route} />
        {flagged.map((severity) => (
          <SeverityBadge key={severity} severity={severity} count={counts[severity]} />
        ))}
        {flagged.length === 0 ? <span className="text-sm text-slate-600">No flags</span> : null}
      </div>

      {claim.route === "finance_review" && reasons.length > 0 ? (
        <p className="mt-3 text-sm text-slate-700">
          Goes to finance review because of {reasons.join(" and ")}.
        </p>
      ) : claim.route === "auto_approve" ? (
        <p className="mt-3 text-sm text-slate-700">
          Low risk: small, clean and complete, so the approver can approve it in one click.
        </p>
      ) : null}
    </header>
  );
}
