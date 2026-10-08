"use client";

import { ExternalLink, FileText, Maximize2, ZoomIn, ZoomOut } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Skeleton } from "@/components/ui/feedback";
import { InlineError } from "@/components/ui/feedback";
import type { Box } from "@/lib/api/types";
import { boxFor, fieldLabel } from "@/lib/claims/fields";
import { useDocumentFile } from "@/lib/hooks/queries";
import { useObjectUrl } from "@/lib/hooks/useObjectUrl";

const ZOOM_STEP = 0.5;
const ZOOM_MIN = 1;
const ZOOM_MAX = 4;
/** The zoom a phone jumps to when a field is selected, so its value is legible. */
export const ZOOM_FOCUS = 2;

/**
 * Whether this browser can show a PDF inside the page. Chrome for Android cannot (and may
 * download the file instead), and says so with `navigator.pdfViewerEnabled === false`; where the
 * flag does not exist we try the embed, which carries its own "open the PDF" fallback.
 */
export function canEmbedPdf(): boolean {
  return (navigator as Navigator & { pdfViewerEnabled?: boolean }).pdfViewerEnabled !== false;
}

/** Where the highlight sits, as CSS percentages of the page (boxes are normalised 0-1). */
const pct = (fraction: number) => `${Number((fraction * 100).toFixed(3))}%`;

export function overlayStyle(box: Box) {
  return { left: pct(box.x), top: pct(box.y), width: pct(box.w), height: pct(box.h) };
}

/** What the caption under the viewer says about the current selection. */
export function viewerCaption(options: {
  selectedField: string | null;
  located: boolean;
  anyLocations: boolean;
  isPdf: boolean;
}): string {
  const { selectedField, located, anyLocations, isPdf } = options;
  if (isPdf) {
    return "PDF preview. Field highlights are shown on image receipts; open the PDF to check values against the original.";
  }
  if (selectedField && located) return `Highlighted on the receipt: ${fieldLabel(selectedField)}.`;
  if (selectedField) {
    return `${fieldLabel(selectedField)} is selected, but its position on the receipt isn't available. Zoom in to check it against the original.`;
  }
  return anyLocations
    ? "Select a field to see where it is printed."
    : "Field locations aren't available for this receipt. Zoom in to check each value against the original.";
}

/**
 * The ORIGINAL uploaded file (fetched as a Blob because the file endpoint needs the X-Persona
 * header, so an <img src> cannot point at it), zoomable, with a click-to-verify highlight
 * drawn from `boxes[field]`. Images get the overlay; PDFs use the browser's own viewer.
 * A missing or malformed box never breaks anything: the field is simply not highlighted.
 */
