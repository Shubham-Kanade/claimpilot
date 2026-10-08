"use client";

import { FileText, X } from "lucide-react";

import { formatBytes } from "@/lib/format";
import { useObjectUrl } from "@/lib/hooks/useObjectUrl";
import { isImageFile } from "@/lib/upload/limits";

function Thumb({ file, onRemove }: { file: File; onRemove: () => void }) {
  const preview = useObjectUrl(isImageFile(file) ? file : null);
  return (
    <li className="group shadow-card relative flex min-w-0 flex-col overflow-hidden rounded-xl border border-slate-200 bg-white">
      <div className="flex aspect-[4/3] items-center justify-center overflow-hidden bg-slate-100">
        {preview ? (
          // eslint-disable-next-line @next/next/no-img-element -- local blob preview, not optimisable
          <img src={preview} alt="" className="size-full object-cover" loading="lazy" />
        ) : (
          <FileText className="size-8 text-slate-500" aria-hidden="true" />
        )}
      </div>
      <div className="min-w-0 px-2.5 py-2">
        <p className="truncate text-xs font-medium text-slate-800" title={file.name}>
          {file.name}
        </p>
        <p className="text-xs text-slate-600">{formatBytes(file.size)}</p>
      </div>
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Remove ${file.name}`}
        className="absolute top-1.5 right-1.5 inline-flex size-8 items-center justify-center rounded-full bg-white/95 text-slate-700 shadow ring-1 ring-slate-200 hover:bg-rose-50 hover:text-rose-700"
      >
        <X className="size-4" aria-hidden="true" />
      </button>
    </li>
  );
}

/** Thumbnails of the files that will be uploaded, each removable. */
export function SelectedFiles({
  files,
  onRemove,
}: {
  files: readonly File[];
  onRemove: (index: number) => void;
}) {
  if (files.length === 0) return null;
  return (
    <ul
      aria-label="Receipts to upload"
      className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5"
    >
      {files.map((file, index) => (
        <Thumb
          key={`${file.name}-${file.size}-${file.lastModified}-${index}`}
          file={file}
          onRemove={() => onRemove(index)}
        />
      ))}
    </ul>
  );
}
