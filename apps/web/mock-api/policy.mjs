// @ts-check
/**
 * The expense policy as far as the mock enforces it: hotel nightly cap (4.1), daily meals limit
 * (5.1), per-head cap on client entertainment (5.2), alcohol (6.1), mobile cap (8.1), learning
 * pre-approval (9.1) and personal expenses (10.1), plus the routing rule. Wording, severities,
 * field lists and the quoted clause text are the backend's (policy/rules/*.py,
 * config/policy.yaml). Not enforced: 2.1 (it would depend on the wall clock), 3.1, 7.x.
 */

import { POLICY_CLAUSES, POLICY_PARAMS } from "./data.mjs";
import { dayLabelPadded, formatInr, round2, sumOf } from "./format.mjs";
import { makeFinding } from "./trust.mjs";

/** @typedef {import("./types.mjs").Claim} Claim */
/** @typedef {import("./types.mjs").Employee} Employee */
/** @typedef {import("./types.mjs").Finding} Finding */
/** @typedef {import("./types.mjs").ProcessedDocument} ProcessedDocument */
/** @typedef {import("./types.mjs").Receipt} Receipt */
/** @typedef {import("./types.mjs").Severity} Severity */
/** @typedef {"tier1" | "tier2"} Tier */

/** Currency is rounded to paise; compare with a hair of slack so 9,000.00 is not "over" 9,000. */
const EPSILON = 0.005;
/** @type {Record<Tier, string>} */
const TIER_NAMES = { tier1: "Tier-1", tier2: "Tier-2" };

// --- cities (policy/cities.py) -------------------------------------------------------------------

/** @type {Record<string, string>} */
const ALIASES = {
  "new delhi": "delhi",
  bangalore: "bengaluru",
  bombay: "mumbai",
  madras: "chennai",
  calcutta: "kolkata",
  poona: "pune",
  gurgaon: "gurugram",
  cochin: "kochi",
  panjim: "panaji",
  bhubaneshwar: "bhubaneswar",
  trivandrum: "thiruvananthapuram",
  baroda: "vadodara",
  vizag: "visakhapatnam",
  mysore: "mysuru",
  pondicherry: "puducherry",
  allahabad: "prayagraj",
};
const ALIAS_PATTERN = new RegExp(
  `\\b(${Object.keys(ALIASES)
    .sort((a, b) => b.length - a.length)
    .join("|")})\\b`,
  "g",
);

/**
 * Canonical lower-case form of a city name ("Bengaluru, KA" and "Bangalore" agree), or null.
 * @param {string | null | undefined} name
 */
