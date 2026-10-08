import type { Employee } from "../api/types";

/** The persona a first-time visitor acts as (Asha Menon, an L3 employee in Pune). */
export const DEFAULT_PERSONA_ID = "DEMO-ASHA";

const isDemoPersona = (employee: Employee) => employee.id.startsWith("DEMO-");

/** The demo personas (DEMO-*) first, the golden-dataset personas (P00x) after; stable otherwise. */
export function orderPersonas(employees: readonly Employee[]): Employee[] {
  return [...employees].sort((a, b) => Number(isDemoPersona(b)) - Number(isDemoPersona(a)));
}

/**
 * Who a visitor with no saved choice acts as: DEMO-ASHA when she exists (and is not known to be
 * an approver), otherwise the first employee who is not an approver, otherwise the first employee.
 * `roles` says which employees are approvers once it is known (null while still loading).
 */
export function pickDefaultPersona(
  employees: readonly Employee[],
  approverIds: ReadonlySet<string> | null,
): string | null {
  if (employees.length === 0) return null;
  const preferred = employees.find((e) => e.id === DEFAULT_PERSONA_ID);
  if (preferred && !approverIds?.has(preferred.id)) return preferred.id;
  if (approverIds === null) return null; // roles still loading and no preferred persona
  return (employees.find((e) => !approverIds.has(e.id)) ?? employees[0]).id;
}
