// @ts-check
/**
 * GSTIN (Indian GST Identification Number) checks (domain/gstin.py): 15 characters, the 2-digit
 * state code, a 10-character PAN, an entity number, `Z`, and a mod-36 check character.
 */

const CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ";
const GSTIN_PATTERN = /^(\d{2})([A-Z]{5}\d{4}[A-Z])([1-9A-Z])Z([0-9A-Z])$/;

/** GST state and union-territory codes (25 merged into 26 in 2020; 28 is the old Andhra Pradesh). */
export const STATE_CODES = new Set([
  ...Array.from({ length: 24 }, (_, i) => String(i + 1).padStart(2, "0")),
  "26",
  "27",
  ...Array.from({ length: 10 }, (_, i) => String(i + 29)),
]);

/**
 * The 15th (check) character of a GSTIN, a mod-36 checksum over the first 14 characters.
 * @param {string} first14
 */
export function checksumChar(first14) {
  let total = 0;
  for (let i = 0; i < 14; i += 1) {
    const product = CHARSET.indexOf(first14.charAt(i)) * (i % 2 === 0 ? 1 : 2);
    total += Math.floor(product / 36) + (product % 36);
  }
  return CHARSET.charAt((36 - (total % 36)) % 36);
}

/**
 * Is this a valid GSTIN, and if not, why: `format`, `state_code` or `checksum`.
 * @param {string} value
 * @returns {{ valid: boolean; reason: "format" | "state_code" | "checksum" | null }}
 */
export function validateGstin(value) {
  const gstin = value.trim().toUpperCase();
  const match = GSTIN_PATTERN.exec(gstin);
  if (!match) return { valid: false, reason: "format" };
  if (!STATE_CODES.has(match[1] ?? "")) return { valid: false, reason: "state_code" };
  if (checksumChar(gstin.slice(0, 14)) !== gstin.charAt(14)) {
    return { valid: false, reason: "checksum" };
  }
  return { valid: true, reason: null };
}
