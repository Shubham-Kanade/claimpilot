"use client";

import { Camera, CloudUpload } from "lucide-react";
import { useId, useRef, useState, type ChangeEvent, type DragEvent } from "react";

import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";
import { ACCEPT_ATTRIBUTE, MAX_FILE_MB, MAX_FILES } from "@/lib/upload/limits";

/**
 * The big drop target. Works with drag and drop, a click, and the keyboard (the hidden input is
 * a real, focusable file input labelled by the zone). On phones an extra "Take photo" button
 * opens the camera directly (`capture="environment"`).
 */
export function DropZone({
  onFiles,
  disabled = false,
}: {
  /** Called with every file the user picked or dropped (validation happens in the parent). */
  onFiles: (files: File[]) => void;
  disabled?: boolean;
}) {
  const inputId = useId();
  const hintId = useId();
  const cameraRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  function handleChange(event: ChangeEvent<HTMLInputElement>) {
    const picked = Array.from(event.target.files ?? []);
    event.target.value = ""; // so picking the same file again still fires onChange
    if (picked.length > 0) onFiles(picked);
  }

  function hasFiles(event: DragEvent): boolean {
    return Array.from(event.dataTransfer?.types ?? []).includes("Files");
  }

  function onDragOver(event: DragEvent<HTMLDivElement>) {
    if (disabled || !hasFiles(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    setDragging(true);
  }

  function onDragLeave(event: DragEvent<HTMLDivElement>) {
    // Ignore leave events fired when moving over a child element.
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
    setDragging(false);
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    if (!hasFiles(event)) return;
    event.preventDefault();
    setDragging(false);
    if (disabled) return;
    const dropped = Array.from(event.dataTransfer.files);
    if (dropped.length > 0) onFiles(dropped);
  }

  return (
    <div
      onDragEnter={onDragOver}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      data-dragging={dragging || undefined}
      className={cn(
        "rounded-3xl border-2 border-dashed bg-white px-5 py-8 text-center transition-colors sm:px-10 sm:py-12",
        "has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-indigo-700",
        dragging
          ? "border-indigo-600 bg-indigo-50 ring-4 ring-indigo-100"
          : "border-slate-300 hover:border-indigo-400",
        disabled && "pointer-events-none opacity-60",
      )}
    >
      <input
        id={inputId}
        type="file"
        multiple
        accept={ACCEPT_ATTRIBUTE}
        disabled={disabled}
        onChange={handleChange}
        aria-label="Choose receipts to upload"
        aria-describedby={hintId}
        className="sr-only"
      />
      <label htmlFor={inputId} className="flex cursor-pointer flex-col items-center gap-3">
        <span
          aria-hidden="true"
          className="flex size-14 items-center justify-center rounded-2xl bg-indigo-50 text-indigo-700"
        >
          <CloudUpload className="size-7" />
        </span>
        <span className="text-xl font-semibold tracking-tight text-slate-900">
          {dragging ? "Release to add your receipts" : "Drop your receipts here"}
        </span>
        <span className="max-w-md text-sm text-slate-600">
          Photos, PDFs and UPI screenshots. Mixed piles are fine: ClaimPilot sorts them out.
        </span>
        <span className="mt-1 inline-flex h-11 items-center rounded-xl bg-indigo-600 px-5 text-sm font-semibold text-white shadow-sm">
          Choose files
        </span>
      </label>

      <div className="mt-3 hidden justify-center max-sm:flex pointer-coarse:flex">
        <input
          ref={cameraRef}
          type="file"
          accept="image/*"
          capture="environment"
          multiple
          disabled={disabled}
          onChange={handleChange}
          className="hidden"
          aria-hidden="true"
          tabIndex={-1}
        />
        <Button
          variant="secondary"
          icon={<Camera className="size-4" aria-hidden="true" />}
          onClick={() => cameraRef.current?.click()}
        >
          Take photo
        </Button>
      </div>

      <p id={hintId} className="mt-4 text-xs text-slate-600">
        JPEG, PNG, WebP or PDF · up to {MAX_FILES} files · {MAX_FILE_MB} MB each
      </p>
    </div>
  );
}
