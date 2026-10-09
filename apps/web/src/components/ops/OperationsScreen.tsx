"use client";

import { Activity, Ban, CircleAlert, FileQuestion, Search, X } from "lucide-react";
import { useId, useRef, useState, type FormEvent, type ReactNode } from "react";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Chip } from "@/components/ui/Chip";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/feedback";
import type { LlmOps, OpsCall, OpsFailure, OpsRoute } from "@/lib/api/types";
import { cn } from "@/lib/cn";
import {
  formatDateTime,
  formatLatency,
  formatRate,
  formatUSD,
  humanize,
  pluralize,
} from "@/lib/format";
import { useLlmOps } from "@/lib/hooks/queries";
import type { Tone } from "@/lib/labels";

/** The windows people can pick: hours as the API takes them (1..168). */
export const WINDOWS: readonly { hours: number; label: string }[] = [
  { hours: 1, label: "1 h" },
  { hours: 24, label: "24 h" },
  { hours: 168, label: "7 days" },
];

/** How many of the newest calls the API looks at before it says `sampled`. */
export const SAMPLE_LIMIT = "5,000";

const FAILURE_KIND: Record<OpsFailure["kind"], { label: string; tone: Tone; icon: ReactNode }> = {
  not_recorded: { label: "Not recorded", tone: "info", icon: <FileQuestion /> },
  budget: { label: "Budget used up", tone: "warn", icon: <Ban /> },
  error: { label: "Error", tone: "danger", icon: <CircleAlert /> },
};

/** "Recorded" calls are replays: their latency and cost are as recorded, never money spent now. */
function ModeChip({ mode }: { mode: string }) {
  const live = mode === "live";
  return (
    <Chip
      size="sm"
      tone={live ? "brand" : "neutral"}
      title={live ? "Called the model for real" : "Replayed from a recording: as recorded"}
    >
      {live ? "Live" : "Recorded"}
    </Chip>
  );
}

function Kpi({
  label,
  value,
  note,
  testId,
}: {
  label: string;
  value: ReactNode;
  note: ReactNode;
  testId: string;
}) {
  return (
    <div data-testid={testId} className="flex min-w-0 flex-col gap-1">
      <div className="text-sm font-medium text-slate-600">{label}</div>
      <div className="text-2xl font-semibold tracking-tight text-slate-900 tabular-nums sm:text-3xl">
        {value}
      </div>
      <p className="text-xs leading-relaxed text-slate-600">{note}</p>
    </div>
  );
}

export function KpiTiles({ ops }: { ops: LlmOps }) {
  const t = ops.totals;
  const livePct = t.calls ? Math.round((t.live_calls / t.calls) * 100) : 0;
  return (
    <Card>
      <div className="grid grid-cols-1 gap-x-8 gap-y-6 sm:grid-cols-2 lg:grid-cols-5">
        <Kpi
          testId="kpi-calls"
          label="AI calls"
          value={t.calls.toLocaleString("en-IN")}
          note={`${t.input_tokens.toLocaleString("en-IN")} tokens in, ${t.output_tokens.toLocaleString("en-IN")} out`}
        />
        <Kpi
          testId="kpi-live"
          label="Live vs recorded"
          value={`${livePct}% live`}
          note={`${t.live_calls.toLocaleString("en-IN")} live, ${t.recorded_calls.toLocaleString("en-IN")} recorded (replayed)`}
        />
        <Kpi
          testId="kpi-errors"
          label="Errors"
          value={t.errors.toLocaleString("en-IN")}
          note={`Error rate ${formatRate(t.error_rate)}. ${t.not_recorded} not recorded, ${t.budget_refusals} refused by the budget.`}
        />
        <Kpi
          testId="kpi-cost"
          label="Live cost (spent)"
          value={formatUSD(t.live_cost_usd)}
          note={`Recorded replays would have cost ${formatUSD(t.recorded_cost_usd)}: as recorded, not spent.`}
        />
        <Kpi
          testId="kpi-cache"
          label="Cache share"
          value={formatRate(t.cache_read_share)}
          note={`${t.cache_read_tokens.toLocaleString("en-IN")} input tokens read from the prompt cache.`}
        />
      </div>
    </Card>
  );
}

