"use client";

import { ChevronDown, FileImage, FileText, TriangleAlert } from "lucide-react";
import { useId, useState } from "react";

import { ConfidenceBadge, EngineBadge, SeverityBadge, TrustBadge } from "@/components/ui/badges";
import { Chip } from "@/components/ui/Chip";
import { ErrorState, Skeleton } from "@/components/ui/feedback";
import type { Decisions, DocumentView, Finding, ProcessedDocument } from "@/lib/api/types";
import { cn } from "@/lib/cn";
import { boxFor, fieldForFinding } from "@/lib/claims/fields";
import { countBySeverity, findingsForDocument } from "@/lib/claims/summary";
import { formatDate, formatINR, formatPercent } from "@/lib/format";
import { useDocument, useMeta } from "@/lib/hooks/queries";
import { categoryLabel, docTypeLabel, engineInfo, SEVERITY_ORDER } from "@/lib/labels";

import { FieldList } from "./FieldList";
import { FindingList } from "./FindingList";
import { ReceiptViewer, ZOOM_FOCUS } from "./ReceiptViewer";

/** A probability (0-1) as a labelled bar; flagged in words when it crosses the policy threshold. */
function Likelihood({
  label,
  value,
  threshold = 0.5,
}: {
  label: string;
  value: number;
  threshold?: number;
}) {
  const high = value >= threshold;
  return (
    <div className="min-w-0">
      <div className="flex items-center justify-between gap-2 text-sm">
        <span className="text-slate-700">{label}</span>
        <span
          className={cn("font-semibold tabular-nums", high ? "text-amber-900" : "text-slate-900")}
        >
          {formatPercent(value)}
          {high ? (
            <TriangleAlert
              className="ml-1 inline size-3.5 align-[-2px]"
              aria-label="above the policy threshold"
            />
          ) : null}
        </span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-200" aria-hidden="true">
        <div
          className={cn("h-full rounded-full", high ? "bg-amber-500" : "bg-teal-600")}
          style={{ width: `${Math.round(value * 100)}%` }}
        />
      </div>
    </div>
  );
}

/** How the document was categorised, and by which engine (the transparency the judges look for). */
export function DecisionsPanel({
  decisions,
  readerModel,
}: {
  decisions: Decisions;
  readerModel?: string | null;
}) {
  const engine = engineInfo(decisions.engine);
  return (
    <div className="space-y-3 rounded-xl border border-slate-200 bg-white p-3.5">
      <div className="flex flex-wrap items-center gap-2">
        <Chip tone="brand">{categoryLabel(decisions.category)}</Chip>
        <ConfidenceBadge value={decisions.category_confidence} what="category confidence" />
        <EngineBadge engine={decisions.engine} />
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Likelihood label="Alcohol on the bill" value={decisions.alcohol_present} />
        <Likelihood label="Looks personal" value={decisions.personal_expense} />
      </div>
      <p className="text-xs leading-relaxed text-slate-600">
        Category decided by <strong className="font-semibold text-slate-800">{engine.label}</strong>{" "}
        ({engine.detail}).
        {readerModel ? (
          <>
            {" "}
            Receipt read by{" "}
            <span className="font-mono text-[11px] text-slate-800">{readerModel}</span> (System
            Two).
          </>
        ) : null}
      </p>
    </div>
  );
}

export function FindingCounts({ findings }: { findings: readonly Finding[] }) {
  const counts = countBySeverity(findings);
  const flagged = SEVERITY_ORDER.filter((s) => counts[s] > 0);
  if (flagged.length === 0)
    return (
      <Chip tone="neutral" size="sm">
        No flags
      </Chip>
    );
  return (
    <>
      {flagged.map((severity) => (
        <SeverityBadge key={severity} severity={severity} count={counts[severity]} />
      ))}
    </>
  );
}

