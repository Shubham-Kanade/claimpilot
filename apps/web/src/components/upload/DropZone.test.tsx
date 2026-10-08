import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DropZone } from "./DropZone";

const png = (name = "receipt.png") => new File(["x"], name, { type: "image/png" });

function dataTransfer(files: File[], types: string[] = ["Files"]) {
  return { files, types, dropEffect: "none" } as unknown as DataTransfer;
}

describe("DropZone", () => {
  it("is operable with the keyboard: a labelled, focusable file input", async () => {
    const user = userEvent.setup();
    render(<DropZone onFiles={vi.fn()} />);
    const input = screen.getByLabelText("Choose receipts to upload");
    expect(input).toHaveAttribute("type", "file");
    expect(input).toHaveAttribute("multiple");
    await user.tab();
    expect(input).toHaveFocus();
  });

  it("accepts only JPEG, PNG, WebP and PDF in the picker and says so", () => {
    render(<DropZone onFiles={vi.fn()} />);
    const accept = screen.getByLabelText("Choose receipts to upload").getAttribute("accept") ?? "";
    expect(accept).toContain("image/jpeg");
    expect(accept).toContain("application/pdf");
    expect(screen.getByText(/up to 30 files · 15 MB each/)).toBeInTheDocument();
  });

  it("passes picked files to the parent and lets the same file be picked again", async () => {
    const user = userEvent.setup();
    const onFiles = vi.fn();
    render(<DropZone onFiles={onFiles} />);
    const input = screen.getByLabelText("Choose receipts to upload") as HTMLInputElement;
    await user.upload(input, [png("a.png"), png("b.png")]);
    expect(onFiles).toHaveBeenCalledTimes(1);
    expect(onFiles.mock.calls[0][0].map((f: File) => f.name)).toEqual(["a.png", "b.png"]);
    expect(input.value).toBe("");
  });

  it("accepts dropped files and shows a drop hint while dragging over", () => {
    const onFiles = vi.fn();
    const { container } = render(<DropZone onFiles={onFiles} />);
    const zone = container.firstElementChild as HTMLElement;
    fireEvent.dragOver(zone, { dataTransfer: dataTransfer([png()]) });
    expect(screen.getByText("Release to add your receipts")).toBeInTheDocument();
    expect(zone).toHaveAttribute("data-dragging");
    fireEvent.drop(zone, { dataTransfer: dataTransfer([png("dropped.png")]) });
    expect(onFiles).toHaveBeenCalledTimes(1);
    expect(onFiles.mock.calls[0][0][0].name).toBe("dropped.png");
    expect(screen.getByText("Drop your receipts here")).toBeInTheDocument();
  });

  it("leaves non-file drags (text, links) alone", () => {
    const onFiles = vi.fn();
    const { container } = render(<DropZone onFiles={onFiles} />);
    const zone = container.firstElementChild as HTMLElement;
    fireEvent.dragOver(zone, { dataTransfer: dataTransfer([], ["text/plain"]) });
    expect(zone).not.toHaveAttribute("data-dragging");
    fireEvent.drop(zone, { dataTransfer: dataTransfer([], ["text/plain"]) });
    expect(onFiles).not.toHaveBeenCalled();
  });

  it("clears the highlight when the drag leaves", () => {
    const { container } = render(<DropZone onFiles={vi.fn()} />);
    const zone = container.firstElementChild as HTMLElement;
    fireEvent.dragOver(zone, { dataTransfer: dataTransfer([png()]) });
    fireEvent.dragLeave(zone, { relatedTarget: document.body });
    expect(zone).not.toHaveAttribute("data-dragging");
  });

  it("ignores drops and disables the pickers while disabled", () => {
    const onFiles = vi.fn();
    const { container } = render(<DropZone onFiles={onFiles} disabled />);
    const zone = container.firstElementChild as HTMLElement;
    fireEvent.drop(zone, { dataTransfer: dataTransfer([png()]) });
    expect(onFiles).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Choose receipts to upload")).toBeDisabled();
  });

  it("offers a camera button that opens the rear camera on phones", async () => {
    const user = userEvent.setup();
    const onFiles = vi.fn();
    const { container } = render(<DropZone onFiles={onFiles} />);
    const camera = container.querySelector('input[capture="environment"]') as HTMLInputElement;
    expect(camera).toBeInTheDocument();
    expect(camera).toHaveAttribute("accept", "image/*");
    expect(camera).toHaveAttribute("multiple");
    const click = vi.spyOn(camera, "click");
    await user.click(screen.getByRole("button", { name: "Take photo", hidden: true }));
    expect(click).toHaveBeenCalled();
    fireEvent.change(camera, { target: { files: [png("camera.png")] } });
    expect(onFiles).toHaveBeenCalled();
  });
});