export function normaliseCity(name) {
  if (!name) return null;
  const head = name.split(/[,(/|\-–]/, 1)[0] ?? "";
  const letters = Array.from(head.toLowerCase(), (ch) =>
    /[\p{L}\p{N}\p{M}]/u.test(ch) ? ch : " ",
  );
  const cleaned = letters.join("").split(/\s+/).filter(Boolean).join(" ");
  if (!cleaned) return null;
  return cleaned.replace(ALIAS_PATTERN, (m) => ALIASES[m] ?? m);
}

/**
 * Clause 1.2: Tier-1 for the listed metros, Tier-2 for every other city, null for no city.
 * @param {string | null | undefined} city
 * @returns {Tier | null}
 */
export function tierOf(city) {
  const name = normaliseCity(city);
  if (name == null) return null;
  const padded = ` ${name} `;
  for (const listed of POLICY_PARAMS.tier1Cities) {
    const key = normaliseCity(listed);
    if (key && padded.includes(` ${key} `)) return "tier1";
  }
  return "tier2";
}

/**
 * Tier of the first city that is known, else of the employee's base city, else Tier-2.
 * @param {Employee} employee
 * @param {Array<string | null | undefined>} cities
 * @returns {Tier}
 */
function tierFor(employee, cities) {
  for (const city of [...cities, employee.base_city]) {
    const tier = tierOf(city);
    if (tier) return tier;
  }
  return "tier2";
}

// --- receipt text (policy/receipt_text.py) -------------------------------------------------------

const ALCOHOL = new RegExp(
  "\\b(beer|wine|whisk(?:e)?y|vodka|rum|gin|brandy|cocktail|lager|champagne|tequila|scotch|liquor|prosecco)\\b" +
    "|बीयर|वाइन|व्हिस्की|शराब",
  "i",
);
const NOT_ALCOHOL =
  /non[- ]?alcoholic|alcohol[- ]?free|zero[- ]?alcohol|0\.0\s*%|mocktail|ginger (?:beer|ale)|root beer/i;
const ROOM = /\b(?:room|rooms|tariff|accommodation|lodging|suite|stay|night|nights|rent)\b/i;
const EXTRA =
  /room service|service charge|laundry|mini[- ]?bar|restaurant|breakfast|lunch|dinner|food|beverage|\bbar\b|\bspa\b|telephone|parking|wi-?fi|internet|\bgst\b|\btax|\bcess\b|round[- ]?off|discount|deposit|advance|\bcab\b|transfer/i;
const NIGHTS = /(\d{1,2})\s*nights?\b/i;

/**
 * Line items that are alcoholic drinks.
 * @param {Receipt} receipt
 */
export function alcoholItems(receipt) {
  return receipt.line_items.filter(
    (item) => ALCOHOL.test(item.description) && !NOT_ALCOHOL.test(item.description),
  );
}

/**
 * @param {Receipt["line_items"][number]} item
 */
function lineNights(item) {
  const quantity = item.quantity;
  if (quantity != null && quantity > 1 && Number.isInteger(quantity)) return quantity;
  const match = NIGHTS.exec(item.description);
  return match && Number(match[1]) > 0 ? Number(match[1]) : 1;
}

/**
 * Per-night room rates of a hotel folio (before GST), or null when no room charge can be found.
 * @param {Receipt} receipt
 * @returns {{ rates: number[]; nightsKnown: boolean } | null}
 */
export function hotelStay(receipt) {
  const candidates = receipt.line_items.filter(
    (item) => item.amount > 0 && !EXTRA.test(item.description),
  );
  const roomLines = candidates.filter((item) => ROOM.test(item.description));
  /** @type {number[]} */
  const rates = [];
  for (const item of roomLines.length ? roomLines : candidates) {
    const nights = lineNights(item);
    const perNight =
      item.unit_price && item.quantity && item.quantity > 1
        ? item.unit_price
        : item.amount / nights;
    for (let i = 0; i < nights; i += 1) rates.push(round2(perNight));
  }
  if (rates.length) return { rates, nightsKnown: true };
  if (receipt.line_items.length) return null; // only extras (room service, laundry ...)
  let amount = receipt.subtotal ?? null;
  if (amount == null && receipt.total != null) {
    const t = receipt.taxes;
    amount =
      receipt.total - sumOf([t.cgst, t.sgst, t.igst, t.cess]) - (receipt.service_charge ?? 0);
  }
  if (amount == null || amount <= 0) return null;
  return { rates: [round2(amount)], nightsKnown: false };
}

// --- attendees ------------------------------------------------------------------------------------

/** @type {Record<string, number>} */
const NUMBER_WORDS = Object.fromEntries(
  [
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
  ].map((word, i) => [word, i + 1]),
);
const COUNT = `(\\d{1,3}|${Object.keys(NUMBER_WORDS).join("|")})`;
const CALENDAR_LIST = /attendees\s*:\s*([\s\S]+)$/i;
const BARE_COUNT = new RegExp(
  `^\\s*${COUNT}\\s*(?:people|persons?|guests?|pax|attendees?|of us|in all|including me)?\\s*\\.?$`,
  "i",
);
const PARTY_OF = new RegExp(
  `\\b(?:party|group|table) of ${COUNT}\\b|\\b${COUNT} (?:people|persons|guests|pax|attendees|of us)\\b`,
  "i",
);
const NAME_SPLIT = /,|;|\band\b|&|\n/i;
const SELF = new Set(["me", "myself", "i", "self"]);
const NOT_A_NAME = new Set(["n/a", "na", "none", "nil", "nobody", "no one", "unknown", "tbd"]);

/**
 * @param {string} token
 */
function toInt(token) {
  return NUMBER_WORDS[token.toLowerCase()] ?? Number(token);
}

/**
 * A listed name that is (part of) the employee's name; short fragments do not count.
 * @param {string} listed
 * @param {string} employeeName
 */
function isSamePerson(listed, employeeName) {
  const a = listed.toLowerCase().trim();
  const b = employeeName.toLowerCase().trim();
  return a === b || (a.length >= 4 && (b.includes(a) || a.includes(b)));
}

/**
 * How many people were at an event, from the attendees answer: "4", "party of four" and "six of
 * us" are headcounts; a list of names counts the names, plus the employee unless already listed.
 * Null when nothing countable is there.
 * @param {string} answer
 * @param {string} employeeName
 * @returns {number | null}
 */
export function attendeeHeadcount(answer, employeeName) {
  let text = answer.trim();
  const listed = CALENDAR_LIST.exec(text);
  if (listed) text = listed[1] ?? text;
  const bare = BARE_COUNT.exec(text);
  if (bare) return toInt(bare[1] ?? "") || null;
  const party = PARTY_OF.exec(text);
  if (party) return toInt(party[1] ?? party[2] ?? "") || null;
  text = text.replace(/\([^)]*\)/g, " "); // "(Orion Retail)" company notes
  const names = text
    .split(NAME_SPLIT)
    .map((part) => part.trim())
    .filter((part) => /\p{L}/u.test(part) && !NOT_A_NAME.has(part.toLowerCase()));
  if (!names.length) return null;
  const others = names.filter((n) => !SELF.has(n.toLowerCase()));
  const namedSelf = others.some((n) => isSamePerson(n, employeeName));
  return others.length + (namedSelf ? 0 : 1);
}

