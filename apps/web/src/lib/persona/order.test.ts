import { describe, expect, it } from "vitest";

import type { Employee } from "@/lib/api/types";
import { ADVIKA, ASHA, RAVI } from "@/test/fixtures";

import { DEFAULT_PERSONA_ID, orderPersonas, pickDefaultPersona } from "./order";

const person = (id: string): Employee => ({ ...ADVIKA, id, name: id });

describe("orderPersonas", () => {
  it("lists the DEMO-* personas first and the dataset personas after, keeping each group's order", () => {
    const ordered = orderPersonas([
      person("P001"),
      person("DEMO-ASHA"),
      person("P005"),
      person("DEMO-RAVI"),
      person("DEMO-MEERA"),
    ]);
    expect(ordered.map((e) => e.id)).toEqual([
      "DEMO-ASHA",
      "DEMO-RAVI",
      "DEMO-MEERA",
      "P001",
      "P005",
    ]);
  });

  it("does not mutate its input", () => {
    const input = [person("P001"), person("DEMO-ASHA")];
    orderPersonas(input);
    expect(input.map((e) => e.id)).toEqual(["P001", "DEMO-ASHA"]);
  });
});

describe("pickDefaultPersona", () => {
  it("is Asha Menon for a first-time visitor", () => {
    expect(DEFAULT_PERSONA_ID).toBe("DEMO-ASHA");
    expect(pickDefaultPersona([ADVIKA, ASHA, RAVI], new Set(["DEMO-RAVI"]))).toBe("DEMO-ASHA");
  });

  it("picks her straight away, before anyone's role is known", () => {
    expect(pickDefaultPersona([ADVIKA, ASHA, RAVI], null)).toBe("DEMO-ASHA");
  });

  it("falls back to the first non-approver when she is missing or an approver", () => {
    expect(pickDefaultPersona([RAVI, ADVIKA], new Set(["DEMO-RAVI"]))).toBe("P001");
    expect(pickDefaultPersona([ASHA, ADVIKA], new Set(["DEMO-ASHA"]))).toBe("P001");
  });

  it("waits for the roles when there is no preferred persona to pick blindly", () => {
    expect(pickDefaultPersona([RAVI, ADVIKA], null)).toBeNull();
  });

  it("takes the first employee when everyone is an approver, and nobody for an empty directory", () => {
    expect(pickDefaultPersona([RAVI], new Set(["DEMO-RAVI"]))).toBe("DEMO-RAVI");
    expect(pickDefaultPersona([], new Set())).toBeNull();
  });
});
