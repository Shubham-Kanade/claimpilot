/**
 * Friendly names for the GENERATED API types (src/lib/api/schema.d.ts, produced by
 * `npm run gen:api` from openapi.json). Nothing here is hand-written API shape: every type is an
 * alias or is derived from the generated `components` / `paths`.
 */
import type { components, paths } from "./schema";

type S = components["schemas"];

export type ClaimView = S["ClaimView"];
export type ClaimStatus = S["ClaimStatus"];
export type ClaimMode = S["ClaimMode"];
export type OpenQuestion = S["OpenQuestion"];
export type QuestionKind = S["QuestionKind"];
export type Finding = S["Finding"];
export type Severity = S["Severity"];
export type FindingSource = S["FindingSource"];
export type DocumentView = S["DocumentView"];
export type ProcessedDocument = S["ProcessedDocument"];
export type ExtractedReceipt = S["ExtractedReceipt"];
export type LineItem = S["LineItem"];
export type TaxBreakup = S["TaxBreakup"];
export type Decisions = S["Decisions"];
export type DocType = S["DocType"];
export type ExpenseCategory = S["ExpenseCategory"];
export type PaymentMethod = S["PaymentMethod"];
export type Box = S["Box"];
export type BatchView = S["BatchView"];
export type BatchCreated = S["BatchCreated"];
export type DocumentRef = S["DocumentRef"];
export type Employee = S["Employee"];
export type Me = S["Me"];
export type MetaInfo = S["MetaInfo"];
export type RouteInfo = S["RouteInfo"];
export type Stats = S["Stats"];
export type PromptOut = S["PromptOut"];
export type ReplyOut = S["ReplyOut"];
export type Problem = S["Problem"];
export type ResetResult = S["ResetResult"];

export type BatchStarted = S["BatchStarted"];
export type DocumentExtracted = S["DocumentExtracted"];
export type DocumentChecked = S["DocumentChecked"];
export type DocumentFailed = S["DocumentFailed"];
export type ClaimsReady = S["ClaimsReady"];
export type BatchDone = S["BatchDone"];
export type BatchFailed = S["BatchFailed"];

/** The discriminated union of progress events, taken straight from the /history response. */
export type PipelineEvent =
  paths["/v1/batches/{batch_id}/history"]["get"]["responses"]["200"]["content"]["application/json"][number];

export type PipelineEventType = PipelineEvent["type"];

/** The `status` query values accepted by GET /v1/approvals. */
export type ApprovalStatus = "submitted" | "approved" | "rejected";

/** The `route` values on a ClaimView (the API types the field as a plain string). */
export type ClaimRoute = "auto_approve" | "finance_review";
