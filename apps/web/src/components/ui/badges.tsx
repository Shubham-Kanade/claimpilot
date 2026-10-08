import {
  BadgeCheck,
  Briefcase,
  CalendarDays,
  CircleCheck,
  CircleHelp,
  CircleX,
  Clock,
  FileText,
  Info,
  MessageCircleQuestion,
  Plane,
  Repeat,
  Send,
  ShieldAlert,
  ShieldCheck,
  ShieldX,
  Sparkles,
  TriangleAlert,
  Zap,
} from "lucide-react";
import type { ReactNode } from "react";

import type { ClaimMode, ClaimStatus, Severity } from "@/lib/api/types";
import { confidenceLabel, formatPercent, humanize } from "@/lib/format";
import {
  engineInfo,
  MODE_LABELS,
  routeInfo,
  SEVERITY_INFO,
  statusInfo,
  verdictInfo,
  type Tone,
} from "@/lib/labels";

import { Chip } from "./Chip";
import { ScoreMeter } from "./ProgressBar";

const STATUS_ICONS: Record<ClaimStatus, ReactNode> = {
  draft: <FileText />,
  needs_info: <MessageCircleQuestion />,
  ready: <CircleCheck />,
  submitted: <Send />,
  approved: <BadgeCheck />,
  rejected: <CircleX />,
};

export function StatusChip({ status, size }: { status: string; size?: "sm" | "md" }) {
  const info = statusInfo(status);
  return (
    <Chip
      tone={info.tone}
      icon={STATUS_ICONS[status as ClaimStatus] ?? <FileText />}
      size={size}
      title={info.hint}
    >
      {info.label}
    </Chip>
  );
}

export function RouteChip({
  route,
  size,
}: {
  route: string | null | undefined;
  size?: "sm" | "md";
}) {
  const info = routeInfo(route);
  return (
    <Chip
      tone={info.tone}
      icon={route === "auto_approve" ? <Zap /> : <Clock />}
      size={size}
      title={info.hint}
    >
      {info.label}
    </Chip>
  );
}

const MODE_ICONS: Record<ClaimMode, ReactNode> = {
  trip: <Plane />,
  period: <Repeat />,
  event: <CalendarDays />,
  allowance: <Briefcase />,
};

export function ModeBadge({ mode, size }: { mode: ClaimMode; size?: "sm" | "md" }) {
  return (
    <Chip tone="brand" icon={MODE_ICONS[mode]} size={size}>
      {MODE_LABELS[mode]}
    </Chip>
  );
}

const SEVERITY_ICONS: Record<Severity, ReactNode> = {
  high: <ShieldAlert />,
  warn: <TriangleAlert />,
  info: <Info />,
};

/** Severity as icon + word (+ optional count): never colour alone. */
export function SeverityBadge({
  severity,
  count,
  size,
}: {
  severity: Severity;
  count?: number;
  size?: "sm" | "md";
}) {
  const info = SEVERITY_INFO[severity];
  return (
    <Chip tone={info.tone} icon={SEVERITY_ICONS[severity]} size={size}>
      {count === undefined ? info.label : `${count} ${info.label.toLowerCase()}`}
    </Chip>
  );
}

/** Per-field / per-decision confidence: level word + percentage, with a matching icon. */
export function ConfidenceBadge({
  value,
  what = "confidence",
  size = "sm",
}: {
  value: number;
  what?: string;
  size?: "sm" | "md";
}) {
  const level = confidenceLabel(value);
  const tone: Tone = level === "high" ? "success" : level === "medium" ? "warn" : "danger";
  const icon =
    level === "high" ? <CircleCheck /> : level === "medium" ? <CircleHelp /> : <TriangleAlert />;
  return (
    <Chip tone={tone} icon={icon} size={size} title={`${what}: ${formatPercent(value)}`}>
      {`${humanize(level)} ${formatPercent(value)}`}
      <span className="sr-only"> {what}</span>
    </Chip>
  );
}

/** Which engine decided: System One (Jev), the language model (System Two), or both. */
export function EngineBadge({ engine, size = "sm" }: { engine: string; size?: "sm" | "md" }) {
  const info = engineInfo(engine);
  return (
    <Chip
      tone={info.system === "one" ? "accent" : info.system === "two" ? "brand" : "neutral"}
      icon={info.system === "one" ? <Zap /> : <Sparkles />}
      size={size}
      title={info.detail}
    >
      {info.label}
      <span className="sr-only"> ({info.detail})</span>
    </Chip>
  );
}

const VERDICT_ICONS: Record<string, ReactNode> = {
  clean: <ShieldCheck />,
  review: <ShieldAlert />,
  block: <ShieldX />,
};

/** Trust verdict (+ score): "Looks genuine · 100". */
export function TrustBadge({
  verdict,
  score,
  size = "sm",
  showMeter = false,
}: {
  verdict: string | null | undefined;
  score?: number | null;
  size?: "sm" | "md";
  showMeter?: boolean;
}) {
  const info = verdictInfo(verdict);
  return (
    <span className="inline-flex items-center gap-2">
      <Chip
        tone={info.tone}
        icon={VERDICT_ICONS[verdict ?? ""] ?? <ShieldCheck />}
        size={size}
        title={info.hint}
      >
        {info.label}
        {score !== null && score !== undefined ? (
          <>
            {" "}
            <span className="tabular-nums">· {score}</span>
            <span className="sr-only"> out of 100 trust score</span>
          </>
        ) : null}
      </Chip>
      {showMeter && score !== null && score !== undefined ? <ScoreMeter score={score} /> : null}
    </span>
  );
}
