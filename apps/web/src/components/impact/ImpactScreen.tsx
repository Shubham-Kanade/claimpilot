"use client";

import { useMeta } from "@/lib/hooks/queries";
import { costProfile } from "@/lib/labels";

import { StatsWidget } from "./StatsWidget";

/** /impact: the impact meter in full, with honest labels (time saved is an estimate). */
export function ImpactScreen() {
  const profile = costProfile(useMeta().data);
  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 px-4 py-8 sm:px-6 sm:py-12 lg:px-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900 sm:text-3xl">Impact</h1>
        <p className="mt-1 max-w-2xl text-sm text-slate-600 sm:text-base">
          What ClaimPilot has done so far and what it cost. Counts and costs are measured; time
          saved is an estimate based on a stated assumption, never a measurement.
        </p>
      </div>
      <StatsWidget variant="full" />
      <section
        aria-labelledby="method-heading"
        className="shadow-card rounded-2xl bg-white p-5 text-sm leading-relaxed text-slate-700 ring-1 ring-slate-200"
      >
        <h2 id="method-heading" className="mb-1 text-base font-semibold text-slate-900">
          How these numbers are worked out
        </h2>
        <ul className="list-disc space-y-1 pl-5">
          <li>
            <strong className="font-semibold">Time saved</strong> = receipts processed × the assumed
            minutes of manual work per receipt (shown on the tile).
          </li>
          <li>
            <strong className="font-semibold">LLM cost per receipt</strong> comes from the cost
            ledger: every model call is recorded with its token cost.
            {profile === "recorded"
              ? " In this demo the answers are replayed from recordings, so these are the costs recorded with them, not money spent now."
              : profile === "recorded-and-live"
                ? " In this demo the sample receipts replay recorded answers (their recorded cost), while receipts you upload yourself are read live and cost real money, within a shared daily budget."
                : ""}
          </li>
          <li>
            <strong className="font-semibold">Low-risk</strong> claims are small, have no flags and
            no open questions, so an approver can approve them in one click. Nothing is approved
            without that click.
          </li>
        </ul>
      </section>
    </div>
  );
}
