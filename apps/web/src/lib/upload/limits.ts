import { formatBytes, pluralize } from "../format";

/**
 * Client-side upload limits. They mirror the API (services/api config: MAX_UPLOAD_MB=15,
 * max_batch_files=30) so people get instant, friendly feedback instead of a failed upload; the
 * server stays the source of truth and its 413/422 problems are mapped in api/problems.ts.
 */
export const MAX_FILES = 30;
export const MAX_FILE_BYTES = 15 * 1024 * 1024;
export const MAX_FILE_MB = MAX_FILE_BYTES / (1024 * 1024);

const EXTENSION_TYPES: Record<string, string> = {
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  webp: "image/webp",
  pdf: "application/pdf",
};

export const ACCEPTED_TYPES: readonly string[] = [
  "image/jpeg",
  "image/png",
  "image/webp",
  "application/pdf",
];

/** Value for the `accept` attribute of the file input. */
export const ACCEPT_ATTRIBUTE = [
  ...ACCEPTED_TYPES,
  ...Object.keys(EXTENSION_TYPES).map((e) => `.${e}`),
].join(",");

/** The file's MIME type, falling back to its extension when the browser reports none. */
export function detectType(file: Pick<File, "name" | "type">): string | null {
  if (ACCEPTED_TYPES.includes(file.type)) return file.type;
  if (file.type && file.type !== "application/octet-stream") return file.type;
  const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
  return EXTENSION_TYPES[extension] ?? null;
}

export function isImageFile(file: Pick<File, "name" | "type">): boolean {
  return detectType(file)?.startsWith("image/") ?? false;
}

export type RejectionReason = "type" | "size" | "limit" | "duplicate";

export interface Rejection {
  name: string;
  reason: RejectionReason;
  message: string;
}

export function fileKey(file: Pick<File, "name" | "size" | "lastModified">): string {
  return `${file.name}|${file.size}|${file.lastModified}`;
}

export interface ValidationResult {
  accepted: File[];
  rejected: Rejection[];
}

/** Which of `incoming` can join `current`, and why the others cannot. */
export function validateFiles(
  current: readonly File[],
  incoming: readonly File[],
): ValidationResult {
  const accepted: File[] = [];
  const rejected: Rejection[] = [];
  const seen = new Set(current.map(fileKey));

  for (const file of incoming) {
    const type = detectType(file);
    if (!type || !ACCEPTED_TYPES.includes(type)) {
      const heic = /heic|heif/i.test(type ?? "") || /\.(heic|heif)$/i.test(file.name);
      rejected.push({
        name: file.name,
        reason: "type",
        message: heic
          ? `${file.name}: HEIC photos aren't supported. Set the camera to "Most Compatible" (JPEG) or share it as a JPEG.`
          : `${file.name}: only JPEG, PNG, WebP and PDF files are accepted.`,
      });
      continue;
    }
    if (file.size > MAX_FILE_BYTES) {
      rejected.push({
        name: file.name,
        reason: "size",
        message: `${file.name}: ${formatBytes(file.size)} is over the ${MAX_FILE_MB} MB limit per file.`,
      });
      continue;
    }
    if (seen.has(fileKey(file))) {
      rejected.push({
        name: file.name,
        reason: "duplicate",
        message: `${file.name}: already added.`,
      });
      continue;
    }
    if (current.length + accepted.length >= MAX_FILES) {
      rejected.push({
        name: file.name,
        reason: "limit",
        message: `${file.name}: not added, ${MAX_FILES} receipts is the most per upload.`,
      });
      continue;
    }
    seen.add(fileKey(file));
    accepted.push(file);
  }
  return { accepted, rejected };
}

/** One sentence for the live region: "Skipped 2 files: ...". */
export function summarizeRejections(rejected: readonly Rejection[]): string {
  if (rejected.length === 0) return "";
  const lead = `Skipped ${pluralize(rejected.length, "file")}.`;
  return `${lead} ${rejected.map((r) => r.message).join(" ")}`;
}

export function totalBytes(files: readonly File[]): number {
  return files.reduce((sum, file) => sum + file.size, 0);
}
