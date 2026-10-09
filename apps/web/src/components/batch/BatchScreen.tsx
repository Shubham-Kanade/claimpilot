"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ArrowRight, CircleCheck, Clock3, Coins, Wifi, WifiOff } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useEffect, useState } from "react";

import { Button, buttonClasses } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ErrorState, PageSkeleton, Skeleton } from "@/components/ui/feedback";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { batchProgress, type BatchState } from "@/lib/batch/batchReducer";
import { useBatchStream, useNow } from "@/lib/batch/useBatchStream";
import { costProfile } from "@/lib/labels";
import { formatClock, formatUSD, parseApiTimestamp, pluralize } from "@/lib/format";
import { qk, useMeta } from "@/lib/hooks/queries";
import { usePersona } from "@/lib/persona/PersonaProvider";

import { DocumentProgressCard } from "./DocumentProgressCard";

/** Seconds before a batch that finished while you watched opens its claims by itself. */
export const AUTO_OPEN_SECONDS = 5;

/** A batch is never running for an hour: such a figure means the two clocks disagree. */
const MAX_RUNNING_SECONDS = 3600;

/**
 * How long the batch took. Finished: the server's own start-to-finish time (no clock skew). Still
 * running: now minus the server's start time, shown only while it is plausible.
 */
export function elapsedSeconds(state: BatchState, now: number | null): number | null {
  const start = parseApiTimestamp(state.createdAt);
  if (Number.isNaN(start)) return null;
  const finished = parseApiTimestamp(state.finishedAt);
  if (!Number.isNaN(finished)) return Math.max(0, (finished - start) / 1000);
  if (now === null) return null;
  const running = Math.max(0, (now - start) / 1000);
  return running > MAX_RUNNING_SECONDS ? null : running;
}

function headline(state: BatchState): { title: string; sub: string } {
  const { total, finished, read, failed } = batchProgress(state);
  if (state.phase === "done") {
    const claims = state.summary?.claims ?? state.claimIds.length;
    return {
      title: claims === 1 ? "1 claim ready" : `${claims} claims ready`,
      sub:
        failed > 0
          ? `${pluralize(total - failed, "receipt")} processed, ${failed} could not be read.`
          : `All ${pluralize(total, "receipt")} read, checked and grouped.`,
    };
  }
  if (state.phase === "failed") {
    return { title: "Processing stopped", sub: state.error ?? "Something went wrong." };
  }
  return {
    title: "Reading your receipts",
    sub:
      total === 0
        ? "Getting started…"
        : `${read} of ${total} read · ${finished} of ${total} checked`,
  };
}

