import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { META } from "@/test/fixtures";
import { problem, server, url } from "@/test/server";
import { streamOf } from "@/test/streams";

import { API_BASE_URL, ApiError, api, createApi, isApiError, resolveApiUrl } from "./client";
import { parseSse } from "./sse";

describe("api client", () => {
  it("returns typed meta info without a persona header", async () => {
    let persona: string | null = "unset";
    server.use(
      http.get(url("/v1/meta"), ({ request }) => {
        persona = request.headers.get("X-Persona");
        return HttpResponse.json(META);
      }),
    );
    await expect(api.meta()).resolves.toEqual(META);
    expect(persona).toBeNull();
  });

  it("uses the configured API base URL", () => {
    expect(API_BASE_URL).toBe("http://localhost:8000");
  });

  it("sends the acting persona as X-Persona on every call", async () => {
    let seen: string | null = null;
    server.use(
      http.get(url("/v1/claims"), ({ request }) => {
        seen = request.headers.get("X-Persona");
        return HttpResponse.json([]);
      }),
    );
    await createApi({ persona: "DEMO-ASHA" }).listClaims();
    expect(seen).toBe("DEMO-ASHA");
  });

  it("passes filters as query parameters and skips empty ones", async () => {
    let search = "";
    server.use(
      http.get(url("/v1/claims"), ({ request }) => {
        search = new URL(request.url).search;
        return HttpResponse.json([]);
      }),
    );
    await createApi({ persona: "P001" }).listClaims({ status: "ready", route: null });
    expect(search).toBe("?status=ready");
  });

  it("throws an ApiError carrying the problem+json type, title and detail", async () => {
    server.use(
      http.get(url("/v1/claims/c1"), () =>
        problem(409, "claim_not_ready", "The claim is not ready to submit", {
          status: "needs_info",
          unanswered: ["q1"],
        }),
      ),
    );
    const error = await createApi({ persona: "P001" })
      .getClaim("c1")
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(isApiError(error)).toBe(true);
    expect(error).toMatchObject({
      status: 409,
      type: "claim_not_ready",
      title: "The claim is not ready to submit",
      detail: { status: "needs_info", unanswered: ["q1"] },
    });
  });

  it("keeps working with plain (non problem+json) error bodies", async () => {
    server.use(
      http.get(url("/v1/meta"), () => HttpResponse.json({ title: "unavailable" }, { status: 503 })),
    );
    await expect(api.meta()).rejects.toBeInstanceOf(ApiError);
    await expect(api.meta()).rejects.toMatchObject({ status: 503, type: "http_503" });
  });

  it("maps FastAPI validation errors to a validation_error problem", async () => {
    server.use(
      http.get(url("/v1/stats"), () =>
        HttpResponse.json({ detail: [{ loc: ["header"], msg: "x", type: "y" }] }, { status: 422 }),
      ),
    );
    await expect(createApi({ persona: "P001" }).stats()).rejects.toMatchObject({
      status: 422,
      type: "validation_error",
    });
  });

  it("turns an unreachable server into a status-0 network_error", async () => {
    server.use(http.get(url("/v1/me"), () => HttpResponse.error()));
    await expect(createApi({ persona: "P001" }).me()).rejects.toMatchObject({
      status: 0,
      type: "network_error",
    });
  });

  it("re-throws aborts untouched so callers can ignore them", async () => {
    const controller = new AbortController();
    controller.abort();
    server.use(http.get(url("/v1/meta"), () => HttpResponse.json(META)));
    const error = await api.meta(controller.signal).catch((e: unknown) => e);
    expect(error).not.toBeInstanceOf(ApiError);
    expect((error as DOMException).name).toBe("AbortError");
  });

  it("uploads every file as multipart form data under the field name `files`", async () => {
    let parts = 0;
    server.use(
      http.post(url("/v1/batches"), async ({ request }) => {
        const form = await request.formData();
        parts = form.getAll("files").length;
        return HttpResponse.json(
          {
            batch_id: "bat-1",
            status: "queued",
            documents: [],
            events_url: "/v1/batches/bat-1/events",
          },
          { status: 202 },
        );
      }),
    );
    const created = await createApi({ persona: "P001" }).createBatch([
      new File(["a"], "a.png", { type: "image/png" }),
      new File(["b"], "b.pdf", { type: "application/pdf" }),
    ]);
    expect(created.batch_id).toBe("bat-1");
    expect(parts).toBe(2);
  });

  it("sends {confirmed: true} with the Idempotency-Key header on submit", async () => {
    let key: string | null = null;
    let body: unknown = null;
    server.use(
      http.post(url("/v1/claims/c1/submit"), async ({ request }) => {
        key = request.headers.get("Idempotency-Key");
        body = await request.json();
        return HttpResponse.json({ id: "c1" });
      }),
    );
    await createApi({ persona: "P001" }).submit("c1", "key-123");
    expect(key).toBe("key-123");
    expect(body).toEqual({ confirmed: true });
  });

  it("posts replies, answers and decisions as JSON", async () => {
    const bodies: Record<string, unknown> = {};
    server.use(
      http.post(url("/v1/claims/c1/reply"), async ({ request }) => {
        bodies.reply = await request.json();
        return HttpResponse.json({ claim: {}, understood: {}, follow_up: null });
      }),
      http.post(url("/v1/claims/c1/answers"), async ({ request }) => {
        bodies.answers = await request.json();
        return HttpResponse.json({});
      }),
      http.post(url("/v1/claims/c1/decision"), async ({ request }) => {
        bodies.decision = await request.json();
        return HttpResponse.json({});
      }),
    );
    const client = createApi({ persona: "DEMO-RAVI" });
    await client.reply("c1", "hello");
    await client.answer("c1", { q1: "a" });
    await client.decide("c1", { approved: false, comment: "no invoice" });
    await client.decide("c1", { approved: true });
    expect(bodies.reply).toEqual({ text: "hello" });
    expect(bodies.answers).toEqual({ answers: { q1: "a" } });
    expect(bodies.decision).toEqual({ approved: true, comment: "" });
  });

  it("fetches a document file as a Blob with its content type", async () => {
    server.use(
      http.get(
        url("/v1/documents/d1/file"),
        () =>
          new HttpResponse(new Uint8Array([137, 80, 78, 71]), {
            headers: { "Content-Type": "image/png" },
          }),
      ),
    );
    const file = await createApi({ persona: "P001" }).documentFile("d1");
    expect(file.contentType).toBe("image/png");
    expect(file.blob.size).toBe(4);
  });

  it("resumes the event stream from Last-Event-ID", async () => {
    let lastEventId: string | null = null;
    server.use(
      http.get(url("/v1/batches/b1/events"), ({ request }) => {
        lastEventId = request.headers.get("Last-Event-ID");
        return new HttpResponse("", { headers: { "Content-Type": "text/event-stream" } });
      }),
    );
    await createApi({ persona: "P001" }).batchEvents("b1", { lastEventId: "4" });
    expect(lastEventId).toBe("4");
  });

  it("requests approvals by status and batch history from an index", async () => {
    const searches: string[] = [];
    server.use(
      http.get(url("/v1/approvals"), ({ request }) => {
        searches.push(new URL(request.url).search);
        return HttpResponse.json([]);
      }),
      http.get(url("/v1/batches/b1/history"), ({ request }) => {
        searches.push(new URL(request.url).search);
        return HttpResponse.json([]);
      }),
    );
    const client = createApi({ persona: "DEMO-RAVI" });
    await client.approvals("rejected");
    await client.batchHistory("b1", 3);
    expect(searches).toEqual(["?status=rejected", "?after=3"]);
  });
});

