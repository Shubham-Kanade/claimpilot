// @ts-check
/**
 * `POST /v1/batches`: a dependency-free multipart/form-data parser and the backend's upload
 * checks (api/batches.py), in the backend's order:
 *
 *   no `files` parts            -> 422 no_files
 *   more than 30 files          -> 413 too_many_files
 *   per file, in order          -> 413 file_too_large (first offender), or unsupported
 *   any unsupported file        -> 422 unsupported_files {files: [{filename, reason}]}
 *
 * The file type is sniffed from magic bytes (JPEG, PNG, WebP, PDF); the declared type is never
 * trusted. The parser streams: it keeps at most `maxFileBytes + 1` bytes of each of the first
 * `maxFiles` files (enough to know whether a file is too large), so a 450 MB upload never needs
 * 450 MB of memory to be refused.
 */

import { problem } from "./http.mjs";

/** @typedef {import("node:http").IncomingMessage} IncomingMessage */
/** @typedef {import("./pipeline.mjs").UploadedFile} UploadedFile */

/** The body is not a well-formed multipart/form-data message. */
export class MultipartError extends Error {
  /** @param {string} message */
  constructor(message) {
    super(message);
    this.name = "MultipartError";
  }
}

/**
 * @typedef {Object} FormFile
 * @property {string | null} filename  As sent (not yet sanitised).
 * @property {Buffer} data  At most `maxFileBytes + 1` bytes.
 * @property {boolean} tooLarge
 */

// --- headers of one part -----------------------------------------------------------------------------

/**
 * The `key=value` parameters of a header such as `form-data; name="files"; filename="a b.png"`.
 * Quoted values may contain `\"` and `\\`; other backslashes stay (old browsers sent paths).
 * @param {string} header
 * @returns {Record<string, string>}
 */
export function parseParams(header) {
  /** @type {Record<string, string>} */
  const out = {};
  let i = header.indexOf(";");
  while (i >= 0 && i < header.length) {
    i += 1;
    while (header[i] === " " || header[i] === "\t") i += 1;
    const eq = header.indexOf("=", i);
    if (eq < 0) break;
    const key = header.slice(i, eq).trim().toLowerCase();
    i = eq + 1;
    let value = "";
    if (header[i] === '"') {
      i += 1;
      while (i < header.length && header[i] !== '"') {
        const next = header[i + 1];
        if (header[i] === "\\" && (next === '"' || next === "\\")) {
          value += next;
          i += 2;
        } else {
          value += header[i];
          i += 1;
        }
      }
      i += 1; // the closing quote
    } else {
      const end = header.indexOf(";", i);
      const stop = end < 0 ? header.length : end;
      value = header.slice(i, stop).trim();
      i = stop;
    }
    out[key] = value;
    i = header.indexOf(";", i);
  }
  return out;
}

/**
 * @param {string} block  The header lines of one part.
 * @returns {{ name: string | null; filename: string | null }}
 */
