const inr = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** Format an amount in rupees using Indian digit grouping, e.g. ₹1,23,456.50 */
export function formatINR(amount: number): string {
  return inr.format(amount);
}

/** Map a 0–1 confidence to the label shown next to extracted fields. */
export function confidenceLabel(confidence: number): "high" | "medium" | "low" {
  if (confidence >= 0.9) return "high";
  if (confidence >= 0.7) return "medium";
  return "low";
}

/** 0.934 -> "93%" */
export function formatPercent(fraction: number): string {
  return `${Math.round(fraction * 100)}%`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})/;

interface Ymd {
  year: number;
  month: number;
  day: number;
}

function parseIsoDate(value: string | null | undefined): Ymd | null {
  const match = value ? ISO_DATE.exec(value) : null;
  if (!match) return null;
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;
  return { year, month, day };
}

/**
 * "2026-08-14" -> "14 Aug 2026". Parsed by hand (never through `Date`) so a date never shifts by a
 * day with the viewer's time zone. Unparseable input is returned as-is; null gives an em dash.
 */
export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const ymd = parseIsoDate(value);
  return ymd ? `${ymd.day} ${MONTHS[ymd.month - 1]} ${ymd.year}` : value;
}

/** "12–14 Aug 2026", "30 Jul–2 Aug 2026", "12 Aug 2026"; open-ended ranges degrade gracefully. */
export function formatDateRange(
  start: string | null | undefined,
  end: string | null | undefined,
): string {
  const a = parseIsoDate(start);
  const b = parseIsoDate(end);
  if (!a && !b) return "Dates to be confirmed";
  if (!a || !b) return formatDate(start ?? end);
  if (a.year === b.year && a.month === b.month && a.day === b.day) return formatDate(start);
  if (a.year === b.year && a.month === b.month) {
    return `${a.day}–${b.day} ${MONTHS[b.month - 1]} ${b.year}`;
  }
  if (a.year === b.year) {
    return `${a.day} ${MONTHS[a.month - 1]}–${b.day} ${MONTHS[b.month - 1]} ${b.year}`;
  }
  return `${formatDate(start)}–${formatDate(end)}`;
}

const usdCents = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const usdSubCent = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 4,
  maximumFractionDigits: 4,
});

/** USD spend with enough precision to be meaningful for sub-cent LLM costs: "$0.0042". */
export function formatUSD(amount: number): string {
  if (amount === 0) return usdCents.format(0);
  return Math.abs(amount) < 0.01 ? usdSubCent.format(amount) : usdCents.format(amount);
}

/**
 * A date-time from the API as epoch milliseconds (NaN when missing or malformed). The API sends
 * UTC date-times WITHOUT a zone ("2026-10-08T12:54:16.303656"), which `Date.parse` would read as
 * local time and be off by the viewer's UTC offset (5.5 hours in India): no zone means UTC.
 */
export function parseApiTimestamp(value: string | null | undefined): number {
  if (!value) return Number.NaN;
  // JavaScript only promises milliseconds: trim the microseconds Python writes.
  const trimmed = value.replace(/(\.\d{3})\d+/, "$1");
  // A date alone ("2026-10-08") is read as UTC midnight already; a date-time needs a zone.
  const needsZone = trimmed.includes("T") && !/(?:Z|[+-]\d{2}(?::?\d{2})?)$/i.test(trimmed);
  return Date.parse(needsZone ? `${trimmed}Z` : trimmed);
}

/** Milliseconds -> "850 ms" / "2.4 s". */
export function formatLatency(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

/** A fraction with one decimal, so a small error rate is not rounded away: 0.0123 -> "1.2%". */
export function formatRate(fraction: number): string {
  return `${(fraction * 100).toFixed(1)}%`;
}

/** An API date-time as "9 Oct, 14:05" in the viewer's time zone ("—" when unknown). */
export function formatDateTime(value: string | null | undefined): string {
  const ms = parseApiTimestamp(value);
  if (Number.isNaN(ms)) return "—";
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(ms));
}

/** Seconds -> "0:07" / "1:05" (a running clock). */
export function formatClock(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Seconds -> "9.4 s" / "1 min 12 s". */
export function formatSeconds(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const m = Math.floor(seconds / 60);
  return `${m} min ${Math.round(seconds - m * 60)} s`;
}

/** Minutes -> "42 min" / "3 h 8 min". */
export function formatMinutes(minutes: number): string {
  const total = Math.round(minutes);
  if (total < 60) return `${total} min`;
  const h = Math.floor(total / 60);
  const m = total % 60;
  return m === 0 ? `${h} h` : `${h} h ${m} min`;
}

/** Bytes -> "820 KB" / "1.2 MB". */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** "client_entertainment" -> "Client entertainment" */
export function humanize(code: string): string {
  const spaced = code.replace(/_/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** "1 receipt" / "3 receipts" */
export function pluralize(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}
