// @ts-check
/**
 * Claims: grouping a pile of documents into claims, the questions to ask, the calendar, and the
 * state machine (draft -> needs_info <-> ready -> submitted -> approved | rejected). A port of the
 * backend's claims/{grouping,questions,state,service}.py, kept pure: every function returns a new
 * claim and never mutates the one it is given.
 *
 * Known sample documents carry a GroupHint (see samples.mjs) that fixes their claim's mode, title
 * and city; every other file is grouped like the backend does (one event claim per document for
 * client dinners, courses and conferences, one period claim per category and month for the rest).
 */

import { createHash } from "node:crypto";

import { POLICY_PARAMS } from "./data.mjs";
import {
  addDays,
  dayLabel,
  describe,
  monthLabel,
  parseIso,
  quote,
  round2,
  rupees,
  safeText,
  sentence,
  shortDayLabel,
  toIso,
} from "./format.mjs";
import { evaluateClaim, evaluateDocument, hotelStay, isAnswered, routeFor } from "./policy.mjs";

/** @typedef {import("./types.mjs").Claim} Claim */
/** @typedef {import("./types.mjs").Employee} Employee */
/** @typedef {import("./types.mjs").ExpenseCategory} ExpenseCategory */
/** @typedef {import("./types.mjs").Finding} Finding */
/** @typedef {import("./types.mjs").GroupHint} GroupHint */
/** @typedef {import("./types.mjs").OpenQuestion} OpenQuestion */
/** @typedef {import("./types.mjs").ProcessedDocument} ProcessedDocument */
/** @typedef {import("./types.mjs").QuestionKind} QuestionKind */
/** @typedef {import("./data.mjs").CalendarEvent} CalendarEvent */

/**
 * A processed document with the grouping hint of the sample it is a copy of (if any).
 * @typedef {Object} Member
 * @property {ProcessedDocument} doc
 * @property {GroupHint | null} hint
 */

const LOCKED = new Set(["submitted", "approved", "rejected"]);
const MAX_DATE = "9999-12-31";

/** @type {Record<string, string>} */
const PERIOD_LABELS = {
  mobile_internet: "Mobile & internet",
  local_conveyance: "Local conveyance",
  fuel_vehicle: "Fuel",
  meals: "Meals",
  misc: "Miscellaneous",
  wfh_supplies: "WFH supplies",
  medical: "Medical",
  relocation: "Relocation",
  accommodation: "Accommodation",
  travel_domestic: "Travel",
  travel_international: "International travel",
  client_entertainment: "Client entertainment",
  learning: "Learning",
  conference: "Conference",
};
/** @type {Record<string, string>} */
const EVENT_LABELS = {
  client_entertainment: "Client dinner",
  learning: "Learning",
  conference: "Conference",
};
const EVENT_CATEGORIES = new Set(Object.keys(EVENT_LABELS));
/** @type {QuestionKind[]} */
const KIND_ORDER = [
  "attendees",
  "business_purpose",
  "missing_date",
  "confirm_personal",
  "self_declaration",
  "other",
];
const STATED_ATTENDEES = /\b(?:attendees?|guests?|participants?|attended by)\s*[:-]\s*(.{3,200})/i;
const STATED_PURPOSE = /\b(?:purpose|reason|occasion)\s*[:-]\s*(.{3,200})/i;
const CLIENT_EVENT_KINDS = new Set(["client_dinner", "client_meeting"]);
const STAY_DATE = /\b(\d{1,2})[-/ ]([A-Za-z]{3})[a-z]*\b/;
/** @type {Record<string, number>} */
const MONTH_NUMBER = {
  jan: 1,
  feb: 2,
  mar: 3,
  apr: 4,
  may: 5,
  jun: 6,
  jul: 7,
  aug: 8,
  sep: 9,
  oct: 10,
  nov: 11,
  dec: 12,
};

// --- errors --------------------------------------------------------------------------------------

/** A change that the claim's state or the request does not allow. */
export class ClaimError extends Error {
  /**
   * @param {"locked" | "blank" | "unknown"} kind
   * @param {string} message
   */
  constructor(kind, message) {
    super(message);
    this.name = "ClaimError";
    this.kind = kind;
  }
}

