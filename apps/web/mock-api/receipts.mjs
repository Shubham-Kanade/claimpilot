// @ts-check
/**
 * The ten sample receipts, copied from the synthetic ground truth
 * (data/synth/fixtures/truth/s42-*.json, the `receipt` object of each file).
 *
 * They are embedded rather than read at runtime so the mock runs from apps/web alone. The contract
 * test compares them with the truth files whenever those exist, so they cannot drift silently.
 * Adjustments the scenarios make (low-confidence fields ...) are applied in samples.mjs, never here.
 */

/** @typedef {import("./types.mjs").Receipt} Receipt */
/** @typedef {import("./types.mjs").LineItem} LineItem */

/**
 * A line item with every field present, in the backend's field order.
 * @param {string} description
 * @param {number} amount
 * @param {number | null} [quantity]
 * @param {number | null} [unitPrice]
 * @returns {LineItem}
 */
export function lineItem(description, amount, quantity = null, unitPrice = null) {
  return { description, quantity, unit_price: unitPrice, amount };
}

/**
 * An ExtractedReceipt with every field present (the backend serialises nulls and empty lists
 * too), in the backend's field order.
 * @param {Partial<Omit<Receipt, "taxes">> & { doc_type: Receipt["doc_type"]; taxes?: Receipt["taxes"] }} p
 * @returns {Receipt}
 */
export function makeReceipt(p) {
  return {
    doc_type: p.doc_type,
    merchant_name: p.merchant_name ?? null,
    merchant_gstin: p.merchant_gstin ?? null,
    merchant_city: p.merchant_city ?? null,
    invoice_number: p.invoice_number ?? null,
    date: p.date ?? null,
    time: p.time ?? null,
    currency: p.currency ?? "INR",
    line_items: p.line_items ?? [],
    subtotal: p.subtotal ?? null,
    taxes: {
      cgst: p.taxes?.cgst ?? null,
      sgst: p.taxes?.sgst ?? null,
      igst: p.taxes?.igst ?? null,
      cess: p.taxes?.cess ?? null,
      gst_rate_percent: p.taxes?.gst_rate_percent ?? null,
    },
    service_charge: p.service_charge ?? null,
    discount: p.discount ?? null,
    total: p.total ?? null,
    payment_method: p.payment_method ?? "unknown",
    upi_reference: p.upi_reference ?? null,
    travel_from: p.travel_from ?? null,
    travel_to: p.travel_to ?? null,
    languages: p.languages ?? [],
    handwritten: p.handwritten ?? false,
    low_confidence_fields: p.low_confidence_fields ?? [],
    contains_instructions: p.contains_instructions ?? false,
  };
}

/** Truth id (`s42-0002` ...) of each sample, keyed like TRUTH_RECEIPTS. */
export const TRUTH_IDS = {
  fuel: "s42-0002",
  handwritten: "s42-0003",
  cab: "s42-0005",
  restaurant: "s42-0006",
  learning: "s42-0011",
  upi: "s42-0013",
  flight: "s42-0069",
  hotel: "s42-0082",
  mobile: "s42-0086",
  wfh: "s42-0097",
};

