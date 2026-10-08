import { describe, expect, it } from "vitest";

import { newIdempotencyKey } from "./idempotency";

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe("newIdempotencyKey", () => {
  it("returns a v4 UUID and a different one each time", () => {
    const a = newIdempotencyKey();
    const b = newIdempotencyKey();
    expect(a).toMatch(UUID_V4);
    expect(a).not.toBe(b);
  });

  it("works without crypto.randomUUID (plain-http LAN addresses on phones)", () => {
    const insecure = {
      getRandomValues: (array: Uint8Array) => {
        array.forEach((_, i) => (array[i] = (i * 37 + 11) % 256));
        return array;
      },
    } as unknown as Crypto;
    const key = newIdempotencyKey(insecure);
    expect(key).toMatch(UUID_V4);
    expect(newIdempotencyKey(insecure)).toBe(key); // deterministic stub, proves the fallback ran
  });
});
