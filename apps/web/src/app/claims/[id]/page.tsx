import type { Metadata } from "next";
import { Suspense } from "react";

import { ClaimRoute } from "@/components/claims/ClaimReviewScreen";
import { PageSkeleton } from "@/components/ui/feedback";

export const metadata: Metadata = {
  title: "Review claim",
};

export default function ClaimPage({ params }: PageProps<"/claims/[id]">) {
  return (
    <Suspense
      fallback={
        <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
          <PageSkeleton rows={2} />
        </div>
      }
    >
      <ClaimRoute params={params} />
    </Suspense>
  );
}
