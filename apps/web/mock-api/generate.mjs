// @ts-check
/**
 * Plans for files the mock does not know: a plausible receipt, decisions and trust findings
 * generated DETERMINISTICALLY from the sha256 of the bytes (the same file always reads the same),
 * so any photo or PDF works in a demo. Merchants are fictional, amounts are 80 to 9,000 rupees,
 * dates fall in the 30 days before today, arithmetic is consistent (no tampering) and most files
 * are clean (trust 100); a few carry an edited-software warning (trust 85), an alcohol line or a
 * personal-expense hint, so claims are not all identical.
 */

import { addDays, round2 } from "./format.mjs";
import { checksumChar } from "./gstin.mjs";
import { lineItem, makeReceipt } from "./receipts.mjs";
import { costFromSha, planForSample } from "./samples.mjs";
import { editedSoftware, pdfEditorProducer, sortBySeverity } from "./trust.mjs";

/** @typedef {import("./types.mjs").DocPlan} DocPlan */
/** @typedef {import("./types.mjs").ExpenseCategory} ExpenseCategory */
/** @typedef {import("./types.mjs").Receipt} Receipt */
/** @typedef {import("./types.mjs").LineItem} LineItem */
/** @typedef {import("./samples.mjs").SampleLibrary} SampleLibrary */

/** What the extractor says about a file named like a bad photo. */
export const UNREADABLE_MESSAGE =
  "We could not read this file. It looks blurry or corrupted. Retake the photo and try again.";

/** What the hosted demo says about a receipt it has no recording of (pipeline/process.py). */
export const DEMO_MISS_MESSAGE =
  "This demo reads only its recorded sample receipts. To read your own, run ClaimPilot with " +
  "your own API key (see the README).";

/** Filenames containing one of these words fail to read (case-insensitive). */
const UNREADABLE_NAME = /blur|corrupt|unreadable/i;

// --- deterministic randomness ---------------------------------------------------------------------

/**
 * A small deterministic generator (sfc32) seeded from a hex digest.
 * @param {string} hex
 */
export function makeRng(hex) {
  let a = Number.parseInt(hex.slice(0, 8), 16) >>> 0;
  let b = Number.parseInt(hex.slice(8, 16), 16) >>> 0;
  let c = Number.parseInt(hex.slice(16, 24), 16) >>> 0;
  let d = Number.parseInt(hex.slice(24, 32), 16) >>> 0;
  const next = () => {
    let t = (a + b) | 0;
    a = b ^ (b >>> 9);
    b = (c + (c << 3)) | 0;
    c = (c << 21) | (c >>> 11);
    d = (d + 1) | 0;
    t = (t + d) | 0;
    c = (c + t) | 0;
    return (t >>> 0) / 4294967296;
  };
  for (let i = 0; i < 16; i += 1) next();
  return {
    next,
    /** An integer from `lo` to `hi`, both included. */
    int: (/** @type {number} */ lo, /** @type {number} */ hi) =>
      lo + Math.floor(next() * (hi - lo + 1)),
    /** @template T @param {readonly T[]} items @returns {T} */
    pick: (items) => /** @type {T} */ (items[Math.floor(next() * items.length)]),
    chance: (/** @type {number} */ p) => next() < p,
  };
}

/** @typedef {ReturnType<typeof makeRng>} Rng */

// --- GSTIN (domain/gstin.py) ------------------------------------------------------------------------

/**
 * A syntactically valid (checksum-correct) but fictional GSTIN.
 * @param {string} stateCode
 * @param {Rng} rng
 */
function generateGstin(stateCode, rng) {
  const letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  const letter = () => letters.charAt(rng.int(0, 25));
  const pan =
    letter() +
    letter() +
    letter() +
    "CPHFATBLJG".charAt(rng.int(0, 9)) +
    letter() +
    String(rng.int(0, 9999)).padStart(4, "0") +
    letter();
  const first14 = `${stateCode}${pan}${rng.int(1, 9)}Z`;
  return first14 + checksumChar(first14);
}

// --- templates ---------------------------------------------------------------------------------------

/** [state name, GST state code] of the cities a bill can be from. */
const CITIES = [
  ["Mumbai", "27"],
  ["Pune", "27"],
  ["Bengaluru", "29"],
  ["Hyderabad", "36"],
  ["Chennai", "33"],
  ["Delhi", "07"],
  ["Kolkata", "19"],
  ["Ahmedabad", "24"],
  ["Jaipur", "08"],
  ["Kochi", "32"],
  ["Chandigarh", "04"],
  ["Indore", "23"],
];

/**
 * An item pool entry: description, price range per unit, quantity range.
 * @typedef {[string, number, number, number?, number?]} Entry
 */