describe("relative API base (hosted demo: one origin behind a reverse proxy)", () => {
  it("resolves absolute bases as they are, tolerating a trailing slash", () => {
    expect(resolveApiUrl("http://localhost:8000", "/v1/meta").href).toBe(
      "http://localhost:8000/v1/meta",
    );
    expect(resolveApiUrl("https://api.example.com/", "/v1/meta").href).toBe(
      "https://api.example.com/v1/meta",
    );
  });

  it("resolves a relative base against the page origin", () => {
    expect(resolveApiUrl("/api", "/v1/meta", "https://demo.example.com").href).toBe(
      "https://demo.example.com/api/v1/meta",
    );
    expect(resolveApiUrl("/api/", "/v1/claims", "https://demo.example.com:8443").href).toBe(
      "https://demo.example.com:8443/api/v1/claims",
    );
    // in the browser (jsdom) the origin comes from window.location
    expect(resolveApiUrl("/api", "/v1/meta").href).toBe("http://localhost:3000/api/v1/meta");
  });

  it("explains a relative base that cannot be resolved (no browser)", () => {
    vi.stubGlobal("window", undefined); // server-side rendering has no window
    try {
      expect(() => resolveApiUrl("/api", "/v1/meta")).toThrow(
        /relative and can only be used in a browser/,
      );
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("makes real requests through a relative base, with query strings and the persona header", async () => {
    let seen: { path: string; search: string; persona: string | null } | null = null;
    server.use(
      http.get("http://localhost:3000/api/v1/claims", ({ request }) => {
        const u = new URL(request.url);
        seen = { path: u.pathname, search: u.search, persona: request.headers.get("X-Persona") };
        return HttpResponse.json([]);
      }),
    );
    await createApi({ baseUrl: "/api", persona: "DEMO-ASHA" }).listClaims({ status: "ready" });
    expect(seen).toEqual({ path: "/api/v1/claims", search: "?status=ready", persona: "DEMO-ASHA" });
  });

  it("streams server-sent events through a relative base too", async () => {
    let lastEventId: string | null = null;
    server.use(
      http.get("http://localhost:3000/api/v1/batches/b1/events", ({ request }) => {
        lastEventId = request.headers.get("Last-Event-ID");
        return new HttpResponse(
          streamOf('id: 5\nevent: batch_done\ndata: {"type":"batch_done"}\n\n'),
          {
            headers: { "Content-Type": "text/event-stream" },
          },
        );
      }),
    );
    const response = await createApi({ baseUrl: "/api", persona: "P001" }).batchEvents("b1", {
      lastEventId: "4",
    });
    const frames: string[] = [];
    for await (const frame of parseSse(response.body!)) frames.push(`${frame.id}:${frame.event}`);
    expect(lastEventId).toBe("4");
    expect(frames).toEqual(["5:batch_done"]);
  });

  it("uploads files and posts JSON through a relative base", async () => {
    const calls: string[] = [];
    server.use(
      http.post("http://localhost:3000/api/v1/batches", () => {
        calls.push("batches");
        return HttpResponse.json(
          { batch_id: "b", status: "queued", documents: [], events_url: "/x" },
          { status: 202 },
        );
      }),
      http.post("http://localhost:3000/api/v1/demo/reset", () => {
        calls.push("reset");
        return HttpResponse.json({ batches: 1, documents: 2, claims: 3 });
      }),
    );
    const client = createApi({ baseUrl: "/api", persona: "P001" });
    await client.createBatch([new File(["x"], "a.png", { type: "image/png" })]);
    await expect(client.demoReset()).resolves.toEqual({ batches: 1, documents: 2, claims: 3 });
    expect(calls).toEqual(["batches", "reset"]);
  });
});

describe("demo reset", () => {
  it("posts to /v1/demo/reset with the acting persona", async () => {
    let persona: string | null = null;
    server.use(
      http.post(url("/v1/demo/reset"), ({ request }) => {
        persona = request.headers.get("X-Persona");
        return HttpResponse.json({ batches: 2, documents: 11, claims: 10 });
      }),
    );
    await expect(createApi({ persona: "DEMO-ASHA" }).demoReset()).resolves.toEqual({
      batches: 2,
      documents: 11,
      claims: 10,
    });
    expect(persona).toBe("DEMO-ASHA");
  });

  it("surfaces demo_disabled when the deployment is not the demo", async () => {
    server.use(
      http.post(url("/v1/demo/reset"), () =>
        problem(404, "demo_disabled", "Start over is for the demo only"),
      ),
    );
    await expect(createApi({ persona: "P001" }).demoReset()).rejects.toMatchObject({
      status: 404,
      type: "demo_disabled",
    });
  });
});

describe("X-Sandbox (one demo sandbox per visitor)", () => {
  const FORMAT = /^[A-Za-z0-9_-]{16,64}$/;

  /** Records the X-Sandbox / X-Persona headers of every request the handlers below see. */
  function record() {
    const seen: { path: string; sandbox: string | null; persona: string | null }[] = [];
    const note = (request: Request) => {
      seen.push({
        path: new URL(request.url).pathname,
        sandbox: request.headers.get("X-Sandbox"),
        persona: request.headers.get("X-Persona"),
      });
    };
    server.use(
      http.get(url("/v1/meta"), ({ request }) => (note(request), HttpResponse.json(META))),
      http.get(url("/v1/claims"), ({ request }) => (note(request), HttpResponse.json([]))),
      http.post(url("/v1/demo/reset"), ({ request }) => {
        note(request);
        return HttpResponse.json({ batches: 0, documents: 0, claims: 0 });
      }),
      http.post(url("/v1/batches"), ({ request }) => {
        note(request);
        return HttpResponse.json(
          { batch_id: "b", status: "queued", documents: [], events_url: "/e" },
          { status: 202 },
        );
      }),
      http.get(url("/v1/batches/b/events"), ({ request }) => {
        note(request);
        return new HttpResponse(streamOf(), { headers: { "Content-Type": "text/event-stream" } });
      }),
      http.get(url("/v1/documents/d/file"), ({ request }) => {
        note(request);
        return new HttpResponse(new Uint8Array([1]), { headers: { "Content-Type": "image/png" } });
      }),
    );
    return seen;
  }

  it("is sent on JSON, upload, SSE, blob and reset requests, and on the persona-less client", async () => {
    const seen = record();
    const asha = createApi({ persona: "DEMO-ASHA" });
    await asha.listClaims();
    await asha.createBatch([new File(["x"], "a.png", { type: "image/png" })]);
    await asha.batchEvents("b");
    await asha.documentFile("d");
    await asha.demoReset();
    await api.meta();
    expect(seen.map((s) => s.path)).toEqual([
      "/v1/claims",
      "/v1/batches",
      "/v1/batches/b/events",
      "/v1/documents/d/file",
      "/v1/demo/reset",
      "/v1/meta",
    ]);
    for (const s of seen) expect(s.sandbox, s.path).toMatch(FORMAT);
    // one visitor, one sandbox: the same id on every request
    expect(new Set(seen.map((s) => s.sandbox)).size).toBe(1);
  });

  it("is also sent by per-persona clients made on the fly (the persona override paths)", async () => {
    const seen = record();
    await createApi({ persona: "DEMO-ASHA" }).demoReset();
    await createApi({ persona: "DEMO-RAVI" }).listClaims();
    expect(seen.map((s) => s.persona)).toEqual(["DEMO-ASHA", "DEMO-RAVI"]);
    expect(seen[0].sandbox).toBe(seen[1].sandbox);
    expect(seen[0].sandbox).toMatch(FORMAT);
  });

  it("uses the id saved in localStorage, and keeps it across calls", async () => {
    window.localStorage.setItem("claimpilot.sandbox", "e2e-sandbox-00000000000001");
    const seen = record();
    const client = createApi({ persona: "DEMO-ASHA" });
    await client.listClaims();
    await client.listClaims();
    expect(seen.map((s) => s.sandbox)).toEqual([
      "e2e-sandbox-00000000000001",
      "e2e-sandbox-00000000000001",
    ]);
  });
});

describe("llmOps", () => {
  it("asks for the window and the trace, and leaves out what is not set", async () => {
    const asked: string[] = [];
    server.use(
      http.get(url("/v1/ops/llm"), ({ request }) => {
        asked.push(new URL(request.url).search);
        return HttpResponse.json({ hours: 24 });
      }),
    );
    const client = createApi({ persona: "DEMO-ASHA" });
    await client.llmOps();
    await client.llmOps({ hours: 168, traceId: "abc" });
    await client.llmOps({ hours: 1, traceId: null });
    expect(asked).toEqual(["", "?hours=168&trace_id=abc", "?hours=1"]);
  });
});
