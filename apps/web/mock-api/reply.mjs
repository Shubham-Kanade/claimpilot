// @ts-check
/**
 * Turn the employee's one free-text reply into answers for the claim's open questions
 * (pipeline/reply.py). With exactly one open question the whole reply is the answer. With several:
 *
 *   1. a reply that follows the prompt's numbering ("1. ... 2. ...", every number once, in order)
 *      is split on it, like the backend does without calling a model;
 *   2. otherwise the backend asks a small LLM to split the reply; the mock does it
 *      deterministically: "Attendees: ...; Purpose: ..." style markers name the question they
 *      answer, and
 *   3. an unlabelled reply is cut on newlines, semicolons and sentence ends, and the segments fill
 *      the open questions in order (the first one is the attendees, the next the business purpose).
 *      Extra segments are appended to the last answer; a one-segment reply answers only the first
 *      question, and whatever stays open is asked again in a single message.
 */

import { combinedPrompt, unansweredOf } from "./claims.mjs";

/** @typedef {import("./types.mjs").Claim} Claim */
/** @typedef {import("./types.mjs").QuestionKind} QuestionKind */

const MARKER =
  /\b(attendees?|guests?|participants?|who attended|business purpose|purpose|reason|occasion)\s*[:\-–]\s*/gi;
const NUMBER_MARKER = /(?:^|\s)(\d{1,2})[.)]\s+/g;
const ABBREVIATION = /\b(?:mr|mrs|ms|dr|prof|sr|jr|st|vs|inc|ltd|pvt|co|no)\.$/i;

/**
 * @param {string} word
 * @returns {QuestionKind}
 */
function kindOfMarker(word) {
  return /purpose|reason|occasion/i.test(word) ? "business_purpose" : "attendees";
}

/**
 * Trim a segment and drop bullets and trailing punctuation.
 * @param {string} segment
 */
function clean(segment) {
  return segment
    .trim()
    .replace(/^(?:[-*•]|\d{1,2}[.)])\s+/, "")
    .replace(/[\s.;,]+$/, "")
    .trim();
}

/**
 * Cut on sentence ends, but not after an abbreviation ("Mr. Rao").
 * @param {string} text
 */
function sentences(text) {
  /** @type {string[]} */
  const out = [];
  let start = 0;
  for (const match of text.matchAll(/[.!?]+\s+/g)) {
    const end = (match.index ?? 0) + match[0].trimEnd().length;
    if (ABBREVIATION.test(text.slice(start, end))) continue;
    out.push(text.slice(start, end));
    start = (match.index ?? 0) + match[0].length;
  }
  out.push(text.slice(start));
  return out;
}

/**
 * @param {string} text
 */
function plainSegments(text) {
  return text
    .split(/[\n;]+/)
    .flatMap(sentences)
    .map(clean)
    .filter(Boolean);
}

/**
 * Answers written as "1. ... 2. ..." in the order the questions were asked, or null. Every number
 * from 1 to `count` must appear once, in order, with something after it (pipeline/reply.py
 * `parse_numbered`); a reply that merely contains digits is not mistaken for numbering.
 * @param {string} text
 * @param {number} count
 * @returns {string[] | null}
 */
export function parseNumbered(text, count) {
  if (count < 1) return null;
  let wanted = 1;
  /** @type {RegExpMatchArray[]} */
  const starts = [];
  for (const match of text.matchAll(NUMBER_MARKER)) {
    if (Number(match[1]) === wanted) {
      starts.push(match);
      wanted += 1;
    }
  }
  if (wanted !== count + 1) return null;
  const answers = starts.map((match, i) => {
    const from = (match.index ?? 0) + match[0].length;
    const to = starts[i + 1]?.index ?? text.length;
    return text.slice(from, to).trim();
  });
  return answers.every(Boolean) ? answers : null;
}

/**
 * Question id -> answer, for the questions the reply actually answers.
 * @param {Claim} claim
 * @param {string} text
 * @returns {Record<string, string>}
 */
export function interpretReply(claim, text) {
  const open = unansweredOf(claim);
  /** @type {Record<string, string>} */
  const understood = {};
  const [only] = open;
  if (!open.length) return understood;
  if (open.length === 1 && only) return { [only.id]: text.trim() };

  const numbered = parseNumbered(text, open.length);
  if (numbered) {
    open.forEach((q, i) => {
      understood[q.id] = numbered[i] ?? "";
    });
    return understood;
  }

  /** @type {Map<string, string>} */
  const assigned = new Map();
  const free = () => open.filter((q) => !assigned.has(q.id));
  const body = text.trim();

  /** @type {string[]} */
  let plain;
  const markers = [...body.matchAll(MARKER)];
  if (markers.length) {
    markers.forEach((m, i) => {
      const from = (m.index ?? 0) + m[0].length;
      const to = markers[i + 1]?.index ?? body.length;
      const answer = clean(body.slice(from, to));
      const target = free().find((q) => q.kind === kindOfMarker(m[1] ?? ""));
      if (answer && target) assigned.set(target.id, answer);
    });
    plain = plainSegments(body.slice(0, markers[0]?.index ?? 0));
  } else {
    plain = plainSegments(body);
  }

  const slots = free();
  plain.forEach((segment, i) => {
    const slot = slots[Math.min(i, slots.length - 1)];
    if (!slot) return;
    const earlier = assigned.get(slot.id);
    assigned.set(slot.id, earlier ? `${earlier} ${segment}` : segment);
  });

  for (const q of open) {
    const answer = assigned.get(q.id);
    if (answer) understood[q.id] = answer;
  }
  return understood;
}

/**
 * What to say next: the combined prompt for whatever is still open (null when done).
 * @param {Claim} claim
 * @param {boolean} understood  Whether any answer was taken from the reply.
 * @returns {string | null}
 */
export function followUpMessage(claim, understood) {
  const prompt = combinedPrompt(claim);
  if (prompt === null || understood) return prompt;
  return `I couldn't match that to my questions, so I'll ask again.\n${prompt}`;
}
