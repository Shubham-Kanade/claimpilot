"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { use } from "react";

import { ClaimEvidence } from "@/components/evidence/ClaimEvidence";
import { buttonClasses } from "@/components/ui/Button";
import { ErrorState, PageSkeleton } from "@/components/ui/feedback";
import { useClaim } from "@/lib/hooks/queries";
import { usePersona } from "@/lib/persona/PersonaProvider";

import { AssistantPanel } from "./AssistantPanel";
import { ClaimHeader } from "./ClaimHeader";
import { SubmitBar } from "./SubmitBar";

function BackLink() {
  return (
    <Link
      href="/claims"
      className="inline-flex items-center gap-1.5 rounded-lg text-sm font-semibold text-indigo-700 hover:underline"
    >
      <ArrowLeft className="size-4" aria-hidden="true" />
      My claims
    </Link>
  );
}

/**
 * /claims/[id], the hero screen. Left: the assistant (one combined question, one reply).
 * Right: the evidence (every receipt with its original file, extracted fields, decisions and
 * findings). Bottom: the sticky submit bar, the only way to submit.
 */
export function ClaimReviewScreen({ claimId }: { claimId: string }) {
  const { personaId, persona, employees, status, error: personaError, reload } = usePersona();
  const query = useClaim(claimId);

  if (status === "error") {
    return (
      <div className="mx-auto w-full max-w-3xl px-4 py-12">
        <ErrorState as="h1" error={personaError} onRetry={reload} />
      </div>
    );
  }

  if (query.isError) {
    return (
      <div className="mx-auto w-full max-w-3xl space-y-4 px-4 py-12">
        <BackLink />
        <ErrorState
          as="h1"
          error={query.error}
          onRetry={() => void query.refetch()}
          actions={
            <Link href="/claims" className={buttonClasses("secondary")}>
              Back to my claims
            </Link>
          }
        />
      </div>
    );
  }

  if (!query.data) {
    return (
      <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
        <PageSkeleton rows={2} />
      </div>
    );
  }

  const claim = query.data;
  const isOwner = claim.employee_id === personaId;
  const owner = employees.find((e) => e.id === claim.employee_id) ?? null;

  return (
    <div className="mx-auto w-full max-w-6xl space-y-5 px-4 pt-6 pb-40 sm:px-6 sm:pt-8 lg:px-8">
      <BackLink />
      <ClaimHeader claim={claim} owner={owner} />

      {!isOwner && persona ? (
        <p className="rounded-xl bg-sky-50 px-4 py-3 text-sm text-sky-900 ring-1 ring-sky-200 ring-inset">
          You&apos;re viewing {owner ? `${owner.name}'s` : "another employee's"} claim as{" "}
          {persona.name}. It is read-only here.
        </p>
      ) : null}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:items-start">
        <div className="lg:sticky lg:top-24 lg:max-h-[calc(100dvh-7rem)] lg:overflow-y-auto lg:rounded-2xl">
          <AssistantPanel key={claim.id} claim={claim} canAnswer={isOwner} />
        </div>
        <ClaimEvidence claim={claim} />
      </div>

      <SubmitBar key={claim.id} claim={claim} isOwner={isOwner} />
    </div>
  );
}

/** Route adapter: unwraps the params promise handed down by the (server) page. */
export function ClaimRoute({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  // Remount per claim and per persona: reply drafts, answers and the idempotency key must
  // never leak between claims or between people.
  const { personaId } = usePersona();
  return <ClaimReviewScreen key={`${personaId}:${id}`} claimId={id} />;
}
