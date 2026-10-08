import { ApiError } from "./client";

/**
 * The ONE place where API problem+json `type` codes become words a person understands.
 * (The codes are the backend's stable machine codes; see services/api/src/claimpilot/problem.py.)
 */

export type ErrorKind =
  "network" | "auth" | "forbidden" | "not-found" | "conflict" | "input" | "server";

export interface FriendlyError {
  /** The problem `type` code (or a synthetic one such as `network_error`). */
  type: string;
  kind: ErrorKind;
  /** Short heading. */
  title: string;
  /** One or two sentences: what happened and what to do next. */
  message: string;
  /** What the UI can offer: retry the call, choose a persona, or go and upload receipts. */
  action?: "retry" | "pick-persona" | "upload";
}

type Copy = Pick<FriendlyError, "kind" | "title" | "message" | "action">;
type CopyFactory = (error: ApiError) => Copy;

function fileNames(error: ApiError): string[] {
  const files = error.detail?.files;
  if (!Array.isArray(files)) return [];
  return files.flatMap((f) =>
    typeof f === "object" &&
    f !== null &&
    typeof (f as { filename?: unknown }).filename === "string"
      ? [(f as { filename: string }).filename]
      : [],
  );
}

function countOf(error: ApiError, key: string): number {
  const value = error.detail?.[key];
  return Array.isArray(value) ? value.length : 0;
}

const BY_TYPE: Record<string, Copy | CopyFactory> = {
  network_error: {
    kind: "network",
    title: "Can't reach ClaimPilot",
    message: "We couldn't connect to the service. Check your connection and try again.",
    action: "retry",
  },
  missing_persona: {
    kind: "auth",
    title: "Choose who you're acting as",
    message: "Pick a persona from the menu at the top of the page to continue.",
    action: "pick-persona",
  },
  unknown_persona: {
    kind: "auth",
    title: "Choose who you're acting as",
    message: "That persona isn't available any more. Pick one from the menu at the top.",
    action: "pick-persona",
  },
  approver_only: {
    kind: "forbidden",
    title: "Approvers only",
    message: "Only approvers can do this. Switch to an approver persona to review claims.",
  },
  not_your_claim: {
    kind: "forbidden",
    title: "This isn't your claim",
    message: "Only the employee who owns a claim can answer questions or submit it.",
  },
  batch_not_found: {
    kind: "not-found",
    title: "Upload not found",
    message: "We couldn't find that upload for this persona. It may belong to someone else.",
    action: "upload",
  },
  claim_not_found: {
    kind: "not-found",
    title: "Claim not found",
    message: "We couldn't find that claim for this persona. It may belong to someone else.",
  },
  document_not_found: {
    kind: "not-found",
    title: "Receipt not found",
    message: "We couldn't find that receipt for this persona.",
  },
  file_unavailable: {
    kind: "not-found",
    title: "Original file unavailable",
    message: "The original image is no longer stored, but the details we read are still shown.",
  },
  no_files: {
    kind: "input",
    title: "Nothing to process",
    message: "Add at least one receipt (photo, PDF or screenshot) and try again.",
  },
  too_many_files: {
    kind: "input",
    title: "Too many files",
    message: "You can upload up to 30 receipts at a time. Split the pile into two uploads.",
  },
  file_too_large: (error) => ({
    kind: "input",
    title: "A file is too large",
    message: `${error.title}. Try a smaller photo, or compress the PDF, then upload again.`,
  }),
  unsupported_files: (error) => {
    const names = fileNames(error);
    return {
      kind: "input",
      title: "Some files can't be read",
      message: `${names.length ? `${names.join(", ")} ${names.length === 1 ? "is" : "are"} not` : "Some files are not"} JPEG, PNG, WebP or PDF. Remove ${names.length === 1 ? "it" : "them"} and try again.`,
    };
  },
  claim_locked: {
    kind: "conflict",
    title: "This claim is already submitted",
    message: "Submitted claims can't be changed any more.",
  },
  claim_not_ready: (error) => {
    const open = countOf(error, "unanswered");
    return {
      kind: "conflict",
      title: "Not ready to submit yet",
      message:
        open > 0
          ? `${open === 1 ? "One question is" : `${open} questions are`} still open. Answer ${open === 1 ? "it" : "them"} in the assistant, then confirm.`
          : "The claim still needs a detail from you before it can be submitted.",
    };
  },
  claim_not_submitted: {
    kind: "conflict",
    title: "Not submitted yet",
    message: "Only a submitted claim can be approved or rejected.",
  },
  confirmation_required: {
    kind: "input",
    title: "Confirmation needed",
    message: "ClaimPilot never submits without your explicit confirmation. Confirm and try again.",
  },
  unknown_question: {
    kind: "conflict",
    title: "That question is no longer open",
    message: "Reload the claim to see the latest questions.",
    action: "retry",
  },
  blank_answer: {
    kind: "input",
    title: "Answer can't be blank",
    message: "Type your answer, then send it.",
  },
  comment_required: {
    kind: "input",
    title: "Add a reason",
    message: "Say why you're rejecting this claim. Finance keeps the reason on record.",
  },
  demo_disabled: {
    kind: "forbidden",
    title: "Start over isn't available",
    message: "Only the public demo can be reset. This deployment keeps your data.",
  },
  validation_error: {
    kind: "input",
    title: "Please check your input",
    message: "Something in the request wasn't valid. Review what you entered and try again.",
  },
};

const BY_STATUS: Record<number, Copy> = {
  401: BY_TYPE.missing_persona as Copy,
  403: {
    kind: "forbidden",
    title: "Not allowed",
    message: "This persona isn't allowed to do that.",
  },
  404: { kind: "not-found", title: "Not found", message: "We couldn't find what you asked for." },
  413: {
    kind: "input",
    title: "Upload too large",
    message: "That upload is too large. Try fewer or smaller files.",
  },
  429: {
    kind: "server",
    title: "Slow down a little",
    message: "Too many requests at once. Wait a moment and try again.",
    action: "retry",
  },
};

const SERVER_ERROR: Copy = {
  kind: "server",
  title: "Something went wrong on our side",
  message: "Nothing you did caused this. Please try again in a moment.",
  action: "retry",
};

/** Turn anything thrown by the API layer into friendly, actionable copy. */
export function friendlyError(error: unknown): FriendlyError {
  if (!(error instanceof ApiError)) {
    return { type: "unexpected", ...SERVER_ERROR };
  }
  const entry = BY_TYPE[error.type];
  const copy =
    typeof entry === "function"
      ? entry(error)
      : (entry ?? BY_STATUS[error.status] ?? (error.status >= 500 ? SERVER_ERROR : undefined));
  return {
    type: error.type,
    ...(copy ?? { kind: "server" as const, title: error.title, message: "Please try again." }),
  };
}

/** True when the problem means "choose a persona" (HTTP 401). */
export function needsPersona(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}
