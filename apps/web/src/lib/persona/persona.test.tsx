import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { AppHeader } from "@/components/layout/AppHeader";
import { PersonaSwitcher } from "@/components/layout/PersonaSwitcher";
import { useClaims } from "@/lib/hooks/queries";
import { ADVIKA, ASHA, EMPLOYEES, RAVI, makeClaim } from "@/test/fixtures";
import { navigationState } from "@/test/navigation";
import { renderApp } from "@/test/render";
import { problem, server, url } from "@/test/server";

import { usePersona } from "./PersonaProvider";
import {
  getServerPersona,
  getStoredPersona,
  PERSONA_STORAGE_KEY,
  setStoredPersona,
  subscribePersona,
} from "./store";

function Probe() {
  const p = usePersona();
  return (
    <div>
      <p data-testid="status">{p.status}</p>
      <p data-testid="id">{p.personaId ?? "none"}</p>
      <p data-testid="role">{p.meLoading ? "loading" : p.isApprover ? "approver" : "employee"}</p>
      <p data-testid="api">{p.api.persona ?? "none"}</p>
      <button onClick={() => p.setPersonaId("P001")}>pick advika</button>
      <button onClick={() => p.reload()}>reload</button>
    </div>
  );
}

describe("persona store", () => {
  it("reads and writes localStorage and notifies subscribers", () => {
    const listener = vi.fn();
    const unsubscribe = subscribePersona(listener);
    expect(getStoredPersona()).toBeNull();
    setStoredPersona("P001");
    expect(window.localStorage.getItem(PERSONA_STORAGE_KEY)).toBe("P001");
    expect(getStoredPersona()).toBe("P001");
    expect(listener).toHaveBeenCalledTimes(1);

    // another tab changing the choice also notifies
    window.dispatchEvent(new StorageEvent("storage", { key: PERSONA_STORAGE_KEY }));
    expect(listener).toHaveBeenCalledTimes(2);
    window.dispatchEvent(new StorageEvent("storage", { key: "something-else" }));
    expect(listener).toHaveBeenCalledTimes(2);

    setStoredPersona(null);
    expect(getStoredPersona()).toBeNull();
    unsubscribe();
    setStoredPersona("P005");
    expect(listener).toHaveBeenCalledTimes(3); // no call after unsubscribe
  });

  it("has no persona on the server", () => {
    expect(getServerPersona()).toBeNull();
  });

  it("survives blocked storage (private mode)", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });
    expect(getStoredPersona()).toBeNull();
    expect(() => setStoredPersona("P001")).not.toThrow();
  });
});

describe("PersonaProvider", () => {
  it("a first-time visitor acts as Asha Menon (DEMO-ASHA)", async () => {
    renderApp(<Probe />, { persona: null });
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("ready"));
    expect(screen.getByTestId("id")).toHaveTextContent(ASHA.id);
    expect(screen.getByTestId("api")).toHaveTextContent(ASHA.id);
    await waitFor(() => expect(screen.getByTestId("role")).toHaveTextContent("employee"));
  });

  it("skips approvers when choosing the default", async () => {
    renderApp(<Probe />, { persona: null, approverIds: [ASHA.id, RAVI.id] });
    await waitFor(() => expect(screen.getByTestId("id")).toHaveTextContent(ADVIKA.id));
  });

  it("uses the saved persona and learns its role from /v1/me", async () => {
    renderApp(<Probe />, { persona: RAVI.id });
    await waitFor(() => expect(screen.getByTestId("id")).toHaveTextContent(RAVI.id));
    await waitFor(() => expect(screen.getByTestId("role")).toHaveTextContent("approver"));
  });

  it("ignores a saved persona that no longer exists", async () => {
    renderApp(<Probe />, { persona: "GHOST" });
    await waitFor(() => expect(screen.getByTestId("id")).toHaveTextContent(ASHA.id));
  });

  it("switches persona, persists the choice and re-targets the API client", async () => {
    const user = userEvent.setup();
    renderApp(<Probe />, { persona: ASHA.id });
    await waitFor(() => expect(screen.getByTestId("id")).toHaveTextContent(ASHA.id));
    await user.click(screen.getByRole("button", { name: "pick advika" }));
    await waitFor(() => expect(screen.getByTestId("id")).toHaveTextContent("P001"));
    expect(screen.getByTestId("api")).toHaveTextContent("P001");
    expect(window.localStorage.getItem(PERSONA_STORAGE_KEY)).toBe("P001");
  });

  it("reports an error, and recovers when the directory comes back", async () => {
    const user = userEvent.setup();
    renderApp(<Probe />, {
      persona: null,
      handlers: [http.get(url("/v1/employees"), () => problem(503, "http_503", "down"))],
    });
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("error"));
    server.use(http.get(url("/v1/employees"), () => HttpResponse.json(EMPLOYEES)));
    await user.click(screen.getByRole("button", { name: "reload" }));
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("ready"));
    expect(screen.getByTestId("id")).toHaveTextContent(ASHA.id);
  });

  it("refetches persona-scoped data when the persona changes (queries are keyed by persona)", async () => {
    const user = userEvent.setup();
    const seen: Array<string | null> = [];
    server.use(
      http.get(url("/v1/claims"), ({ request }) => {
        const persona = request.headers.get("X-Persona");
        seen.push(persona);
        return HttpResponse.json([
          makeClaim({
            id: `claim-of-${persona}`,
            title: `Claim of ${persona}`,
            employee_id: persona ?? "",
          }),
        ]);
      }),
    );
    function Claims() {
      const claims = useClaims();
      const { setPersonaId } = usePersona();
      return (
        <div>
          <button onClick={() => setPersonaId("P001")}>switch</button>
          <ul>
            {claims.data?.map((c) => (
              <li key={c.id}>{c.title}</li>
            ))}
          </ul>
        </div>
      );
    }
    renderApp(<Claims />, { persona: ASHA.id });
    expect(await screen.findByText("Claim of DEMO-ASHA")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "switch" }));
    expect(await screen.findByText("Claim of P001")).toBeInTheDocument();
    expect(screen.queryByText("Claim of DEMO-ASHA")).not.toBeInTheDocument();
    expect(seen).toEqual(["DEMO-ASHA", "P001"]);
  });
});

