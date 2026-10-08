import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getToasts, showToast } from "@/lib/toast";

import { TOAST_VISIBLE_MS, Toaster } from "./Toaster";

describe("Toaster", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("is an always-present polite live region, empty until something is shown", () => {
    render(<Toaster />);
    const region = screen.getByRole("status");
    expect(region).toHaveAttribute("aria-live", "polite");
    expect(region).toBeEmptyDOMElement();
  });

  it("shows a toast raised anywhere in the app, and removes it with the dismiss button", () => {
    render(<Toaster />);
    act(() => {
      showToast("The sample receipts are Asha Menon's, so we switched to her");
    });
    expect(screen.getByRole("status")).toHaveTextContent(
      "The sample receipts are Asha Menon's, so we switched to her",
    );
    fireEvent.click(screen.getByRole("button", { name: "Dismiss notification" }));
    expect(screen.queryByTestId("toast")).not.toBeInTheDocument();
    expect(getToasts()).toEqual([]);
  });

  it("goes away by itself after ten seconds", () => {
    render(<Toaster />);
    act(() => {
      showToast("Gone soon");
    });
    act(() => {
      vi.advanceTimersByTime(TOAST_VISIBLE_MS - 1);
    });
    expect(screen.getByText("Gone soon")).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(2);
    });
    expect(screen.queryByText("Gone soon")).not.toBeInTheDocument();
  });

  it("waits while the pointer or keyboard focus is on it, then starts the clock again", () => {
    render(<Toaster />);
    act(() => {
      showToast("Read me");
    });
    const toast = screen.getByTestId("toast");

    fireEvent.mouseEnter(toast);
    act(() => {
      vi.advanceTimersByTime(TOAST_VISIBLE_MS * 3);
    });
    expect(screen.getByText("Read me")).toBeInTheDocument();

    fireEvent.mouseLeave(toast);
    fireEvent.focus(screen.getByRole("button", { name: "Dismiss notification" }));
    act(() => {
      vi.advanceTimersByTime(TOAST_VISIBLE_MS * 3);
    });
    expect(screen.getByText("Read me")).toBeInTheDocument();

    fireEvent.blur(screen.getByRole("button", { name: "Dismiss notification" }));
    act(() => {
      vi.advanceTimersByTime(TOAST_VISIBLE_MS + 1);
    });
    expect(screen.queryByText("Read me")).not.toBeInTheDocument();
  });
});
