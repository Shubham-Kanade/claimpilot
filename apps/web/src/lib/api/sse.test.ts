import { describe, expect, it } from "vitest";

import { streamOf } from "@/test/streams";

import { isTerminal, parseSse, toPipelineEvent, type SseFrame } from "./sse";
import type { PipelineEvent } from "./types";

async function collect(stream: ReadableStream<Uint8Array>, signal?: AbortSignal) {
  const frames: SseFrame[] = [];
  for await (const frame of parseSse(stream, signal)) frames.push(frame);
  return frames;
}

describe("parseSse", () => {
  it("parses id / event / data frames", async () => {
    const frames = await collect(
      streamOf('id: 0\nevent: batch_started\ndata: {"type":"batch_started","total":2}\n\n'),
    );
    expect(frames).toEqual([
      { id: "0", event: "batch_started", data: '{"type":"batch_started","total":2}' },
    ]);
  });

  it("ignores keep-alive comment lines", async () => {
    const frames = await collect(
      streamOf(": keepalive\n\n", "id: 1\nevent: x\ndata: {}\n\n", ": keepalive\n\n"),
    );
    expect(frames).toHaveLength(1);
    expect(frames[0].id).toBe("1");
  });

  it("reassembles a frame split across chunks, even mid-line and mid-field", async () => {
    const frames = await collect(
      streamOf("id: 4\nev", "ent: document_checked\nda", 'ta: {"a"', ":1}\n", "\n"),
    );
    expect(frames).toEqual([{ id: "4", event: "document_checked", data: '{"a":1}' }]);
  });

  it("reads several frames from one chunk", async () => {
    const frames = await collect(streamOf("id: 0\ndata: a\n\nid: 1\ndata: b\n\n"));
    expect(frames.map((f) => [f.id, f.data])).toEqual([
      ["0", "a"],
      ["1", "b"],
    ]);
  });

  it("handles CRLF and lone CR line endings, including a CRLF split across chunks", async () => {
    const crlf = await collect(streamOf("id: 1\r\ndata: x\r\n\r\n"));
    expect(crlf).toEqual([{ id: "1", event: "message", data: "x" }]);
    const split = await collect(streamOf("id: 2\r", "\ndata: y\r", "\n\r", "\n"));
    expect(split).toEqual([{ id: "2", event: "message", data: "y" }]);
    const cr = await collect(streamOf("data: z\r\r"));
    expect(cr[0].data).toBe("z");
  });

  it("joins multi-line data with newlines and strips one leading space", async () => {
    const frames = await collect(streamOf("data: line one\ndata:  line two\ndata\n\n"));
    expect(frames[0].data).toBe("line one\n line two\n");
  });

  it("defaults the event name to 'message' and skips frames without data", async () => {
    const frames = await collect(streamOf("id: 9\n\nid: 10\ndata: ok\n\n"));
    expect(frames).toEqual([{ id: "10", event: "message", data: "ok" }]);
  });

  it("discards a trailing incomplete frame when the stream closes", async () => {
    const frames = await collect(streamOf("id: 0\ndata: complete\n\nid: 1\ndata: partial"));
    expect(frames.map((f) => f.data)).toEqual(["complete"]);
  });

  it("ignores unknown fields, retry and ids containing NUL", async () => {
    const frames = await collect(streamOf("retry: 3000\nfoo: bar\nid: a\0b\ndata: x\n\n"));
    expect(frames).toEqual([{ id: null, event: "message", data: "x" }]);
  });

  it("decodes multi-byte characters split across chunks", async () => {
    const bytes = new TextEncoder().encode("data: ₹100\n\n");
    const half = 8; // inside the three-byte rupee sign
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, half));
        controller.enqueue(bytes.slice(half));
        controller.close();
      },
    });
    expect((await collect(stream))[0].data).toBe("₹100");
  });

  it("stops reading when the signal is aborted", async () => {
    const controller = new AbortController();
    controller.abort();
    expect(await collect(streamOf("data: x\n\n"), controller.signal)).toEqual([]);
  });
});

describe("toPipelineEvent", () => {
  const frame = (data: string): SseFrame => ({ id: "0", event: "x", data });

  it("accepts every documented event type", () => {
    for (const type of [
      "batch_started",
      "document_extracted",
      "document_checked",
      "document_failed",
      "claims_ready",
      "batch_done",
      "batch_failed",
    ]) {
      expect(toPipelineEvent(frame(JSON.stringify({ type })))?.type).toBe(type);
    }
  });

  it("returns null for unknown types, non-objects and malformed JSON", () => {
    expect(toPipelineEvent(frame('{"type":"mystery"}'))).toBeNull();
    expect(toPipelineEvent(frame("42"))).toBeNull();
    expect(toPipelineEvent(frame("null"))).toBeNull();
    expect(toPipelineEvent(frame("{not json"))).toBeNull();
    expect(toPipelineEvent(frame('{"nope":1}'))).toBeNull();
  });
});

describe("isTerminal", () => {
  it("is true only for batch_done and batch_failed", () => {
    const make = (type: string) => ({ type }) as unknown as PipelineEvent;
    expect(isTerminal(make("batch_done"))).toBe(true);
    expect(isTerminal(make("batch_failed"))).toBe(true);
    expect(isTerminal(make("document_checked"))).toBe(false);
    expect(isTerminal(make("claims_ready"))).toBe(false);
  });
});
