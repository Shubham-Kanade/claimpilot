import type {
  ClaimMode,
  ClaimStatus,
  DocType,
  ExpenseCategory,
  PaymentMethod,
  QuestionKind,
  Severity,
} from "./api/types";
import { humanize } from "./format";

/** Visual tone shared by chips, badges and callouts. */
export type Tone = "neutral" | "brand" | "accent" | "success" | "warn" | "danger" | "info";

export const CATEGORY_LABELS: Record<ExpenseCategory, string> = {
  travel_domestic: "Domestic travel",
  travel_international: "International travel",
  accommodation: "Accommodation",
  local_conveyance: "Local conveyance",
  meals: "Meals",
  client_entertainment: "Client entertainment",
  fuel_vehicle: "Fuel & vehicle",
  mobile_internet: "Mobile & internet",
  relocation: "Relocation",
  learning: "Learning",
  conference: "Conference",
  medical: "Medical",
  wfh_supplies: "WFH supplies",
  misc: "Miscellaneous",
};

export const DOC_TYPE_LABELS: Record<DocType, string> = {
  restaurant_bill: "Restaurant bill",
  gst_invoice: "GST invoice",
  hotel_folio: "Hotel folio",
  cab_receipt: "Cab receipt",
  flight_ticket: "Flight ticket",
  train_ticket: "Train ticket",
  fuel_slip: "Fuel slip",
  mobile_bill: "Mobile bill",
  upi_payment: "UPI payment",
  handwritten_bill: "Handwritten bill",
  other: "Document",
};

export const MODE_LABELS: Record<ClaimMode, string> = {
  trip: "Trip",
  period: "Monthly",
  event: "Event",
  allowance: "Allowance",
};

export const PAYMENT_LABELS: Record<PaymentMethod, string> = {
  cash: "Cash",
  card: "Card",
  upi: "UPI",
  wallet: "Wallet",
  netbanking: "Net banking",
  unknown: "Not stated",
};

export const QUESTION_KIND_LABELS: Record<QuestionKind, string> = {
  attendees: "Attendees",
  business_purpose: "Business purpose",
  missing_date: "Missing date",
  confirm_personal: "Business or personal?",
  self_declaration: "Self-declaration",
  other: "Detail",
};

export interface StatusInfo {
  label: string;
  tone: Tone;
  /** One line used as tooltip / helper text. */
  hint: string;
}

export const STATUS_INFO: Record<ClaimStatus, StatusInfo> = {
  draft: { label: "Draft", tone: "neutral", hint: "Just grouped from your receipts." },
  needs_info: {
    label: "Needs your input",
    tone: "warn",
    hint: "One question is waiting for your answer.",
  },
  ready: { label: "Ready to submit", tone: "brand", hint: "Everything is in. Review and confirm." },
  submitted: { label: "Submitted", tone: "accent", hint: "Sent to finance." },
  approved: { label: "Approved", tone: "success", hint: "Finance approved this claim." },
  rejected: { label: "Rejected", tone: "danger", hint: "Finance rejected this claim." },
};

export function statusInfo(status: string): StatusInfo {
  return (
    STATUS_INFO[status as ClaimStatus] ?? { label: humanize(status), tone: "neutral", hint: "" }
  );
}

export interface SeverityInfo {
  label: string;
  /** Plural heading for a group of findings. */
  heading: string;
  tone: Tone;
}

export const SEVERITY_INFO: Record<Severity, SeverityInfo> = {
  high: { label: "High risk", heading: "High risk", tone: "danger" },
  warn: { label: "Warning", heading: "Warnings", tone: "warn" },
  info: { label: "Note", heading: "Notes", tone: "info" },
};

/** Highest severity first. */
export const SEVERITY_ORDER: readonly Severity[] = ["high", "warn", "info"];

export function routeInfo(route: string | null | undefined): {
  label: string;
  tone: Tone;
  hint: string;
} {
  if (route === "auto_approve") {
    // The route is named after what the policy engine concluded, not after what happens next:
    // an approver still has to click. Say what the person sees.
    return {
      label: "Low risk",
      tone: "success",
      hint: "Low risk: small, clean and complete, so it can be approved in one click.",
    };
  }
  if (route === "finance_review") {
    return {
      label: "Finance review",
      tone: "warn",
      hint: "A person in finance will review this claim.",
    };
  }
  return { label: "Routing pending", tone: "neutral", hint: "" };
}

export function engineInfo(engine: string): {
  label: string;
  detail: string;
  system: "one" | "two" | "other";
} {
  switch (engine) {
    case "jev":
      return { label: "Jev", detail: "System One · fast typed decision", system: "one" };
    case "llm":
      return { label: "LLM", detail: "System Two · language model decided", system: "two" };
    case "jev+llm":
      return {
        label: "Jev + LLM",
        detail: "System One decided; the language model answered what Jev was unsure of",
        system: "one",
      };
    case "fake":
      return { label: "Test engine", detail: "Deterministic stand-in", system: "other" };
    case "truth":
      return { label: "Ground truth", detail: "Labelled test data", system: "other" };
    default:
      return { label: humanize(engine), detail: "Decision engine", system: "other" };
  }
}

export function verdictInfo(verdict: string | null | undefined): {
  label: string;
  tone: Tone;
  hint: string;
} {
  switch (verdict) {
    case "clean":
      return { label: "Looks genuine", tone: "success", hint: "No trust concerns found." };
    case "review":
      return {
        label: "Needs review",
        tone: "warn",
        hint: "A person should look at this document.",
      };
    case "block":
      return {
        label: "Blocked",
        tone: "danger",
        hint: "Not safe to approve automatically; a human must decide.",
      };
    default:
      return { label: "Not checked", tone: "neutral", hint: "" };
  }
}

export function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category as ExpenseCategory] ?? humanize(category);
}

export function docTypeLabel(docType: string): string {
  return DOC_TYPE_LABELS[docType as DocType] ?? humanize(docType);
}

/**
 * Where the LLM costs shown come from, by GET /v1/meta: `recorded` (llm_mode replay: the cost
 * recorded with the replayed answers, nothing is spent now), `recorded-and-live` (the hybrid
 * profile: live + llm_record, samples replay and other receipts are read live) or `live`.
 */
export type CostProfile = "recorded" | "recorded-and-live" | "live";

export function costProfile(
  meta: { llm_mode?: string; llm_record?: boolean } | null | undefined,
): CostProfile {
  if (meta?.llm_mode === "replay") return "recorded";
  if (meta?.llm_mode === "live" && meta.llm_record) return "recorded-and-live";
  return "live";
}
