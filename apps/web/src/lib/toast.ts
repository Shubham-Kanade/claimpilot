/**
 * A tiny store of short notifications ("toasts"), readable with `useSyncExternalStore`.
 *
 * It lives outside React so that a toast raised on one screen survives the navigation to the
 * next (the sample-receipts flow raises one, then moves on to the live-progress screen). The
 * `Toaster` in the root layout renders it; nothing else should reach into the list.
 */
export interface Toast {
  id: number;
  text: string;
}

let toasts: readonly Toast[] = [];
let nextId = 1;
const listeners = new Set<() => void>();

function publish(next: readonly Toast[]): void {
  toasts = next;
  for (const listener of listeners) listener();
}

/** Show a toast; returns its id. The same text twice in a row replaces the earlier toast. */
export function showToast(text: string): number {
  const id = nextId++;
  publish([...toasts.filter((toast) => toast.text !== text), { id, text }]);
  return id;
}

export function dismissToast(id: number): void {
  if (toasts.some((toast) => toast.id === id)) publish(toasts.filter((toast) => toast.id !== id));
}

export function clearToasts(): void {
  if (toasts.length > 0) publish([]);
}

export function getToasts(): readonly Toast[] {
  return toasts;
}

const NO_TOASTS: readonly Toast[] = [];

/** On the server (and while hydrating) there are never any toasts. */
export function getServerToasts(): readonly Toast[] {
  return NO_TOASTS;
}

export function subscribeToasts(onChange: () => void): () => void {
  listeners.add(onChange);
  return () => {
    listeners.delete(onChange);
  };
}
