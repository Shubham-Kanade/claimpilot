/**
 * The demo sandbox id: one per visitor (browser), sent as `X-Sandbox` on every API request so two
 * visitors acting as the same demo employee never see or delete each other's data. The API only
 * honours it in demo mode (and ignores it elsewhere).
 *
 * It lives in localStorage and is created on first use. Storage can be blocked (private mode,
 * sandboxed iframe): then the id is kept in memory for the life of the tab. Nothing is cached in
 * a module variable except that fallback, which tests reset with `resetSandboxFallback()`.
 */
export const SANDBOX_STORAGE_KEY = "claimpilot.sandbox";

/** What the API accepts: ^[A-Za-z0-9_-]{16,64}$. */
export const SANDBOX_ID_PATTERN = /^[A-Za-z0-9_-]{16,64}$/;

let memoryFallback: string | null = null;

/** Tests only: forget the in-memory fallback. */
export function resetSandboxFallback(): void {
  memoryFallback = null;
}

/** 24 random bytes as base64url without padding: 32 characters. */
export function newSandboxId(): string {
  const bytes = new Uint8Array(24);
  // getRandomValues works in insecure contexts too (randomUUID does not)
  globalThis.crypto.getRandomValues(bytes);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** The visitor's sandbox id, created (and saved) the first time it is asked for. */
export function getSandboxId(): string {
  try {
    const stored = window.localStorage.getItem(SANDBOX_STORAGE_KEY);
    if (stored && SANDBOX_ID_PATTERN.test(stored)) return stored;
    const id = memoryFallback ?? newSandboxId();
    window.localStorage.setItem(SANDBOX_STORAGE_KEY, id);
    memoryFallback = null;
    return id;
  } catch {
    // storage blocked (or no window): the same id for the rest of this tab
    memoryFallback ??= newSandboxId();
    return memoryFallback;
  }
}
