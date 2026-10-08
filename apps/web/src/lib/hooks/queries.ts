"use client";

import { useMutation, useQueryClient, type QueryClient } from "@tanstack/react-query";

import { api as publicApi, createApi } from "../api/client";
import type { ApprovalStatus, ClaimView } from "../api/types";
import { usePersona } from "../persona/PersonaProvider";
import { useApiQuery } from "./hydration";

/**
 * Server state, keyed by persona: every persona-scoped key starts with ["p", personaId, ...], so
 * switching persona never shows (or reuses) another persona's data and simply refetches.
 */
export const qk = {
  meta: ["meta"] as const,
  all: (p: string) => ["p", p] as const,
  claims: (p: string) => ["p", p, "claims"] as const,
  claim: (p: string, id: string) => ["p", p, "claim", id] as const,
  prompt: (p: string, id: string) => ["p", p, "prompt", id] as const,
  document: (p: string, id: string) => ["p", p, "document", id] as const,
  file: (p: string, id: string) => ["p", p, "file", id] as const,
  approvals: (p: string, status?: ApprovalStatus) =>
    status ? (["p", p, "approvals", status] as const) : (["p", p, "approvals"] as const),
  stats: (p: string) => ["p", p, "stats"] as const,
};

/** Engines and models in use (GET /v1/meta). Public: needs no persona. */
export function useMeta() {
  return useApiQuery({
    queryKey: qk.meta,
    queryFn: ({ signal }) => publicApi.meta(signal),
    staleTime: 5 * 60_000,
  });
}

/** All claims the acting persona may see, newest first. Filtering happens in the UI. */
export function useClaims() {
  const { api, personaId } = usePersona();
  return useApiQuery({
    queryKey: qk.claims(personaId ?? ""),
    queryFn: ({ signal }) => api.listClaims({}, signal),
    enabled: personaId !== null,
  });
}

export function useClaim(claimId: string) {
  const { api, personaId } = usePersona();
  return useApiQuery({
    queryKey: qk.claim(personaId ?? "", claimId),
    queryFn: ({ signal }) => api.getClaim(claimId, signal),
    enabled: personaId !== null,
  });
}

/** The ONE combined question for a claim (null `prompt` when nothing is open). */
export function usePrompt(claimId: string, enabled = true) {
  const { api, personaId } = usePersona();
  return useApiQuery({
    queryKey: qk.prompt(personaId ?? "", claimId),
    queryFn: ({ signal }) => api.getPrompt(claimId, signal),
    enabled: personaId !== null && enabled,
  });
}

export function useDocument(documentId: string) {
  const { api, personaId } = usePersona();
  return useApiQuery({
    queryKey: qk.document(personaId ?? "", documentId),
    queryFn: ({ signal }) => api.getDocument(documentId, signal),
    enabled: personaId !== null,
  });
}

/** The original file as a Blob. Only fetched once `enabled` (e.g. when a card is expanded). */
export function useDocumentFile(documentId: string, enabled: boolean) {
  const { api, personaId } = usePersona();
  return useApiQuery({
    queryKey: qk.file(personaId ?? "", documentId),
    queryFn: ({ signal }) => api.documentFile(documentId, signal),
    enabled: personaId !== null && enabled,
    staleTime: Infinity,
    gcTime: 5 * 60_000,
    retry: false,
  });
}

export function useApprovals(status: ApprovalStatus, enabled = true) {
  const { api, personaId } = usePersona();
  return useApiQuery({
    queryKey: qk.approvals(personaId ?? "", status),
    queryFn: ({ signal }) => api.approvals(status, signal),
    enabled: personaId !== null && enabled,
  });
}

export function useStats() {
  const { api, personaId } = usePersona();
  return useApiQuery({
    queryKey: qk.stats(personaId ?? ""),
    queryFn: ({ signal }) => api.stats(signal),
    enabled: personaId !== null,
  });
}

function refreshAfterClaimChange(queryClient: QueryClient, personaId: string, claim: ClaimView) {
  queryClient.setQueryData(qk.claim(personaId, claim.id), claim);
  void queryClient.invalidateQueries({ queryKey: qk.claims(personaId) });
  void queryClient.invalidateQueries({ queryKey: qk.prompt(personaId, claim.id) });
  void queryClient.invalidateQueries({ queryKey: qk.stats(personaId) });
  void queryClient.invalidateQueries({ queryKey: qk.approvals(personaId) });
}

/** POST /claims/{id}/reply: one free-text reply that may answer several questions. */
export function useReply(claimId: string) {
  const { api, personaId } = usePersona();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (text: string) => api.reply(claimId, text),
    onSuccess: (result) => refreshAfterClaimChange(queryClient, personaId ?? "", result.claim),
  });
}

/** POST /claims/{id}/answers: answer, or edit an earlier answer to, specific questions. */
export function useAnswers(claimId: string) {
  const { api, personaId } = usePersona();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (answers: Record<string, string>) => api.answer(claimId, answers),
    onSuccess: (claim) => refreshAfterClaimChange(queryClient, personaId ?? "", claim),
  });
}

/** POST /claims/{id}/submit. The caller owns the Idempotency-Key (stable across retries). */
export function useSubmitClaim(claimId: string) {
  const { api, personaId } = usePersona();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (idempotencyKey: string) => api.submit(claimId, idempotencyKey),
    onSuccess: (claim) => refreshAfterClaimChange(queryClient, personaId ?? "", claim),
  });
}

/** POST /claims/{id}/decision (approvers). Rejecting needs a non-blank comment. */
export function useDecision() {
  const { api, personaId } = usePersona();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { claimId: string; approved: boolean; comment?: string }) =>
      api.decide(vars.claimId, { approved: vars.approved, comment: vars.comment }),
    onSuccess: (claim) => refreshAfterClaimChange(queryClient, personaId ?? "", claim),
  });
}

/**
 * POST /v1/demo/reset ("Start over", public demo only). An approver clears everyone's data, so
 * every persona-scoped cache entry is dropped or refreshed, not just the acting persona's.
 * `persona` resets as someone else: used right after the app switched to the sample owner,
 * before React has re-rendered with the new acting persona.
 */
export function useDemoReset() {
  const { api } = usePersona();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ persona }: { persona?: string }) =>
      (persona ? createApi({ persona }) : api).demoReset(),
    onSuccess: () => {
      const stale = new Set(["file", "document", "claim", "prompt"]);
      queryClient.removeQueries({
        predicate: (query) => query.queryKey[0] === "p" && stale.has(String(query.queryKey[2])),
      });
      void queryClient.invalidateQueries({ queryKey: ["p"] });
    },
  });
}

/** POST /v1/batches: upload the pile (as `persona` when given, else as the acting persona). */
export function useCreateBatch() {
  const { api, personaId } = usePersona();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ files, persona }: { files: readonly File[]; persona?: string }) =>
      (persona ? createApi({ persona }) : api).createBatch(files),
    onSuccess: (_batch, { persona }) => {
      void queryClient.invalidateQueries({ queryKey: qk.claims(persona ?? personaId ?? "") });
    },
  });
}
