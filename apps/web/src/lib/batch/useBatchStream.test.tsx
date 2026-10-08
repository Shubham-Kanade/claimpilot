import { renderHook, waitFor, act } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { ApiError, createApi, type ApiClient } from "@/lib/api/client";
import type { PipelineEvent } from "@/lib/api/types";
import { PersonaContext } from "@/lib/persona/PersonaProvider";
import { makeBatchView } from "@/test/fixtures";
import { fakePersona } from "@/test/render";
import { controlledStream, frame, streamOf } from "@/test/streams";

import { useBatchStream } from "./useBatchStream";

const started: PipelineEvent = { type: "batch_started", batch_id: "bat-1", total: 2 };
const extracted = (id: string, position: number): PipelineEvent => ({
  type: "document_extracted",
  batch_id: "bat-1",
  document_id: id,
  filename: `${id}.png`,
  position,
  doc_type: "cab_receipt",
  merchant: "Raahi Cabs",
  total: 672.74,
  category: "local_conveyance",
  category_confidence: 0.95,
  engine: "jev",
  cached: false,
  cost_usd: 0.0005,
});
const checked = (id: string): PipelineEvent => ({
  type: "document_checked",
  batch_id: "bat-1",
  document_id: id,
  trust_score: 100,
  verdict: "clean",
  findings: 0,
});
const done: PipelineEvent = {
  type: "batch_done",
  batch_id: "bat-1",
  processed: 2,
  failed: 0,
  claims: 1,
  cost_usd: 0.001,
};

function sseResponse(body: ReadableStream<Uint8Array>) {
  return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
}

type Impl = Partial<{
  getBatch: () => Promise<ReturnType<typeof makeBatchView>>;
  batchHistory: () => Promise<PipelineEvent[]>;
  batchEvents: (
    id: string,
    options?: { lastEventId?: string | null; signal?: AbortSignal },
  ) => Promise<Response>;
}>;

function setup(impl: Impl = {}) {
  const api: ApiClient = createApi({ persona: "DEMO-ASHA" });
  const getBatch = vi
    .spyOn(api, "getBatch")
    .mockImplementation(impl.getBatch ?? (async () => makeBatchView()));
  const batchHistory = vi
    .spyOn(api, "batchHistory")
    .mockImplementation(impl.batchHistory ?? (async () => []));
  const batchEvents = vi
    .spyOn(api, "batchEvents")
    .mockImplementation(impl.batchEvents ?? (async () => sseResponse(streamOf())));
  const wrapper = ({ children }: { children: ReactNode }) => (
    <PersonaContext.Provider value={fakePersona({ api })}>{children}</PersonaContext.Provider>
  );
  return { api, getBatch, batchHistory, batchEvents, wrapper };
}

const FAST = { reconnectDelayMs: 1, maxReconnects: 2 };

