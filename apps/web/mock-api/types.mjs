// @ts-check
/**
 * Shared JSDoc types for the mock API.
 *
 * Every wire shape comes from the GENERATED contract (src/lib/api/schema.d.ts), never from a
 * hand-written copy, so `npx tsc --noEmit` fails when the mock drifts from openapi.json. The types
 * below only add the mock's own bookkeeping (state records, simulation plans).
 */

/** @typedef {import("../src/lib/api/schema").components["schemas"]} Schemas */

/** @typedef {Schemas["AnswersIn"]} AnswersIn */
/** @typedef {Schemas["Box"]} Box */
/** @typedef {Schemas["ClaimMode"]} ClaimMode */
/** @typedef {Schemas["ClaimStatus"]} ClaimStatus */
/** @typedef {Schemas["Decisions"]} Decisions */
/** @typedef {Schemas["DocType"]} DocType */
/** @typedef {Schemas["Employee"]} Employee */
/** @typedef {Schemas["ExpenseCategory"]} ExpenseCategory */
/** @typedef {Schemas["Finding"]} Finding */
/** @typedef {Schemas["FindingSource"]} FindingSource */
/** @typedef {Schemas["LineItem"]} LineItem */
/** @typedef {Schemas["OpenQuestion"]} OpenQuestion */
/** @typedef {Schemas["QuestionKind"]} QuestionKind */
/** @typedef {Schemas["Severity"]} Severity */
/** @typedef {Schemas["TaxBreakup"]} TaxBreakup */

/**
 * A receipt as the mock always emits it: the backend serialises every field, so the optional
 * collections of the generated type are required here.
 * @typedef {Schemas["ExtractedReceipt"] & {
 *   line_items: LineItem[];
 *   taxes: TaxBreakup;
 *   languages: string[];
 *   low_confidence_fields: string[];
 * }} Receipt
 */

/**
 * A processed document as the mock emits it: the generated type with its collections required.
 * @typedef {Schemas["ProcessedDocument"] & {
 *   receipt: Receipt;
 *   findings: Finding[];
 *   boxes: Record<string, Box>;
 * }} ProcessedDocument
 */

/**
 * A claim as the mock stores it (the ClaimView with its optional collections made required).
 * @typedef {Schemas["ClaimView"] & { findings: Finding[]; open_questions: OpenQuestion[] }} Claim
 */

/**
 * @typedef {Schemas["BatchStarted"]
 *   | Schemas["DocumentExtracted"]
 *   | Schemas["DocumentChecked"]
 *   | Schemas["DocumentFailed"]
 *   | Schemas["ClaimsReady"]
 *   | Schemas["BatchDone"]
 *   | Schemas["BatchFailed"]} PipelineEvent
 */

/**
 * How a known sample document is assigned to a claim (see data.mjs).
 * `city: null` means "the employee's base city".
 * @typedef {Object} GroupHint
 * @property {"trip" | "period" | "event"} mode
 * @property {string} title
 * @property {string | null} city
 */

/**
 * What the simulated pipeline "reads" from one file: decided when the file is uploaded, revealed
 * step by step (extracted, then checked) by the batch timeline.
 * @typedef {Object} DocPlan
 * @property {string | null} sample  Key of the known sample document, or null for any other file.
 * @property {Receipt} receipt
 * @property {Decisions} decisions
 * @property {Finding[]} trustFindings  Trust findings of the file itself (duplicates come later).
 * @property {Record<string, Box>} boxes
 * @property {GroupHint | null} hint
 * @property {number} costUsd  What reading this file costs when it is not cached.
 * @property {string | null} failure  Set when the file cannot be read (blurry, corrupt ...).
 */

/**
 * @typedef {Object} DocRecord
 * @property {string} id
 * @property {string} batchId
 * @property {string} employeeId
 * @property {string} filename
 * @property {number} position
 * @property {"queued" | "processed" | "failed"} status
 * @property {string | null} error
 * @property {Buffer | null} bytes
 * @property {string} sha256
 * @property {DocPlan} plan
 * @property {ProcessedDocument | null} processed
 * @property {{ score: number; verdict: string } | null} trust
 * @property {number} costUsd  What was actually charged (0 when cached).
 */

/**
 * @typedef {Object} BatchRecord
 * @property {string} id
 * @property {string} employeeId
 * @property {"queued" | "processing" | "done" | "failed"} status
 * @property {number} total
 * @property {number} processed
 * @property {number} failed
 * @property {string} createdAt  ISO timestamp.
 * @property {string | null} finishedAt
 * @property {string | null} error
 * @property {string[]} docIds
 * @property {string[]} claimIds
 * @property {PipelineEvent[]} events
 * @property {Set<(index: number, event: PipelineEvent) => void>} listeners
 * @property {number} order  Monotonic; a higher number is a newer batch.
 */

/**
 * @typedef {Object} ClaimRecord
 * @property {Claim} view
 * @property {number} order  Order of the batch that produced the claim.
 * @property {number} position  Position of the claim inside its batch.
 */

/**
 * @typedef {Object} State
 * @property {{ batch: number; doc: number; fin: number }} counters
 * @property {number} batchOrder
 * @property {Map<string, BatchRecord>} batches
 * @property {Map<string, DocRecord>} docs
 * @property {Map<string, ClaimRecord>} claims
 * @property {Set<string>} seenHashes  sha256 of every file the pipeline has read since the seed.
 * @property {Map<string, Map<string, string>>} uploads  persona id -> sha256 -> first document id.
 * @property {Map<string, string>} idempotency  `${claimId}|${key}` -> submission reference.
 * @property {number[]} batchSeconds  Duration of every batch run since the seed (simulated seconds).
 */

/**
 * @typedef {Object} Persona
 * @property {Employee} employee
 * @property {boolean} isApprover
 */

/**
 * Everything a request handler or a batch simulation needs. `state` is replaced (not mutated) by a
 * reset, so always read it through the context.
 * @typedef {Object} Context
 * @property {State} state
 * @property {import("./samples.mjs").SampleLibrary} samples
 * @property {number} speed  Divides every simulated delay.
 * @property {AbortController} abort  Aborted on reset and close: stops every running simulation.
 * @property {() => Date} now
 * @property {{ maxFileBytes: number; maxFiles: number; categoryQuestions: boolean; demo: boolean; demoStrict: boolean; llmMode: string; llmRecord: boolean; dailyLlmBudgetUsd: number }} config
 * @property {Set<() => void>} streams  Open event streams; each callback ends one.
 */

export {};
