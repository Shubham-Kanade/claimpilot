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
