import { FileSearch, MessageCircleQuestion, SendHorizontal } from "lucide-react";
import type { ReactNode } from "react";

interface Step {
  icon: ReactNode;
  title: string;
  body: string;
}

const STEPS: readonly Step[] = [
  {
    icon: <FileSearch />,
    title: "Drop the whole pile",
    body: "Photos, PDFs, UPI screenshots. ClaimPilot reads every receipt, checks it against policy and for tampering, and groups them into claims.",
  },
  {
    icon: <MessageCircleQuestion />,
    title: "Answer once",
    body: "It only asks what it can't work out itself, in one combined question. Details already in your calendar are filled in for you.",
  },
  {
    icon: <SendHorizontal />,
    title: "Confirm and submit",
    body: "Nothing is ever sent to finance without your explicit confirmation. You see exactly what was flagged, and why.",
  },
];

/** The three-step explainer under the drop zone. */
export function Explainer() {
  return (
    <section aria-labelledby="how-heading">
      <h2 id="how-heading" className="sr-only">
        How it works
      </h2>
      <ol className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {STEPS.map((step, index) => (
          <li
            key={step.title}
            className="shadow-card relative rounded-2xl border border-slate-200 bg-white p-5"
          >
            <div className="mb-3 flex items-center gap-3">
              <span
                aria-hidden="true"
                className="flex size-10 items-center justify-center rounded-xl bg-indigo-50 text-indigo-700 [&>svg]:size-5"
              >
                {step.icon}
              </span>
              <span className="text-sm font-semibold text-teal-800">Step {index + 1}</span>
            </div>
            <h3 className="text-base font-semibold text-slate-900">{step.title}</h3>
            <p className="mt-1 text-sm leading-relaxed text-slate-600">{step.body}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}