/** @type {Record<keyof typeof TRUTH_IDS, Receipt>} */
export const TRUTH_RECEIPTS = {
  fuel: makeReceipt({
    doc_type: "fuel_slip",
    merchant_name: "Agni Petroleum - Gachibowli",
    merchant_gstin: "36DUQTY9721A9ZM",
    merchant_city: "Hyderabad",
    invoice_number: "930368",
    date: "2026-07-04",
    time: "13:16",
    line_items: [lineItem("Power Petrol", 2665.3, 24.14, 110.41)],
    total: 2665.3,
    payment_method: "upi",
    languages: ["en"],
  }),

  handwritten: makeReceipt({
    doc_type: "handwritten_bill",
    merchant_name: "Gupta Provision Store",
    merchant_city: "Hyderabad",
    invoice_number: "953",
    date: "2026-07-06",
    line_items: [
      lineItem("Biscuits", 60, 2, 30),
      lineItem("Milk 1L", 65),
      lineItem("Sugar 1kg", 165, 3, 55),
      lineItem("Paper Cups (50)", 140, 2, 70),
    ],
    total: 430,
    payment_method: "cash",
    languages: ["en"],
    handwritten: true,
  }),

  cab: makeReceipt({
    doc_type: "cab_receipt",
    merchant_name: "Raahi Cabs",
    merchant_gstin: "29MMBPY7409A9ZE",
    merchant_city: "Bengaluru",
    invoice_number: "CRN8736011562",
    date: "2026-07-12",
    time: "20:39",
    line_items: [
      lineItem("Base Fare", 65),
      lineItem("Distance Fare (26.1 km)", 443.7),
      lineItem("Ride Time Fare (88 min)", 132),
    ],
    subtotal: 640.7,
    taxes: { cgst: 16.02, sgst: 16.02, gst_rate_percent: 5 },
    total: 672.74,
    payment_method: "upi",
    languages: ["en"],
  }),

  restaurant: makeReceipt({
    doc_type: "restaurant_bill",
    merchant_name: "Mehfil Cafe",
    merchant_gstin: "29UINAM5606A6ZH",
    merchant_city: "Bengaluru",
    invoice_number: "3744",
    date: "2026-07-12",
    time: "20:05",
    line_items: [
      lineItem("Butter Naan", 80, 1, 80),
      lineItem("मसाला डोसा", 125, 1, 125),
      lineItem("Pav Bhaji", 175, 1, 175),
      lineItem("चिकन बिरयानी", 410, 1, 410),
    ],
    subtotal: 790,
    taxes: { cgst: 19.75, sgst: 19.75, gst_rate_percent: 5 },
    total: 829.5,
    payment_method: "card",
    languages: ["en", "hi"],
  }),

  learning: makeReceipt({
    doc_type: "gst_invoice",
    merchant_name: "LearnSphere Academy Pvt Ltd",
    merchant_gstin: "29MWYTZ8200H3ZZ",
    merchant_city: "Bengaluru",
    invoice_number: "XZH/26-27/9794",
    date: "2026-07-18",
    line_items: [lineItem("Certificate in Applied Machine Learning", 19000, 1, 19000)],
    subtotal: 19000,
    taxes: { igst: 3420, gst_rate_percent: 18 },
    total: 22420,
    payment_method: "netbanking",
    languages: ["en"],
  }),

  upi: makeReceipt({
    doc_type: "upi_payment",
    merchant_name: "CHAMELI GARG",
    date: "2026-07-21",
    time: "08:26",
    total: 120,
    payment_method: "upi",
    upi_reference: "615886053797",
    languages: ["en"],
  }),

  flight: makeReceipt({
    doc_type: "flight_ticket",
    merchant_name: "Suryoday Air",
    merchant_gstin: "27CEGFA4772S1ZG",
    merchant_city: "Mumbai",
    invoice_number: "DR1XAJ",
    date: "2026-08-07",
    time: "08:16",
    line_items: [
      lineItem("Base Fare", 7179),
      lineItem("User Development Fee", 481),
      lineItem("Passenger Service Fee", 226),
      lineItem("Aviation Security Fee", 236),
      lineItem("Convenience Fee", 395),
    ],
    subtotal: 8517,
    taxes: { igst: 358.95, gst_rate_percent: 5 },
    total: 8875.95,
    payment_method: "upi",
    travel_from: "Panaji",
    travel_to: "Hyderabad",
    languages: ["en"],
  }),

  hotel: makeReceipt({
    doc_type: "hotel_folio",
    merchant_name: "Lotus Bay Suites",
    merchant_gstin: "04ZPWCN7743D8ZE",
    merchant_city: "Chandigarh",
    invoice_number: "F2626239",
    date: "2026-08-18",
    line_items: [
      lineItem("Room Charges 15-Aug", 7700),
      lineItem("Room Charges 16-Aug", 7200),
      lineItem("Room Charges 17-Aug", 7200),
    ],
    subtotal: 21600,
    taxes: { cgst: 540, sgst: 540, gst_rate_percent: 5 },
    total: 22680,
    payment_method: "card",
    languages: ["en"],
  }),

  mobile: makeReceipt({
    doc_type: "mobile_bill",
    merchant_name: "Nakshatra Mobile",
    merchant_gstin: "27AHRPC5093H1Z1",
    merchant_city: "Mumbai",
    invoice_number: "MB260949826224",
    date: "2026-09-13",
    line_items: [
      lineItem("Monthly Rental - Family Max 999", 999),
      lineItem("International Roaming Pack", 299),
      lineItem("Usage Charges (calls/SMS beyond plan)", 16),
    ],
    subtotal: 1314,
    taxes: { cgst: 118.26, sgst: 118.26, gst_rate_percent: 18 },
    total: 1550.52,
    languages: ["en"],
    contains_instructions: true,
  }),

  wfh: makeReceipt({
    doc_type: "gst_invoice",
    merchant_name: "Prakash Computer World",
    merchant_gstin: "30AUIGE4952F7ZD",
    merchant_city: "Panaji",
    invoice_number: "VIR/26-27/0237",
    date: "2026-08-19",
    line_items: [
      lineItem("USB-C Hub 6-in-1", 3516, 2, 1758),
      lineItem("Laptop Stand (Aluminium)", 3582, 2, 1791),
      lineItem("Wireless Mouse", 2348, 2, 1174),
    ],
    subtotal: 9446,
    taxes: { cgst: 1700.28, sgst: 1700.28, gst_rate_percent: 18 },
    total: 11146.28,
    payment_method: "card",
    languages: ["en"],
  }),
};