// --- document helpers ------------------------------------------------------------------------------

/**
 * The printed date when it is a valid ISO date.
 * @param {ProcessedDocument} doc
 * @returns {string | null}
 */
export function expenseDate(doc) {
  const raw = doc.receipt.date;
  return raw && parseIso(raw) ? raw.trim() : null;
}

/**
 * @param {ProcessedDocument} doc
 */
export function amountOf(doc) {
  return doc.receipt.total ?? 0;
}

/**
 * @param {ProcessedDocument} doc
 */
function isStay(doc) {
  return doc.receipt.doc_type === "hotel_folio" || doc.decisions.category === "accommodation";
}

/**
 * First night of a hotel folio: the room lines are labelled by night (`15-Aug`), else the number of
 * room lines counts back from checkout.
 * @param {ProcessedDocument} doc
 * @returns {string | null}
 */
function stayStart(doc) {
  const checkout = expenseDate(doc);
  const parts = checkout ? parseIso(checkout) : null;
  if (!checkout || !parts) return null;
  /** @type {string[]} */
  const nights = [];
  for (const item of doc.receipt.line_items) {
    const match = STAY_DATE.exec(item.description);
    const month = match ? MONTH_NUMBER[(match[2] ?? "").toLowerCase()] : undefined;
    if (!match || !month) continue;
    const day = Number(match[1]);
    let night = toIso(parts.y, month, day);
    if (!parseIso(night)) continue;
    if (night > checkout) night = toIso(parts.y - 1, month, day); // a December night, January checkout
    nights.push(night);
  }
  if (nights.length) return nights.sort()[0] ?? null;
  const stay = hotelStay(doc.receipt);
  return addDays(checkout, -(stay?.nightsKnown ? stay.rates.length : 1));
}

/**
 * @param {ProcessedDocument[]} docs
 * @returns {{ start: string | null; end: string | null }}
 */
function datesOf(docs) {
  /** @type {string[]} */
  const starts = [];
  /** @type {string[]} */
  const ends = [];
  for (const doc of docs) {
    const day = expenseDate(doc);
    if (!day) continue;
    starts.push((isStay(doc) ? stayStart(doc) : null) ?? day);
    ends.push(day);
  }
  return {
    start: starts.length ? (starts.sort()[0] ?? null) : null,
    end: ends.length ? (ends.sort().at(-1) ?? null) : null,
  };
}

/**
 * `clm-<employee>-<10 hex of sha256 of the sorted document ids>`: the same documents always give
 * the same claim id.
 * @param {Employee} employee
 * @param {ProcessedDocument[]} docs
 */
export function claimId(employee, docs) {
  const joined = docs
    .map((d) => d.id)
    .sort()
    .join("|");
  return `clm-${employee.id}-${createHash("sha256").update(joined).digest("hex").slice(0, 10)}`;
}

// --- grouping (claims/grouping.py) ------------------------------------------------------------------

/**
 * @param {Employee} employee
 * @param {"trip" | "period" | "event"} mode
 * @param {ProcessedDocument[]} docs
 * @param {string} title
 * @param {string | null} city
 * @returns {Claim}
 */
function makeClaim(employee, mode, docs, title, city) {
  const ordered = [...docs].sort((a, b) => {
    const da = expenseDate(a) ?? MAX_DATE;
    const db = expenseDate(b) ?? MAX_DATE;
    return da === db ? (a.id < b.id ? -1 : a.id > b.id ? 1 : 0) : da < db ? -1 : 1;
  });
  const { start, end } = datesOf(ordered);
  const currencies = new Set(
    ordered.map((d) => (d.receipt.currency ?? "INR").trim().toUpperCase()),
  );
  return {
    id: claimId(employee, ordered),
    employee_id: employee.id,
    title,
    mode,
    status: "draft",
    document_ids: ordered.map((d) => d.id),
    total: round2(ordered.reduce((sum, d) => sum + amountOf(d), 0)),
    currency: currencies.size === 1 ? ([...currencies][0] ?? "INR") : "INR",
    start_date: start,
    end_date: end,
    city,
    findings: [],
    open_questions: [],
    submission_reference: null,
    batch_id: null,
    route: null,
  };
}

