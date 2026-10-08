import Link from "next/link";

import { buttonClasses } from "@/components/ui/Button";

export default function NotFound() {
  return (
    <div className="mx-auto flex max-w-xl flex-col items-center px-6 py-20 text-center">
      <p className="text-sm font-semibold text-indigo-700">404</p>
      <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">
        We couldn&apos;t find that page
      </h1>
      <p className="mt-2 text-slate-600">
        The link may be out of date. Head back and pick up where you left off.
      </p>
      <Link href="/" className={`${buttonClasses("primary")} mt-6`}>
        Back to upload
      </Link>
    </div>
  );
}
