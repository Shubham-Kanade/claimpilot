import { afterEach, describe, expect, it, vi } from "vitest";

import {
  getSandboxId,
  newSandboxId,
  resetSandboxFallback,
  SANDBOX_ID_PATTERN,
  SANDBOX_STORAGE_KEY,
} from "./store";

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
  resetSandboxFallback();
});

describe("sandbox id", () => {
  it("is 32 URL-safe characters the API accepts", () => {
    for (let i = 0; i < 50; i += 1) {
      const id = newSandboxId();
      expect(id).toHaveLength(32);
      expect(id).toMatch(SANDBOX_ID_PATTERN);
      expect(id).not.toMatch(/[+/=]/);
    }
    expect(newSandboxId()).not.toBe(newSandboxId());
  });

  it("is created on first use, saved, and reused", () => {
    expect(window.localStorage.getItem(SANDBOX_STORAGE_KEY)).toBeNull();
    const first = getSandboxId();
    expect(window.localStorage.getItem(SANDBOX_STORAGE_KEY)).toBe(first);
    expect(getSandboxId()).toBe(first);
    expect(getSandboxId()).toBe(first);
  });

  it("follows what is in storage on every call (another tab, a test, a pinned id)", () => {
    window.localStorage.setItem(SANDBOX_STORAGE_KEY, "e2e-sandbox-00000000000001");
    expect(getSandboxId()).toBe("e2e-sandbox-00000000000001");
    window.localStorage.setItem(SANDBOX_STORAGE_KEY, "another-sandbox-0000000001");
    expect(getSandboxId()).toBe("another-sandbox-0000000001");
  });

  it("replaces a saved value the API would refuse", () => {
    window.localStorage.setItem(SANDBOX_STORAGE_KEY, "short");
    const id = getSandboxId();
    expect(id).toMatch(SANDBOX_ID_PATTERN);
    expect(window.localStorage.getItem(SANDBOX_STORAGE_KEY)).toBe(id);
  });

  it("keeps one id in memory when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const id = getSandboxId();
    expect(id).toMatch(SANDBOX_ID_PATTERN);
    expect(getSandboxId()).toBe(id);
  });

  it("moves the in-memory id into storage once storage works again", () => {
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const id = getSandboxId();
    getItem.mockRestore();
    expect(getSandboxId()).toBe(id);
    expect(window.localStorage.getItem(SANDBOX_STORAGE_KEY)).toBe(id);
  });
});