/**
 * The city most of the documents were bought in, else the employee's base city.
 * @param {ProcessedDocument[]} docs
 * @param {Employee} employee
 */
function modalCity(docs, employee) {
  /** @type {Map<string, number>} */
  const seen = new Map();
  for (const doc of docs) {
    const city = (doc.receipt.merchant_city ?? "").trim();
    if (city) seen.set(city, (seen.get(city) ?? 0) + 1);
  }
  const ranked = [...seen.entries()].sort(
    ([a, na], [b, nb]) => nb - na || (a < b ? -1 : a > b ? 1 : 0),
  );
  return ranked[0]?.[0] ?? employee.base_city;
}

/**
 * Which calendar month an undated document most likely belongs to: the newest month of its own
 * category, else the newest month of any category, else the current month.
 * @param {ProcessedDocument[]} everything
 * @param {string} today
 */
function monthGuesser(everything, today) {
  /** @type {Map<string, string>} */
  const byCategory = new Map();
  let newest = null;
  for (const doc of everything) {
    const day = expenseDate(doc);
    if (!day) continue;
    const month = day.slice(0, 7);
    const best = byCategory.get(doc.decisions.category);
    if (best === undefined || month > best) byCategory.set(doc.decisions.category, month);
    if (newest === null || month > newest) newest = month;
  }
  const fallback = newest ?? today.slice(0, 7);
  return (/** @type {ProcessedDocument} */ doc) => {
    const day = expenseDate(doc);
    return day ? day.slice(0, 7) : (byCategory.get(doc.decisions.category) ?? fallback);
  };
}

/**
 * Group one employee's processed documents into claims, in a deterministic order: by first date,
 * then mode, title and id.
 * @param {Employee} employee
 * @param {Member[]} members
 * @param {string} today  ISO date, the month of last resort for undated documents.
 * @returns {Claim[]}
 */
export function groupDocuments(employee, members, today) {
  /** @type {Map<string, Member>} */
  const unique = new Map();
  for (const member of members) unique.set(member.doc.id, member);
  const everything = [...unique.values()].map((m) => m.doc);
  const monthOf = monthGuesser(everything, today);

  /** @type {Map<string, Member[]>} */
  const buckets = new Map();
  for (const member of unique.values()) {
    const { doc, hint } = member;
    const category = doc.decisions.category;
    let key;
    if (hint?.mode === "trip") key = `trip|${hint.title}`;
    else if (hint ? hint.mode === "event" : EVENT_CATEGORIES.has(category)) {
      key = `event|${doc.sha256}`; // the same file twice is one occasion
    } else key = `period|${category}|${monthOf(doc)}`;
    buckets.set(key, [...(buckets.get(key) ?? []), member]);
  }

  /** @type {Claim[]} */
  const claims = [];
  for (const [key, bucket] of buckets) {
    const docs = bucket.map((m) => m.doc);
    const hint = bucket.find((m) => m.hint)?.hint ?? null;
    const first = docs[0];
    if (!first) continue;
    const category = first.decisions.category;
    if (hint) {
      claims.push(
        makeClaim(employee, hint.mode, docs, hint.title, hint.city ?? employee.base_city),
      );
    } else if (key.startsWith("event|")) {
      const label = EVENT_LABELS[category] ?? "Event";
      const day = expenseDate(first);
      const printed = (first.receipt.merchant_city ?? "").trim();
      const city = printed && category !== "learning" ? printed : null;
      claims.push(
        makeClaim(
          employee,
          "event",
          docs,
          day ? `${label} ${dayLabel(day)}` : `${label} (date needed)`,
          city ?? employee.base_city,
        ),
      );
    } else {
      const label = PERIOD_LABELS[category] ?? "Expenses";
      const [, , month = ""] = key.split("|");
      const [year, mm] = month.split("-").map(Number);
      const dated = docs.some((d) => expenseDate(d));
      claims.push(
        makeClaim(
          employee,
          "period",
          docs,
          dated && year && mm ? `${label} ${monthLabel(year, mm)}` : `${label} (date needed)`,
          modalCity(docs, employee),
        ),
      );
    }
  }
  return claims.sort((a, b) => {
    const left = [a.start_date ?? MAX_DATE, a.mode, a.title, a.id];
    const right = [b.start_date ?? MAX_DATE, b.mode, b.title, b.id];
    for (let i = 0; i < left.length; i += 1) {
      const x = left[i] ?? "";
      const y = right[i] ?? "";
      if (x !== y) return x < y ? -1 : 1;
    }
    return 0;
  });
}

