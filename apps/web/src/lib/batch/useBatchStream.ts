"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { ApiError } from "../api/client";
import { isTerminal, parseSse, toPipelineEvent } from "../api/sse";
import { usePersona } from "../persona/PersonaProvider";
import { batchReducer, initialBatchState, type BatchState } from "./batchReducer";

export type StreamConnection =
  /** loading the snapshot / opening the stream */
  | "connecting"
  /** the stream is open */
  | "live"
  /** the connection dropped; trying again from the last event seen */
  | "reconnecting"
  /** the batch reached batch_done / batch_failed */
  | "closed"
  /** could not (re)connect; `error` says why and `retry()` starts over */
  | "error";

export interface BatchStream {
  state: BatchState;
  connection: StreamConnection;
  error: ApiError | null;
  /** True once at least one event arrived over the live stream (not just from history). */
  sawLive: boolean;
  /** Start over after a fatal error. */
  retry: () => void;
}

export interface BatchStreamOptions {
  /** Base delay before reconnecting; doubles per attempt (default 1000 ms, capped at 8 s). */
  reconnectDelayMs?: number;
  /** Consecutive failed reconnects before giving up (default 6). */
  maxReconnects?: number;
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });
}

/**
 * The one hook that turns a batch's progress into UI state. Reload-safe by construction:
 *
 *  1. GET /v1/batches/{id}          -> the documents and what has already happened,
 *  2. GET /v1/batches/{id}/history  -> every event so far (as JSON), applied in order,
 *  3. GET /v1/batches/{id}/events   -> the live stream, resumed with `Last-Event-ID` = the last
 *     event index applied, read with fetch + ReadableStream (EventSource cannot send X-Persona).
 *
 * It stops at `batch_done` / `batch_failed`, reconnects with back-off (and the same
 * `Last-Event-ID`) when the connection drops, and aborts cleanly on unmount. The reducer ignores
 * any event index it has already applied, so replays never double count.
 */
export function useBatchStream(batchId: string, options: BatchStreamOptions = {}): BatchStream {
  const { api, personaId } = usePersona();
  const { reconnectDelayMs = 1000, maxReconnects = 6 } = options;

  const [state, dispatch] = useReducer(batchReducer, batchId, initialBatchState);
  const [connection, setConnection] = useState<StreamConnection>("connecting");
  const [error, setError] = useState<ApiError | null>(null);
  const [sawLive, setSawLive] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const lastIndexRef = useRef(-1);
  const finishedRef = useRef(false);

  useEffect(() => {
    if (!personaId) return;
    const controller = new AbortController();
    const { signal } = controller;

    async function snapshot() {
      dispatch({ type: "hydrate", batch: await api.getBatch(batchId, signal) });
    }

    async function run() {
      try {
        await snapshot();
        const history = await api.batchHistory(batchId, lastIndexRef.current + 1, signal);
        for (const event of history) {
          lastIndexRef.current += 1;
          dispatch({ type: "event", event, index: lastIndexRef.current });
        }
        let finished = history.some(isTerminal);

        let failures = 0;
        while (!finished) {
          try {
            const lastEventId = lastIndexRef.current >= 0 ? String(lastIndexRef.current) : null;
            const response = await api.batchEvents(batchId, { lastEventId, signal });
            if (!response.body)
              throw new ApiError(0, { type: "network_error", title: "No stream" });
            setConnection("live");
            let gotEvent = false;
            for await (const frame of parseSse(response.body, signal)) {
              const event = toPipelineEvent(frame);
              if (!event) continue;
              const declared =
                frame.id !== null && /^\d+$/.test(frame.id) ? Number(frame.id) : null;
              const index = declared ?? lastIndexRef.current + 1;
              lastIndexRef.current = Math.max(lastIndexRef.current, index);
              dispatch({ type: "event", event, index });
              setSawLive(true);
              gotEvent = true;
              failures = 0;
              if (isTerminal(event)) {
                finished = true;
                break;
              }
            }
            // The server closed the stream without a terminal event. Count it as a failed
            // attempt unless it delivered something, so a flapping server cannot loop forever.
            if (!finished && !gotEvent) failures += 1;
            if (failures > maxReconnects) throw new ApiError(0, { type: "network_error" });
          } catch (cause) {
            if (signal.aborted) return;
            // 401/403/404/422: retrying cannot help. Network errors and 5xx are retried.
            if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500) throw cause;
            failures += 1;
            if (failures > maxReconnects) {
              throw cause instanceof ApiError ? cause : new ApiError(0, { type: "network_error" });
            }
          }
          if (!finished) {
            setConnection("reconnecting");
            await sleep(Math.min(reconnectDelayMs * 2 ** Math.max(0, failures - 1), 8000), signal);
          }
        }

        finishedRef.current = true;
        await snapshot().catch(() => undefined); // pick up the claims the batch produced
        setConnection("closed");
      } catch (cause) {
        if (signal.aborted) return;
        setError(cause instanceof ApiError ? cause : new ApiError(0, { type: "network_error" }));
        setConnection("error");
      }
    }

    // A finished batch (seen before this route was hidden and shown again) needs no new stream.
    if (!finishedRef.current) void run();
    return () => controller.abort();
  }, [api, batchId, personaId, attempt, reconnectDelayMs, maxReconnects]);

  const retry = useCallback(() => {
    setError(null);
    setConnection("connecting");
    setAttempt((n) => n + 1);
  }, []);

  return { state, connection, error, sawLive, retry };
}

/** Wall-clock ticker for "elapsed" displays; null until the first tick (keeps render pure). */
export function useNow(active: boolean, intervalMs = 1000): number | null {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    if (!active) return;
    const tick = () => setNow(Date.now());
    const timer = setInterval(tick, intervalMs);
    const first = setTimeout(tick, 0);
    return () => {
      clearInterval(timer);
      clearTimeout(first);
    };
  }, [active, intervalMs]);
  return now;
}