/** A table that cannot fit scrolls sideways in a region the keyboard can reach. */
function Scroller({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div
      tabIndex={0}
      role="group"
      aria-label={label}
      className="thin-scroll overflow-x-auto rounded-xl border border-slate-200 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-indigo-700"
    >
      {children}
    </div>
  );
}

const TH = "px-3 py-2 font-medium whitespace-nowrap";
const TD = "px-3 py-2 whitespace-nowrap tabular-nums";

export function RoutesTable({ routes }: { routes: readonly OpsRoute[] }) {
  return (
    <Scroller label="AI calls by route and model, scrolls sideways if needed">
      <table className="w-full min-w-[44rem] text-sm">
        <caption className="sr-only">
          AI calls per route and model. Live columns are real calls and real money; recorded columns
          are replays, as recorded.
        </caption>
        <thead className="bg-slate-50 text-left text-xs text-slate-600">
          <tr>
            <th scope="col" className={TH}>
              Route
            </th>
            <th scope="col" className={TH}>
              Model
            </th>
            <th scope="col" className={cn(TH, "text-right")}>
              Calls
            </th>
            <th scope="col" className={cn(TH, "text-right")}>
              Live
            </th>
            <th scope="col" className={cn(TH, "text-right italic")}>
              Recorded
            </th>
            <th scope="col" className={cn(TH, "text-right")}>
              Errors
            </th>
            <th scope="col" className={cn(TH, "text-right")}>
              p50
            </th>
            <th scope="col" className={cn(TH, "text-right")}>
              p95
            </th>
            <th scope="col" className={cn(TH, "text-right")}>
              Live cost
            </th>
            <th scope="col" className={cn(TH, "text-right italic")}>
              Recorded cost (as recorded)
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {routes.map((r) => (
            <tr key={`${r.route}|${r.model_key}`}>
              <th
                scope="row"
                className="px-3 py-2 text-left font-medium whitespace-nowrap text-slate-900"
              >
                {humanize(r.route)}
              </th>
              <td className={cn(TD, "font-mono text-xs text-slate-700")}>{r.model_key}</td>
              <td className={cn(TD, "text-right text-slate-900")}>{r.calls}</td>
              <td className={cn(TD, "text-right text-slate-900")}>{r.live_calls}</td>
              <td className={cn(TD, "text-right text-slate-600 italic")}>
                {r.calls - r.live_calls}
              </td>
              <td
                className={cn(
                  TD,
                  "text-right",
                  r.errors > 0 ? "font-semibold text-rose-800" : "text-slate-700",
                )}
              >
                {r.errors}
              </td>
              <td className={cn(TD, "text-right text-slate-700")}>{formatLatency(r.p50_ms)}</td>
              <td className={cn(TD, "text-right text-slate-700")}>{formatLatency(r.p95_ms)}</td>
              <td className={cn(TD, "text-right text-slate-900")}>{formatUSD(r.live_cost_usd)}</td>
              <td className={cn(TD, "text-right text-slate-600 italic")}>
                {formatUSD(r.recorded_cost_usd)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Scroller>
  );
}

export function FailureList({
  failures,
  onTrace,
}: {
  failures: readonly OpsFailure[];
  onTrace: (traceId: string) => void;
}) {
  if (failures.length === 0) {
    return (
      <p className="text-sm text-slate-600">
        No failures in this window for your session. Nothing went wrong.
      </p>
    );
  }
  return (
    <ul className="space-y-2.5" aria-label="Recent failures">
      {failures.map((f, index) => {
        const kind = FAILURE_KIND[f.kind] ?? FAILURE_KIND.error;
        return (
          <li
            key={`${f.at}-${f.document_id ?? ""}-${index}`}
            data-kind={f.kind}
            className="rounded-xl border border-slate-200 bg-white p-3.5 shadow-sm"
          >
            <div className="flex flex-wrap items-center gap-2">
              <Chip size="sm" tone={kind.tone} icon={kind.icon}>
                {kind.label}
              </Chip>
              <span className="text-xs text-slate-600">
                {humanize(f.route)} · <span className="font-mono">{f.model_key}</span> ·{" "}
                {formatDateTime(f.at)}
              </span>
            </div>
            <p className="mt-2 text-sm leading-relaxed text-slate-900">{f.message}</p>
            {f.trace_id ? (
              <button
                type="button"
                onClick={() => onTrace(f.trace_id as string)}
                className="mt-2 inline-flex min-h-9 items-center gap-1.5 text-sm font-semibold text-indigo-700 hover:underline"
              >
                <Search className="size-4" aria-hidden="true" />
                Show trace <span className="font-mono text-xs">{f.trace_id.slice(0, 12)}</span>
              </button>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

export function TraceCalls({ calls }: { calls: readonly OpsCall[] }) {
  return (
    <Scroller label="Calls of this trace, scrolls sideways if needed">
      <table className="w-full min-w-[40rem] text-sm">
        <caption className="sr-only">The AI calls of one trace, oldest first</caption>
        <thead className="bg-slate-50 text-left text-xs text-slate-600">
          <tr>
            <th scope="col" className={TH}>
              Time
            </th>
            <th scope="col" className={TH}>
              Route
            </th>
            <th scope="col" className={TH}>
              Model
            </th>
            <th scope="col" className={TH}>
              Mode
            </th>
            <th scope="col" className={cn(TH, "text-right")}>
              Latency
            </th>
            <th scope="col" className={cn(TH, "text-right")}>
              Cost
            </th>
            <th scope="col" className={TH}>
              Result
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {calls.map((c, index) => (
            <tr key={`${c.at}-${c.route}-${index}`}>
              <td className={TD}>{formatDateTime(c.at)}</td>
              <th scope="row" className="px-3 py-2 text-left font-medium whitespace-nowrap">
                {humanize(c.route)}
              </th>
              <td className={cn(TD, "font-mono text-xs")}>{c.model_key}</td>
              <td className="px-3 py-2 whitespace-nowrap">
                <ModeChip mode={c.mode} />
              </td>
              <td className={cn(TD, "text-right")}>
                {formatLatency(c.latency_ms)}
                {c.mode === "live" ? "" : " (as recorded)"}
              </td>
              <td className={cn(TD, "text-right")}>
                {formatUSD(c.cost_usd)}
                {c.mode === "live" ? "" : " (as recorded)"}
              </td>
              <td className="px-3 py-2 text-slate-800">
                {c.error ? <span className="font-medium text-rose-800">{c.error}</span> : "OK"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Scroller>
  );
}

function OpsSkeleton() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Loading AI operations">
      <Card>
        <div className="grid grid-cols-2 gap-6 lg:grid-cols-5">
          {Array.from({ length: 5 }, (_, i) => (
            <div key={i} className="space-y-2">
              <Skeleton className="h-4 w-24" />
              <Skeleton className="h-8 w-20" />
              <Skeleton className="h-3 w-full" />
            </div>
          ))}
        </div>
      </Card>
      <Skeleton className="h-48 w-full rounded-2xl" />
    </div>
  );
}

/** /operations: how the AI calls are going, system-wide, plus this session's failures and traces. */
export function OperationsScreen() {
  const [hours, setHours] = useState(24);
  const [draft, setDraft] = useState("");
  const [traceId, setTraceId] = useState("");
  const query = useLlmOps(hours, traceId || null);
  const traceHeading = useRef<HTMLHeadingElement>(null);
  const inputId = useId();
  const ops = query.data;

  function showTrace(id: string) {
    setDraft(id);
    setTraceId(id.trim());
    // bring the person to the result: keyboard users land on its heading
    requestAnimationFrame(() => {
      traceHeading.current?.focus();
      traceHeading.current?.scrollIntoView?.({ block: "start" });
    });
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    setTraceId(draft.trim());
  }

  function clearTrace() {
    setDraft("");
    setTraceId("");
  }

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 px-4 py-8 sm:px-6 sm:py-12 lg:px-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900 sm:text-3xl">
            AI operations
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-slate-600 sm:text-base">
            How the AI calls are going: system-wide, all visitors.
          </p>
        </div>
        <div role="group" aria-label="Time window" className="flex gap-1.5">
          {WINDOWS.map((w) => (
            <button
              key={w.hours}
              type="button"
              aria-pressed={hours === w.hours}
              onClick={() => setHours(w.hours)}
              className={cn(
                "inline-flex h-10 min-w-14 items-center justify-center rounded-full px-4 text-sm font-semibold ring-1 transition-colors ring-inset",
                hours === w.hours
                  ? "bg-indigo-600 text-white ring-indigo-600"
                  : "bg-white text-slate-700 ring-slate-300 hover:bg-slate-50",
              )}
            >
              {w.label}
            </button>
          ))}
        </div>
      </div>

      {query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : !ops ? (
        <OpsSkeleton />
      ) : (
        <>
          <div role="status" aria-live="polite" className="space-y-1 text-xs text-slate-600">
            <p>
              {ops.since ? `Counting since ${formatDateTime(ops.since)}.` : "No calls counted yet."}{" "}
              Recorded calls are replays: their latency and cost are as recorded, never money spent.
            </p>
            {ops.sampled ? (
              <p data-testid="sampled-notice" className="font-medium text-amber-900">
                Sampled: only the newest {SAMPLE_LIMIT} calls in this window are counted, so the
                totals are a lower bound.
              </p>
            ) : null}
          </div>

          {ops.totals.calls === 0 ? (
            <EmptyState
              icon={<Activity className="size-6" />}
              title="No AI calls in this window yet"
            >
              Upload some receipts, or pick a longer window.
            </EmptyState>
          ) : (
            <>
              <KpiTiles ops={ops} />
              <section aria-labelledby="routes-heading" className="space-y-3">
                <h2 id="routes-heading" className="text-lg font-semibold text-slate-900">
                  By route and model
                </h2>
                <RoutesTable routes={ops.routes} />
              </section>
            </>
          )}

          <section aria-labelledby="failures-heading" className="space-y-3">
            <h2 id="failures-heading" className="text-lg font-semibold text-slate-900">
              Recent failures
            </h2>
            <p className="text-xs text-slate-600">
              Only your own session&apos;s failures are listed, not other visitors&apos;.
            </p>
            <FailureList failures={ops.failures} onTrace={showTrace} />
          </section>

          <section aria-labelledby="trace-heading" className="space-y-3">
            <h2
              id="trace-heading"
              ref={traceHeading}
              tabIndex={-1}
              className="text-lg font-semibold text-slate-900 outline-none"
            >
              Trace
            </h2>
            <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
              <div className="min-w-0 flex-1 basis-64">
                <label htmlFor={inputId} className="text-sm font-medium text-slate-800">
                  Trace id
                </label>
                <input
                  id={inputId}
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  maxLength={64}
                  spellCheck={false}
                  autoComplete="off"
                  placeholder="Paste a trace id, or press Show trace on a failure"
                  className="mt-1 h-11 w-full rounded-xl border border-slate-300 bg-white px-3.5 font-mono text-sm"
                />
              </div>
              <Button type="submit" icon={<Search className="size-4" aria-hidden="true" />}>
                Show trace
              </Button>
              {traceId ? (
                <Button
                  type="button"
                  variant="secondary"
                  onClick={clearTrace}
                  icon={<X className="size-4" aria-hidden="true" />}
                >
                  Clear
                </Button>
              ) : null}
            </form>
            {traceId ? (
              ops.trace_calls.length > 0 ? (
                <>
                  <p role="status" className="text-sm text-slate-700">
                    {pluralize(ops.trace_calls.length, "call")} in trace{" "}
                    <span className="font-mono text-xs">{traceId}</span>.
                  </p>
                  <TraceCalls calls={ops.trace_calls} />
                </>
              ) : (
                <p role="status" className="text-sm text-slate-600">
                  No calls found for this trace in your session.
                </p>
              )
            ) : (
              <p className="text-sm text-slate-600">
                A trace is everything one upload asked the AI to do. Traces are yours only.
              </p>
            )}
          </section>
        </>
      )}
    </div>
  );
}
