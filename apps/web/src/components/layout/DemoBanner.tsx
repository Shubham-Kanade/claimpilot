"use client";

import { Info, X } from "lucide-react";
import { useSyncExternalStore } from "react";

import { useMeta } from "@/lib/hooks/queries";
import { README_URL } from "@/lib/config";
import {
  dismissBanner,
  isBannerDismissed,
  isBannerDismissedOnServer,
  subscribeBanner,
} from "@/lib/demo/notice";

/**
 * A slim notice, shown only when GET /v1/meta says this is the public demo: the sample receipts
 * are replayed from recordings, nothing is real data, and how to run it on your own receipts.
 * Dismissible for the rest of the browser session.
 */
export function DemoBanner() {
  const meta = useMeta();
  const dismissed = useSyncExternalStore(
    subscribeBanner,
    isBannerDismissed,
    isBannerDismissedOnServer,
  );

  if (!meta.data?.demo || dismissed) return null;

  return (
    // A labelled region: page content must sit inside a landmark (axe "region" rule).
    <section
      aria-label="Demo notice"
      className="bg-slate-900 text-slate-100"
      data-testid="demo-banner"
    >
      <div className="mx-auto flex max-w-6xl items-start gap-2.5 px-4 py-2 text-xs leading-relaxed sm:items-center sm:px-6 sm:text-sm lg:px-8">
        <Info className="mt-0.5 size-4 shrink-0 text-sky-300 sm:mt-0" aria-hidden="true" />
        <p className="flex-1">
          <strong className="font-semibold text-white">Demo:</strong> the sample receipts are
          replayed from recordings, nothing here is real data. To read your own receipts run
          ClaimPilot locally with your own API key.{" "}
          <a
            href={README_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="font-semibold text-white underline underline-offset-2 hover:text-sky-200"
          >
            See the README
          </a>
          .
        </p>
        <button
          type="button"
          onClick={dismissBanner}
          aria-label="Dismiss demo notice"
          className="-my-1 -mr-1.5 inline-flex size-8 shrink-0 items-center justify-center rounded-md text-slate-300 hover:bg-white/10 hover:text-white"
        >
          <X className="size-4" aria-hidden="true" />
        </button>
      </div>
    </section>
  );
}
