"use client";

import { CircleCheck, FlaskConical, Sparkles, Trash2, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, useSyncExternalStore } from "react";

import { StartOver } from "@/components/demo/StartOver";
import { StatsWidget } from "@/components/impact/StatsWidget";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ErrorState, InlineError } from "@/components/ui/feedback";
import { ApiError } from "@/lib/api/client";
import {
  getServerNotice,
  getStartedOverNotice,
  setStartedOverNotice,
  subscribeNotice,
} from "@/lib/demo/notice";
import { formatBytes, pluralize } from "@/lib/format";
import { useCreateBatch, useDemoReset, useMeta, useUploadLimits } from "@/lib/hooks/queries";
import { usePersona } from "@/lib/persona/PersonaProvider";
import { summarizeRejections, totalBytes, validateFiles } from "@/lib/upload/limits";
import { showToast } from "@/lib/toast";
import { loadSampleFiles, SAMPLE_PERSONA_ID, SAMPLE_RECEIPTS } from "@/lib/upload/samples";

import { DropZone } from "./DropZone";
import { EngineFooter } from "./EngineFooter";
import { Explainer } from "./Explainer";
import { SamplePile } from "./SamplePile";
import { SelectedFiles } from "./SelectedFiles";

const SAMPLES_UNAVAILABLE =
  "Couldn't load the sample receipts. Check your connection and try again.";

