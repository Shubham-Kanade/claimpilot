import { render, renderHook, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/client";
import { useObjectUrl } from "@/lib/hooks/useObjectUrl";

import { Button } from "./Button";
import { Chip } from "./Chip";
import { EmptyState, ErrorState, InlineError, PageSkeleton, Skeleton, Spinner } from "./feedback";
import { ProgressBar, ScoreMeter } from "./ProgressBar";

const problem = (status: number, type: string, title = "t") =>
  new ApiError(status, { type, title, status });

describe("ErrorState", () => {
  it("is an alert with friendly title and message, never the raw error", () => {
    render(<ErrorState error={problem(404, "claim_not_found")} />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Claim not found");
    expect(alert).not.toHaveTextContent("claim_not_found");
  });

  it("offers retry only when a retry makes sense", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    const { rerender } = render(<ErrorState error={problem(503, "http_503")} onRetry={onRetry} />);
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    rerender(<ErrorState error={problem(404, "claim_not_found")} onRetry={onRetry} />);
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("shows extra actions such as a link back", () => {
    render(<ErrorState error={problem(403, "approver_only")} actions={<button>Go home</button>} />);
    expect(screen.getByRole("button", { name: "Go home" })).toBeInTheDocument();
  });

  it("covers every kind of problem with an icon", () => {
    for (const [status, type] of [
      [0, "network_error"],
      [401, "missing_persona"],
      [403, "approver_only"],
      [404, "claim_not_found"],
      [409, "claim_locked"],
      [422, "blank_answer"],
      [500, "http_500"],
    ] as const) {
      const { unmount } = render(<ErrorState error={problem(status, type)} />);
      expect(screen.getByRole("alert").querySelector("svg")).not.toBeNull();
      unmount();
    }
  });
});

describe("page-level states", () => {
  it("render an h1 when they replace the whole page (every page needs one)", () => {
    const { rerender } = render(<ErrorState as="h1" error={problem(404, "claim_not_found")} />);
    expect(screen.getByRole("heading", { level: 1, name: "Claim not found" })).toBeInTheDocument();
    rerender(<EmptyState as="h1" title="Approvers only" />);
    expect(screen.getByRole("heading", { level: 1, name: "Approvers only" })).toBeInTheDocument();
    rerender(<EmptyState title="No claims yet" />);
    expect(screen.getByRole("heading", { level: 2, name: "No claims yet" })).toBeInTheDocument();
  });
});

describe("InlineError", () => {
  it("renders mapped API errors and plain messages", () => {
    const { rerender } = render(<InlineError error={problem(422, "comment_required")} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Add a reason.");
    rerender(<InlineError message="Couldn't load the samples." />);
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn't load the samples.");
  });
});

describe("small building blocks", () => {
  it("EmptyState has a heading, text and actions", () => {
    render(
      <EmptyState title="No claims yet" action={<button>Upload</button>}>
        Drop a pile of receipts.
      </EmptyState>,
    );
    expect(screen.getByRole("heading", { name: "No claims yet" })).toBeInTheDocument();
    expect(screen.getByText("Drop a pile of receipts.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload" })).toBeInTheDocument();
  });

  it("Spinner announces itself and Skeleton is hidden from assistive tech", () => {
    const { container } = render(
      <>
        <Spinner label="Reading" />
        <Skeleton className="h-4 w-4" />
        <PageSkeleton rows={2} />
      </>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Reading");
    expect(container.querySelectorAll('[aria-hidden="true"].skeleton').length).toBeGreaterThan(2);
  });

  it("Button shows a spinner and is disabled while loading", () => {
    render(<Button loading>Save</Button>);
    const button = screen.getByRole("button", { name: "Save" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
  });

  it("Button supports every variant and size", () => {
    for (const variant of [
      "primary",
      "secondary",
      "ghost",
      "danger",
      "accent",
      "success",
    ] as const) {
      for (const size of ["sm", "md", "lg"] as const) {
        const { unmount } = render(
          <Button variant={variant} size={size}>
            x
          </Button>,
        );
        expect(screen.getByRole("button")).toBeInTheDocument();
        unmount();
      }
    }
  });

  it("Chip always renders its text, with an optional decorative icon", () => {
    const { container } = render(
      <Chip tone="success" icon={<svg />}>
        Done
      </Chip>,
    );
    expect(screen.getByText("Done")).toBeInTheDocument();
    expect(container.querySelector('[aria-hidden="true"] svg')).not.toBeNull();
  });

  it("ProgressBar is a real progressbar and clamps its value", () => {
    const { rerender } = render(<ProgressBar percent={42.4} label="Overall progress" />);
    expect(screen.getByRole("progressbar", { name: "Overall progress" })).toHaveAttribute(
      "aria-valuenow",
      "42",
    );
    rerender(<ProgressBar percent={250} label="x" tone="success" />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
    rerender(<ProgressBar percent={-5} label="x" tone="danger" />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "0");
  });

  it("ScoreMeter reflects the score band", () => {
    const { container, rerender } = render(<ScoreMeter score={90} />);
    expect(container.querySelector(".bg-emerald-600")).not.toBeNull();
    rerender(<ScoreMeter score={60} />);
    expect(container.querySelector(".bg-amber-500")).not.toBeNull();
    rerender(<ScoreMeter score={20} />);
    expect(container.querySelector(".bg-rose-600")).not.toBeNull();
  });
});

describe("useObjectUrl", () => {
  it("creates a blob URL and revokes it on unmount", () => {
    const created = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:one");
    const revoked = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    const blob = new Blob(["x"]);
    const { result, unmount } = renderHook(() => useObjectUrl(blob));
    expect(result.current).toBe("blob:one");
    expect(created).toHaveBeenCalledWith(blob);
    unmount();
    expect(revoked).toHaveBeenCalledWith("blob:one");
  });

  it("returns null without a blob and swaps URLs when the blob changes", () => {
    const urls = ["blob:a", "blob:b"];
    vi.spyOn(URL, "createObjectURL").mockImplementation(() => urls.shift()!);
    const revoked = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    const { result, rerender } = renderHook(({ blob }) => useObjectUrl(blob), {
      initialProps: { blob: null as Blob | null },
    });
    expect(result.current).toBeNull();
    rerender({ blob: new Blob(["1"]) });
    expect(result.current).toBe("blob:a");
    rerender({ blob: new Blob(["2"]) });
    expect(revoked).toHaveBeenCalledWith("blob:a");
    expect(result.current).toBe("blob:b");
  });
});