describe("PersonaSwitcher", () => {
  it("lists the DEMO-* personas first, the synthetic employees after, and marks approvers", async () => {
    renderApp(<PersonaSwitcher />, { persona: ASHA.id });
    const select = await screen.findByRole("combobox", { name: "Acting as" });
    await waitFor(() => expect(select).toHaveValue(ASHA.id));
    await waitFor(() =>
      expect(screen.getByRole("option", { name: "Ravi Iyer · L5 · approver" })).toBeInTheDocument(),
    );
    const groups = screen.getAllByRole("group");
    expect(groups.map((g) => g.getAttribute("label"))).toEqual([
      "Demo personas",
      "More synthetic employees",
    ]);
    expect(groups[0]).toHaveTextContent("Asha Menon · L3 · Pune");
    expect(groups[0]).toHaveTextContent("Ravi Iyer · L5 · approver");
    expect(groups[1]).toHaveTextContent("Advika Hayer · L4 · Hyderabad");
    // the very first option is a demo persona
    expect(screen.getAllByRole("option")[0]).toHaveTextContent("Asha Menon");
    expect(EMPLOYEES).toHaveLength(3);
  });

  it("changes the acting persona and remembers it", async () => {
    const user = userEvent.setup();
    renderApp(<PersonaSwitcher />, { persona: ASHA.id });
    const select = await screen.findByRole("combobox", { name: "Acting as" });
    await waitFor(() => expect(select).toHaveValue(ASHA.id));
    await user.selectOptions(select, RAVI.id);
    await waitFor(() => expect(select).toHaveValue(RAVI.id));
    expect(window.localStorage.getItem(PERSONA_STORAGE_KEY)).toBe(RAVI.id);
  });

  it("says so when the API is unreachable", async () => {
    renderApp(<PersonaSwitcher />, {
      handlers: [http.get(url("/v1/employees"), () => problem(503, "http_503", "down"))],
    });
    expect(await screen.findByText("API unreachable")).toBeInTheDocument();
  });
});

describe("AppHeader navigation", () => {
  it("shows Upload, My claims and Impact to employees and marks the current page", async () => {
    navigationState.pathname = "/claims/clm-1";
    renderApp(<AppHeader />, { persona: ASHA.id });
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: "Acting as" })).toHaveValue(ASHA.id),
    );
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(nav).toHaveTextContent("Upload");
    expect(screen.getByRole("link", { name: "My claims" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Impact" })).not.toHaveAttribute("aria-current");
    expect(screen.queryByRole("link", { name: "Approvals" })).not.toBeInTheDocument();
  });

  it("adds Approvals for approvers", async () => {
    renderApp(<AppHeader />, { persona: RAVI.id });
    expect(await screen.findByRole("link", { name: "Approvals" })).toHaveAttribute(
      "href",
      "/approvals",
    );
  });

  it("links the logo home", async () => {
    renderApp(<AppHeader />);
    expect(screen.getByRole("link", { name: "ClaimPilot home" })).toHaveAttribute("href", "/");
    await act(async () => undefined);
  });
});
