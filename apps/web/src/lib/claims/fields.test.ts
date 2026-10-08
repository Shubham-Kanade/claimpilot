import { describe, expect, it } from "vitest";

import { makeFinding, makeReceipt } from "@/test/fixtures";

import { boxFor, buildFieldRows, fieldForFinding, fieldLabel } from "./fields";

const box = { page: 0, x: 0.1, y: 0.2, w: 0.3, h: 0.04 };

describe("buildFieldRows", () => {
  it("lists the fields that were read, formatted for India", () => {
    const rows = buildFieldRows(makeReceipt());
    const byKey = Object.fromEntries(rows.map((r) => [r.key, r.value]));
    expect(byKey.merchant_name).toBe("Lotus Bay Suites");
    expect(byKey.merchant_gstin).toBe("04ZPWCN7743D8ZE");
    expect(byKey.date).toBe("18 Aug 2026");
    expect(byKey.total).toBe("₹22,680.00");
    expect(byKey.subtotal).toBe("₹21,600.00");
    expect(byKey["taxes.cgst"]).toBe("₹540.00");
    expect(byKey["taxes.gst_rate_percent"]).toBe("5%");
    expect(byKey.payment_method).toBe("Card");
    expect(byKey.languages).toBe("EN");
  });

  it("omits optional fields that were not printed", () => {
    const keys = buildFieldRows(
      makeReceipt({ time: null, upi_reference: null, discount: null }),
    ).map((r) => r.key);
    expect(keys).not.toContain("time");
    expect(keys).not.toContain("upi_reference");
    expect(keys).not.toContain("discount");
    expect(keys).not.toContain("taxes.igst");
  });

  it("flags required fields that are missing as 'Not found' and low confidence", () => {
    const rows = buildFieldRows(makeReceipt({ merchant_name: null, date: null, total: null }));
    for (const key of ["merchant_name", "date", "total"]) {
      expect(rows.find((r) => r.key === key)).toMatchObject({
        value: "Not found",
        missing: true,
        low: true,
      });
    }
  });

  it("marks low-confidence fields, including nested tax fields", () => {
    const rows = buildFieldRows(
      makeReceipt({
        low_confidence_fields: ["date", "cgst", "taxes"],
        upi_reference: "615886053797",
      }),
    );
    const low = (key: string) => rows.find((r) => r.key === key)?.low;
    expect(low("date")).toBe(true);
    expect(low("taxes.cgst")).toBe(true);
    expect(low("taxes.sgst")).toBe(true); // the whole `taxes` group is low
    expect(low("total")).toBe(false);
    expect(low("upi_reference")).toBe(false);
  });

  it("shows currency, handwriting and UPI details when relevant", () => {
    const rows = buildFieldRows(
      makeReceipt({
        currency: "USD",
        handwritten: true,
        payment_method: "upi",
        upi_reference: "615886053797",
        travel_from: "Panaji",
        travel_to: "Hyderabad",
        languages: ["en", "hi"],
      }),
    );
    const byKey = Object.fromEntries(rows.map((r) => [r.key, r.value]));
    expect(byKey.currency).toBe("USD");
    expect(byKey.handwritten).toBe("Yes");
    expect(byKey.payment_method).toBe("UPI");
    expect(byKey.upi_reference).toBe("615886053797");
    expect(byKey.travel_from).toBe("Panaji");
    expect(byKey.languages).toBe("EN, HI");
  });

  it("tolerates missing optional collections (the API types them as optional)", () => {
    const receipt = makeReceipt({ languages: undefined, taxes: undefined, line_items: undefined });
    expect(() => buildFieldRows(receipt)).not.toThrow();
  });
});

describe("boxFor", () => {
  it("finds a region by field name or by its last path segment", () => {
    expect(boxFor({ total: box }, "total")).toEqual(box);
    expect(boxFor({ cgst: box }, "taxes.cgst")).toEqual(box);
  });

  it("returns null instead of throwing for missing, empty or malformed boxes", () => {
    expect(boxFor({}, "total")).toBeNull();
    expect(boxFor(undefined, "total")).toBeNull();
    expect(boxFor(null, "total")).toBeNull();
    expect(boxFor({ total: box }, null)).toBeNull();
    expect(boxFor({ total: { ...box, x: Number.NaN } }, "total")).toBeNull();
  });
});

describe("fieldForFinding", () => {
  it("prefers a field that has a location", () => {
    const finding = makeFinding({ fields: ["line_items", "subtotal"] });
    expect(fieldForFinding(finding, { subtotal: box })).toBe("subtotal");
  });

  it("falls back to the first named field when nothing is located", () => {
    expect(fieldForFinding(makeFinding({ fields: ["total"] }), {})).toBe("total");
    expect(fieldForFinding(makeFinding({ fields: [] }), {})).toBeNull();
  });
});

describe("fieldLabel", () => {
  it("names known fields and humanises unknown ones", () => {
    expect(fieldLabel("merchant_gstin")).toBe("GSTIN");
    expect(fieldLabel("taxes.sgst")).toBe("SGST");
    expect(fieldLabel("something_else")).toBe("Something else");
  });
});
