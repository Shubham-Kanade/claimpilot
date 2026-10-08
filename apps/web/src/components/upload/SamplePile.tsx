import { ChevronRight } from "lucide-react";

import { SAMPLE_RECEIPTS } from "@/lib/upload/samples";

/**
 * "What is in the sample pile?": the 15 sample files in upload order, so nothing about the demo
 * is hidden. A native disclosure (details/summary): keyboard and screen-reader friendly for free.
 */
export function SamplePile() {
  return (
    <details
      data-testid="sample-pile"
      className="group mt-4 max-w-2xl rounded-xl border border-slate-200 bg-white/70 text-sm"
    >
      <summary className="flex min-h-11 cursor-pointer items-center gap-2 rounded-xl px-3.5 py-2 font-medium text-indigo-800 marker:content-none hover:bg-white [&::-webkit-details-marker]:hidden">
        <ChevronRight
          className="size-4 shrink-0 transition-transform group-open:rotate-90 motion-reduce:transition-none"
          aria-hidden="true"
        />
        What is in the sample pile?
      </summary>
      <div className="space-y-3 border-t border-slate-100 px-3.5 py-3">
        <p className="leading-relaxed text-slate-600">
          One week of Asha Menon&apos;s expenses, uploaded in this order. Eleven are honest; four
          are traps ClaimPilot should catch.
        </p>
        <ol className="list-decimal space-y-1 pl-5 text-slate-700 marker:text-slate-500">
          {SAMPLE_RECEIPTS.map((sample) => (
            <li key={sample.file} className="pl-1">
              {sample.label}
            </li>
          ))}
        </ol>
      </div>
    </details>
  );
}
