import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

import { API_BASE_URL } from "@/lib/api/client";

import { ASHA, EMPLOYEES, META, makeStats } from "./fixtures";

/** One MSW server for the whole unit-test run: any request without a handler fails the test. */
export const server = setupServer();

export const url = (path: string): string => `${API_BASE_URL}${path}`;

/** A problem+json error response, exactly like the real API. */
export function problem(
  status: number,
  type: string,
  title: string,
  detail?: Record<string, unknown>,
) {
  return HttpResponse.json(
    { type, title, status, ...(detail ? { detail } : {}) },
    { status, headers: { "Content-Type": "application/problem+json" } },
  );
}

/** Directory + roles + meta + stats: what every screen needs before its own data. */
export function baseHandlers(approverIds: readonly string[] = ["DEMO-RAVI"]) {
  return [
    http.get(url("/v1/employees"), () => HttpResponse.json(EMPLOYEES)),
    http.get(url("/v1/me"), ({ request }) => {
      const id = request.headers.get("X-Persona");
      const employee = EMPLOYEES.find((e) => e.id === id);
      if (!employee) return problem(401, "unknown_persona", `Unknown persona ${id}`);
      return HttpResponse.json({ employee, is_approver: approverIds.includes(employee.id) });
    }),
    http.get(url("/v1/meta"), () => HttpResponse.json(META)),
    http.get(url("/v1/stats"), () => HttpResponse.json(makeStats())),
  ];
}

export { ASHA };
