import type { PipelineEvent } from "@/lib/api/types";

/** A ReadableStream that yields the given text chunks (UTF-8 encoded) and then closes. */
export function streamOf(...chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let index = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (index < chunks.length) controller.enqueue(encoder.encode(chunks[index++]));
      else controller.close();
    },
  });
}

/** A stream you push chunks into by hand (to test live updates and cancellation). */
export function controlledStream() {
  const encoder = new TextEncoder();
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  let cancelled = false;
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
    cancel() {
      cancelled = true;
    },
  });
  return {
    stream,
    push: (text: string) => controller.enqueue(encoder.encode(text)),
    close: () => controller.close(),
    get cancelled() {
      return cancelled;
    },
  };
}

/** One SSE frame exactly as the API writes it: id, event name, JSON data, blank line. */
export function frame(index: number, event: PipelineEvent): string {
  return `id: ${index}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`;
}
