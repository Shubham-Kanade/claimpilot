// @ts-check
/**
 * `GET /v1/batches/{id}/events`: the batch's events as server-sent events, framed exactly like the
 * backend does:
 *
 *   id: <index>\nevent: <type>\ndata: <json>\n\n
 *
 * `index` is the event's 0-based position in the batch's list. A client that connects late (or
 * reconnects with `Last-Event-ID`) gets every event from `start` first, then the live ones. While
 * nothing happens a `: keepalive` comment goes out every 15 s (divided by speed). The stream ends
 * after the terminal event (`batch_done` / `batch_failed`).
 */

import { TERMINAL_TYPES } from "./events.mjs";

/** @typedef {import("node:http").IncomingMessage} IncomingMessage */
/** @typedef {import("node:http").ServerResponse} ServerResponse */
/** @typedef {import("./types.mjs").BatchRecord} BatchRecord */
/** @typedef {import("./types.mjs").Context} Context */
/** @typedef {import("./types.mjs").PipelineEvent} PipelineEvent */

const HEARTBEAT_MS = 15_000;

/**
 * @param {number} index
 * @param {PipelineEvent} event
 */
export function frame(index, event) {
  return `id: ${index}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`;
}

/**
 * @param {Context} ctx
 * @param {IncomingMessage} req
 * @param {ServerResponse} res
 * @param {BatchRecord} batch
 * @param {number} start  Index of the first event to send.
 */
export function streamEvents(ctx, req, res, batch, start) {
  res.writeHead(200, {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    Connection: "keep-alive",
  });
  res.flushHeaders();
  req.socket.setNoDelay(true);

  let open = true;
  let next = start; // index of the next event the client has not seen
  /** @type {ReturnType<typeof setTimeout> | undefined} */
  let heartbeat;

  const close = () => {
    if (!open) return;
    open = false;
    clearTimeout(heartbeat);
    batch.listeners.delete(onEvent);
    ctx.streams.delete(close);
    res.end();
  };
  const arm = () => {
    clearTimeout(heartbeat);
    heartbeat = setTimeout(() => {
      if (!open) return;
      res.write(": keepalive\n\n");
      arm();
    }, HEARTBEAT_MS / ctx.speed);
  };
  /**
   * @param {number} index
   * @param {PipelineEvent} event
   */
  const onEvent = (index, event) => {
    if (!open || index < next) return; // before the point the client resumes from
    next = index + 1;
    res.write(frame(index, event));
    if (TERMINAL_TYPES.has(event.type)) close();
    else arm();
  };

  ctx.streams.add(close);
  res.on("close", close);

  // Everything missed so far, then the live events.
  for (let index = start; open && index < batch.events.length; index += 1) {
    const event = batch.events[index];
    if (event) onEvent(index, event);
  }
  if (!open) return;
  if (batch.status === "done" || batch.status === "failed") {
    close(); // finished, and the client asked for events that never existed
    return;
  }
  batch.listeners.add(onEvent);
  arm();
}
