import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { createElement } from "react";
import { afterAll, afterEach, beforeAll, vi } from "vitest";

import { setStartedOverNotice } from "@/lib/demo/notice";
import { clearToasts } from "@/lib/toast";

import { navigationMock, navigationState, router } from "./navigation";
import { server } from "./server";

// No App Router in jsdom: next/navigation and next/link get light stand-ins for every test file.
vi.mock("next/navigation", () => navigationMock);
vi.mock("next/link", () => ({
  // eslint-disable-next-line @typescript-eslint/no-unused-vars -- `prefetch` must not reach the DOM
  default: ({ href, children, prefetch: _prefetch, ...rest }: Record<string, unknown>) =>
    createElement(
      "a",
      { href: typeof href === "string" ? href : String(href), ...rest },
      children as never,
    ),
}));

// No real network in unit tests: anything MSW has no handler for fails loudly.
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));

afterEach(() => {
  cleanup();
  for (const fn of Object.values(router)) fn.mockClear();
  navigationState.pathname = "/";
  server.resetHandlers();
  window.localStorage.clear();
  window.sessionStorage.clear();
  setStartedOverNotice(null);
  clearToasts();
  vi.restoreAllMocks();
});

afterAll(() => server.close());

// jsdom has no layout engine: stub the few browser APIs the UI touches.
if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  })) as typeof window.matchMedia;
}
if (!URL.createObjectURL) {
  URL.createObjectURL = () => "blob:mock";
  URL.revokeObjectURL = () => undefined;
}