// --- questions (claims/questions.py) --------------------------------------------------------------------

/**
 * @param {QuestionKind} kind
 * @param {string[]} scope
 */
function questionId(kind, scope) {
  const digest = createHash("sha256")
    .update([kind, ...[...scope].sort()].join("|"))
    .digest("hex")
    .slice(0, 8);
  return `q-${kind}-${digest}`;
}

/**
 * @param {QuestionKind} kind
 * @param {string[]} scope
 * @param {string[]} documentIds
 * @param {string} text
 * @param {string | null} [answer]
 * @returns {OpenQuestion}
 */
function question(kind, scope, documentIds, text, answer = null) {
  return { id: questionId(kind, scope), kind, text, document_ids: [...documentIds], answer };
}

/**
 * A value the receipt itself states ("Guests: ...", "Purpose: ..."); never taken from a document
 * that contains instructions to an AI system.
 * @param {ProcessedDocument} doc
 * @param {RegExp} pattern
 */
function statedOnDocument(doc, pattern) {
  if (doc.receipt.contains_instructions) return null;
  for (const item of doc.receipt.line_items ?? []) {
    const match = pattern.exec(item.description);
    if (match) {
      const value = safeText(match[1], 200);
      if (value) return `from receipt: ${value}`;
    }
  }
  return null;
}

/**
 * Anything beyond a total on the document: an invoice number, UPI reference, GSTIN or line items.
 * @param {ProcessedDocument} doc
 */
function hasReceiptEvidence(doc) {
  const r = doc.receipt;
  return Boolean(
    r.invoice_number || r.upi_reference || r.merchant_gstin || (r.line_items ?? []).length,
  );
}

/**
 * Small conveyance with no real receipt: the policy accepts the employee's own word (3.1).
 * @param {ProcessedDocument} doc
 */
function needsDeclaration(doc) {
  return (
    doc.decisions.category === "local_conveyance" &&
    doc.receipt.total != null &&
    amountOf(doc) <= POLICY_PARAMS.receiptThreshold &&
    !hasReceiptEvidence(doc)
  );
}

/**
 * @param {ProcessedDocument} doc
 */
function amountAndDay(doc) {
  const day = expenseDate(doc);
  return rupees(amountOf(doc)) + (day ? ` on ${shortDayLabel(day)}` : "");
}

/**
 * The questions this claim needs answered, with answers already given carried over.
 * @param {Claim} claim
 * @param {ProcessedDocument[]} docs
 * @returns {OpenQuestion[]}
 */
export function buildQuestions(claim, docs) {
  const byId = new Map(docs.map((d) => [d.id, d]));
  const members = claim.document_ids.flatMap((id) => {
    const doc = byId.get(id);
    return doc ? [doc] : [];
  });
  /** @type {OpenQuestion[]} */
  const wanted = [];

  if (claim.mode === "trip" && members.length) {
    wanted.push(
      question(
        "business_purpose",
        [claim.id],
        claim.document_ids,
        `What was the business purpose of the ${claim.title}?`,
      ),
    );
  }
  for (const doc of members) {
    const day = expenseDate(doc);
    const when = day ? ` on ${shortDayLabel(day)}` : "";
    if (doc.decisions.category === "client_entertainment") {
      const what = `the client dinner${when} (${rupees(amountOf(doc))})`;
      wanted.push(
        question(
          "attendees",
          [doc.id],
          [doc.id],
          `Who attended ${what}? Please give names and company.`,
          statedOnDocument(doc, STATED_ATTENDEES),
        ),
        question(
          "business_purpose",
          [doc.id],
          [doc.id],
          `What was the business purpose of ${what}?`,
          statedOnDocument(doc, STATED_PURPOSE),
        ),
      );
    }
    if (day === null) {
      wanted.push(
        question(
          "missing_date",
          [doc.id],
          [doc.id],
          `What is the date on ${describe(doc)}? It is missing or illegible.`,
        ),
      );
    }
    if (doc.decisions.personal_expense >= POLICY_PARAMS.personalThreshold) {
      wanted.push(
        question(
          "confirm_personal",
          [doc.id],
          [doc.id],
          `${sentence(describe(doc))} looks personal. Is it a business expense? If yes, say what ` +
            "it was for; if not, I will leave it out.",
        ),
      );
    }
  }
  const undeclared = members.filter(needsDeclaration);
  if (undeclared.length) {
    const items = undeclared.map(amountAndDay).join(", ");
    const text =
      undeclared.length === 1
        ? `There is no receipt for this small conveyance item (${items}). Policy lets you declare ` +
          "it yourself: please confirm it was for work and give the route or purpose."
        : `There is no receipt for these small conveyance items (${items}). Policy lets you declare ` +
          "them yourself: please confirm they were for work and give the route or purpose of each.";
    const ids = undeclared.map((d) => d.id);
    wanted.push(question("self_declaration", ids, ids, text));
  }

  const previous = new Map(claim.open_questions.map((q) => [q.id, q]));
  const merged = wanted.map((q) => {
    const earlier = previous.get(q.id);
    return earlier && isAnswered(earlier) ? { ...q, answer: earlier.answer ?? null } : q;
  });
  // Array.prototype.sort is stable: questions of one kind keep their order.
  return merged.sort((a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind));
}

