import { ShieldAlert, TriangleAlert } from "lucide-react";

import type { Finding } from "@/lib/api/types";
import { cn } from "@/lib/cn";
import { plainFindingMessage, topFinding } from "@/lib/claims/summary";
import { SEVERITY_INFO } from "@/lib/labels";

/**
 * The one flag that best explains why a claim needs attention, in words, right on its card (so
 * a list says WHY, not just "1 high risk"). Nothing for a claim with no high or warning flags.
 * `inline` renders spans only, for use inside a button.
 */
export function TopFlag({
  findings,
  inline = false,
  className,
}: {
  findings: readonly Finding[] | null | undefined;
  inline?: boolean;
  className?: string;
}) {
  const top = topFinding(findings);
  if (!top) return null;
  const high = top.severity === "high";
  const Icon = high ? ShieldAlert : TriangleAlert;
  const Tag = inline ? "span" : "p";
  return (
    <Tag
      data-testid="top-flag"
      className={cn(
        "flex items-start gap-1.5 text-sm leading-snug font-normal",
        high ? "text-rose-900" : "text-amber-900",
        className,
      )}
    >
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <span className="line-clamp-3 min-w-0">
        <span className="sr-only">{SEVERITY_INFO[top.severity].label}: </span>
        {plainFindingMessage(top.message)}
      </span>
    </Tag>
  );
}
