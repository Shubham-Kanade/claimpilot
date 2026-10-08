import { describe, expect, it } from "vitest";

import { ApiError } from "./client";
import { friendlyError, needsPersona } from "./problems";

const err = (status: number, type: string, title = "title", detail?: Record<string, unknown>) =>
  new ApiError(status, { type, title, status, ...(detail ? { detail } : {}) });

describe("friendlyError", () => {
  it("maps every documented problem type to friendly copy", () => {
    const types = [
      "network_error",
      "missing_persona",
      "unknown_persona",
      "approver_only",
      "not_your_claim",
      "batch_not_found",
      "claim_not_found",
      "document_not_found",
      "file_unavailable",
      "no_files",
      "too_many_files",
      "claim_locked",
      "claim_not_submitted",
      "confirmation_required",
      "unknown_question",
      "blank_answer",
      "comment_required",
      "demo_disabled",
      "validation_error",
    ];
    for (const type of types) {
      const friendly = friendlyError(err(422, type));
      expect(friendly.type).toBe(type);
      expect(friendly.title.length).toBeGreaterThan(3);
      expect(friendly.message.length).toBeGreaterThan(10);
      // Never leaks machine codes or HTTP jargon to the user.
      expect(`${friendly.title} ${friendly.message}`).not.toMatch(/_|HTTP|status \d/);
    }
  });

  it("asks for a reason when a rejection has no comment (comment_required)", () => {
    const friendly = friendlyError(err(422, "comment_required", "Say why the claim is rejected"));
    expect(friendly.title).toBe("Add a reason");
    expect(friendly.message).toMatch(/rejecting/i);
    expect(friendly.kind).toBe("input");
  });

  it("explains an unreachable server and offers a retry", () => {
    const friendly = friendlyError(
      new ApiError(0, { type: "network_error", title: "x", status: 0 }),
    );
    expect(friendly.kind).toBe("network");
    expect(friendly.action).toBe("retry");
  });

  it("asks people to pick a persona on 401", () => {
    const friendly = friendlyError(err(401, "unknown_persona"));
    expect(friendly.kind).toBe("auth");
    expect(friendly.action).toBe("pick-persona");
    expect(needsPersona(err(401, "missing_persona"))).toBe(true);
    expect(needsPersona(err(403, "approver_only"))).toBe(false);
    expect(needsPersona(new Error("x"))).toBe(false);
  });

  it("names the unsupported files from the problem detail", () => {
    const friendly = friendlyError(
      err(422, "unsupported_files", "Some files are not JPEG", {
        files: [
          { filename: "notes.docx", reason: "x" },
          { filename: "scan.heic", reason: "y" },
        ],
      }),
    );
    expect(friendly.message).toContain("notes.docx, scan.heic");
    expect(friendly.message).toContain("are not");
  });

  it("handles a single unsupported file and a missing file list", () => {
    expect(
      friendlyError(err(422, "unsupported_files", "t", { files: [{ filename: "a.txt" }] })).message,
    ).toContain("a.txt is not");
    expect(friendlyError(err(422, "unsupported_files")).message).toContain("Some files are not");
  });

  it("includes the offending file name for file_too_large", () => {
    const friendly = friendlyError(err(413, "file_too_large", "big.png is larger than 15 MB"));
    expect(friendly.message).toContain("big.png is larger than 15 MB");
  });

  it("says how many questions are open for claim_not_ready", () => {
    expect(friendlyError(err(409, "claim_not_ready", "t", { unanswered: ["a"] })).message).toMatch(
      /One question is still open/,
    );
    expect(
      friendlyError(err(409, "claim_not_ready", "t", { unanswered: ["a", "b"] })).message,
    ).toMatch(/2 questions are still open/);
    expect(friendlyError(err(409, "claim_not_ready")).message).toMatch(/needs a detail/);
  });

  it("falls back by HTTP status for unknown problem types", () => {
    expect(friendlyError(err(404, "something_new")).kind).toBe("not-found");
    expect(friendlyError(err(403, "something_new")).kind).toBe("forbidden");
    expect(friendlyError(err(413, "something_new")).kind).toBe("input");
    expect(friendlyError(err(429, "something_new")).action).toBe("retry");
    expect(friendlyError(err(500, "boom")).action).toBe("retry");
  });

  it("uses the problem title for unknown, non-server errors", () => {
    const friendly = friendlyError(err(418, "teapot", "I am a teapot"));
    expect(friendly.title).toBe("I am a teapot");
  });

  it("treats non-API errors as a generic server problem", () => {
    const friendly = friendlyError(new TypeError("x"));
    expect(friendly.kind).toBe("server");
    expect(friendly.type).toBe("unexpected");
  });
});
