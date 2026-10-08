// @ts-check
/**
 * Request validation with FastAPI's error bodies (`{"detail": [{type, loc, msg, input}]}`, status
 * 422, `application/json`): the shapes of AnswersIn, ReplyIn, SubmitIn and DecisionIn
 * (api/schemas.py) and the `after` query parameter of /history. These are the framework's own
 * errors, distinct from the problem+json domain errors.
 */

import { ValidationFailure } from "./http.mjs";

/** @typedef {Record<string, unknown>} ErrorItem */

/**
 * @param {unknown} value
 * @returns {value is Record<string, unknown>}
 */
function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * @param {(string | number)[]} loc
 * @param {unknown} input
 * @returns {ErrorItem}
 */
function missing(loc, input) {
  return { type: "missing", loc, msg: "Field required", input };
}

/**
 * @param {unknown} body
 * @returns {ErrorItem}
 */
function notAnObject(body) {
  return {
    type: "model_attributes_type",
    loc: ["body"],
    msg: "Input should be a valid dictionary or object to extract fields from",
    input: body,
  };
}

/**
 * Unknown fields are an error (the backend's models forbid extras).
 * @param {Record<string, unknown>} body
 * @param {string[]} allowed
 * @returns {ErrorItem[]}
 */
function extras(body, allowed) {
  return Object.keys(body)
    .filter((key) => !allowed.includes(key))
    .map((key) => ({
      type: "extra_forbidden",
      loc: ["body", key],
      msg: "Extra inputs are not permitted",
      input: body[key],
    }));
}

/**
 * Pydantic's lax boolean: true/false, 0/1 and a few spellings of them.
 * @param {unknown} value
 * @returns {boolean | ErrorItem}  The boolean, or the error (without `loc`).
 */
function parseBool(value) {
  if (typeof value === "boolean") return value;
  if (value === 0 || value === 1) return value === 1;
  if (typeof value === "string") {
    const lowered = value.trim().toLowerCase();
    if (["1", "on", "t", "true", "y", "yes"].includes(lowered)) return true;
    if (["0", "off", "f", "false", "n", "no"].includes(lowered)) return false;
    return {
      type: "bool_parsing",
      msg: "Input should be a valid boolean, unable to interpret input",
      input: value,
    };
  }
  return { type: "bool_type", msg: "Input should be a valid boolean", input: value };
}

/**
 * @param {unknown} value
 * @param {string} field
 * @param {{ min?: number; max?: number }} [limits]
 * @returns {string | ErrorItem}
 */
function parseString(value, field, limits = {}) {
  const loc = ["body", field];
  if (typeof value !== "string") {
    return { type: "string_type", loc, msg: "Input should be a valid string", input: value };
  }
  if (limits.min !== undefined && value.length < limits.min) {
    return {
      type: "string_too_short",
      loc,
      msg: `String should have at least ${limits.min} character${limits.min === 1 ? "" : "s"}`,
      input: value,
      ctx: { min_length: limits.min },
    };
  }
  if (limits.max !== undefined && value.length > limits.max) {
    return {
      type: "string_too_long",
      loc,
      msg: `String should have at most ${limits.max} characters`,
      input: value,
      ctx: { max_length: limits.max },
    };
  }
  return value;
}

/**
 * @param {ErrorItem[]} errors
 */
function raise(errors) {
  if (errors.length) throw new ValidationFailure(errors);
}

/**
 * The request has no body at all.
 * @returns {ErrorItem}
 */
function bodyMissing() {
  return missing(["body"], null);
}

/**
 * @param {unknown} body
 * @returns {{ answers: Record<string, string> }}
 */
