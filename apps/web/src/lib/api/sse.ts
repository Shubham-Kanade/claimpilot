import type { PipelineEvent, PipelineEventType } from "./types";

/**
 * A minimal Server-Sent Events reader on top of `fetch` + `ReadableStream`.
 *
 * Why not `EventSource`? It cannot send custom headers, and every ClaimPilot call needs
 * `X-Persona` (and `Last-Event-ID` for resuming). The wire format is the standard one:
 * `id:` / `event:` / `data:` lines, a blank line ends a frame, and lines starting with `:` are
 * comments (the server sends `: keepalive`) and are ignored.
 */
export interface SseFrame {
  /** The `id:` of this frame, if it declared one. */
  id: string | null;
  /** The `event:` name (the spec default is "message"). */
  event: string;
  /** All `data:` lines joined with "\n". */
  data: string;
}

/** Split a decoded text buffer into complete lines, returning the unfinished tail. */
function takeLines(buffer: string, final: boolean): { lines: string[]; rest: string } {
  const lines: string[] = [];
  let start = 0;
  for (let i = 0; i < buffer.length; i++) {
    const ch = buffer[i];
    if (ch !== "\n" && ch !== "\r") continue;
    // A "\r" at the very end may be the first half of "\r\n": wait for the next chunk.
    if (ch === "\r" && i === buffer.length - 1 && !final) break;
    lines.push(buffer.slice(start, i));
    if (ch === "\r" && buffer[i + 1] === "\n") i++;
    start = i + 1;
  }
  return { lines, rest: buffer.slice(start) };
}

export async function* parseSse(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<SseFrame, void, void> {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let id: string | null = null;
  let event = "";
  let data: string[] = [];

  try {
    for (;;) {
      if (signal?.aborted) return;
      const { value, done } = await reader.read();
      if (value) buffer += decoder.decode(value, { stream: true });
      if (done) buffer += decoder.decode();
      const { lines, rest } = takeLines(buffer, done);
      buffer = rest;

      for (const line of lines) {
        if (line === "") {
          if (data.length > 0) yield { id, event: event || "message", data: data.join("\n") };
          id = null;
          event = "";
          data = [];
          continue;
        }
        if (line.startsWith(":")) continue; // comment / keep-alive
        const colon = line.indexOf(":");
        const field = colon === -1 ? line : line.slice(0, colon);
        let fieldValue = colon === -1 ? "" : line.slice(colon + 1);
        if (fieldValue.startsWith(" ")) fieldValue = fieldValue.slice(1);
        if (field === "data") data.push(fieldValue);
        else if (field === "event") event = fieldValue;
        else if (field === "id" && !fieldValue.includes("\0")) id = fieldValue;
        // "retry" and unknown fields are ignored: reconnection is handled by useBatchStream.
      }
      // An incomplete trailing frame (no blank line before the stream closed) is discarded.
      if (done) return;
    }
  } finally {
    reader.cancel().catch(() => undefined);
  }
}

export const TERMINAL_EVENTS: ReadonlySet<PipelineEventType> = new Set([
  "batch_done",
  "batch_failed",
]);

const KNOWN_EVENTS: ReadonlySet<string> = new Set<PipelineEventType>([
  "batch_started",
  "document_extracted",
  "document_checked",
  "document_failed",
  "claims_ready",
  "batch_done",
  "batch_failed",
]);

export function isTerminal(event: PipelineEvent): boolean {
  return TERMINAL_EVENTS.has(event.type);
}

/** Decode a frame's JSON `data` into a PipelineEvent; unknown or malformed frames give null. */
export function toPipelineEvent(frame: SseFrame): PipelineEvent | null {
  try {
    const parsed: unknown = JSON.parse(frame.data);
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      typeof (parsed as { type?: unknown }).type === "string" &&
      KNOWN_EVENTS.has((parsed as { type: string }).type)
    ) {
      return parsed as PipelineEvent;
    }
  } catch {
    // fall through: a malformed frame must never break the stream
  }
  return null;
}