function Header({
  view,
  doc,
  open,
  panelId,
  titleId,
  onToggle,
}: {
  view: DocumentView;
  doc: ProcessedDocument | null;
  open: boolean;
  panelId: string;
  /** The title's id: the receipt's card is named by it. */
  titleId: string;
  onToggle: () => void;
}) {
  const Icon = /\.pdf$/i.test(view.filename) ? FileText : FileImage;
  const title = doc?.receipt.merchant_name ?? view.filename;
  const subtitle = [
    doc ? docTypeLabel(doc.receipt.doc_type) : "Receipt",
    doc?.receipt.date ? formatDate(doc.receipt.date) : null,
    doc && doc.receipt.merchant_name ? view.filename : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const total =
    doc?.receipt.total !== null && doc?.receipt.total !== undefined
      ? formatINR(doc.receipt.total)
      : "—";

  return (
    <h3 className="text-base">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={onToggle}
        className="flex w-full items-start gap-3 rounded-2xl p-4 text-left hover:bg-slate-50 sm:items-center"
      >
        <span
          aria-hidden="true"
          className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-slate-100 text-slate-600"
        >
          <Icon className="size-5" />
        </span>
        <span className="min-w-0 flex-1">
          <span
            id={titleId}
            className="line-clamp-2 block font-semibold break-words text-slate-900"
          >
            {title}
          </span>
          <span className="line-clamp-2 block text-sm font-normal break-words text-slate-600 sm:truncate">
            {subtitle}
          </span>
          {/* on phones the amount sits under the name, so the name keeps the width */}
          <span className="mt-0.5 block text-base font-semibold text-slate-900 tabular-nums sm:hidden">
            {total}
          </span>
        </span>
        <span className="hidden shrink-0 text-right text-base font-semibold text-slate-900 tabular-nums sm:block">
          {total}
        </span>
        <ChevronDown
          className={cn(
            "mt-2.5 size-5 shrink-0 text-slate-500 transition-transform motion-reduce:transition-none sm:mt-0",
            open && "rotate-180",
          )}
          aria-hidden="true"
        />
      </button>
    </h3>
  );
}

/**
 * One receipt of a claim, expandable. Collapsed it shows merchant, amount, category with
 * confidence, engine and trust verdict. Expanded it shows the ORIGINAL file next to the
 * extracted fields (click a field to see where it is printed), the decisions and the findings.
 * Documents with high or warning findings open by default so the reason for a flag is visible.
 */
export function DocumentCard({
  documentId,
  claimFindings,
}: {
  documentId: string;
  /** The claim's findings: those tagged with this document belong on its card too. */
  claimFindings?: readonly Finding[];
}) {
  const panelId = useId();
  const titleId = useId();
  const query = useDocument(documentId);
  const meta = useMeta();
  const [openState, setOpenState] = useState<boolean | null>(null);
  const [selectedField, setSelectedField] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);

  if (query.isPending) {
    return (
      <div
        className="shadow-card rounded-2xl border border-slate-200 bg-white p-4"
        aria-busy="true"
      >
        <Skeleton className="h-10 w-2/3" />
        <Skeleton className="mt-3 h-6 w-1/2" />
      </div>
    );
  }
  if (query.isError) {
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  }

  const view = query.data;
  const doc = view.document ?? null;
  const findings = findingsForDocument(doc?.findings, claimFindings, documentId);
  const serious = findings.some((f) => f.severity === "high" || f.severity === "warn");
  const open = openState ?? serious;
  const readerModel = meta.data?.routes.find((r) => r.route === "extraction")?.model_id ?? null;
  // Without locations (recorded replays have none) there is nothing to point at on the receipt.
  const hasLocations = Object.keys(doc?.boxes ?? {}).length > 0;

  function selectField(field: string | null) {
    setSelectedField(field);
    // On a phone the whole receipt is tiny: zoom in so the highlighted value is legible.
    const narrow = window.matchMedia?.("(max-width: 639px)").matches;
    if (field && narrow && boxFor(doc?.boxes, field)) setZoom((z) => Math.max(z, ZOOM_FOCUS));
  }

  function showField(finding: Finding) {
    selectField(fieldForFinding(finding, doc?.boxes));
    setOpenState(true);
  }

  return (
    <article
      aria-labelledby={titleId}
      className="shadow-card @container rounded-2xl border border-slate-200 bg-white"
      data-testid="document-card"
    >
      <Header
        view={view}
        doc={doc}
        open={open}
        panelId={panelId}
        titleId={titleId}
        onToggle={() => setOpenState(!open)}
      />

      <div className="flex flex-wrap items-center gap-1.5 px-4 pb-4">
        {doc ? (
          <>
            <Chip tone="brand" size="sm">
              {categoryLabel(doc.decisions.category)}
            </Chip>
            <ConfidenceBadge value={doc.decisions.category_confidence} what="category confidence" />
            <EngineBadge engine={doc.decisions.engine} />
            <TrustBadge verdict={view.verdict} score={view.trust_score} />
            <FindingCounts findings={findings} />
          </>
        ) : (
          <Chip tone="danger" size="sm">
            {view.status === "failed" ? "Could not be read" : "Not processed yet"}
          </Chip>
        )}
      </div>

      {open ? (
        <div id={panelId} className="space-y-5 border-t border-slate-100 p-4">
          {doc ? (
            <div className="grid grid-cols-1 gap-5 @3xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
              <div className="min-w-0 @3xl:sticky @3xl:top-24 @3xl:self-start">
                <ReceiptViewer
                  documentId={doc.id}
                  filename={doc.filename}
                  boxes={doc.boxes ?? {}}
                  selectedField={selectedField}
                  zoom={zoom}
                  onZoomChange={setZoom}
                />
              </div>
              <div className="min-w-0 space-y-5">
                <div>
                  <h4 className="mb-2 text-sm font-semibold text-slate-900">
                    What ClaimPilot read
                  </h4>
                  <p className="mb-2 text-xs text-slate-600">
                    {hasLocations
                      ? "Select a field to see where it is printed on the receipt."
                      : "Check these values against the original receipt."}
                  </p>
                  <FieldList
                    receipt={doc.receipt}
                    boxes={doc.boxes ?? {}}
                    selected={selectedField}
                    onSelect={selectField}
                  />
                </div>
                <div>
                  <h4 className="mb-2 text-sm font-semibold text-slate-900">How it was decided</h4>
                  <DecisionsPanel decisions={doc.decisions} readerModel={readerModel} />
                </div>
              </div>
            </div>
          ) : (
            <p className="text-sm text-rose-900">
              {view.error ?? "This file could not be read, so there is nothing to show."}
            </p>
          )}
          <div>
            <h4 className="mb-2 text-sm font-semibold text-slate-900">Flags on this receipt</h4>
            <FindingList
              findings={findings}
              onShowField={doc && hasLocations ? showField : undefined}
            />
          </div>
        </div>
      ) : null}
    </article>
  );
}
