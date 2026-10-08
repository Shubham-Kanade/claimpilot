import { cn } from "@/lib/cn";

/** A determinate progress bar with a proper progressbar role and accessible name. */
export function ProgressBar({
  percent,
  label,
  className,
  tone = "brand",
}: {
  percent: number;
  label: string;
  className?: string;
  tone?: "brand" | "success" | "danger";
}) {
  const value = Math.max(0, Math.min(100, Math.round(percent)));
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={value}
      className={cn("h-2.5 w-full overflow-hidden rounded-full bg-slate-200", className)}
    >
      <div
        className={cn(
          "h-full rounded-full transition-[width] duration-500 ease-out motion-reduce:transition-none",
          tone === "brand" && "bg-gradient-to-r from-indigo-600 to-teal-500",
          tone === "success" && "bg-emerald-600",
          tone === "danger" && "bg-rose-600",
        )}
        style={{ width: `${value}%` }}
      />
    </div>
  );
}

/** A tiny 0-100 meter used for trust scores (the number is always shown next to it). */
export function ScoreMeter({ score, className }: { score: number; className?: string }) {
  const value = Math.max(0, Math.min(100, score));
  const color = value >= 80 ? "bg-emerald-600" : value >= 50 ? "bg-amber-500" : "bg-rose-600";
  return (
    <span
      aria-hidden="true"
      className={cn("inline-block h-1.5 w-14 overflow-hidden rounded-full bg-slate-200", className)}
    >
      <span className={cn("block h-full rounded-full", color)} style={{ width: `${value}%` }} />
    </span>
  );
}
