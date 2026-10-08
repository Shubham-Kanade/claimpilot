"use client";

import type { ClaimView } from "@/lib/api/types";
import { orderReceipts } from "@/lib/claims/summary";
import { pluralize } from "@/lib/format";

import { DocumentCard } from "./DocumentCard";
import { FindingList } from "./FindingList";

/**
 * The evidence behind a claim: findings about the claim as a whole, then every receipt as an
 * expandable card (original file, extracted fields with confidence, decisions, findings).
 * Read-only by construction, so the approver view reuses it as is.
 */
export function ClaimEvidence({ claim }: { claim: ClaimView }) {
  // Findings about one receipt are shown on its card; these are about the claim itself.
  const claimLevel = (claim.findings ?? []).filter((f) => !f.document_id);

  return (
    <section aria-labelledby={`evidence-${claim.id}`} className="space-y-4">
      <h2
        id={`evidence-${claim.id}`}
        className="text-lg font-semibold tracking-tight text-slate-900"
      >
        Evidence · {pluralize(claim.document_ids.length, "receipt")}
      </h2>

      {claimLevel.length > 0 ? (
        <div className="shadow-card rounded-2xl border border-slate-200 bg-white p-4 sm:p-5">
          <h3 className="mb-3 text-sm font-semibold text-slate-900">Flags on the whole claim</h3>
          <FindingList findings={claimLevel} />
        </div>
      ) : null}

      <div className="space-y-3">
        {orderReceipts(claim).map((documentId) => (
          <DocumentCard key={documentId} documentId={documentId} claimFindings={claim.findings} />
        ))}
      </div>
    </section>
  );
}