// --- findings ------------------------------------------------------------------------------------

/** @type {Record<string, Severity>} */
const CLAUSE_SEVERITY = {
  4.1: "high",
  5.1: "warn",
  5.2: "warn",
  6.1: "high",
  8.1: "warn",
  9.1: "warn",
  10.1: "warn",
};

/**
 * A policy finding that cites its clause (id and quoted text).
 * @param {string} clauseId
 * @param {string} code
 * @param {string} message
 * @param {{ severity?: Severity; fields?: string[]; expected?: number | string | null; actual?: number | string | null }} [extra]
 * @returns {Finding}
 */
function policyFinding(clauseId, code, message, extra = {}) {
  const clause = POLICY_CLAUSES[clauseId];
  return makeFinding({
    code,
    severity: extra.severity ?? CLAUSE_SEVERITY[clauseId] ?? "warn",
    message,
    fields: extra.fields,
    expected: extra.expected,
    actual: extra.actual,
    source: "policy",
    clause_id: clauseId,
    clause_text: clause ? clause.text : null,
  });
}

/**
 * @param {Employee} employee
 */
function gradeOf(employee) {
  return employee.grade.trim().toUpperCase();
}

/**
 * @param {ProcessedDocument} doc
 */
function amountOf(doc) {
  return doc.receipt.total ?? 0;
}

/**
 * @param {ProcessedDocument} doc
 */
function isInr(doc) {
  return (doc.receipt.currency ?? "INR").trim().toUpperCase() === "INR";
}

/**
 * 4.1: each night's room rate (before GST) against the cap for the employee's grade and the city tier.
 * @param {Employee} employee
 * @param {ProcessedDocument} doc
 * @returns {Finding[]}
 */
function hotelCap(employee, doc) {
  const receipt = doc.receipt;
  const isHotel = doc.decisions.category === "accommodation" || receipt.doc_type === "hotel_folio";
  if (!isHotel || !isInr(doc)) return [];
  const grade = gradeOf(employee);
  const caps = POLICY_PARAMS.nightlyCap[grade];
  const stay = hotelStay(receipt);
  if (!caps || !stay) return [];
  const tier = tierOf(receipt.merchant_city);
  const cap = caps[tier ?? "tier1"];
  const over = stay.rates.filter((rate) => rate > cap + EPSILON);
  if (!over.length) return [];
  const top = Math.max(...over);
  const where =
    tier == null
      ? "(the bill prints no city, so the Tier-1 cap applies)"
      : `in a ${TIER_NAMES[tier]} city`;
  if (!stay.nightsKnown) {
    return [
      policyFinding(
        "4.1",
        "hotel_over_cap",
        `This hotel bill is ${formatInr(top)} before GST and does not say how many nights it ` +
          `covers. If it is one night it is above the ${formatInr(cap)} cap for grade ${grade} ${where}.`,
        { severity: "warn", fields: ["line_items"], expected: cap, actual: top },
      ),
    ];
  }
  const extra = over.length > 1 ? ` (${over.length} of ${stay.rates.length} nights are over)` : "";
  return [
    policyFinding(
      "4.1",
      "hotel_over_cap",
      `Hotel night ${formatInr(top)} exceeds the ${formatInr(cap)} cap for grade ${grade} ${where}${extra}.`,
      { fields: ["line_items"], expected: cap, actual: top },
    ),
  ];
}

