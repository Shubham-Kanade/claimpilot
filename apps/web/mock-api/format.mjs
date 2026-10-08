// @ts-check
/**
 * Number, date and wording helpers. They mirror the backend (policy/money.py, claims/labels.py,
 * trust/gst.py) character for character, because the strings they build are shown to people and
 * quoted by tests.
 */

/** @typedef {import("./types.mjs").Receipt} Receipt */

export const EN_DASH = "–"; // between the dates of a range, e.g. 15-18 Aug 2026
export const LEFT_QUOTE = "“";
export const RIGHT_QUOTE = "”";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** @type {Record<string, string>} */
const DOC_LABELS = {
  restaurant_bill: "restaurant bill",
  gst_invoice: "invoice",
  hotel_folio: "hotel bill",
  cab_receipt: "cab receipt",
  flight_ticket: "flight ticket",
  train_ticket: "train ticket",
  fuel_slip: "fuel slip",
  mobile_bill: "mobile bill",
  upi_payment: "UPI payment",
  handwritten_bill: "handwritten bill",
  other: "document",
};

// --- numbers -------------------------------------------------------------------------------------

/**
 * Python's `round()`: halves go to the even neighbour (`round(829.5) == 830`, `round(830.5) == 830`).
 * @param {number} x
 */
export function roundHalfEven(x) {
  const rounded = Math.round(x);
  return Math.abs(x % 1) === 0.5 && rounded % 2 !== 0 ? rounded - 1 : rounded;
}

/**
 * Two decimals, for amounts that are sums of two-decimal amounts.
 * @param {number} x
 */
export function round2(x) {
  return Math.round(x * 100) / 100;
}

/**
 * Sum of amounts, counting a missing one as zero.
 * @param {Array<number | null | undefined>} values
 */
export function sumOf(values) {
  let total = 0;
  for (const value of values) total += value ?? 0;
  return total;
}

/**
 * `₹1,22,420`: Indian digit grouping, paise only when present (policy/money.py `format_inr`).
 * @param {number} amount
 */
export function formatInr(amount) {
  const paiseTotal = roundHalfEven(Math.abs(amount) * 100);
  const rupeeCount = Math.floor(paiseTotal / 100);
  const paise = paiseTotal % 100;
  let digits = String(rupeeCount);
  if (digits.length > 3) {
    let head = digits.slice(0, -3);
    const tail = digits.slice(-3);
    /** @type {string[]} */
    const pairs = [];
    while (head.length > 2) {
      pairs.push(head.slice(-2));
      head = head.slice(0, -2);
    }
    if (head) pairs.push(head);
    digits = [...pairs.reverse(), tail].join(",");
  }
  const sign = amount < 0 && paiseTotal ? "-" : "";
  const suffix = paise ? `.${String(paise).padStart(2, "0")}` : "";
  return `${sign}₹${digits}${suffix}`;
}

/**
 * Whole rupees, for wording a person reads (`₹830`); findings keep the exact amount.
 * @param {number} amount
 */
export function rupees(amount) {
  return formatInr(roundHalfEven(amount));
}

/**
 * `₹22,100.00`: Western grouping with two decimals, as the trust checks print amounts
 * (trust/gst.py `_money`).
 * @param {number} value
 */
export function money(value) {
  const [whole = "0", frac = "00"] = Math.abs(value).toFixed(2).split(".");
  return `${value < 0 ? "-" : ""}₹${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${frac}`;
}

/**
 * Python's `f"{x:g}"` for the rates a bill prints (`5`, `18`, `2.5`).
 * @param {number} value
 */
export function formatG(value) {
  return String(Number(value.toPrecision(6)));
}

// --- dates (ISO `YYYY-MM-DD` strings throughout) ---------------------------------------------------

/**
 * @typedef {Object} Ymd
 * @property {number} y
 * @property {number} m  1 to 12
 * @property {number} d
 */

/**
 * The parts of a valid ISO date, or null.
 * @param {string | null | undefined} iso
 * @returns {Ymd | null}
 */
export function parseIso(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec((iso ?? "").trim());
  if (!match) return null;
  const y = Number(match[1]);
  const m = Number(match[2]);
  const d = Number(match[3]);
  const probe = new Date(Date.UTC(y, m - 1, d));
  return probe.getUTCFullYear() === y && probe.getUTCMonth() === m - 1 && probe.getUTCDate() === d
    ? { y, m, d }
    : null;
}

/**
 * @param {number} y
 * @param {number} m  1 to 12
 * @param {number} d
 */
