import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DemoBanner } from "@/components/layout/DemoBanner";
import { Toaster } from "@/components/layout/Toaster";
import { UploadScreen } from "@/components/upload/UploadScreen";
import { PersonaProvider, personaKeys } from "@/lib/persona/PersonaProvider";
import { setStoredPersona } from "@/lib/persona/store";
import { ASHA, EMPLOYEES, META_DEMO } from "@/test/fixtures";
import { makeTestQueryClient } from "@/test/render";
import { baseHandlers, server } from "@/test/server";

import { useHydrated } from "./hydration";
import { qk } from "./queries";

function App({ client }: { client: QueryClient }) {
  return (
    <QueryClientProvider client={client}>
      <PersonaProvider>
        <DemoBanner />
        <UploadScreen />
        <Toaster />
      </PersonaProvider>
    </QueryClientProvider>
  );
}

/** A client whose cache already holds the API's answers: they arrived before React hydrated. */
function warmClient(): QueryClient {
  const client = makeTestQueryClient();
  client.setQueryData(qk.meta, META_DEMO);
  client.setQueryData(personaKeys.employees, EMPLOYEES);
  for (const employee of EMPLOYEES) {
    client.setQueryData(personaKeys.me(employee.id), {
      employee,
      is_approver: employee.id === "DEMO-RAVI",
    });
  }
  return client;
}

let container: HTMLElement | null = null;
afterEach(() => {
  container?.remove();
  container = null;
});

async function hydrate(html: string, client: QueryClient) {
  container = document.createElement("div");
  container.innerHTML = html;
  document.body.appendChild(container);
  const recoverable: unknown[] = [];
  const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
  let root!: ReturnType<typeof hydrateRoot>;
  await act(async () => {
    root = hydrateRoot(container!, <App client={client} />, {
      onRecoverableError: (error) => recoverable.push(error),
    });
  });
  return { recoverable, consoleError, unmount: () => act(() => root.unmount()) };
}

describe("hydration: the first client render must equal the server HTML", () => {
  for (const [label, stored] of [
    ["a first-time visitor (no saved persona)", false],
    ["a returning visitor (persona saved)", true],
  ] as const) {
    it(`hydrates the upload screen cleanly for ${label}, with every answer already cached`, async () => {
      server.use(...baseHandlers());
      if (stored) setStoredPersona(ASHA.id);

      // what the server printed: nothing is known yet, so the page is in its "pending" state
      const html = renderToString(<App client={makeTestQueryClient()} />);
      expect(html).toContain("Try with sample receipts");
      expect(html).not.toContain("Each run starts clean"); // demo mode is not known yet
      expect(html).not.toContain("Demo:"); // so no banner either

      // the browser: the same page, but the API's answers arrived before this part hydrated
      const { recoverable, consoleError, unmount } = await hydrate(html, warmClient());
      expect(recoverable).toEqual([]);
      expect(consoleError).not.toHaveBeenCalled();

      // ... and as soon as it is hydrated, the answers show
      expect(await screen.findByText(/Each run starts clean/)).toBeInTheDocument();
      expect(screen.getByTestId("demo-banner")).toBeInTheDocument();
      await unmount();
    });
  }

  it("useHydrated is false while hydrating and true for any render after that", async () => {
    function Probe() {
      return <span>{useHydrated() ? "hydrated" : "server"}</span>;
    }
    expect(renderToString(<Probe />)).toContain("server");
    // a plain client render (not hydration), like a navigation inside the app, is hydrated at once
    render(<Probe />);
    expect(screen.getByText("hydrated")).toBeInTheDocument();

    const seen: string[] = [];
    function Spy() {
      const hydrated = useHydrated();
      seen.push(hydrated ? "hydrated" : "server");
      return <span>{hydrated ? "hydrated" : "server"}</span>;
    }
    container = document.createElement("div");
    container.innerHTML = renderToString(<Spy />);
    document.body.appendChild(container);
    await act(async () => {
      hydrateRoot(container!, <Spy />);
    });
    expect(seen[0]).toBe("server"); // the hydration render matches the server HTML
    expect(seen.at(-1)).toBe("hydrated"); // and the next one is the real thing
  });
});