/**
 * @typedef {Object} Template
 * @property {number} weight
 * @property {Receipt["doc_type"]} docType
 * @property {ExpenseCategory} category
 * @property {string[]} merchants
 * @property {Entry[]} items
 * @property {[number, number]} count  How many items.
 * @property {number} lead  The first `lead` entries of `items` are always on the bill.
 * @property {number | null} gst  Percent, or null when the bill prints no GST.
 * @property {Receipt["payment_method"][]} payments
 * @property {[number, number]} confidence  Category confidence range.
 * @property {boolean} [pdf]  May be a PDF.
 * @property {string} invoice  Invoice number prefix.
 */

/** @type {Template[]} */
const TEMPLATES = [
  {
    weight: 14,
    docType: "restaurant_bill",
    category: "meals",
    merchants: [
      "Kesar Kitchen",
      "Banyan Leaf Cafe",
      "Masala Lane",
      "Tiffin Tales",
      "Spice Route Bistro",
    ],
    items: [
      ["Veg Thali", 150, 260],
      ["Paneer Butter Masala", 210, 320],
      ["Butter Naan", 40, 70, 1, 4],
      ["Masala Dosa", 90, 160],
      ["Filter Coffee", 45, 90, 1, 3],
      ["Veg Biryani", 180, 300],
      ["Fresh Lime Soda", 60, 110, 1, 2],
      ["Dal Tadka", 150, 240],
    ],
    count: [2, 4],
    lead: 0,
    gst: 5,
    payments: ["card", "upi", "cash"],
    confidence: [0.88, 0.97],
    invoice: "",
  },
  {
    weight: 4,
    docType: "restaurant_bill",
    category: "client_entertainment",
    merchants: [
      "Harbour Lights Bistro",
      "Lakeview Grill",
      "Maple & Mint Dining",
      "Olive Courtyard",
    ],
    items: [
      ["Tasting Menu", 1200, 1800, 2, 4],
      ["Appetiser Platter", 650, 950, 1, 2],
      ["Signature Main Course", 480, 780, 2, 4],
      ["Dessert Trio", 320, 520, 1, 3],
      ["Sparkling Water", 90, 150, 2, 4],
    ],
    count: [3, 4],
    lead: 0,
    gst: 5,
    payments: ["card"],
    confidence: [0.72, 0.84],
    invoice: "",
  },
  {
    weight: 12,
    docType: "cab_receipt",
    category: "local_conveyance",
    merchants: ["Zippy Cabs", "Namma Rides", "CityGo Cabs", "Swift Ride", "Metro Wheels"],
    items: [
      ["Base Fare", 45, 95],
      ["Distance Fare", 120, 520],
      ["Ride Time Fare", 40, 190],
    ],
    count: [3, 3],
    lead: 3,
    gst: 5,
    payments: ["upi", "card"],
    confidence: [0.9, 0.97],
    invoice: "CRN",
  },
  {
    weight: 8,
    docType: "upi_payment",
    category: "local_conveyance",
    merchants: ["RAMESH YADAV", "SUNITA DEVI", "MOHAN LAL", "KAVITA JOSHI", "IMRAN SHAIKH"],
    items: [],
    count: [0, 0],
    lead: 0,
    gst: null,
    payments: ["upi"],
    confidence: [0.86, 0.94],
    invoice: "",
  },
  {
    weight: 8,
    docType: "fuel_slip",
    category: "fuel_vehicle",
    merchants: [
      "Bharat Fuel Point",
      "Sunrise Petroleum",
      "Highway Star Fuels",
      "Greenfield Fuel Stop",
    ],
    items: [["Power Petrol", 100, 112]],
    count: [1, 1],
    lead: 1,
    gst: null,
    payments: ["upi", "card", "cash"],
    confidence: [0.93, 0.98],
    invoice: "",
  },
  {
    weight: 8,
    docType: "mobile_bill",
    category: "mobile_internet",
    merchants: ["Tarang Telecom", "Orbit Broadband", "Vayu Mobile"],
    items: [
      ["Monthly Rental - Family Plan", 699, 1199],
      ["Usage Charges", 20, 180],
      ["Data Add-on Pack", 99, 299],
      ["International Roaming Pack", 199, 599],
    ],
    count: [2, 3],
    lead: 1,
    gst: 18,
    payments: ["netbanking", "unknown"],
    confidence: [0.92, 0.98],
    pdf: true,
    invoice: "MB",
  },
  {
    weight: 8,
    docType: "gst_invoice",
    category: "wfh_supplies",
    merchants: [
      "Pixel Mart Electronics",
      "Digital Hub Computers",
      "ByteBay Systems",
      "Ergo Works Furniture",
    ],
    items: [
      ["USB-C Hub 6-in-1", 900, 2200, 1, 2],
      ["Laptop Stand (Aluminium)", 900, 2100, 1, 2],
      ["Wireless Mouse", 500, 1400, 1, 2],
      ["Webcam 1080p", 1200, 3200],
      ["Mechanical Keyboard", 1800, 4200],
      ["Monitor Riser", 700, 1600],
    ],
    count: [1, 3],
    lead: 0,
    gst: 18,
    payments: ["card", "netbanking", "upi"],
    confidence: [0.82, 0.93],
    pdf: true,
    invoice: "INV/26-27/",
  },
  {
    weight: 4,
    docType: "gst_invoice",
    category: "learning",
    merchants: ["BrightPath Learning Pvt Ltd", "SkillForge Academy", "Northstar Institute"],
    items: [
      ["Online Course - Data Analytics Foundations", 2500, 6500],
      ["Certification Exam Fee", 1500, 4500],
      ["Workshop - Effective Presentations", 1800, 5200],
    ],
    count: [1, 1],
    lead: 0,
    gst: 18,
    payments: ["netbanking", "card"],
    confidence: [0.82, 0.95],
    pdf: true,
    invoice: "INV/26-27/",
  },
  {
    weight: 5,
    docType: "handwritten_bill",
    category: "misc",
    merchants: [
      "Shree Stationers",
      "Mehta General Store",
      "Balaji Provision Store",
      "Lakshmi Traders",
    ],
    items: [
      ["Notebooks (5)", 150, 320],
      ["Pens (box)", 60, 180],
      ["Chart Paper", 40, 120, 1, 5],
      ["Tea", 20, 60, 1, 6],
      ["Snacks", 80, 240],
    ],
    count: [2, 4],
    lead: 0,
    gst: null,
    payments: ["cash"],
    confidence: [0.6, 0.78],
    invoice: "",
  },
  {
    weight: 5,
    docType: "gst_invoice",
    category: "misc",
    merchants: ["Office Basics Stationery", "Paper Plane Supplies", "Deskmate Traders"],
    items: [
      ["A4 Paper Ream", 280, 520, 1, 6],
      ["Printer Cartridge", 900, 2400],
      ["Whiteboard Markers (set)", 120, 360, 1, 3],
      ["Desk Organiser", 250, 780],
    ],
    count: [2, 3],
    lead: 0,
    gst: 18,
    payments: ["card", "upi"],
    confidence: [0.75, 0.9],
    pdf: true,
    invoice: "INV/26-27/",
  },
];

