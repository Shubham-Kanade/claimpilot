import { Crosshair } from "lucide-react";

import { SeverityBadge } from "@/components/ui/badges";
import type { Finding, Severity } from "@/lib/api/types";
import { cn } from "@/lib/cn";
import {
  comparisonLabels,
  formatFindingValue,
  groupBySeverity,
  hasComparison,
  splitDocumentReferences,
} from "@/lib/claims/summary";
import { useDocument } from "@/lib/hooks/queries";
import { SEVERITY_INFO } from "@/lib/labels";

const SOURCE_LABEL: Record<Finding["source"] & string, string> = {
  trust: "Trust check",
  policy: "Policy check",
  decision: "System One",
  system: "System note",
};

const BORDER: Record<Severity, string> = {
  high: "border-l-rose-600",
  warn: "border-l-amber-500",
  info: "border-l-sky-500",
};

/** The quoted policy clause, styled as a citation. */
export function PolicyCitation({ clauseId, text }: { clauseId: string; text?: string | null }) {
  return (
    <figure className="mt-3 rounded-lg border-l-4 border-teal-600 bg-teal-50/70 px-3 py-2.5">
      {text ? (
        <blockquote className="text-sm leading-relaxed text-slate-800 italic">
          <span aria-hidden="true">“</span>
          {text}
          <span aria-hidden="true">”</span>
        </blockquote>
      ) : null}
      <figcaption className="mt-1 text-xs font-semibold text-teal-800">
        Policy clause {clauseId}
      </figcaption>
    </figure>
  );
}

/** A reference to another receipt, by its file name (the backend only knows its id). */
function ReceiptName({ id }: { id: string }) {
  const receipt = useDocument(id);
  const filename = receipt.data?.filename;
  return filename ? (
    <span className="font-semibold break-all">{filename}</span>
  ) : (
    <>another receipt</>
  );
}

/**
 * A finding's message. Duplicate findings point at the earlier receipt by id: show its file name
 * instead (looked up; "another receipt" while that is unknown), never a 32-character code.
 */
export function FindingMessage({ message }: { message: string }) {
  return (
    <>
      {splitDocumentReferences(message).map((part, index) =>
        "text" in part ? (
          <span key={index}>{part.text}</span>
        ) : (
          <ReceiptName key={index} id={part.documentId} />
        ),
      )}
    </>
  );
}

export function FindingItem({
  finding,
  onShowField,
}: {
  finding: Finding;
  /** Offered when the finding names receipt fields: highlight them on the receipt. */
  onShowField?: (finding: Finding) => void;
}) {
  const source = SOURCE_LABEL[finding.source ?? "trust"];
  const compare = hasComparison(finding);
  const labels = comparisonLabels(finding);
  return (
    <li
      data-severity={finding.severity}
      className={cn(
        "rounded-xl border border-l-4 border-slate-200 bg-white p-3.5 shadow-sm sm:p-4",
        BORDER[finding.severity],
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <SeverityBadge severity={finding.severity} size="sm" />
        <span className="text-xs font-medium text-slate-600">{source}</span>
      </div>
      <p className="mt-2 text-sm leading-relaxed font-medium text-slate-900">
        <FindingMessage message={finding.message} />
      </p>

      {compare ? (
        <dl className="mt-3 grid grid-cols-2 gap-2 text-sm">
          <div className="min-w-0 rounded-lg bg-slate-50 px-3 py-2 ring-1 ring-slate-200 ring-inset">
            <dt className="text-xs font-medium text-slate-600">{labels.expected}</dt>
            <dd className="font-semibold break-words text-slate-900 tabular-nums">
              {formatFindingValue(finding, finding.expected)}
            </dd>
          </div>
          <div className="min-w-0 rounded-lg bg-slate-50 px-3 py-2 ring-1 ring-slate-200 ring-inset">
            <dt className="text-xs font-medium text-slate-600">{labels.actual}</dt>
            <dd className="font-semibold break-words text-slate-900 tabular-nums">
              {formatFindingValue(finding, finding.actual)}
            </dd>
          </div>
        </dl>
      ) : null}

      {finding.clause_id ? (
        <PolicyCitation clauseId={finding.clause_id} text={finding.clause_text} />
      ) : null}

      {onShowField && finding.fields.length > 0 ? (
        <button
          type="button"
          onClick={() => onShowField(finding)}
          className="mt-3 inline-flex items-center gap-1.5 text-sm font-semibold text-indigo-700 hover:underline"
        >
          <Crosshair className="size-4" aria-hidden="true" />
          Show on the receipt
        </button>
      ) : null}
    </li>
  );
}

/** Findings grouped by severity (high, warning, note): icon + word, message, evidence, citation. */
export function FindingList({
  findings,
  onShowField,
  emptyText = "No flags on this document.",
}: {
  findings: readonly Finding[];
  onShowField?: (finding: Finding) => void;
  emptyText?: string;
}) {
  if (findings.length === 0) {
    return <p className="text-sm text-slate-600">{emptyText}</p>;
  }
  return (
    <div className="space-y-4">
      {groupBySeverity(findings).map((group) => (
        <div key={group.severity}>
          <h4 className="mb-2 text-xs font-semibold tracking-wide text-slate-600 uppercase">
            {SEVERITY_INFO[group.severity].heading} ({group.findings.length})
          </h4>
          <ul className="space-y-2.5">
            {group.findings.map((finding, index) => (
              <FindingItem
                key={`${finding.code}-${finding.document_id ?? ""}-${index}`}
                finding={finding}
                onShowField={onShowField}
              />
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
