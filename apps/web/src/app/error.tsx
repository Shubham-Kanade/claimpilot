"use client";

import { useEffect } from "react";

import { Button } from "@/components/ui/Button";

export default function RouteError({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="mx-auto flex max-w-xl flex-col items-center px-6 py-20 text-center">
      <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Something went wrong</h1>
      <p className="mt-2 text-slate-600">
        This page hit an unexpected problem. Nothing was submitted or changed. Please try again.
      </p>
      <Button className="mt-6" onClick={() => retry()}>
        Try again
      </Button>
    </div>
  );
}