/**
 * Open questions that still have no answer.
 * @param {Claim} claim
 */
export function unansweredOf(claim) {
  return claim.open_questions.filter((q) => !isAnswered(q));
}

/**
 * ONE message asking every unanswered question of the claim; null when there is nothing to ask.
 * @param {Claim} claim
 * @returns {string | null}
 */
export function combinedPrompt(claim) {
  const open = unansweredOf(claim);
  if (!open.length) return null;
  const intro =
    open.length === 1
      ? `To finish ${quote(claim.title)} I need one detail:`
      : `To finish ${quote(claim.title)} I need a few details:`;
  const lines = open.map((q, i) => `${i + 1}. ${q.text}`);
  return [intro, ...lines, ...(open.length > 1 ? ["You can answer in one message."] : [])].join(
    "\n",
  );
}

const CATEGORY_QUESTION_PREFIX = "q-category-";
/** These already ask what the document was for, so a second question would ask the same twice. */
const PURPOSE_ASKING_KINDS = new Set(["confirm_personal", "self_declaration"]);

/**
 * Ask what a document was for when System One was not sure of its category (below 0.7
 * confidence), unless the claim already asks about that document (pipeline/finalize.py).
 * @param {Claim} claim
 * @param {ProcessedDocument[]} docs
 * @param {number} [minConfidence]
 * @returns {Claim}
 */
export function withCategoryQuestions(claim, docs, minConfidence = 0.7) {
  const byId = new Map(docs.map((d) => [d.id, d]));
  const existing = new Set(claim.open_questions.map((q) => q.id));
  const explained = new Set(
    claim.open_questions
      .filter((q) => PURPOSE_ASKING_KINDS.has(q.kind))
      .flatMap((q) => q.document_ids ?? []),
  );
  /** @type {OpenQuestion[]} */
  const added = [];
  for (const id of claim.document_ids) {
    const doc = byId.get(id);
    if (!doc) continue;
    const qid = `${CATEGORY_QUESTION_PREFIX}${doc.id.slice(0, 12)}`;
    if (
      doc.decisions.category_confidence >= minConfidence ||
      existing.has(qid) ||
      explained.has(doc.id)
    ) {
      continue;
    }
    const day = expenseDate(doc);
    added.push({
      id: qid,
      kind: "other",
      text: `What was ${describe(doc)}${day ? ` on ${shortDayLabel(day)}` : ""} for?`,
      document_ids: [doc.id],
      answer: null,
    });
  }
  if (!added.length) return claim;
  return refreshStatus({ ...claim, open_questions: [...claim.open_questions, ...added] });
}

// --- calendar ---------------------------------------------------------------------------------------

/**
 * The one client event on the document's date; null when there is none or it is unclear.
 * @param {ProcessedDocument} doc
 * @param {CalendarEvent[]} events  Client dinners and meetings only.
 */
