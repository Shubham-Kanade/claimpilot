/**
 * A fresh random Idempotency-Key (RFC 4122 v4 UUID).
 *
 * `crypto.randomUUID()` only exists in secure contexts (https or localhost), so opening the demo
 * from a phone over a plain-http LAN address would otherwise break "Confirm & submit". The
 * `getRandomValues` fallback works everywhere.
 */
export function newIdempotencyKey(cryptoImpl: Crypto = globalThis.crypto): string {
  if (typeof cryptoImpl.randomUUID === "function") return cryptoImpl.randomUUID();
  const bytes = cryptoImpl.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant 10xx
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