describe("useBatchStream", () => {
  it("loads the snapshot and history, then streams live events until batch_done", async () => {
    const live = controlledStream();
    const { wrapper, batchEvents, batchHistory } = setup({
      batchHistory: async () => [started],
      batchEvents: async () => sseResponse(live.stream),
    });
    const { result } = renderHook(() => useBatchStream("bat-1", FAST), { wrapper });

    await waitFor(() => expect(result.current.connection).toBe("live"));
    expect(batchHistory).toHaveBeenCalledWith("bat-1", 0, expect.any(AbortSignal));
    // History held one event (index 0), so the stream resumes after it.
    expect(batchEvents).toHaveBeenCalledWith("bat-1", {
      lastEventId: "0",
      signal: expect.any(AbortSignal),
    });
    expect(result.current.state.started).toBe(true);

    act(() => {
      live.push(frame(1, extracted("doc-000001", 0)));
      live.push(frame(2, checked("doc-000001")));
    });
    await waitFor(() => expect(result.current.state.docs["doc-000001"]?.phase).toBe("checked"));
    expect(result.current.state.docs["doc-000001"].merchant).toBe("Raahi Cabs");
    expect(result.current.state.costUsd).toBeCloseTo(0.0005, 6);

    act(() => live.push(frame(3, done)));
    await waitFor(() => expect(result.current.connection).toBe("closed"));
    expect(result.current.state.phase).toBe("done");
    expect(result.current.state.summary?.claims).toBe(1);
    expect(batchEvents).toHaveBeenCalledTimes(1);
    expect(live.cancelled).toBe(true); // the connection is released at the end
  });

  it("starts streaming from the beginning when the batch has no history yet", async () => {
    const { wrapper, batchEvents } = setup({
      batchEvents: async () => sseResponse(streamOf(frame(0, started), frame(1, done))),
    });
    const { result } = renderHook(() => useBatchStream("bat-1", FAST), { wrapper });
    await waitFor(() => expect(result.current.connection).toBe("closed"));
    expect(batchEvents).toHaveBeenCalledWith("bat-1", {
      lastEventId: null,
      signal: expect.any(AbortSignal),
    });
  });

  it("does not open a stream when the history already ends in a terminal event", async () => {
    const { wrapper, batchEvents } = setup({
      batchHistory: async () => [started, extracted("doc-000001", 0), done],
    });
    const { result } = renderHook(() => useBatchStream("bat-1", FAST), { wrapper });
    await waitFor(() => expect(result.current.connection).toBe("closed"));
    expect(batchEvents).not.toHaveBeenCalled();
    expect(result.current.state.phase).toBe("done");
  });

  it("reconnects with the last event id when the connection drops", async () => {
    let calls = 0;
    const { wrapper, batchEvents } = setup({
      batchEvents: async () => {
        calls += 1;
        if (calls === 1)
          return sseResponse(streamOf(frame(0, started), frame(1, extracted("doc-000001", 0))));
        return sseResponse(streamOf(frame(2, checked("doc-000001")), frame(3, done)));
      },
    });
    const { result } = renderHook(() => useBatchStream("bat-1", FAST), { wrapper });
    await waitFor(() => expect(result.current.connection).toBe("closed"));
    expect(batchEvents).toHaveBeenCalledTimes(2);
    expect(batchEvents.mock.calls[1][1]).toMatchObject({ lastEventId: "1" });
    expect(result.current.state.docs["doc-000001"].phase).toBe("checked");
    expect(result.current.state.phase).toBe("done");
  });

  it("retries after a network error and shows it as reconnecting", async () => {
    let calls = 0;
    const { wrapper } = setup({
      batchEvents: async () => {
        calls += 1;
        if (calls === 1) throw new ApiError(0, { type: "network_error", title: "x", status: 0 });
        return sseResponse(streamOf(frame(0, done)));
      },
    });
    const { result } = renderHook(() => useBatchStream("bat-1", FAST), { wrapper });
    await waitFor(() => expect(result.current.connection).toBe("closed"));
    expect(calls).toBe(2);
  });

  it("gives up after too many failed reconnects and offers retry()", async () => {
    const { wrapper, batchEvents } = setup({
      batchEvents: async () => {
        throw new ApiError(503, { type: "http_503", title: "down", status: 503 });
      },
    });
    const { result } = renderHook(() => useBatchStream("bat-1", FAST), { wrapper });
    await waitFor(() => expect(result.current.connection).toBe("error"));
    expect(result.current.error).toBeInstanceOf(ApiError);
    expect(batchEvents).toHaveBeenCalledTimes(3); // first try + 2 reconnects

    batchEvents.mockImplementation(async () => sseResponse(streamOf(frame(0, done))));
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.connection).toBe("closed"));
    expect(result.current.error).toBeNull();
  });

  it("does not retry client errors such as 404 (not your batch)", async () => {
    const { wrapper, batchEvents } = setup({
      getBatch: async () => {
        throw new ApiError(404, { type: "batch_not_found", title: "No such batch", status: 404 });
      },
    });
    const { result } = renderHook(() => useBatchStream("bat-1", FAST), { wrapper });
    await waitFor(() => expect(result.current.connection).toBe("error"));
    expect(result.current.error).toMatchObject({ status: 404, type: "batch_not_found" });
    expect(batchEvents).not.toHaveBeenCalled();
  });

  it("skips malformed frames and unknown event types without breaking the stream", async () => {
    const { wrapper } = setup({
      batchEvents: async () =>
        sseResponse(
          streamOf(
            ": keepalive\n\n",
            'id: 0\nevent: mystery\ndata: {"type":"mystery"}\n\n',
            "id: 1\nevent: batch_started\ndata: {broken\n\n",
            frame(2, extracted("doc-000001", 0)),
            frame(3, done),
          ),
        ),
    });
    const { result } = renderHook(() => useBatchStream("bat-1", FAST), { wrapper });
    await waitFor(() => expect(result.current.connection).toBe("closed"));
    expect(result.current.state.docs["doc-000001"].phase).toBe("read");
  });

  it("aborts the stream when the component unmounts", async () => {
    const live = controlledStream();
    let signal: AbortSignal | undefined;
    const { wrapper } = setup({
      batchEvents: async (_id, options) => {
        signal = options?.signal;
        return sseResponse(live.stream);
      },
    });
    const { result, unmount } = renderHook(() => useBatchStream("bat-1", FAST), { wrapper });
    await waitFor(() => expect(result.current.connection).toBe("live"));
    unmount();
    expect(signal?.aborted).toBe(true);
  });

  it("waits for a persona before touching the API", async () => {
    const api = createApi({ persona: null });
    const getBatch = vi.spyOn(api, "getBatch");
    const wrapper = ({ children }: { children: ReactNode }) => (
      <PersonaContext.Provider value={fakePersona({ api, personaId: null, persona: null })}>
        {children}
      </PersonaContext.Provider>
    );
    const { result } = renderHook(() => useBatchStream("bat-1", FAST), { wrapper });
    expect(result.current.connection).toBe("connecting");
    expect(getBatch).not.toHaveBeenCalled();
  });
});