function eventForDocument(doc, events) {
  const day = expenseDate(doc);
  if (!day) return null;
  const sameDay = events.filter((e) => e.date === day);
  const preferred = doc.decisions.category === "client_entertainment" ? "client_dinner" : null;
  const narrowed = sameDay.filter((e) => e.kind.toLowerCase() === preferred);
  const pool = narrowed.length ? narrowed : sameDay;
  return pool.length === 1 ? (pool[0] ?? null) : null; // two candidates: better to ask than to guess
}

/**
 * @param {OpenQuestion} q
 * @param {Claim} claim
 * @param {Map<string, ProcessedDocument>} byId
 * @param {CalendarEvent[]} events
 * @returns {string | null}
 */
function calendarAnswer(q, claim, byId, events) {
  if (claim.mode === "trip" && q.kind === "business_purpose") {
    const { start_date: start, end_date: end } = claim;
    if (!start || !end) return null;
    const titles = events
      .filter((e) => start <= e.date && e.date <= end)
      .sort((a, b) => (a.date === b.date ? (a.title < b.title ? -1 : 1) : a.date < b.date ? -1 : 1))
      .map((e) => e.title);
    return titles.length ? `from calendar: ${titles.join("; ")}` : null;
  }
  for (const docId of q.document_ids ?? []) {
    const doc = byId.get(docId);
    const event = doc ? eventForDocument(doc, events) : null;
    if (!event) continue;
    if (q.kind === "business_purpose") return `from calendar: ${event.title}`;
    if (q.kind === "attendees" && event.attendees.length) {
      return `from calendar: ${event.title} (attendees: ${event.attendees.join(", ")})`;
    }
  }
  return null;
}

/**
 * Answer attendee and purpose questions from a calendar event on the same day. Only client dinners
 * and meetings count, and only when exactly one fits. Answered questions are left alone.
 * @param {Claim} claim
 * @param {ProcessedDocument[]} docs
 * @param {CalendarEvent[]} events  The owner's calendar.
 * @returns {Claim}
 */
export function applyCalendar(claim, docs, events) {
  const clientEvents = events.filter((e) => CLIENT_EVENT_KINDS.has(e.kind.toLowerCase()));
  const byId = new Map(docs.map((d) => [d.id, d]));
  const updated = claim.open_questions.map((q) => {
    if (isAnswered(q) || (q.kind !== "attendees" && q.kind !== "business_purpose")) return q;
    const answer = calendarAnswer(q, claim, byId, clientEvents);
    return answer ? { ...q, answer } : q;
  });
  return refreshStatus({ ...claim, open_questions: updated });
}

// --- state machine (claims/state.py) -----------------------------------------------------------------

/**
 * Derive draft / needs_info / ready from the questions; locked claims are left alone.
 * @param {Claim} claim
 * @returns {Claim}
 */
export function refreshStatus(claim) {
  if (LOCKED.has(claim.status)) return claim;
  const status = !claim.document_ids.length
    ? "draft"
    : unansweredOf(claim).length
      ? "needs_info"
      : "ready";
  return claim.status === status ? claim : { ...claim, status };
}

/**
 * Record the employee's answer to one question (a correction replaces an earlier answer).
 * @param {Claim} claim
 * @param {string} questionId
 * @param {string} answer
 * @returns {Claim}
 * @throws {ClaimError} locked (409), blank or unknown (422)
 */
export function answerQuestion(claim, questionId, answer) {
  if (LOCKED.has(claim.status)) {
    throw new ClaimError("locked", `a ${claim.status} claim can no longer be changed`);
  }
  const text = answer.trim();
  if (!text) throw new ClaimError("blank", "an answer cannot be blank");
  if (!claim.open_questions.some((q) => q.id === questionId)) {
    throw new ClaimError("unknown", questionId);
  }
  const open_questions = claim.open_questions.map((q) =>
    q.id === questionId ? { ...q, answer: text } : q,
  );
  return refreshStatus({ ...claim, open_questions });
}

/**
 * Submit a ready claim with its finance reference.
 * @param {Claim} claim
 * @param {string} reference
 * @returns {Claim}
 */
export function submitClaim(claim, reference) {
  return { ...claim, status: "submitted", submission_reference: reference };
}

