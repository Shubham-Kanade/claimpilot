"use client";

import { Cpu, Zap } from "lucide-react";

import { Card } from "@/components/ui/Card";
import { Chip } from "@/components/ui/Chip";
import { Skeleton } from "@/components/ui/feedback";
import type { MetaInfo } from "@/lib/api/types";
import { useMeta } from "@/lib/hooks/queries";
import { humanize } from "@/lib/format";

/** What each route does, in words (route names are the API's stable identifiers). */
const ROUTE_PURPOSE: Record<string, string> = {
  extraction: "Reads each receipt",
  extraction_retry: "Re-reads when unsure",
  decision_fallback: "Decides when Jev is unsure",
  reply_parse: "Understands your reply",
  locate: "Finds where each value is printed",
  agent_chat: "Chats with you",
  question_draft: "Drafts questions",
  policy_compile: "Compiles the policy",
  eval_judge: "Grades answers (evals)",
};

/** How GET /v1/meta's `decision_engine` is worded for people. */
const DECISION_ENGINES: Record<string, string> = {
  jev: "Jev (System One)",
  llm: "LLM (System Two)",
  fake: "Test engine",
};

/** Who decides what, in words that match how this deployment is configured. */
function EngineExplanation({ engine }: { engine: string }) {
  const strong = "font-semibold text-slate-800";
  return (
    <p className="text-sm leading-relaxed text-slate-600">
      {engine === "jev" ? (
        <>
          <strong className={strong}>System One</strong> (Jev) makes fast, typed decisions such as
          the expense category. <strong className={strong}>System Two</strong> (a language model)
          reads the receipts and talks to you.
        </>
      ) : engine === "llm" ? (
        <>
          <strong className={strong}>System Two</strong> (a language model) reads the receipts,
          decides the expense category and talks to you. Here the typed decision model Jev (
          <strong className={strong}>System One</strong>) is not switched on.
        </>
      ) : (
        <>
          <strong className={strong}>System Two</strong> (a language model) reads the receipts and
          talks to you; the expense category comes from the {humanize(engine).toLowerCase()} engine.
        </>
      )}{" "}
      Every card shows which engine did what.
    </p>
  );
}

export function EnginePanel({ meta }: { meta: MetaInfo }) {
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
      <div className="space-y-3">
        <div className="flex flex-wrap gap-2">
          <Chip tone="neutral" icon={<Cpu />}>
            LLM mode: {meta.llm_mode}
          </Chip>
          {meta.demo ? <Chip tone="info">Public demo</Chip> : null}
          <Chip tone="neutral">Runtime: {meta.runtime}</Chip>
          <Chip tone="accent" icon={<Zap />}>
            Decisions by: {DECISION_ENGINES[meta.decision_engine] ?? humanize(meta.decision_engine)}
          </Chip>
        </div>
        <EngineExplanation engine={meta.decision_engine} />
      </div>
      <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
        {meta.routes.map((route) => (
          <div key={route.route} className="min-w-0 border-b border-slate-100 pb-2">
            <dt className="text-xs font-medium text-slate-600">
              {ROUTE_PURPOSE[route.route] ?? humanize(route.route)}
            </dt>
            <dd className="truncate font-mono text-xs text-slate-900" title={route.model_id}>
              {route.model_id}
              {route.effort ? <span className="text-slate-600"> · {route.effort}</span> : null}
              {route.overridden ? <span className="text-amber-800"> · overridden</span> : null}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/** Transparency footer for the upload page: which engines and models do the work (GET /v1/meta). */
export function EngineFooter() {
  const meta = useMeta();
  return (
    <section aria-labelledby="engines-heading">
      <Card>
        <h2 id="engines-heading" className="mb-4 text-base font-semibold text-slate-900">
          Which AI does what
        </h2>
        {meta.isPending ? (
          <div className="space-y-2" aria-busy="true">
            <Skeleton className="h-5 w-64" />
            <Skeleton className="h-16 w-full" />
          </div>
        ) : meta.isError ? (
          <p className="text-sm text-slate-600">
            Engine details are unavailable right now, but ClaimPilot still works.
          </p>
        ) : (
          <EnginePanel meta={meta.data} />
        )}
      </Card>
    </section>
  );
}