/**
 * 6.1: alcohol is never reimbursable (System One says likely, or a line item names a drink).
 * @param {ProcessedDocument} doc
 * @returns {Finding[]}
 */
function alcohol(doc) {
  const items = alcoholItems(doc.receipt);
  const likely = doc.decisions.alcohol_present >= POLICY_PARAMS.alcoholThreshold;
  if (!items.length && !likely) return [];
  if (items.length) {
    const excluded = round2(items.reduce((s, i) => s + i.amount, 0));
    const noun = items.length === 1 ? "line" : "lines";
    return [
      policyFinding(
        "6.1",
        "alcohol_not_reimbursable",
        `This bill includes alcohol (${items.length} ${noun}, ${formatInr(excluded)}). ` +
          "Alcohol is not reimbursable, so that amount is taken out of the claim.",
        { fields: ["line_items"], expected: excluded, actual: doc.receipt.total ?? null },
      ),
    ];
  }
  return [
    policyFinding(
      "6.1",
      "alcohol_not_reimbursable",
      "This bill appears to include alcohol, which is not reimbursable. The items do not say how " +
        "much, so the alcohol amount has to be taken out by hand.",
      { fields: ["line_items"], actual: doc.receipt.total ?? null },
    ),
  ];
}

/**
 * 8.1: a mobile or internet bill above the monthly cap for the employee's grade.
 * @param {Employee} employee
 * @param {ProcessedDocument} doc
 * @returns {Finding[]}
 */
function mobileCap(employee, doc) {
  if (doc.decisions.category !== "mobile_internet" || !isInr(doc)) return [];
  const grade = gradeOf(employee);
  const cap = POLICY_PARAMS.mobileMonthlyCap[grade];
  if (cap == null || amountOf(doc) <= cap + EPSILON) return [];
  return [
    policyFinding(
      "8.1",
      "mobile_over_cap",
      `This mobile or internet bill is ${formatInr(amountOf(doc))}, above the ${formatInr(cap)} ` +
        `monthly cap for grade ${grade}.`,
      { fields: ["total"], expected: cap, actual: amountOf(doc) },
    ),
  ];
}

/**
 * 9.1: learning above the threshold needs the manager's approval before purchase.
 * @param {ProcessedDocument} doc
 * @returns {Finding[]}
 */
function learningPreapproval(doc) {
  const threshold = POLICY_PARAMS.learningPreapproval;
  if (doc.decisions.category !== "learning" || !isInr(doc)) return [];
  if (amountOf(doc) <= threshold + EPSILON) return [];
  return [
    policyFinding(
      "9.1",
      "preapproval_required",
      `This learning expense is ${formatInr(amountOf(doc))}, above ${formatInr(threshold)}, so it ` +
        "needs your manager's approval from before the purchase.",
      { fields: ["total"], expected: threshold, actual: amountOf(doc) },
    ),
  ];
}

/**
 * 10.1: System One thinks this is a personal expense.
 * @param {ProcessedDocument} doc
 * @returns {Finding[]}
 */
function personalExpense(doc) {
  if (doc.decisions.personal_expense < POLICY_PARAMS.personalThreshold) return [];
  return [
    policyFinding(
      "10.1",
      "personal_expense_flagged",
      `This ${formatInr(amountOf(doc))} expense looks personal rather than business, and ` +
        "personal expenses are not reimbursable.",
      { actual: amountOf(doc) },
    ),
  ];
}

/**
 * Policy findings decidable from one document, in clause order; each carries the document id.
 * @param {Employee} employee  The claim's owner (limits depend on the grade).
 * @param {ProcessedDocument} doc
 * @returns {Finding[]}
 */
export function evaluateDocument(employee, doc) {
  return [
    ...hotelCap(employee, doc),
    ...alcohol(doc),
    ...mobileCap(employee, doc),
    ...learningPreapproval(doc),
    ...personalExpense(doc),
  ].map((finding) => ({ ...finding, document_id: doc.id }));
}

