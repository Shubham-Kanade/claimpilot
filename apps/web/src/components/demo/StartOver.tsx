"use client";

import { RotateCcw } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { InlineError } from "@/components/ui/feedback";
import type { ResetResult } from "@/lib/api/types";
import { setStartedOverNotice } from "@/lib/demo/notice";
import { pluralize } from "@/lib/format";
import { useDemoReset, useMeta } from "@/lib/hooks/queries";
import { usePersona } from "@/lib/persona/PersonaProvider";

/** "Removed 11 receipts and 10 claims." */
export function describeReset(result: ResetResult): string {
  return `Removed ${pluralize(result.documents, "receipt")} and ${pluralize(result.claims, "claim")}.`;
}

/**
 * "Start over" for the public demo (GET /v1/meta says `demo`): deletes the acting persona's
 * uploads and claims IN THIS VISITOR'S DEMO SESSION (an approver clears the whole session, never
 * another visitor's sandbox) so the sample receipts can be run again.
 * Destructive, so it always asks first; afterwards every cached query is refreshed and you land
 * on the upload screen with a confirmation. Renders nothing outside demo mode.
 */
export function StartOver({
  variant = "button",
  redirectTo = "/",
}: {
  variant?: "button" | "link";
  /** Where to go afterwards (null: stay on this page, e.g. when already on the upload screen). */
  redirectTo?: string | null;
}) {
  const router = useRouter();
  const meta = useMeta();
  const { isApprover } = usePersona();
  const reset = useDemoReset();
  const [open, setOpen] = useState(false);
  const cancelRef = useRef<HTMLButtonElement>(null);

  if (!meta.data?.demo) return null;

  function confirm() {
    reset.mutate(
      {},
      {
        onSuccess: (done) => {
          setStartedOverNotice(`Started over. ${describeReset(done)}`);
          setOpen(false);
          if (redirectTo) router.push(redirectTo);
        },
      },
    );
  }

  return (
    <>
      {variant === "link" ? (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="inline-flex items-center gap-1.5 text-sm font-semibold text-indigo-700 hover:underline"
        >
          <RotateCcw className="size-4" aria-hidden="true" />
          Start over
        </button>
      ) : (
        <Button
          variant="secondary"
          onClick={() => setOpen(true)}
          icon={<RotateCcw className="size-4" aria-hidden="true" />}
        >
          Start over
        </Button>
      )}

      <Dialog
        open={open}
        onClose={() => !reset.isPending && setOpen(false)}
        title="Start over?"
        description={
          isApprover
            ? "As an approver this clears everything in this demo session, including the approvals queue. Nobody else's data is affected."
            : "This deletes the receipts and claims you uploaded in this demo session so you can run the samples again. Nobody else's data is affected."
        }
        dismissible={!reset.isPending}
        initialFocusRef={cancelRef}
        footer={
          <>
            <Button
              ref={cancelRef}
              variant="secondary"
              onClick={() => setOpen(false)}
              disabled={reset.isPending}
            >
              Cancel
            </Button>
            <Button variant="danger" onClick={confirm} loading={reset.isPending}>
              Delete and start over
            </Button>
          </>
        }
      >
        <p className="text-sm leading-relaxed text-slate-700">
          Nothing was sent to finance from here, and no AI model is called. This only clears the
          demo data.
        </p>
        {reset.isError ? <InlineError className="mt-3" error={reset.error} /> : null}
      </Dialog>
    </>
  );
}