/**
 * @param {Rng} rng
 * @param {Template[]} pool
 */
function weighted(rng, pool) {
  const total = pool.reduce((s, t) => s + t.weight, 0);
  let roll = rng.next() * total;
  for (const template of pool) {
    roll -= template.weight;
    if (roll < 0) return template;
  }
  return /** @type {Template} */ (pool[pool.length - 1]);
}

/**
 * @param {Rng} rng
 * @param {number} digits
 */
function digitString(rng, digits) {
  let out = String(rng.int(1, 9));
  while (out.length < digits) out += String(rng.int(0, 9));
  return out;
}

/**
 * Receipt line items for a template, within the 80..9,000 rupee range.
 * @param {Template} t
 * @param {Rng} rng
 * @param {boolean} fuel
 * @returns {LineItem[]}
 */
function buildItems(t, rng, fuel) {
  const count = rng.int(t.count[0], t.count[1]);
  const chosen = t.items.slice(0, t.lead);
  const rest = t.items.slice(t.lead);
  while (chosen.length < count && rest.length) {
    chosen.push(/** @type {Entry} */ (rest.splice(rng.int(0, rest.length - 1), 1)[0]));
  }
  return chosen.map(([description, lo, hi, qlo = 1, qhi = 1]) => {
    if (fuel) {
      const litres = round2(8 + rng.next() * 32);
      const rate = round2(lo + rng.next() * (hi - lo));
      return lineItem(description, round2(litres * rate), litres, rate);
    }
    const quantity = rng.int(qlo, qhi);
    const unit = Math.max(lo, Math.round(rng.int(lo, hi) / 5) * 5);
    const hasQuantity = qhi > 1 || t.docType === "restaurant_bill";
    return hasQuantity
      ? lineItem(description, quantity * unit, quantity, unit)
      : lineItem(description, unit);
  });
}

/**
 * The plan for a file the mock does not know.
 * @param {string} sha256
 * @param {{ mediaType: string; today: string }} options
 * @returns {DocPlan}
 */