export function toIso(y, m, d) {
  return `${String(y).padStart(4, "0")}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

/**
 * @param {Date} date
 */
export function isoOfDate(date) {
  return toIso(date.getUTCFullYear(), date.getUTCMonth() + 1, date.getUTCDate());
}

/**
 * The date `days` after `iso` (negative for before).
 * @param {string} iso
 * @param {number} days
 */
export function addDays(iso, days) {
  const parts = parseIso(iso);
  if (!parts) throw new Error(`not an ISO date: ${iso}`);
  return isoOfDate(new Date(Date.UTC(parts.y, parts.m - 1, parts.d + days)));
}

/**
 * `Aug 2026`.
 * @param {number} y
 * @param {number} m  1 to 12
 */
export function monthLabel(y, m) {
  return `${MONTHS[m - 1]} ${y}`;
}

/**
 * `14 Aug 2026`.
 * @param {string} iso
 */
export function dayLabel(iso) {
  const p = parseIso(iso);
  if (!p) return iso;
  return `${p.d} ${monthLabel(p.y, p.m)}`;
}

/**
 * `14 Aug`.
 * @param {string} iso
 */
export function shortDayLabel(iso) {
  const p = parseIso(iso);
  if (!p) return iso;
  return `${p.d} ${MONTHS[p.m - 1]}`;
}

/**
 * A date range with an en dash: `15–18 Aug 2026`, `30 Jul–2 Aug 2026`, `12 Aug 2026`.
 * @param {string} start
 * @param {string} end
 */
export function dateRange(start, end) {
  const s = parseIso(start);
  const e = parseIso(end);
  if (!s || !e) return start === end ? start : `${start}${EN_DASH}${end}`;
  if (start === end) return dayLabel(start);
  if (s.y === e.y && s.m === e.m) return `${s.d}${EN_DASH}${dayLabel(end)}`;
  if (s.y === e.y) return `${shortDayLabel(start)}${EN_DASH}${dayLabel(end)}`;
  return `${dayLabel(start)}${EN_DASH}${dayLabel(end)}`;
}

/**
 * `04 Jul 2026` (Python's `%d %b %Y`), used inside trust-finding messages.
 * @param {string} iso
 */
export function dayLabelPadded(iso) {
  const p = parseIso(iso);
  if (!p) return iso;
  return `${String(p.d).padStart(2, "0")} ${monthLabel(p.y, p.m)}`;
}

/**
 * Whole days from `a` to `b`.
 * @param {string} a
 * @param {string} b
 */
export function daysBetween(a, b) {
  const x = parseIso(a);
  const y = parseIso(b);
  if (!x || !y) return 0;
  return Math.round((Date.UTC(y.y, y.m - 1, y.d) - Date.UTC(x.y, x.m - 1, x.d)) / 86_400_000);
}

// --- wording -------------------------------------------------------------------------------------

/**
 * Python's `str.isprintable()` for one character.
 * @param {string} ch
 */
function isPrintable(ch) {
  if (ch === " ") return true;
  return !/[\p{C}\p{Zl}\p{Zp}\p{Zs}]/u.test(ch);
}

/**
 * Receipt text made safe to quote in a question: single line, no control characters, short
 * (claims/labels.py `safe_text`). Receipts are untrusted input.
 * @param {string | null | undefined} value
 * @param {number} [limit]
 */
export function safeText(value, limit = 40) {
  if (!value) return "";
  const printable = Array.from(value, (ch) => (isPrintable(ch) ? ch : " ")).join("");
  const words = printable.replaceAll('"', "'").split(/\s+/).filter(Boolean).join(" ");
  return Array.from(words).slice(0, limit).join("").trimEnd();
}

/**
 * Typographic quotes around receipt text so it reads as quoted, not as part of the sentence.
 * @param {string} value
 */
export function quote(value) {
  return `${LEFT_QUOTE}${value}${RIGHT_QUOTE}`;
}

/**
 * Capitalise the first letter only (a merchant name keeps its case).
 * @param {string} text
 */
export function sentence(text) {
  return text.slice(0, 1).toUpperCase() + text.slice(1);
}

/**
 * `the ₹651 restaurant bill from “Chulha Cafe”`; the merchant only when it is printed.
 * @param {{ receipt: Receipt }} doc
 */
export function describe(doc) {
  const r = doc.receipt;
  const label = DOC_LABELS[r.doc_type] ?? "document";
  const text = r.total == null ? `the ${label}` : `the ${rupees(r.total)} ${label}`;
  const merchant = safeText(r.merchant_name);
  if (!merchant) return text;
  const preposition = r.doc_type === "upi_payment" ? "to" : "from";
  return `${text} ${preposition} ${quote(merchant)}`;
}
