/**
 * The acting persona (an employee id) lives in localStorage so it survives reloads.
 * It is exposed as an external store so React can read it with `useSyncExternalStore`
 * (server snapshot: null, so the static shell never depends on the browser).
 */
export const PERSONA_STORAGE_KEY = "claimpilot.persona";

const listeners = new Set<() => void>();

export function getStoredPersona(): string | null {
  try {
    return window.localStorage.getItem(PERSONA_STORAGE_KEY);
  } catch {
    return null; // storage blocked (private mode, sandboxed iframe): fall back to the default
  }
}

export function getServerPersona(): string | null {
  return null;
}

export function setStoredPersona(id: string | null): void {
  try {
    if (id === null) window.localStorage.removeItem(PERSONA_STORAGE_KEY);
    else window.localStorage.setItem(PERSONA_STORAGE_KEY, id);
  } catch {
    // ignore: the in-memory subscribers below still update, the choice just won't persist
  }
  for (const listener of listeners) listener();
}

export function subscribePersona(onChange: () => void): () => void {
  listeners.add(onChange);
  const onStorage = (event: StorageEvent) => {
    if (event.key === PERSONA_STORAGE_KEY || event.key === null) onChange();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("storage", onStorage);
  };
}