export function generatePlan(sha256, { mediaType, today }) {
  const rng = makeRng(sha256);
  const isPdf = mediaType === "application/pdf";
  const template = weighted(rng, isPdf ? TEMPLATES.filter((t) => t.pdf) : TEMPLATES);
  const [city, stateCode] = rng.pick(CITIES);
  const merchant = rng.pick(template.merchants);
  const date = addDays(today, -rng.int(0, 29));
  const hour = String(rng.int(8, 21)).padStart(2, "0");
  const minute = String(rng.int(0, 59)).padStart(2, "0");
  const payment = rng.pick(template.payments);
  const alcoholBill = template.docType === "restaurant_bill" && rng.chance(0.08);
  const personalHint = template.docType === "handwritten_bill" && rng.chance(0.3);
  const edited = rng.chance(0.1);
  const confidence = round2(
    template.confidence[0] + rng.next() * (template.confidence[1] - template.confidence[0]),
  );

  /** @type {Receipt} */
  let receipt;
  if (template.docType === "upi_payment") {
    receipt = makeReceipt({
      doc_type: "upi_payment",
      merchant_name: merchant,
      date,
      time: `${hour}:${minute}`,
      total: rng.int(16, 90) * 5,
      payment_method: "upi",
      upi_reference: digitString(rng, 12),
      languages: ["en"],
    });
  } else {
    let items = buildItems(template, rng, template.docType === "fuel_slip");
    if (alcoholBill) {
      const quantity = rng.int(1, 3);
      const unit = rng.int(36, 64) * 5;
      items = [...items, lineItem("Beer 650 ml", quantity * unit, quantity, unit)];
    }
    // Keep the bill inside the 80..9,000 range the demo promises.
    const gstFactor = template.gst ? 1 + template.gst / 100 : 1;
    while (items.length > 1 && items.reduce((s, i) => s + i.amount, 0) * gstFactor > 9000) {
      items = items.slice(0, -1);
    }
    const subtotal = round2(items.reduce((s, i) => s + i.amount, 0));
    const half = template.gst ? round2((subtotal * template.gst) / 200) : null;
    const total = half == null ? subtotal : round2(subtotal + half * 2);
    const handwritten = template.docType === "handwritten_bill";
    receipt = makeReceipt({
      doc_type: template.docType,
      merchant_name: merchant,
      merchant_gstin: template.gst ? generateGstin(stateCode ?? "27", rng) : null,
      merchant_city: city ?? null,
      invoice_number: `${template.invoice}${digitString(rng, template.invoice === "INV/26-27/" ? 4 : 6)}`,
      date,
      time: handwritten || isPdf ? null : `${hour}:${minute}`,
      line_items: items,
      subtotal: template.gst ? subtotal : null,
      taxes: template.gst ? { cgst: half, sgst: half, gst_rate_percent: template.gst } : undefined,
      total,
      payment_method: payment,
      languages: template.category === "meals" && rng.chance(0.2) ? ["en", "hi"] : ["en"],
      handwritten,
      low_confidence_fields: handwritten ? ["date"] : [],
    });
  }

  const engine = confidence < 0.7 ? "llm" : "jev";
  const trustFindings = edited
    ? [
        isPdf
          ? pdfEditorProducer(rng.pick(["Sejda", "iLovePDF", "Smallpdf"]))
          : editedSoftware(rng.pick(["Adobe Photoshop", "Canva", "Snapseed", "Pixlr"])),
      ]
    : [];

  return {
    sample: null,
    receipt,
    decisions: {
      category: template.category,
      category_confidence: confidence,
      alcohol_present: alcoholBill ? 0.93 : round2(0.01 + rng.next() * 0.07),
      personal_expense: personalHint
        ? round2(0.55 + rng.next() * 0.25)
        : round2(0.01 + rng.next() * 0.09),
      engine,
    },
    trustFindings: sortBySeverity(trustFindings),
    boxes: {},
    hint: null,
    costUsd: costFromSha(sha256),
    failure: null,
  };
}

/**
 * What the pipeline will read from an uploaded file: the scenario of a known sample (recognised by
 * the sha256 of its bytes), or a generated receipt; a file named like a bad photo cannot be read.
 * In strict demo mode only the recorded samples can be read, like the hosted demo.
 * @param {SampleLibrary} library
 * @param {{ sha256: string; filename: string; mediaType: string; today: string }} file
 * @param {{ strict?: boolean }} [options]
 * @returns {DocPlan}
 */
export function planForUpload(library, file, options = {}) {
  const known = library.bySha.get(file.sha256);
  const plan = known
    ? planForSample(known, file.sha256)
    : generatePlan(file.sha256, { mediaType: file.mediaType, today: file.today });
  let failure = null;
  if (UNREADABLE_NAME.test(file.filename)) failure = UNREADABLE_MESSAGE;
  else if (options.strict && !known) failure = DEMO_MISS_MESSAGE;
  return { ...plan, failure };
}
