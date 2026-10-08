import type { ReactNode } from "react";

import { cn } from "@/lib/cn";
import type { Tone } from "@/lib/labels";

/** Tinted text on a tinted background; every pairing is >= 4.5:1 (WCAG AA). */
export const TONE_CLASSES: Record<Tone, string> = {
  neutral: "bg-slate-100 text-slate-700 ring-slate-200",
  brand: "bg-indigo-50 text-indigo-700 ring-indigo-200",
  accent: "bg-teal-50 text-teal-800 ring-teal-200",
  success: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  warn: "bg-amber-50 text-amber-900 ring-amber-300",
  danger: "bg-rose-50 text-rose-800 ring-rose-200",
  info: "bg-sky-50 text-sky-800 ring-sky-200",
};

export interface ChipProps {
  tone?: Tone;
  /** Decorative icon before the text. Meaning must always also be in the text. */
  icon?: ReactNode;
  size?: "sm" | "md";
  className?: string;
  title?: string;
  children: ReactNode;
}

/** A small status label: always icon + text, never colour alone. */
export function Chip({
  tone = "neutral",
  icon,
  size = "md",
  className,
  title,
  children,
}: ChipProps) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex max-w-full items-center gap-1 rounded-full font-medium ring-1 ring-inset",
        size === "sm" ? "px-2 py-0.5 text-xs" : "px-2.5 py-1 text-xs sm:text-sm",
        TONE_CLASSES[tone],
        className,
      )}
    >
      {icon ? (
        <span aria-hidden="true" className="inline-flex shrink-0 [&>svg]:size-3.5">
          {icon}
        </span>
      ) : null}
      <span className="truncate">{children}</span>
    </span>
  );
}
