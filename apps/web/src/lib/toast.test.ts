import { describe, expect, it, vi } from "vitest";

import {
  clearToasts,
  dismissToast,
  getServerToasts,
  getToasts,
  showToast,
  subscribeToasts,
} from "./toast";

describe("toast store", () => {
  it("adds toasts in order and gives each its own id", () => {
    const first = showToast("One");
    const second = showToast("Two");
    expect(second).not.toBe(first);
    expect(getToasts().map((toast) => toast.text)).toEqual(["One", "Two"]);
  });

  it("replaces an identical toast instead of stacking duplicates", () => {
    showToast("Switched to Asha");
    const again = showToast("Switched to Asha");
    expect(getToasts()).toEqual([{ id: again, text: "Switched to Asha" }]);
  });

  it("dismisses one toast, ignores unknown ids, and clears them all", () => {
    const keep = showToast("Keep");
    const drop = showToast("Drop");
    dismissToast(drop);
    dismissToast(9_999_999);
    expect(getToasts().map((toast) => toast.id)).toEqual([keep]);
    clearToasts();
    expect(getToasts()).toEqual([]);
    clearToasts(); // nothing to clear: no change, no notification
  });

  it("tells subscribers about changes only, and stops after unsubscribe", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeToasts(listener);
    const id = showToast("Hello");
    dismissToast(id);
    expect(listener).toHaveBeenCalledTimes(2);
    dismissToast(id); // already gone
    clearToasts(); // already empty
    expect(listener).toHaveBeenCalledTimes(2);
    unsubscribe();
    showToast("Ignored by the old subscriber");
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("has no toasts on the server, so the static shell never depends on them", () => {
    showToast("Browser only");
    expect(getServerToasts()).toEqual([]);
  });
});
