import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderOptions } from "@testing-library/react";
import type { RequestHandler } from "msw";
import type { ReactElement, ReactNode } from "react";

import { createApi } from "@/lib/api/client";
import {
  PersonaContext,
  PersonaProvider,
  type PersonaContextValue,
} from "@/lib/persona/PersonaProvider";
import { setStoredPersona } from "@/lib/persona/store";

import { ASHA, EMPLOYEES } from "./fixtures";
import { baseHandlers, server } from "./server";

export function makeTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } },
  });
}

interface Options extends Omit<RenderOptions, "wrapper"> {
  /** Employee id to act as (saved like the persona switcher would); null saves nothing. */
  persona?: string | null;
  approverIds?: readonly string[];
  /** Extra MSW handlers; they take precedence over the base directory/meta/stats handlers. */
  handlers?: RequestHandler[];
}

/**
 * Renders with the REAL providers (React Query + persona directory) against MSW: the directory,
 * roles, meta and stats endpoints are mocked, everything else a test needs is added with
 * `server.use(...)` before calling this.
 */
export function renderApp(ui: ReactElement, options: Options = {}) {
  const { persona = ASHA.id, approverIds, handlers = [], ...rest } = options;
  server.use(...handlers, ...baseHandlers(approverIds));
  if (persona !== null) setStoredPersona(persona);
  const client = makeTestQueryClient();
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <PersonaProvider>{children}</PersonaProvider>
      </QueryClientProvider>
    );
  }
  return { client, ...render(ui, { wrapper: Wrapper, ...rest }) };
}

/** A fake persona (no network) for hooks and components that only need `usePersona()`. */
export function fakePersona(overrides: Partial<PersonaContextValue> = {}): PersonaContextValue {
  return {
    status: "ready",
    error: null,
    employees: EMPLOYEES,
    personaId: ASHA.id,
    persona: ASHA,
    isApprover: false,
    meLoading: false,
    approverIds: new Set<string>(),
    setPersonaId: () => undefined,
    api: createApi({ persona: ASHA.id }),
    reload: () => undefined,
    ...overrides,
  };
}

export function renderWithPersona(
  ui: ReactElement,
  persona: Partial<PersonaContextValue> = {},
  options: Omit<RenderOptions, "wrapper"> = {},
) {
  const client = makeTestQueryClient();
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <PersonaContext.Provider value={fakePersona(persona)}>{children}</PersonaContext.Provider>
      </QueryClientProvider>
    );
  }
  return { client, ...render(ui, { wrapper: Wrapper, ...options }) };
}
