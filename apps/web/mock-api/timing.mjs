// @ts-check
/**
 * Simulated time: every delay of the mock goes through `sleep`, which stops (and resolves false)
 * as soon as the context is reset or closed.
 */

/**
 * Wait `ms` milliseconds; resolves true when the time passed, false when `signal` aborted first.
 * @param {number} ms
 * @param {AbortSignal} signal
 * @returns {Promise<boolean>}
 */
export function sleep(ms, signal) {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve(false);
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      resolve(false);
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve(true);
    }, ms);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

/**
 * Wait `ms` of simulated time (divided by the context's speed).
 * @param {{ speed: number; abort: AbortController }} ctx
 * @param {number} ms
 */
export function simulate(ctx, ms) {
  return sleep(ms / ctx.speed, ctx.abort.signal);
}
