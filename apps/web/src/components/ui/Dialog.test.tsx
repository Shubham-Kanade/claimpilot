import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef, useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { Dialog } from "./Dialog";

function Harness({
  dismissible = true,
  onClose = vi.fn(),
}: {
  dismissible?: boolean;
  onClose?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const cancelRef = useRef<HTMLButtonElement>(null);
  return (
    <div>
      <div id="app-root">
        <button onClick={() => setOpen(true)}>Open</button>
        <a href="#x">Outside link</a>
      </div>
      <Dialog
        open={open}
        onClose={() => {
          onClose();
          setOpen(false);
        }}
        title="Submit this claim?"
        description="Check the summary."
        dismissible={dismissible}
        initialFocusRef={cancelRef}
        footer={
          <>
            <button ref={cancelRef}>Cancel</button>
            <button>Confirm</button>
          </>
        }
      >
        <p>Body text</p>
        <input aria-label="A field" />
      </Dialog>
    </div>
  );
}

describe("Dialog", () => {
  it("is a labelled, described modal dialog", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open" }));
    const dialog = screen.getByRole("dialog", { name: "Submit this claim?" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAccessibleDescription("Check the summary.");
  });

  it("moves focus inside, to the requested control", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Open" }));
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
  });

  it("traps Tab and Shift+Tab inside the dialog", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Open" }));
    const close = screen.getByRole("button", { name: "Close dialog" });
    const confirm = screen.getByRole("button", { name: "Confirm" });
    // Cancel -> Confirm -> (wrap) Close -> field -> Cancel ...
    await user.tab();
    expect(confirm).toHaveFocus();
    await user.tab();
    expect(close).toHaveFocus();
    await user.tab({ shift: true });
    expect(confirm).toHaveFocus();
    // from the first focusable element, Shift+Tab wraps to the last
    close.focus();
    await user.tab({ shift: true });
    expect(confirm).toHaveFocus();
    // from the last, Tab wraps to the first
    await user.tab();
    expect(close).toHaveFocus();
  });

  it("closes on Escape and returns focus to the button that opened it", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    const opener = screen.getByRole("button", { name: "Open" });
    await user.click(opener);
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it("closes from the X button and from a click on the backdrop", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    await user.click(screen.getByRole("button", { name: "Open" }));
    await user.click(screen.getByRole("button", { name: "Close dialog" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "Open" }));
    const backdrop = screen.getByRole("dialog").parentElement!;
    await user.pointer({ target: backdrop, keys: "[MouseLeft]" });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("does not close when the click lands inside the dialog", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    await user.click(screen.getByRole("button", { name: "Open" }));
    await user.click(screen.getByText("Body text"));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("makes the page behind inert and locks scrolling while open", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const root = document.getElementById("app-root")!;
    await user.click(screen.getByRole("button", { name: "Open" }));
    expect(root).toHaveAttribute("inert");
    expect(document.body.style.overflow).toBe("hidden");
    await user.keyboard("{Escape}");
    expect(root).not.toHaveAttribute("inert");
    expect(document.body.style.overflow).toBe("");
  });

  it("can be made non-dismissible (while a submission is in flight)", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<Harness dismissible={false} onClose={onClose} />);
    await user.click(screen.getByRole("button", { name: "Open" }));
    await user.keyboard("{Escape}");
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Close dialog" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
