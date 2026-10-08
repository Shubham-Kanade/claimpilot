"use client";

import { AlertTriangle, Inbox, Loader2, UserRound, WifiOff } from "lucide-react";
import type { ReactNode } from "react";

import { friendlyError, type ErrorKind } from "@/lib/api/problems";
import { cn } from "@/lib/cn";

import { Button } from "./Button";
import { Card } from "./Card";

export function Spinner({ className, label }: { className?: string; label?: string }) {
  return (
    <span role="status" className="inline-flex items-center">
      <Loader2
        className={cn("size-5 animate-spin text-indigo-600 motion-reduce:animate-none", className)}
        aria-hidden="true"
      />
      <span className="sr-only">{label ?? "Loading"}</span>
    </span>
  );
}

/** A grey placeholder with the final size of the thing it stands for (no layout shift). */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden="true" className={cn("skeleton", className)} />;
}

export function EmptyState({
  icon,
  title,
  children,
  action,
  className,
  as: Heading = "h2",
}: {
  icon?: ReactNode;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
  /** `h1` when this state IS the page (every page needs exactly one level-one heading). */
  as?: "h1" | "h2";
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-2xl border border-dashed border-slate-300 bg-white/60 px-6 py-12 text-center",
        className,
      )}
    >
      <div
        aria-hidden="true"
        className="mb-4 flex size-12 items-center justify-center rounded-full bg-indigo-50 text-indigo-600"
      >
        {icon ?? <Inbox className="size-6" />}
      </div>
      <Heading className="text-lg font-semibold text-slate-900">{title}</Heading>
      {children ? <p className="mt-1 max-w-md text-sm text-slate-600">{children}</p> : null}
      {action ? <div className="mt-5 flex flex-wrap justify-center gap-3">{action}</div> : null}
    </div>
  );
}

const ERROR_ICONS: Record<ErrorKind, ReactNode> = {
  network: <WifiOff className="size-6" />,
  auth: <UserRound className="size-6" />,
  forbidden: <UserRound className="size-6" />,
  "not-found": <Inbox className="size-6" />,
  conflict: <AlertTriangle className="size-6" />,
  input: <AlertTriangle className="size-6" />,
  server: <AlertTriangle className="size-6" />,
};

/** Friendly error panel for a failed request, with the right next step. */
export function ErrorState({
  error,
  onRetry,
  actions,
  className,
  as: Heading = "h2",
}: {
  error: unknown;
  onRetry?: () => void;
  /** Extra actions, e.g. a link back to the claims list. */
  actions?: ReactNode;
  className?: string;
  /** `h1` when this error replaces the whole page. */
  as?: "h1" | "h2";
}) {
  const friendly = friendlyError(error);
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center rounded-2xl border border-rose-200 bg-rose-50 px-6 py-10 text-center",
        className,
      )}
    >
      <div
        aria-hidden="true"
        className="mb-3 flex size-12 items-center justify-center rounded-full bg-white text-rose-700"
      >
        {ERROR_ICONS[friendly.kind]}
      </div>
      <Heading className="text-lg font-semibold text-rose-900">{friendly.title}</Heading>
      <p className="mt-1 max-w-md text-sm text-rose-900/80">{friendly.message}</p>
      {(onRetry && friendly.action === "retry") || actions ? (
        <div className="mt-5 flex flex-wrap justify-center gap-3">
          {onRetry && friendly.action === "retry" ? (
            <Button variant="secondary" onClick={onRetry}>
              Try again
            </Button>
          ) : null}
          {actions}
        </div>
      ) : null}
    </div>
  );
}

/**
 * Inline (non-blocking) error for forms and panels: pass the caught `error` (mapped to friendly
 * copy) or, for errors that never came from the API, a ready-made `message`.
 */
export function InlineError({
  error,
  message,
  className,
}: {
  error?: unknown;
  message?: string;
  className?: string;
}) {
  const friendly = message ? null : friendlyError(error);
  return (
    <p
      role="alert"
      className={cn(
        "flex items-start gap-2 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-900 ring-1 ring-rose-200 ring-inset",
        className,
      )}
    >
      <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <span>
        {friendly ? (
          <>
            <strong className="font-semibold">{friendly.title}.</strong> {friendly.message}
          </>
        ) : (
          message
        )}
      </span>
    </p>
  );
}

export function PageSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-4" aria-hidden="true">
      <Skeleton className="h-8 w-56" />
      <Skeleton className="h-4 w-80 max-w-full" />
      <div className="grid grid-cols-1 gap-4 pt-2 sm:grid-cols-2">
        {Array.from({ length: rows }, (_, i) => (
          <Card key={i} className="space-y-3">
            <Skeleton className="h-5 w-3/4" />
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-8 w-1/3" />
          </Card>
        ))}
      </div>
    </div>
  );
}
