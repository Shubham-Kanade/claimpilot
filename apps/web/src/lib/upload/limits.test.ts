import { describe, expect, it } from "vitest";

import {
  ACCEPT_ATTRIBUTE,
  detectType,
  fileKey,
  isImageFile,
  DEFAULT_LIMITS,
  limitsFromMeta,
  summarizeRejections,
  totalBytes,
  validateFiles,
} from "./limits";

function file(name: string, size = 1000, type = "", lastModified = 1) {
  const f = new File([new Uint8Array(0)], name, { type, lastModified });
  Object.defineProperty(f, "size", { value: size });
  return f;
}

describe("upload limits mirror the API", () => {
  it("defaults to the API's 30 files of up to 15 MB until /v1/meta says otherwise", () => {
    expect(DEFAULT_LIMITS).toEqual({ maxFiles: 30, maxFileMb: 15 });
    expect(limitsFromMeta(undefined)).toEqual(DEFAULT_LIMITS);
    expect(limitsFromMeta(null)).toEqual(DEFAULT_LIMITS);
  });

  it("takes the limits the API states (the hosted demo: 20 files, 6 MB)", () => {
    expect(limitsFromMeta({ max_batch_files: 20, max_upload_mb: 6 })).toEqual({
      maxFiles: 20,
      maxFileMb: 6,
    });
  });

  it("ignores nonsense and keeps the default for it", () => {
    expect(limitsFromMeta({ max_batch_files: 0, max_upload_mb: -3 })).toEqual(DEFAULT_LIMITS);
    expect(limitsFromMeta({ max_batch_files: Number.NaN })).toEqual(DEFAULT_LIMITS);
    expect(limitsFromMeta({ max_batch_files: 12.7, max_upload_mb: 2.5 })).toEqual({
      maxFiles: 12,
      maxFileMb: 2.5,
    });
  });

  it("validates against the limits it is given, and says which ones", () => {
    const limits = { maxFiles: 2, maxFileMb: 6 };
    const files = [
      file("a.png", 10, "image/png", 1),
      file("b.png", 10, "image/png", 2),
      file("c.png", 10, "image/png", 3),
      file("huge.png", 7 * 1024 * 1024, "image/png", 4),
    ];
    const { accepted, rejected } = validateFiles([], files, limits);
    expect(accepted.map((f) => f.name)).toEqual(["a.png", "b.png"]);
    expect(rejected.map((r) => r.message)).toEqual([
      "c.png: not added, 2 receipts is the most per upload.",
      "huge.png: 7.0 MB is over the 6 MB limit per file.",
    ]);
  });

  it("offers only the accepted types to the file picker", () => {
    expect(ACCEPT_ATTRIBUTE).toContain("image/jpeg");
    expect(ACCEPT_ATTRIBUTE).toContain(".pdf");
    expect(ACCEPT_ATTRIBUTE).not.toContain("heic");
  });
});

describe("detectType", () => {
  it("trusts a declared accepted type", () => {
    expect(detectType(file("a.bin", 1, "image/png"))).toBe("image/png");
  });

  it("falls back to the extension when the browser reports no type", () => {
    expect(detectType(file("scan.JPG"))).toBe("image/jpeg");
    expect(detectType(file("bill.pdf", 1, "application/octet-stream"))).toBe("application/pdf");
    expect(detectType(file("photo.webp"))).toBe("image/webp");
    expect(detectType(file("notes.txt"))).toBeNull();
  });

  it("returns other declared types so they can be rejected with a reason", () => {
    expect(detectType(file("a.heic", 1, "image/heic"))).toBe("image/heic");
  });

  it("knows which files can be previewed as images", () => {
    expect(isImageFile(file("a.png", 1, "image/png"))).toBe(true);
    expect(isImageFile(file("a.pdf", 1, "application/pdf"))).toBe(false);
    expect(isImageFile(file("a.txt"))).toBe(false);
  });
});

describe("validateFiles", () => {
  it("accepts JPEG, PNG, WebP and PDF files", () => {
    const { accepted, rejected } = validateFiles(
      [],
      [
        file("a.jpg", 10, "image/jpeg"),
        file("b.png", 10, "image/png"),
        file("c.webp", 10, "image/webp"),
        file("d.pdf", 10, "application/pdf"),
      ],
    );
    expect(accepted).toHaveLength(4);
    expect(rejected).toEqual([]);
  });

  it("rejects other types with a friendly reason", () => {
    const { accepted, rejected } = validateFiles(
      [],
      [file("notes.docx", 10, "application/msword")],
    );
    expect(accepted).toEqual([]);
    expect(rejected[0]).toMatchObject({ name: "notes.docx", reason: "type" });
    expect(rejected[0].message).toMatch(/JPEG, PNG, WebP and PDF/);
  });

  it("explains HEIC photos specifically", () => {
    const { rejected } = validateFiles(
      [],
      [file("IMG_1.HEIC", 10, "image/heic"), file("IMG_2.heif")],
    );
    expect(rejected).toHaveLength(2);
    for (const r of rejected) expect(r.message).toMatch(/HEIC photos aren't supported/);
  });

  it("rejects files over 15 MB, and accepts exactly 15 MB", () => {
    const { accepted, rejected } = validateFiles(
      [],
      [
        file("big.png", DEFAULT_LIMITS.maxFileMb * 1024 * 1024 + 1, "image/png"),
        file("edge.png", DEFAULT_LIMITS.maxFileMb * 1024 * 1024, "image/png", 2),
      ],
    );
    expect(accepted.map((f) => f.name)).toEqual(["edge.png"]);
    expect(rejected[0]).toMatchObject({ name: "big.png", reason: "size" });
    expect(rejected[0].message).toMatch(/over the 15 MB limit/);
  });

  it("skips files that were already added", () => {
    const first = file("a.png", 10, "image/png", 5);
    const again = file("a.png", 10, "image/png", 5);
    const { accepted, rejected } = validateFiles([first], [again, again]);
    expect(accepted).toEqual([]);
    expect(rejected.map((r) => r.reason)).toEqual(["duplicate", "duplicate"]);
    expect(fileKey(first)).toBe(fileKey(again));
  });

  it("caps the pile at 30 receipts and says which ones were left out", () => {
    const current = Array.from({ length: 28 }, (_, i) => file(`c${i}.png`, 10, "image/png", i));
    const incoming = Array.from({ length: 5 }, (_, i) =>
      file(`n${i}.png`, 10, "image/png", 100 + i),
    );
    const { accepted, rejected } = validateFiles(current, incoming);
    expect(accepted).toHaveLength(2);
    expect(rejected).toHaveLength(3);
    expect(rejected.every((r) => r.reason === "limit")).toBe(true);
    expect(rejected[0].message).toMatch(/30 receipts is the most/);
  });

  it("summarises rejections in one sentence for the live region", () => {
    expect(summarizeRejections([])).toBe("");
    const { rejected } = validateFiles([], [file("a.txt"), file("b.txt")]);
    const summary = summarizeRejections(rejected);
    expect(summary).toMatch(/^Skipped 2 files\./);
    expect(summary).toContain("a.txt");
    expect(summary).toContain("b.txt");
  });

  it("adds up sizes", () => {
    expect(totalBytes([file("a", 100), file("b", 250)])).toBe(350);
  });
});