export function ReceiptViewer({
  documentId,
  filename,
  boxes,
  selectedField,
  zoom: zoomProp,
  onZoomChange,
}: {
  documentId: string;
  filename: string;
  boxes: Readonly<Record<string, Box>>;
  selectedField: string | null;
  /** Controlled zoom (1 = fit to width). Leave out and the viewer keeps its own. */
  zoom?: number;
  onZoomChange?: (zoom: number) => void;
}) {
  const file = useDocumentFile(documentId, true);
  const url = useObjectUrl(file.data?.blob);
  const [ownZoom, setOwnZoom] = useState(ZOOM_MIN);
  const zoom = zoomProp ?? ownZoom;
  const setZoom = (next: (current: number) => number) => {
    const value = next(zoom);
    if (zoomProp === undefined) setOwnZoom(value);
    onZoomChange?.(value);
  };
  const containerRef = useRef<HTMLDivElement>(null);
  const overlayRef = useRef<HTMLDivElement>(null);

  const contentType = file.data?.contentType ?? "";
  const isPdf = contentType.includes("pdf");
  const box = !isPdf ? boxFor(boxes, selectedField) : null;
  const usableBox = box && (box.page ?? 0) === 0 ? box : null;

  // Bring the highlighted region to the middle of the viewer (not the whole page).
  useEffect(() => {
    const container = containerRef.current;
    const overlay = overlayRef.current;
    if (!container || !overlay || typeof container.scrollTo !== "function") return;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    container.scrollTo({
      left: overlay.offsetLeft - container.clientWidth / 2 + overlay.offsetWidth / 2,
      top: overlay.offsetTop - container.clientHeight / 2 + overlay.offsetHeight / 2,
      behavior: reduce ? "auto" : "smooth",
    });
  }, [usableBox, zoom, url]); // `url`: the overlay only exists once the image has loaded

  if (file.isPending) {
    return <Skeleton className="h-64 w-full rounded-xl sm:h-80" />;
  }
  if (file.isError || !url) {
    return file.isError ? (
      <InlineError error={file.error} />
    ) : (
      <Skeleton className="h-64 w-full rounded-xl sm:h-80" />
    );
  }

  const anyLocations = Object.keys(boxes).length > 0;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1" role="group" aria-label="Zoom">
          {!isPdf ? (
            <>
              <button
                type="button"
                onClick={() => setZoom((z) => Math.max(ZOOM_MIN, z - ZOOM_STEP))}
                disabled={zoom <= ZOOM_MIN}
                aria-label="Zoom out"
                className="inline-flex size-9 items-center justify-center rounded-lg text-slate-700 ring-1 ring-slate-300 ring-inset hover:bg-slate-50 disabled:opacity-40"
              >
                <ZoomOut className="size-4" aria-hidden="true" />
              </button>
              <span
                className="w-12 text-center text-xs font-medium text-slate-700 tabular-nums"
                aria-live="polite"
              >
                {Math.round(zoom * 100)}%
              </span>
              <button
                type="button"
                onClick={() => setZoom((z) => Math.min(ZOOM_MAX, z + ZOOM_STEP))}
                disabled={zoom >= ZOOM_MAX}
                aria-label="Zoom in"
                className="inline-flex size-9 items-center justify-center rounded-lg text-slate-700 ring-1 ring-slate-300 ring-inset hover:bg-slate-50 disabled:opacity-40"
              >
                <ZoomIn className="size-4" aria-hidden="true" />
              </button>
              <button
                type="button"
                onClick={() => setZoom(() => ZOOM_MIN)}
                disabled={zoom === ZOOM_MIN}
                aria-label="Fit to width"
                className="inline-flex size-9 items-center justify-center rounded-lg text-slate-700 ring-1 ring-slate-300 ring-inset hover:bg-slate-50 disabled:opacity-40"
              >
                <Maximize2 className="size-4" aria-hidden="true" />
              </button>
            </>
          ) : null}
        </div>
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 text-sm font-semibold text-indigo-700 hover:underline"
        >
          <ExternalLink className="size-4" aria-hidden="true" />
          Open original
        </a>
      </div>

      {isPdf && !canEmbedPdf() ? (
        <div
          role="group"
          aria-label={`Original receipt: ${filename}`}
          className="flex flex-col items-start gap-3 rounded-xl border border-slate-200 bg-slate-50 p-4"
        >
          <p className="flex items-start gap-2 text-sm text-slate-700">
            <FileText className="mt-0.5 size-4 shrink-0 text-slate-500" aria-hidden="true" />
            This receipt is a PDF, and this browser cannot show a PDF inside the page.
          </p>
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex h-11 items-center gap-2 rounded-xl bg-indigo-600 px-4 text-sm font-semibold text-white hover:bg-indigo-700"
          >
            <ExternalLink className="size-4" aria-hidden="true" />
            Open the PDF
          </a>
        </div>
      ) : isPdf ? (
        <object
          data={url}
          type="application/pdf"
          aria-label={`Original receipt: ${filename}`}
          className="h-[28rem] w-full rounded-xl border border-slate-200 bg-slate-100"
        >
          <p className="p-4 text-sm text-slate-700">
            Your browser can&apos;t show PDFs inline.{" "}
            <a
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="font-semibold text-indigo-700 underline"
            >
              Open the PDF
            </a>
            .
          </p>
        </object>
      ) : (
        <div
          ref={containerRef}
          tabIndex={0}
          role="region"
          aria-label={`Original receipt: ${filename}`}
          className="thin-scroll max-h-[30rem] overflow-auto rounded-xl border border-slate-200 bg-slate-100 sm:max-h-[40rem]"
        >
          <div className="relative" style={{ width: `${zoom * 100}%` }}>
            {/* eslint-disable-next-line @next/next/no-img-element -- blob URL of a private upload */}
            <img
              src={url}
              alt={`Original receipt: ${filename}`}
              loading="lazy"
              draggable={false}
              className="block h-auto w-full select-none"
            />
            {usableBox ? (
              <div
                ref={overlayRef}
                data-testid="verify-highlight"
                aria-hidden="true"
                style={overlayStyle(usableBox)}
                className="motion-safe:animate-ring pointer-events-none absolute rounded-sm border-2 border-indigo-600 bg-indigo-500/15 shadow-[0_0_0_2px_rgba(255,255,255,0.8)]"
              />
            ) : null}
          </div>
        </div>
      )}

      <p role="status" aria-live="polite" className="text-xs leading-relaxed text-slate-600">
        {viewerCaption({
          selectedField,
          located: usableBox !== null,
          anyLocations,
          isPdf,
        })}
      </p>
    </div>
  );
}
