import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { server } from "@/test/server";

import {
  loadSampleFiles,
  SAMPLE_PERSONA_ID,
  SAMPLE_RECEIPTS,
  SAMPLES_BASE_PATH,
  type SampleReceipt,
} from "./samples";

const here = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(here, "../../..");
const SAMPLES_DIR = path.join(webRoot, "public", "samples");
/** The pile the demo's recordings were made from (repository root, next to apps/). */
const DEMO_DIR = path.resolve(webRoot, "../../data/synth/demo");

const sha256 = (file: string): string =>
  createHash("sha256").update(readFileSync(file)).digest("hex");

/** The bytes inside a File (read with FileReader: jsdom has no Blob.arrayBuffer in every version). */
function bytesOf(file: File): Promise<Uint8Array> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(file);
  });
}

const MIME_BY_EXTENSION: Record<string, string> = {
  ".jpg": "image/jpeg",
  ".png": "image/png",
  ".pdf": "application/pdf",
};

const EXPECTED_NAMES = [
  "01-client-dinner-saffron-terrace.jpg",
  "02-train-pune-to-mumbai.jpg",
  "03-cab-mumbai-station-to-hotel.png",
  "04-dinner-mumbai-9-oct.jpg",
  "05-hotel-folio-mumbai.pdf",
  "06-train-mumbai-to-pune.jpg",
  "07-cab-pune-to-kestrel-office.jpg",
  "08-cab-pune-hinjewadi-to-kothrud.png",
  "09-auto-slip-pune.jpg",
  "10-mobile-bill-october.pdf",
  "11-upi-payment-120.png",
  "12-client-dinner-saffron-terrace-copy.jpg",
  "13-cab-pune-shivajinagar-to-baner.jpg",
  "14-cafe-bill-banyan-pune.jpg",
  "15-dinner-mumbai-10-oct.jpg",
];

describe("the sample pack (Asha Menon's week)", () => {
  it("is the 15 demo documents, in upload (file name) order", () => {
    expect(SAMPLE_RECEIPTS.map((s) => s.file)).toEqual(EXPECTED_NAMES);
    expect(EXPECTED_NAMES).toEqual([...EXPECTED_NAMES].sort());
    expect(SAMPLE_PERSONA_ID).toBe("DEMO-ASHA");
  });

  it("gives every file a short label and the media type its extension says", () => {
    for (const sample of SAMPLE_RECEIPTS) {
      expect(sample.label.length).toBeGreaterThan(10);
      expect(sample.label.length).toBeLessThanOrEqual(60);
      expect(sample.type).toBe(MIME_BY_EXTENSION[path.extname(sample.file)]);
    }
    expect(new Set(SAMPLE_RECEIPTS.map((s) => s.label)).size).toBe(SAMPLE_RECEIPTS.length);
    expect(SAMPLE_RECEIPTS.filter((s) => s.type === "application/pdf")).toHaveLength(2);
  });

  it("ships exactly these files in public/samples, and nothing else", () => {
    expect(readdirSync(SAMPLES_DIR).sort()).toEqual(EXPECTED_NAMES);
  });

  // The hosted demo replays recorded answers keyed by the bytes of each file: a re-encoded photo
  // is a different document and would not replay. The originals live in data/synth/demo/docs.
  describe.skipIf(!existsSync(path.join(DEMO_DIR, "manifest.jsonl")))(
    "against data/synth/demo",
    () => {
      it("is byte-for-byte what the recordings were made from (sha256)", () => {
        for (const sample of SAMPLE_RECEIPTS) {
          const original = path.join(DEMO_DIR, "docs", sample.file);
          expect(existsSync(original), `${sample.file} is missing from data/synth/demo/docs`).toBe(
            true,
          );
          expect(sha256(path.join(SAMPLES_DIR, sample.file)), sample.file).toBe(sha256(original));
        }
      });

      it("lists the documents in the same order as the demo manifest", () => {
        const manifest = readFileSync(path.join(DEMO_DIR, "manifest.jsonl"), "utf8")
          .split("\n")
          .filter(Boolean)
          .map((line) => path.basename((JSON.parse(line) as { path: string }).path));
        expect(SAMPLE_RECEIPTS.map((s) => s.file)).toEqual(manifest);
      });
    },
  );
});

describe("loadSampleFiles", () => {
  const fetchFrom = (input: RequestInfo | URL, init?: RequestInit) =>
    fetch(new URL(String(input), "http://localhost:3000"), init);

  it("downloads every sample from /samples and wraps it as an uploadable File", async () => {
    const requested: string[] = [];
    server.use(
      http.get("http://localhost:3000/samples/:file", ({ params }) => {
        requested.push(String(params.file));
        return new HttpResponse(new Uint8Array([1, 2, 3]), {
          headers: { "Content-Type": "image/png" },
        });
      }),
    );
    const files = await loadSampleFiles(SAMPLE_RECEIPTS.slice(0, 3), fetchFrom);
    expect(files).toHaveLength(3);
    expect(requested).toEqual(SAMPLE_RECEIPTS.slice(0, 3).map((s) => s.file));
    expect(files.map((f) => f.name)).toEqual(SAMPLE_RECEIPTS.slice(0, 3).map((s) => s.file));
    expect(files[0].type).toBe("image/jpeg");
    expect(files[2].type).toBe("image/png");
  });

  it("keeps the bytes exactly as served (no re-encoding)", async () => {
    const bytes = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0, 16, 74, 70, 73, 70, 0, 1, 1]);
    server.use(
      http.get(`http://localhost:3000${SAMPLES_BASE_PATH}/:file`, () => new HttpResponse(bytes)),
    );
    const [file] = await loadSampleFiles(SAMPLE_RECEIPTS.slice(0, 1), fetchFrom);
    expect(await bytesOf(file)).toEqual(bytes);
  });

  it("uploads in the order of the pack (the second copy of a bill is the one flagged)", async () => {
    server.use(
      http.get("http://localhost:3000/samples/:file", () => new HttpResponse(new Uint8Array([9]))),
    );
    const files = await loadSampleFiles(SAMPLE_RECEIPTS, fetchFrom);
    expect(files.map((f) => f.name)).toEqual(EXPECTED_NAMES);
  });

  it("fails clearly when a sample is missing", async () => {
    server.use(
      http.get(
        "http://localhost:3000/samples/:file",
        () => new HttpResponse(null, { status: 404 }),
      ),
    );
    await expect(loadSampleFiles(SAMPLE_RECEIPTS.slice(0, 1), fetchFrom)).rejects.toThrow(
      /Could not load sample 01-client-dinner-saffron-terrace\.jpg \(404\)/,
    );
  });

  it("is typed so a pack can be given explicitly", async () => {
    const custom: SampleReceipt[] = [
      { file: "x.png", type: "image/png", label: "A custom sample" },
    ];
    server.use(
      http.get("http://localhost:3000/samples/x.png", () => new HttpResponse(new Uint8Array([7]))),
    );
    const [file] = await loadSampleFiles(custom, fetchFrom);
    expect(file.name).toBe("x.png");
    expect(file.size).toBe(1);
  });
});