function parsePartHeaders(block) {
  /** @type {Record<string, string>} */
  const headers = {};
  for (const line of block.split("\r\n")) {
    const colon = line.indexOf(":");
    if (colon > 0)
      headers[line.slice(0, colon).trim().toLowerCase()] = line.slice(colon + 1).trim();
  }
  const params = parseParams(headers["content-disposition"] ?? "");
  let filename = params.filename ?? null;
  const extended = params["filename*"];
  if (extended) {
    const match = /^[^']*'[^']*'(.*)$/.exec(extended);
    try {
      if (match) filename = decodeURIComponent(match[1] ?? "");
    } catch {
      // keep the plain filename
    }
  }
  return { name: params.name ?? null, filename };
}

// --- the streaming parser -------------------------------------------------------------------------------

/**
 * Read the `field` file parts of a multipart request.
 * @param {IncomingMessage} req
 * @param {{ field: string; maxFiles: number; maxFileBytes: number }} options
 * @returns {Promise<{ files: FormFile[]; count: number }>}  `count` includes files whose data was dropped.
 * @throws {MultipartError}
 */
export async function readUploads(req, { field, maxFiles, maxFileBytes }) {
  const contentType = String(req.headers["content-type"] ?? "");
  if (!/^multipart\/form-data\b/i.test(contentType)) throw new MultipartError("not multipart");
  const boundaryMatch = /boundary=(?:"([^"]+)"|([^;\s]+))/i.exec(contentType);
  const boundary = boundaryMatch?.[1] ?? boundaryMatch?.[2];
  if (!boundary) throw new MultipartError("missing boundary");

  const opening = Buffer.from(`--${boundary}`);
  const delimiter = Buffer.from(`\r\n--${boundary}`);
  const limit = maxFileBytes + 1;

  /** @type {FormFile[]} */
  const files = [];
  let count = 0;
  /** @type {"preamble" | "after" | "headers" | "body" | "done"} */
  let state = "preamble";
  let buf = Buffer.alloc(0);
  /** @type {{ file: FormFile | null; chunks: Buffer[]; kept: number } | null} */
  let part = null;

  /** @param {Buffer} data */
  const append = (data) => {
    if (!part?.file) return;
    if (part.kept < limit) {
      const slice = data.subarray(0, limit - part.kept);
      part.chunks.push(slice);
      part.kept += slice.length;
    }
    if (part.kept >= limit) part.file.tooLarge = true;
  };
  const finish = () => {
    if (part?.file) part.file.data = Buffer.concat(part.chunks);
    part = null;
  };

  for await (const chunk of req) {
    buf = buf.length ? Buffer.concat([buf, chunk]) : chunk;
    // Advance the state machine as far as the bytes so far allow.
    for (;;) {
      if (state === "preamble") {
        const at = buf.indexOf(opening);
        if (at < 0) {
          buf = buf.subarray(Math.max(0, buf.length - opening.length + 1));
          break;
        }
        buf = buf.subarray(at + opening.length);
        state = "after";
      }
      if (state === "after") {
        if (buf.length < 2) break;
        if (buf[0] === 0x2d && buf[1] === 0x2d) {
          state = "done";
          buf = Buffer.alloc(0);
          break;
        }
        const eol = buf.indexOf("\r\n");
        if (eol < 0) break;
        buf = buf.subarray(eol + 2);
        state = "headers";
      }
      if (state === "headers") {
        const end = buf.indexOf("\r\n\r\n");
        if (end < 0) {
          if (buf.length > 16 * 1024) throw new MultipartError("part headers too long");
          break;
        }
        const { name, filename } = parsePartHeaders(buf.subarray(0, end).toString("utf8"));
        buf = buf.subarray(end + 4);
        /** @type {FormFile | null} */
        let file = null;
        if (name === field && filename !== null) {
          count += 1;
          if (count <= maxFiles) {
            file = { filename, data: Buffer.alloc(0), tooLarge: false };
            files.push(file);
          }
        }
        part = { file, chunks: [], kept: 0 };
        state = "body";
      }
      if (state === "body") {
        const at = buf.indexOf(delimiter);
        if (at >= 0) {
          append(buf.subarray(0, at));
          finish();
          buf = buf.subarray(at + delimiter.length);
          state = "after";
          continue;
        }
        const safe = buf.length - (delimiter.length - 1);
        if (safe > 0) {
          append(buf.subarray(0, safe));
          buf = buf.subarray(safe);
        }
        break;
      }
      if (state === "done") break;
    }
  }
  if (state !== "done") throw new MultipartError("the multipart body ended early");
  return { files, count };
}

// --- the backend's checks ---------------------------------------------------------------------------------

/**
 * JPEG, PNG, WebP or PDF from the first bytes; null for anything else.
 * @param {Buffer} data
 * @returns {string | null}
 */
export function sniffMediaType(data) {
  if (data.length >= 3 && data[0] === 0xff && data[1] === 0xd8 && data[2] === 0xff) {
    return "image/jpeg";
  }
  if (
    data.length >= 4 &&
    data[0] === 0x89 &&
    data[1] === 0x50 &&
    data[2] === 0x4e &&
    data[3] === 0x47
  ) {
    return "image/png";
  }
  if (data.length >= 4 && data.toString("latin1", 0, 4) === "%PDF") return "application/pdf";
  if (
    data.length >= 12 &&
    data.toString("latin1", 0, 4) === "RIFF" &&
    data.toString("latin1", 8, 12) === "WEBP"
  ) {
    return "image/webp";
  }
  return null;
}

/**
 * The file name as stored: no directories, trimmed, at most 255 characters (`_safe_name`).
 * @param {string | null | undefined} raw
 * @param {number} index
 */
export function safeName(raw, index) {
  const parts = (raw ?? "").replaceAll("\\", "/").replace(/\/+$/, "").split("/");
  const name = (parts.at(-1) ?? "").trim();
  return (name || `receipt-${index + 1}`).slice(0, 255);
}

/**
 * @param {number} bytes
 */
function megabytes(bytes) {
  return String(Number((bytes / (1024 * 1024)).toFixed(3)));
}

/**
 * Apply the upload rules; returns the files to process or throws the problem to answer with.
 * @param {{ files: FormFile[]; count: number }} upload
 * @param {{ maxFiles: number; maxFileBytes: number }} limits
 * @returns {UploadedFile[]}
 */
export function validateUploads(upload, limits) {
  if (upload.count === 0) throw problem(422, "no_files", "Upload at least one file");
  if (upload.count > limits.maxFiles) {
    throw problem(413, "too_many_files", `At most ${limits.maxFiles} files per upload`);
  }
  /** @type {UploadedFile[]} */
  const accepted = [];
  /** @type {Array<{ filename: string; reason: string }>} */
  const rejected = [];
  upload.files.forEach((file, index) => {
    const filename = safeName(file.filename, index);
    if (file.tooLarge) {
      throw problem(
        413,
        "file_too_large",
        `${filename} is larger than ${megabytes(limits.maxFileBytes)} MB`,
      );
    }
    const mediaType = sniffMediaType(file.data);
    if (!mediaType) {
      rejected.push({
        filename,
        reason: "unsupported file type (expected JPEG, PNG, WebP or PDF)",
      });
      return;
    }
    accepted.push({ filename, bytes: file.data, mediaType });
  });
  if (rejected.length) {
    throw problem(422, "unsupported_files", "Some files are not JPEG, PNG, WebP or PDF", {
      files: rejected,
    });
  }
  return accepted;
}