function BatchLive({ batchId, autoOpenSeconds }: { batchId: string; autoOpenSeconds: number }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { personaId } = usePersona();
  const { state, connection, error, sawLive, retry } = useBatchStream(batchId);
  const meta = useMeta().data;
  const demo = meta?.demo ?? false;
  // Replayed answers carry the cost they had when they were recorded: it is not spent now.
  const profile = costProfile(meta);
  const running = state.phase !== "done" && state.phase !== "failed" && connection !== "error";
  const now = useNow(running);
  const progress = batchProgress(state);
  const { title, sub } = headline(state);
  const elapsed = elapsedSeconds(state, now);

  // A batch that finishes while you watch opens its claims after a short countdown (cancellable).
  const [stay, setStay] = useState(false);
  const [ticks, setTicks] = useState(0);
  const destination = state.claimIds.length === 1 ? `/claims/${state.claimIds[0]}` : "/claims";
  const counting = state.phase === "done" && sawLive && !stay;
  const countdown = counting ? Math.max(0, autoOpenSeconds - ticks) : null;

  // The claims and the impact numbers changed: refresh whatever is cached.
  useEffect(() => {
    if (state.phase !== "done" || !personaId) return;
    void queryClient.invalidateQueries({ queryKey: qk.claims(personaId) });
    void queryClient.invalidateQueries({ queryKey: qk.stats(personaId) });
  }, [state.phase, personaId, queryClient]);

  useEffect(() => {
    if (!counting) return;
    const timer = setInterval(() => setTicks((t) => t + 1), 1000);
    return () => clearInterval(timer);
  }, [counting]);

  useEffect(() => {
    if (countdown === 0) router.push(destination);
  }, [countdown, destination, router]);

  if (error) {
    return (
      <div className="mx-auto w-full max-w-3xl px-4 py-12">
        <ErrorState
          as="h1"
          error={error}
          onRetry={retry}
          actions={
            <Link href="/" className={buttonClasses("secondary")}>
              Back to upload
            </Link>
          }
        />
      </div>
    );
  }

  const liveMessage =
    state.phase === "done"
      ? title
      : state.phase === "failed"
        ? `Processing stopped. ${sub}`
        : `${progress.finished} of ${progress.total} receipts checked`;

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 px-4 py-8 sm:px-6 sm:py-12 lg:px-8">
      <p role="status" aria-live="polite" className="sr-only">
        {liveMessage}
      </p>

      <Card className="space-y-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="flex items-center gap-2.5 text-2xl font-semibold tracking-tight text-slate-900 sm:text-3xl">
              {state.phase === "done" ? (
                <CircleCheck className="size-7 text-emerald-600" aria-hidden="true" />
              ) : null}
              {title}
            </h1>
            <p className="mt-1 text-sm text-slate-600 sm:text-base">{sub}</p>
          </div>
          <ConnectionNote connection={connection} />
        </div>

        <ProgressBar
          percent={progress.percent}
          label="Overall progress"
          tone={state.phase === "failed" ? "danger" : state.phase === "done" ? "success" : "brand"}
        />

        <dl className="flex flex-wrap gap-x-8 gap-y-3 text-sm">
          <div className="flex items-center gap-2">
            <Clock3 className="size-4 text-slate-500" aria-hidden="true" />
            <dt className="text-slate-600">Elapsed</dt>
            <dd className="font-semibold text-slate-900 tabular-nums">
              {elapsed === null ? "—" : formatClock(elapsed)}
            </dd>
          </div>
          <div className="flex items-center gap-2">
            <Coins className="size-4 text-slate-500" aria-hidden="true" />
            <dt className="text-slate-600">
              {profile === "recorded"
                ? "LLM cost (recorded)"
                : profile === "recorded-and-live"
                  ? "LLM cost (recorded and live)"
                  : "LLM cost so far"}
            </dt>
            <dd className="font-semibold text-slate-900 tabular-nums">
              {formatUSD(state.costUsd)}
            </dd>
          </div>
          <div className="flex items-center gap-2">
            <dt className="text-slate-600">Receipts</dt>
            <dd className="font-semibold text-slate-900 tabular-nums">{progress.total || "—"}</dd>
          </div>
        </dl>

        {state.phase === "done" ? (
          <div className="flex flex-col gap-3 rounded-xl bg-emerald-50 p-4 ring-1 ring-emerald-200 ring-inset sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm text-emerald-950">
              {countdown !== null && countdown > 0
                ? `Opening your claims in ${countdown}s…`
                : "Your claims are ready to review. Nothing is submitted until you confirm."}
            </p>
            <div className="flex flex-wrap gap-2">
              {countdown !== null && countdown > 0 ? (
                <Button variant="secondary" onClick={() => setStay(true)}>
                  Stay here
                </Button>
              ) : null}
              <Link href={destination} className={buttonClasses("primary")}>
                {state.claimIds.length === 1 ? "Review the claim" : "Review claims"}
                <ArrowRight className="size-4" aria-hidden="true" />
              </Link>
            </div>
          </div>
        ) : null}

        {state.phase === "failed" ? (
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={retry}>
              Check again
            </Button>
            <Link href="/" className={buttonClasses("primary")}>
              Back to upload
            </Link>
          </div>
        ) : null}
      </Card>

      {state.order.length === 0 ? (
        <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-label="Receipts">
          {Array.from({ length: 3 }, (_, i) => (
            <li
              key={i}
              className="min-h-[14.5rem] rounded-2xl border border-slate-200 bg-white p-4"
            >
              <Skeleton className="h-10 w-2/3" />
              <Skeleton className="mt-6 h-4 w-full" />
              <Skeleton className="mt-3 h-4 w-1/2" />
            </li>
          ))}
        </ul>
      ) : (
        <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-label="Receipts">
          {state.order.map((id) => (
            <DocumentProgressCard
              key={id}
              state={state}
              doc={state.docs[id]}
              demo={demo}
              profile={profile}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function ConnectionNote({ connection }: { connection: string }) {
  if (connection === "reconnecting") {
    return (
      <p className="inline-flex items-center gap-1.5 rounded-full bg-amber-50 px-3 py-1 text-xs font-medium text-amber-900 ring-1 ring-amber-300 ring-inset">
        <WifiOff className="size-3.5" aria-hidden="true" />
        Connection lost, reconnecting…
      </p>
    );
  }
  if (connection === "live") {
    return (
      <p className="inline-flex items-center gap-1.5 rounded-full bg-teal-50 px-3 py-1 text-xs font-medium text-teal-800 ring-1 ring-teal-200 ring-inset">
        <Wifi className="size-3.5" aria-hidden="true" />
        Live
      </p>
    );
  }
  return null;
}

/** /batches/[id]: waits for the persona, then streams the batch (keyed so a persona switch resets). */
export function BatchScreen({
  batchId,
  autoOpenSeconds = AUTO_OPEN_SECONDS,
}: {
  batchId: string;
  /** Seconds before a batch that finished while you watched opens its claims. */
  autoOpenSeconds?: number;
}) {
  const { personaId, status, error, reload } = usePersona();
  if (status === "error") {
    return (
      <div className="mx-auto w-full max-w-3xl px-4 py-12">
        <ErrorState as="h1" error={error} onRetry={reload} />
      </div>
    );
  }
  if (!personaId) {
    return (
      <div className="mx-auto w-full max-w-6xl px-4 py-12">
        <PageSkeleton rows={3} />
      </div>
    );
  }
  return (
    <BatchLive
      key={`${personaId}:${batchId}`}
      batchId={batchId}
      autoOpenSeconds={autoOpenSeconds}
    />
  );
}

/** Route adapter: unwraps the params promise handed down by the (server) page. */
export function BatchRoute({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <BatchScreen batchId={id} />;
}
