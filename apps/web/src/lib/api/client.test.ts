import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { API_BASE_URL, ApiError, api, type MetaInfo } from "./client";

const meta: MetaInfo = {
  llm_mode: "replay",
  decision_engine: "llm",
  routes: [
    {
      route: "extraction",
      model_key: "haiku",
      model_id: "claude-haiku-4-5",
      effort: null,
      overridden: false,
    },
  ],
};

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("api client", () => {
  it("returns typed meta info", async () => {
    server.use(http.get(`${API_BASE_URL}/v1/meta`, () => HttpResponse.json(meta)));
    await expect(api.meta()).resolves.toEqual(meta);
  });

  it("throws ApiError with status on failure", async () => {
    server.use(
      http.get(`${API_BASE_URL}/v1/meta`, () =>
        HttpResponse.json({ title: "unavailable" }, { status: 503 }),
      ),
    );
    await expect(api.meta()).rejects.toBeInstanceOf(ApiError);
    await expect(api.meta()).rejects.toMatchObject({ status: 503 });
  });
});
