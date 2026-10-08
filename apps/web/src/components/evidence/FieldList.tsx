import { CircleCheck, Crosshair, TriangleAlert } from "lucide-react";

import { Chip } from "@/components/ui/Chip";
import type { Box, ExtractedReceipt } from "@/lib/api/types";
import { cn } from "@/lib/cn";
import { boxFor, buildFieldRows } from "@/lib/claims/fields";
import { formatINR } from "@/lib/format";

/** The marker next to a field: "Low confidence" (icon + word) or a quiet "read clearly" tick. */
function ConfidenceMarker({ low, missing }: { low: boolean; missing: boolean }) {
  if (low) {
    return (
      <Chip tone="warn" size="sm" icon={<TriangleAlert />}>
        {missing ? "Not found" : "Low confidence"}
      </Chip>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs text-emerald-800">
      <CircleCheck className="size-4" aria-hidden="true" />
      <span className="sr-only">Read clearly</span>
    </span>
  );
}

const LINE_ITEMS_HEADING =
  "flex w-full flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-3 py-2.5 text-left";

const ROW_LAYOUT =
  // Phones: name above value, markers on the right. Wider: name | value | markers.
  "grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-0.5 px-3 py-2.5 text-left first:rounded-t-xl last:rounded-b-xl sm:grid-cols-[7rem_minmax(0,1fr)_auto]";

/**
 * What ClaimPilot read from the receipt, one row per field. When the API says where each value is
 * printed (`boxes`) every row is a button: selecting it highlights that spot on the original.
 * Without any locations (recorded replays have none) the rows are plain text: a button that does
 * nothing would only look broken, and the receipt can still be zoomed and read next to the values.
 */
export function FieldList({
  receipt,
  boxes,
  selected,
  onSelect,
}: {
  receipt: ExtractedReceipt;
  boxes: Readonly<Record<string, Box>>;
  selected: string | null;
  onSelect: (field: string | null) => void;
}) {
  const rows = buildFieldRows(receipt);
  const interactive = Object.keys(boxes).length > 0;
  const lineItems = receipt.line_items ?? [];
  const lineItemsLow = (receipt.low_confidence_fields ?? []).includes("line_items");
  const lineItemsHeading = (
    <>
      <span className="text-sm font-semibold text-slate-900">Line items ({lineItems.length})</span>
      <span className="flex items-center gap-2">
        {boxFor(boxes, "line_items") ? (
          <Crosshair
            className="size-4 text-indigo-600"
            aria-label="Location on the receipt available"
          />
        ) : null}
        <ConfidenceMarker low={lineItemsLow} missing={false} />
      </span>
    </>
  );

  return (
    <div className="space-y-3">
      <ul
        aria-label="Extracted fields"
        className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white"
      >
        {rows.map((row) => {
          const isSelected = selected === row.key;
          const located = boxFor(boxes, row.key) !== null;
          const cells = (
            <>
              <span className="text-xs font-medium text-slate-600">{row.label}</span>
              <span
                className={cn(
                  "col-start-1 row-start-2 min-w-0 text-sm font-semibold break-words text-slate-900 sm:col-start-2 sm:row-start-1",
                  row.mono && "font-mono text-[13px]",
                  row.missing && "font-medium text-amber-900",
                )}
              >
                {row.value}
              </span>
              <span className="col-start-2 row-span-2 row-start-1 flex items-center gap-2 sm:col-start-3 sm:row-span-1">
                {located ? (
                  <Crosshair
                    className="size-4 text-indigo-600"
                    aria-label="Location on the receipt available"
                  />
                ) : null}
                <ConfidenceMarker low={row.low} missing={row.missing} />
              </span>
            </>
          );
          return (
            <li key={row.key}>
              {interactive ? (
                <button
                  type="button"
                  aria-pressed={isSelected}
                  onClick={() => onSelect(isSelected ? null : row.key)}
                  title={located ? "Show where this is printed" : undefined}
                  className={cn(
                    ROW_LAYOUT,
                    "transition-colors hover:bg-slate-50",
                    isSelected &&
                      "bg-indigo-50 ring-2 ring-indigo-500 ring-inset hover:bg-indigo-50",
                  )}
                >
                  {cells}
                </button>
              ) : (
                <div className={ROW_LAYOUT}>{cells}</div>
              )}
            </li>
          );
        })}
      </ul>

      {lineItems.length > 0 ? (
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
          {interactive ? (
            <button
              type="button"
              aria-pressed={selected === "line_items"}
              onClick={() => onSelect(selected === "line_items" ? null : "line_items")}
              className={cn(
                LINE_ITEMS_HEADING,
                "hover:bg-slate-50",
                selected === "line_items" && "bg-indigo-50 ring-2 ring-indigo-500 ring-inset",
              )}
            >
              {lineItemsHeading}
            </button>
          ) : (
            <div className={LINE_ITEMS_HEADING}>{lineItemsHeading}</div>
          )}
          {/* A table that cannot fit scrolls sideways, and then must be reachable by keyboard. */}
          <div
            tabIndex={0}
            role="group"
            aria-label="Line items, scrolls sideways if needed"
            className="thin-scroll overflow-x-auto focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-indigo-700"
          >
            <table className="w-full text-sm">
              <caption className="sr-only">Line items read from the receipt</caption>
              <thead>
                <tr className="text-left text-xs text-slate-600">
                  <th scope="col" className="px-3 py-2 font-medium">
                    Item
                  </th>
                  <th scope="col" className="px-2 py-2 text-right font-medium">
                    Qty
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    Amount
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {lineItems.map((item, index) => (
                  <tr key={`${item.description}-${index}`}>
                    <td className="px-3 py-2 break-words text-slate-900">{item.description}</td>
                    <td className="px-2 py-2 text-right text-slate-700 tabular-nums">
                      {item.quantity ?? "—"}
                    </td>
                    <td className="px-3 py-2 text-right font-medium text-slate-900 tabular-nums">
                      {formatINR(item.amount)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  );
}