export function UploadScreen() {
  const router = useRouter();
  const {
    personaId,
    status,
    error: personaError,
    reload,
    isApprover,
    employees,
    setPersonaId,
  } = usePersona();
  const createBatch = useCreateBatch();
  const reset = useDemoReset();
  const meta = useMeta().data;
  const demo = meta?.demo ?? false;
  const limits = useUploadLimits();
  // The recorded profile can only read the 15 samples: say so next to the drop zone.
  const samplesOnly = demo && meta?.llm_mode === "replay";
  const startedOver = useSyncExternalStore(subscribeNotice, getStartedOverNotice, getServerNotice);
  const [files, setFiles] = useState<File[]>([]);
  const [notice, setNotice] = useState("");
  const [loadingSamples, setLoadingSamples] = useState(false);
  const [sampleFailure, setSampleFailure] = useState<unknown>(null);

  const busy = createBatch.isPending || loadingSamples;
  const ready = personaId !== null;
  // The public demo replays recordings that include Asha Menon's calendar, so the sample pile only
  // replays for her: the button switches to her first. Every trial then starts clean (her earlier
  // uploads are cleared). Without her in the directory the pile is uploaded as the acting persona,
  // and an approver's reset (it would empty the whole session's queue) is never done silently.
  const sampleOwner = demo ? employees.find((e) => e.id === SAMPLE_PERSONA_ID) : undefined;
  const clearsFirst = demo && (sampleOwner !== undefined || !isApprover);

  function addFiles(incoming: File[]) {
    const { accepted, rejected } = validateFiles(files, incoming, limits);
    setFiles((current) => [...current, ...accepted]);
    setNotice(summarizeRejections(rejected));
    createBatch.reset();
  }

  function upload(list: readonly File[], persona?: string) {
    createBatch.mutate(
      { files: list, persona },
      { onSuccess: (batch) => router.push(`/batches/${batch.batch_id}`) },
    );
  }

  async function trySamples() {
    setSampleFailure(null);
    setNotice("");
    setStartedOverNotice(null);
    setLoadingSamples(true);
    try {
      // Everything below acts as the sample owner explicitly: React has not re-rendered with the
      // switched persona yet, so the persona-bound API of this render would still be the old one.
      const actingAs = sampleOwner?.id;
      if (sampleOwner && sampleOwner.id !== personaId) {
        setPersonaId(sampleOwner.id);
        showToast(`The sample receipts are ${sampleOwner.name}'s, so we switched to her`);
      }
      if (clearsFirst) {
        try {
          await reset.mutateAsync({ persona: actingAs });
        } catch (error) {
          // "not the demo" is fine (nothing to clear); any other failure stops the trial
          if (!(error instanceof ApiError && error.type === "demo_disabled")) throw error;
        }
      }
      upload(await loadSampleFiles(), actingAs);
    } catch (error) {
      setSampleFailure(error);
    } finally {
      setLoadingSamples(false);
    }
  }

  if (status === "error") {
    return (
      <div className="mx-auto w-full max-w-3xl px-4 py-12">
        <ErrorState as="h1" error={personaError} onRetry={reload} />
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-6xl space-y-10 px-4 py-8 sm:px-6 sm:py-12 lg:px-8">
      <section aria-labelledby="page-title" className="max-w-3xl">
        <p className="mb-3 inline-flex items-center gap-1.5 rounded-full bg-teal-50 px-3 py-1 text-xs font-semibold text-teal-800 ring-1 ring-teal-200 ring-inset">
          <Sparkles className="size-3.5" aria-hidden="true" />
          AI expense agent
        </p>
        <h1
          id="page-title"
          className="text-3xl font-semibold tracking-tight text-slate-900 sm:text-4xl"
        >
          From a pile of receipts to a claim that&apos;s ready to submit
        </h1>
        <p className="mt-3 text-base leading-relaxed text-slate-600 sm:text-lg">
          Drop photos, PDFs and UPI screenshots. ClaimPilot reads and checks them, groups them into
          claims, and asks only what it can&apos;t work out itself.
        </p>
        <div className="mt-6 flex flex-col items-start gap-3 sm:flex-row sm:items-center">
          <Button
            variant="accent"
            size="lg"
            onClick={() => void trySamples()}
            loading={loadingSamples}
            disabled={!ready || busy}
            icon={<FlaskConical className="size-5" aria-hidden="true" />}
          >
            Try with sample receipts
          </Button>
          <p className="text-sm text-slate-600">
            {SAMPLE_RECEIPTS.length} synthetic receipts from one week of Asha Menon&apos;s expenses,
            including a duplicate, an edited total, a note aimed at an AI reviewer and an alcohol
            bill.
            {clearsFirst
              ? " Each run starts clean: the demo acts as Asha and clears her earlier uploads first."
              : ""}
          </p>
        </div>
        <SamplePile />
        {sampleFailure ? (
          sampleFailure instanceof ApiError ? (
            <InlineError className="mt-3" error={sampleFailure} />
          ) : (
            <InlineError className="mt-3" message={SAMPLES_UNAVAILABLE} />
          )
        ) : null}
        {startedOver ? (
          <p
            role="status"
            className="mt-4 inline-flex items-center gap-2 rounded-lg bg-emerald-50 py-2 pr-2 pl-3 text-sm text-emerald-900 ring-1 ring-emerald-200 ring-inset"
          >
            <CircleCheck className="size-4 shrink-0" aria-hidden="true" />
            {startedOver}
            <button
              type="button"
              onClick={() => setStartedOverNotice(null)}
              aria-label="Dismiss"
              className="inline-flex size-7 items-center justify-center rounded-md hover:bg-emerald-100"
            >
              <X className="size-4" aria-hidden="true" />
            </button>
          </p>
        ) : null}
        {demo ? (
          <div className="mt-3">
            <StartOver variant="link" redirectTo={null} />
          </div>
        ) : null}
      </section>

      <section aria-labelledby="upload-heading" className="space-y-4">
        <h2 id="upload-heading" className="sr-only">
          Upload your own receipts
        </h2>
        <DropZone
          onFiles={addFiles}
          disabled={busy || !ready}
          limits={limits}
          samplesOnly={samplesOnly}
        />

        <p role="status" aria-live="polite" className="min-h-0 text-sm text-amber-900">
          {notice}
        </p>

        {files.length > 0 ? (
          <Card className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="text-sm font-medium text-slate-800">
                {pluralize(files.length, "receipt")} ready · {formatBytes(totalBytes(files))}
              </p>
              <Button
                variant="ghost"
                size="sm"
                icon={<Trash2 className="size-4" aria-hidden="true" />}
                onClick={() => {
                  setFiles([]);
                  setNotice("");
                }}
                disabled={busy}
              >
                Clear all
              </Button>
            </div>
            <SelectedFiles
              files={files}
              onRemove={(index) => setFiles((current) => current.filter((_, i) => i !== index))}
            />
            {createBatch.isError ? <InlineError error={createBatch.error} /> : null}
            <Button
              size="lg"
              className="w-full sm:w-auto"
              onClick={() => upload(files)}
              loading={createBatch.isPending}
              disabled={busy || !ready}
            >
              {createBatch.isPending
                ? "Uploading…"
                : `Process ${pluralize(files.length, "receipt")}`}
            </Button>
          </Card>
        ) : createBatch.isError ? (
          <InlineError error={createBatch.error} />
        ) : null}
      </section>

      <Explainer />

      <section aria-labelledby="impact-heading" className="space-y-3">
        <h2 id="impact-heading" className="text-lg font-semibold text-slate-900">
          Impact so far
        </h2>
        <StatsWidget variant="compact" />
      </section>

      <EngineFooter />
    </div>
  );
}
