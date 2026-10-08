"use client";

import { Info, X } from "lucide-react";
import { useEffect, useState, useSyncExternalStore } from "react";

import { dismissToast, getServerToasts, getToasts, subscribeToasts, type Toast } from "@/lib/toast";

/** How long a toast stays; it waits while the pointer or keyboard focus is on it. */
export const TOAST_VISIBLE_MS = 10_000;

function ToastItem({ toast }: { toast: Toast }) {
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    if (paused) return;
    const timer = window.setTimeout(() => dismissToast(toast.id), TOAST_VISIBLE_MS);
    return () => window.clearTimeout(timer);
  }, [paused, toast.id]);

  return (
    <div
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
      data-testid="toast"
      className="animate-fade-in pointer-events-auto flex max-w-md items-start gap-2.5 rounded-xl bg-slate-900 py-2.5 pr-2 pl-3.5 text-sm text-white shadow-lg ring-1 ring-white/10"
    >
      <Info className="mt-0.5 size-4 shrink-0 text-sky-300" aria-hidden="true" />
      <p className="flex-1 leading-relaxed">{toast.text}</p>
      <button
        type="button"
        onClick={() => dismissToast(toast.id)}
        aria-label="Dismiss notification"
        className="-my-0.5 inline-flex size-8 shrink-0 items-center justify-center rounded-md text-slate-300 hover:bg-white/10 hover:text-white"
      >
        <X className="size-4" aria-hidden="true" />
      </button>
    </div>
  );
}

/**
 * Short notifications, announced politely to screen readers. The live region is always in the
 * page (even when empty) because assistive technology only announces changes to a region it
 * already knows. Lives outside the app root so a toast stays reachable while a dialog is open.
 */
export function Toaster() {
  const toasts = useSyncExternalStore(subscribeToasts, getToasts, getServerToasts);
  return (
    <div
      role="status"
      aria-live="polite"
      className="pointer-events-none fixed inset-x-0 bottom-4 z-[60] flex flex-col items-center gap-2 px-4"
    >
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} />
      ))}
    </div>
  );
}