export function parseAnswersIn(body) {
  if (body === undefined) raise([bodyMissing()]);
  if (!isRecord(body)) raise([notAnObject(body)]);
  const record = /** @type {Record<string, unknown>} */ (body);
  /** @type {ErrorItem[]} */
  const errors = extras(record, ["answers"]);
  /** @type {Record<string, string>} */
  const answers = {};
  if (!("answers" in record)) {
    errors.unshift(missing(["body", "answers"], record));
  } else if (!isRecord(record.answers)) {
    errors.unshift({
      type: "dict_type",
      loc: ["body", "answers"],
      msg: "Input should be a valid dictionary",
      input: record.answers,
    });
  } else {
    for (const [key, value] of Object.entries(record.answers)) {
      if (typeof value === "string") answers[key] = value;
      else {
        errors.unshift({
          type: "string_type",
          loc: ["body", "answers", key],
          msg: "Input should be a valid string",
          input: value,
        });
      }
    }
  }
  raise(errors);
  return { answers };
}

/**
 * @param {unknown} body
 * @returns {{ text: string }}
 */
export function parseReplyIn(body) {
  if (body === undefined) raise([bodyMissing()]);
  if (!isRecord(body)) raise([notAnObject(body)]);
  const record = /** @type {Record<string, unknown>} */ (body);
  /** @type {ErrorItem[]} */
  const errors = extras(record, ["text"]);
  let text = "";
  if (!("text" in record)) {
    errors.unshift(missing(["body", "text"], record));
  } else {
    const parsed = parseString(record.text, "text", { min: 1, max: 2000 });
    if (typeof parsed === "string") text = parsed;
    else errors.unshift(parsed);
  }
  raise(errors);
  return { text };
}

/**
 * @param {unknown} body
 * @returns {{ confirmed: boolean }}
 */
export function parseSubmitIn(body) {
  if (body === undefined) raise([bodyMissing()]);
  if (!isRecord(body)) raise([notAnObject(body)]);
  const record = /** @type {Record<string, unknown>} */ (body);
  /** @type {ErrorItem[]} */
  const errors = extras(record, ["confirmed"]);
  let confirmed = false;
  if (!("confirmed" in record)) {
    errors.unshift(missing(["body", "confirmed"], record));
  } else {
    const parsed = parseBool(record.confirmed);
    if (typeof parsed === "boolean") confirmed = parsed;
    else errors.unshift({ ...parsed, loc: ["body", "confirmed"] });
  }
  raise(errors);
  return { confirmed };
}

/**
 * @param {unknown} body
 * @returns {{ approved: boolean; comment: string }}
 */
export function parseDecisionIn(body) {
  if (body === undefined) raise([bodyMissing()]);
  if (!isRecord(body)) raise([notAnObject(body)]);
  const record = /** @type {Record<string, unknown>} */ (body);
  /** @type {ErrorItem[]} */
  const errors = extras(record, ["approved", "comment"]);
  let approved = false;
  let comment = "";
  if (!("approved" in record)) {
    errors.unshift(missing(["body", "approved"], record));
  } else {
    const parsed = parseBool(record.approved);
    if (typeof parsed === "boolean") approved = parsed;
    else errors.unshift({ ...parsed, loc: ["body", "approved"] });
  }
  if ("comment" in record) {
    const parsed = parseString(record.comment, "comment", { max: 500 });
    if (typeof parsed === "string") comment = parsed;
    else errors.push(parsed);
  }
  raise(errors);
  return { approved, comment };
}

/**
 * The `after` query parameter of /history: an integer, at least 0 (default 0).
 * @param {string | null} raw
 * @returns {number}
 */
export function parseAfter(raw) {
  if (raw === null) return 0;
  const trimmed = raw.trim();
  if (!/^[+-]?\d+$/.test(trimmed)) {
    throw new ValidationFailure([
      {
        type: "int_parsing",
        loc: ["query", "after"],
        msg: "Input should be a valid integer, unable to parse string as an integer",
        input: raw,
      },
    ]);
  }
  const value = Number(trimmed);
  if (value < 0) {
    throw new ValidationFailure([
      {
        type: "greater_than_equal",
        loc: ["query", "after"],
        msg: "Input should be greater than or equal to 0",
        input: raw,
        ctx: { ge: 0 },
      },
    ]);
  }
  return value;
}
