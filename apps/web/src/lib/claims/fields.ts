import type { Box, ExtractedReceipt, Finding } from "../api/types";
import { formatDate, formatINR, humanize } from "../format";
import { PAYMENT_LABELS } from "../labels";

/**
 * Turning an ExtractedReceipt into the rows of the "what we read" list, with a per-field
 * confidence marker and (when the API provides `boxes`) the location to highlight.
 *
 * `boxes` maps a receipt field name to a normalised region. It is often empty, so every lookup
 * here is defensive: a missing box never throws, it just means "no highlight available".
 */
export interface FieldRow {
  /** The receipt field name used as the key in `boxes` and `low_confidence_fields`. */
  key: string;
  label: string;
  value: string;
  /** The field was hard to read or inferred (listed in `low_confidence_fields`). */
  low: boolean;
  /** Missing on the receipt although it is normally required (merchant, date, total). */
  missing: boolean;
  mono?: boolean;
}

function isLow(receipt: ExtractedReceipt, key: string): boolean {
  const low = new Set(receipt.low_confidence_fields ?? []);
  const leaf = key.split(".").pop() ?? key;
  return low.has(key) || low.has(leaf) || low.has(key.split(".")[0]);
}

/** The region for a field: the exact key, or its last path segment (`taxes.cgst` -> `cgst`). */
export function boxFor(
  boxes: Readonly<Record<string, Box>> | null | undefined,
  key: string | null,
): Box | null {
  if (!boxes || !key) return null;
  const leaf = key.split(".").pop() ?? key;
  const box = boxes[key] ?? boxes[leaf] ?? null;
  if (!box) return null;
  const finite = [box.x, box.y, box.w, box.h].every((n) => Number.isFinite(n));
  return finite ? box : null;
}

function money(value: number | null | undefined): string | null {
  return value === null || value === undefined ? null : formatINR(value);
}

export function buildFieldRows(receipt: ExtractedReceipt): FieldRow[] {
  const rows: FieldRow[] = [];
  const taxes = receipt.taxes ?? {};
  const languages = receipt.languages ?? [];
  const add = (
    key: string,
    label: string,
    value: string | null | undefined,
    options: { required?: boolean; mono?: boolean } = {},
  ) => {
    if (value === null || value === undefined || value === "") {
      if (options.required) {
        rows.push({ key, label, value: "Not found", low: true, missing: true });
      }
      return;
    }
    rows.push({ key, label, value, low: isLow(receipt, key), missing: false, mono: options.mono });
  };

  add("merchant_name", "Merchant", receipt.merchant_name, { required: true });
  add("merchant_city", "City", receipt.merchant_city);
  add("merchant_gstin", "GSTIN", receipt.merchant_gstin, { mono: true });
  add("invoice_number", "Invoice no.", receipt.invoice_number, { mono: true });
  add("date", "Date", receipt.date ? formatDate(receipt.date) : null, { required: true });
  add("time", "Time", receipt.time);
  add("subtotal", "Subtotal", money(receipt.subtotal));
  add("taxes.cgst", "CGST", money(taxes.cgst));
  add("taxes.sgst", "SGST", money(taxes.sgst));
  add("taxes.igst", "IGST", money(taxes.igst));
  add("taxes.cess", "Cess", money(taxes.cess));
  add(
    "taxes.gst_rate_percent",
    "GST rate",
    taxes.gst_rate_percent === null || taxes.gst_rate_percent === undefined
      ? null
      : `${taxes.gst_rate_percent}%`,
  );
  add("service_charge", "Service charge", money(receipt.service_charge));
  add("discount", "Discount", money(receipt.discount));
  add("total", "Total", money(receipt.total), { required: true });
  add(
    "payment_method",
    "Paid by",
    receipt.payment_method && receipt.payment_method !== "unknown"
      ? PAYMENT_LABELS[receipt.payment_method]
      : null,
  );
  add("upi_reference", "UPI reference", receipt.upi_reference, { mono: true });
  add("travel_from", "From", receipt.travel_from);
  add("travel_to", "To", receipt.travel_to);
  if (receipt.currency && receipt.currency !== "INR") add("currency", "Currency", receipt.currency);
  if (languages.length > 0) {
    add("languages", "Languages", languages.map((l) => l.toUpperCase()).join(", "));
  }
  if (receipt.handwritten) add("handwritten", "Handwritten", "Yes");
  return rows;
}

/** A readable name for a receipt field key (used in captions and "show on receipt"). */
export function fieldLabel(key: string): string {
  const known: Record<string, string> = {
    merchant_name: "Merchant",
    merchant_city: "City",
    merchant_gstin: "GSTIN",
    invoice_number: "Invoice no.",
    date: "Date",
    time: "Time",
    subtotal: "Subtotal",
    total: "Total",
    line_items: "Line items",
    service_charge: "Service charge",
    discount: "Discount",
    upi_reference: "UPI reference",
    "taxes.cgst": "CGST",
    "taxes.sgst": "SGST",
    "taxes.igst": "IGST",
    "taxes.cess": "Cess",
    "taxes.gst_rate_percent": "GST rate",
  };
  return known[key] ?? humanize(key.split(".").pop() ?? key);
}

/**
 * Which receipt field to highlight for a finding: the first named field that has a location,
 * else simply the first named field (it is marked as selected even without a box).
 */
export function fieldForFinding(
  finding: Pick<Finding, "fields">,
  boxes: Readonly<Record<string, Box>> | null | undefined,
): string | null {
  const fields = finding.fields ?? [];
  return fields.find((f) => boxFor(boxes, f) !== null) ?? fields[0] ?? null;
}