/**
 * 5.1: all of a day's meal bills together against the daily limit of the city tier.
 * @param {Employee} employee
 * @param {Claim} claim
 * @param {ProcessedDocument[]} docs
 * @returns {Finding[]}
 */
function mealsDailyLimit(employee, claim, docs) {
  /** @type {Map<string, ProcessedDocument[]>} */
  const byDay = new Map();
  for (const doc of docs) {
    const day = doc.receipt.date;
    if (doc.decisions.category === "meals" && day && isInr(doc)) {
      byDay.set(day, [...(byDay.get(day) ?? []), doc]);
    }
  }
  /** @type {Finding[]} */
  const findings = [];
  for (const day of [...byDay.keys()].sort()) {
    const meals = byDay.get(day) ?? [];
    const tiers = meals.map((m) => tierFor(employee, [m.receipt.merchant_city, claim.city]));
    const tier = tiers.includes("tier1") ? "tier1" : "tier2";
    const limit = POLICY_PARAMS.mealsDailyLimit[tier];
    const total = round2(meals.reduce((s, m) => s + amountOf(m), 0));
    if (total <= limit + EPSILON) continue;
    const bills = meals.length > 1 ? `${meals.length} bills` : "one bill";
    findings.push(
      policyFinding(
        "5.1",
        "meals_over_limit",
        `Meals on ${dayLabelPadded(day)} add up to ${formatInr(total)} (${bills}), above the ` +
          `${formatInr(limit)} daily limit for ${TIER_NAMES[tier]} cities.`,
        { fields: ["total"], expected: limit, actual: total },
      ),
    );
  }
  return findings;
}

/**
 * 5.2: a per-head cap on client entertainment, checked once the attendees are answered.
 * @param {Employee} employee
 * @param {Claim} claim
 * @param {ProcessedDocument[]} docs
 * @returns {Finding[]}
 */
function entertainmentCap(employee, claim, docs) {
  /** @type {Finding[]} */
  const findings = [];
  const cap = POLICY_PARAMS.entertainmentPerHead;
  for (const doc of docs) {
    if (doc.decisions.category !== "client_entertainment" || !isInr(doc)) continue;
    const question = claim.open_questions.find(
      (q) => q.kind === "attendees" && isAnswered(q) && (q.document_ids ?? []).includes(doc.id),
    );
    const headcount = question?.answer ? attendeeHeadcount(question.answer, employee.name) : null;
    if (!headcount) continue;
    const net = amountOf(doc) - alcoholItems(doc.receipt).reduce((s, i) => s + i.amount, 0);
    const perHead = round2(net / headcount);
    if (perHead <= cap + EPSILON) continue;
    findings.push({
      ...policyFinding(
        "5.2",
        "entertainment_over_cap",
        `This client entertainment costs ${formatInr(perHead)} per head (${formatInr(net)} for ` +
          `${headcount} people, alcohol excluded), above the ${formatInr(cap)} per-head cap.`,
        { fields: ["total"], expected: cap, actual: perHead },
      ),
      document_id: doc.id,
    });
  }
  return findings;
}

/**
 * Policy findings that need several documents or an answer: the daily meals limit (5.1) and the
 * per-head cap on client entertainment (5.2, once the attendees are known).
 * @param {Employee} employee
 * @param {Claim} claim
 * @param {ProcessedDocument[]} docs  The claim's own documents.
 * @returns {Finding[]}
 */
export function evaluateClaim(employee, claim, docs) {
  return [...mealsDailyLimit(employee, claim, docs), ...entertainmentCap(employee, claim, docs)];
}

// --- routing (claims/state.py `route`) -------------------------------------------------------------

/**
 * Auto-approve only a small, clean, complete claim; everything else goes to finance review.
 * @param {Claim} claim
 * @returns {"auto_approve" | "finance_review"}
 */
export function routeFor(claim) {
  const flagged = claim.findings.some((f) => f.severity === "high" || f.severity === "warn");
  const open = claim.open_questions.some((q) => !isAnswered(q));
  return flagged || open || claim.total > POLICY_PARAMS.autoApproveLimit
    ? "finance_review"
    : "auto_approve";
}

/**
 * @param {{ answer?: string | null }} question
 */
export function isAnswered(question) {
  return Boolean(question.answer && question.answer.trim());
}
