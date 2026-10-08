"use client";

import { ChevronDown } from "lucide-react";
import { useId } from "react";

import { Chip } from "@/components/ui/Chip";
import { Skeleton } from "@/components/ui/feedback";
import type { Employee } from "@/lib/api/types";
import { usePersona } from "@/lib/persona/PersonaProvider";

function initials(name: string): string {
  const parts = name.split(/\s+/).filter(Boolean);
  return (parts[0]?.[0] ?? "?") + (parts.length > 1 ? parts[parts.length - 1][0] : "");
}

/** "Asha Menon · L3 · Pune", or "Ravi Iyer · L5 · approver" (short enough for a phone). */
function describe(employee: Employee, approver: boolean): string {
  return `${employee.name} · ${employee.grade} · ${approver ? "approver" : employee.base_city}`;
}

/**
 * "Acting as": the demo has no login, so this picks who is acting. Every API call then sends that
 * employee's id as X-Persona. The choice persists in localStorage. A native <select> keeps it
 * fully keyboard- and screen-reader-accessible (and gives phones their own picker). The demo
 * personas (DEMO-*) are listed first, the synthetic-dataset employees after them.
 */
export function PersonaSwitcher() {
  const selectId = useId();
  const { employees, personaId, persona, setPersonaId, approverIds, status } = usePersona();

  if (status === "error") {
    return <Chip tone="danger">API unreachable</Chip>;
  }
  if (employees.length === 0 || !personaId) {
    return <Skeleton className="h-10 w-40 sm:w-60" />;
  }

  const demoPersonas = employees.filter((e) => e.id.startsWith("DEMO-"));
  const others = employees.filter((e) => !e.id.startsWith("DEMO-"));
  const options = (list: readonly Employee[]) =>
    list.map((e) => (
      <option key={e.id} value={e.id}>
        {describe(e, approverIds.has(e.id))}
      </option>
    ));

  return (
    <div className="flex items-center gap-2">
      <label htmlFor={selectId} className="sr-only">
        Acting as
      </label>
      <div className="relative">
        <span
          aria-hidden="true"
          className="pointer-events-none absolute top-1/2 left-1.5 flex size-7 -translate-y-1/2 items-center justify-center rounded-full bg-indigo-100 text-xs font-semibold text-indigo-800 uppercase"
        >
          {persona ? initials(persona.name) : "?"}
        </span>
        <select
          id={selectId}
          value={personaId}
          onChange={(event) => setPersonaId(event.target.value)}
          className="h-10 w-44 max-w-full cursor-pointer appearance-none truncate rounded-xl border border-slate-300 bg-white pr-8 pl-10 text-sm font-medium text-slate-800 shadow-sm hover:border-slate-400 sm:w-64 lg:w-72"
        >
          {demoPersonas.length > 0 && others.length > 0 ? (
            <>
              <optgroup label="Demo personas">{options(demoPersonas)}</optgroup>
              <optgroup label="More synthetic employees">{options(others)}</optgroup>
            </>
          ) : (
            options(employees)
          )}
        </select>
        <ChevronDown
          className="pointer-events-none absolute top-1/2 right-2.5 size-4 -translate-y-1/2 text-slate-500"
          aria-hidden="true"
        />
      </div>
    </div>
  );
}