/**
 * Finance's decision on a submitted claim.
 * @param {Claim} claim
 * @param {boolean} approved
 * @returns {Claim}
 */
export function decideClaim(claim, approved) {
  return { ...claim, status: approved ? "approved" : "rejected" };
}

// --- composing the pieces (claims/service.py, pipeline/process.py) --------------------------------------

/**
 * Attach every finding, build the questions and refresh the status of a grouped claim.
 * `claim.findings` ends up holding every finding that matters for the claim: the trust findings of
 * its documents, the policy findings per document and the claim-level policy findings; the ones
 * about a document carry its id.
 * @param {Claim} claim
 * @param {ProcessedDocument[]} docs  Every document of the batch (the claim picks its own).
 * @param {Employee} employee
 * @returns {Claim}
 */
export function finalizeClaim(claim, docs, employee) {
  const byId = new Map(docs.map((d) => [d.id, d]));
  const members = claim.document_ids.flatMap((id) => {
    const doc = byId.get(id);
    return doc ? [doc] : [];
  });
  /** @type {Finding[]} */
  const findings = [];
  const seen = new Set();
  /**
   * @param {Finding[]} items
   * @param {string | null} [documentId]
   */
  const add = (items, documentId = null) => {
    for (const item of items) {
      const stamped = item.document_id || !documentId ? item : { ...item, document_id: documentId };
      const key = [stamped.code, stamped.document_id, stamped.clause_id, stamped.message].join(
        "\u0000",
      );
      if (!seen.has(key)) {
        seen.add(key);
        findings.push(stamped);
      }
    }
  };
  for (const doc of members) {
    add(
      (doc.findings ?? []).filter((f) => f.source !== "policy"),
      doc.id,
    );
    add(evaluateDocument(employee, doc), doc.id);
  }
  add(evaluateClaim(employee, claim, members));

  const updated = { ...claim, findings };
  const open_questions = buildQuestions(updated, members);
  return refreshStatus({ ...updated, open_questions });
}

/**
 * Recompute findings, questions and status after something changed (an answer, say), keeping every
 * answer and the pipeline's own "what was this for?" questions (pipeline/finalize.py `refinalize`).
 * An answer can change the policy picture: the headcount of a client dinner changes the per-head
 * check, so the findings and the route change with it.
 * @param {Claim} claim
 * @param {ProcessedDocument[]} docs  The claim's documents.
 * @param {Employee} employee
 * @param {boolean} [categoryQuestions]
 * @returns {Claim}
 */
export function refinalizeClaim(claim, docs, employee, categoryQuestions = true) {
  const ours = claim.open_questions.filter((q) => q.id.startsWith(CATEGORY_QUESTION_PREFIX));
  let rebuilt = finalizeClaim(claim, docs, employee);
  if (ours.length) {
    const known = new Set(rebuilt.open_questions.map((q) => q.id));
    const kept = ours.filter((q) => !known.has(q.id));
    rebuilt = { ...rebuilt, open_questions: [...rebuilt.open_questions, ...kept] };
  }
  return refreshStatus(categoryQuestions ? withCategoryQuestions(rebuilt, docs) : rebuilt);
}

/**
 * The claims of one batch: group, finalize, let the calendar answer what it can, ask about unsure
 * categories and route each claim.
 * @param {Employee} employee
 * @param {Member[]} members
 * @param {{ today: string; calendar: CalendarEvent[]; categoryQuestions?: boolean }} options
 * @returns {Claim[]}
 */
export function buildClaims(employee, members, options) {
  const docs = members.map((m) => m.doc);
  const byId = new Map(docs.map((d) => [d.id, d]));
  return groupDocuments(employee, members, options.today).map((grouped) => {
    const finalized = finalizeClaim(grouped, docs, employee);
    const claimDocs = finalized.document_ids.flatMap((id) => {
      const doc = byId.get(id);
      return doc ? [doc] : [];
    });
    let claim = finalized;
    if (unansweredOf(claim).length && claim.start_date && claim.end_date) {
      claim = applyCalendar(claim, claimDocs, options.calendar);
    }
    if (options.categoryQuestions !== false) claim = withCategoryQuestions(claim, claimDocs);
    return { ...claim, route: routeFor(claim) };
  });
}
