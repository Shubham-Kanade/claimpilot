import type { Metadata } from "next";
import { Suspense } from "react";

import { BatchRoute } from "@/components/batch/BatchScreen";
import { PageSkeleton } from "@/components/ui/feedback";

export const metadata: Metadata = {
  title: "Processing receipts",
};

export default function BatchPage({ params }: PageProps<"/batches/[id]">) {
  return (
    <Suspense
      fallback={
        <div className="mx-auto w-full max-w-6xl px-4 py-12">
          <PageSkeleton rows={3} />
        </div>
      }
    >
      <BatchRoute params={params} />
    </Suspense>
  );
}
