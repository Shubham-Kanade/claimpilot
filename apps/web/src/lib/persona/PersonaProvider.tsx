"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useSyncExternalStore,
  type ReactNode,
} from "react";

import { api as publicApi, createApi, type ApiClient, type ApiError } from "../api/client";
import { useApiQueries, useApiQuery } from "../hooks/hydration";
import type { Employee, Me } from "../api/types";
import { orderPersonas, pickDefaultPersona } from "./order";
import { getServerPersona, getStoredPersona, setStoredPersona, subscribePersona } from "./store";

export interface PersonaContextValue {
  /** `loading` until the directory (and, with no saved choice, each persona's role) is known. */
  status: "loading" | "ready" | "error";
  error: ApiError | null;
  employees: readonly Employee[];
  /** The persona the app is acting as; null only while loading or if the directory is empty. */
  personaId: string | null;
  persona: Employee | null;
  /** Role of the acting persona (from GET /v1/me); `meLoading` is true until it is known. */
  isApprover: boolean;
  meLoading: boolean;
  /** Which directory personas are approvers (known once their /v1/me calls have returned). */
  approverIds: ReadonlySet<string>;
  setPersonaId: (id: string) => void;
  /** An API client that sends the acting persona's X-Persona header. */
  api: ApiClient;
  reload: () => void;
}

/** Exported so tests can provide a fake persona without hitting the network. */
export const PersonaContext = createContext<PersonaContextValue | null>(null);

/** Public so tests and the app share one query key per persona. */
export const personaKeys = {
  employees: ["employees"] as const,
  me: (personaId: string) => ["me", personaId] as const,
};

export function PersonaProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const stored = useSyncExternalStore(subscribePersona, getStoredPersona, getServerPersona);

  const employeesQuery = useApiQuery({
    queryKey: personaKeys.employees,
    queryFn: ({ signal }) => publicApi.employees(signal),
    staleTime: 5 * 60_000,
  });
  // The demo personas (DEMO-*) come first, the golden-dataset personas after them.
  const employees = useMemo(() => orderPersonas(employeesQuery.data ?? []), [employeesQuery.data]);

  // Learn every persona's role once: it labels the switcher and, when the preferred default
  // persona is missing, picks the first non-approver. A handful of tiny requests, cached.
  const meQueries = useApiQueries<Me>(
    employees.map((employee) => ({
      queryKey: personaKeys.me(employee.id),
      queryFn: ({ signal }: { signal: AbortSignal }): Promise<Me> =>
        createApi({ persona: employee.id }).me(signal),
      staleTime: 5 * 60_000,
      retry: false,
    })),
  );

  const approverIds = useMemo(() => {
    const ids = new Set<string>();
    meQueries.forEach((q, i) => {
      if (q.data?.is_approver) ids.add(employees[i].id);
    });
    return ids;
  }, [meQueries, employees]);

  const storedIsValid = stored !== null && employees.some((e) => e.id === stored);
  const rolesSettled = employees.length > 0 && meQueries.every((q) => !q.isPending);

  // A saved choice wins; a first-time visitor acts as DEMO-ASHA (else the first non-approver).
  const personaId: string | null = storedIsValid
    ? stored
    : pickDefaultPersona(employees, rolesSettled ? approverIds : null);

  const persona = employees.find((e) => e.id === personaId) ?? null;
  const index = personaId ? employees.findIndex((e) => e.id === personaId) : -1;
  const meQuery = index >= 0 ? meQueries[index] : undefined;
  const isApprover = meQuery?.data?.is_approver ?? false;

  const setPersonaId = useCallback(
    (id: string) => {
      setStoredPersona(id);
      // Nothing from the previous persona may linger on screen: queries are keyed by persona,
      // and the cached data of the others is dropped so it can never be shown by mistake.
      queryClient.removeQueries({
        predicate: (query) => query.queryKey[0] === "p" && query.queryKey[1] !== id,
      });
    },
    [queryClient],
  );

  const api = useMemo(() => createApi({ persona: personaId }), [personaId]);

  const status: PersonaContextValue["status"] = employeesQuery.isError
    ? "error"
    : personaId
      ? "ready"
      : employeesQuery.isPending || employees.length > 0
        ? "loading"
        : "error";

  const reload = useCallback(() => {
    void employeesQuery.refetch();
  }, [employeesQuery]);

  const value = useMemo<PersonaContextValue>(
    () => ({
      status,
      error: (employeesQuery.error as ApiError | null) ?? null,
      employees,
      personaId,
      persona,
      isApprover,
      meLoading: !meQuery || meQuery.isPending,
      approverIds,
      setPersonaId,
      api,
      reload,
    }),
    [
      status,
      employeesQuery.error,
      employees,
      personaId,
      persona,
      isApprover,
      meQuery,
      approverIds,
      setPersonaId,
      api,
      reload,
    ],
  );

  return <PersonaContext.Provider value={value}>{children}</PersonaContext.Provider>;
}

export function usePersona(): PersonaContextValue {
  const value = useContext(PersonaContext);
  if (!value) throw new Error("usePersona must be used inside <PersonaProvider>");
  return value;
}

/** The persona-bound API client (shortcut for `usePersona().api`). */
export function useApi(): ApiClient {
  return usePersona().api;
}
