import type { ElementType, HTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/cn";

interface CardProps extends HTMLAttributes<HTMLElement> {
  as?: ElementType;
  /** Removes the default padding (for cards with full-bleed media or lists). */
  flush?: boolean;
  children: ReactNode;
}

/** The standard surface: white, rounded, hairline border, soft shadow. */
export function Card({ as: Tag = "div", flush = false, className, children, ...props }: CardProps) {
  return (
    <Tag
      className={cn(
        "shadow-card rounded-2xl border border-slate-200 bg-white",
        !flush && "p-4 sm:p-6",
        className,
      )}
      {...props}
    >
      {children}
    </Tag>
  );
}

export function SectionTitle({
  children,
  id,
  action,
  className,
}: {
  children: ReactNode;
  id?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center justify-between gap-3", className)}>
      <h2 id={id} className="text-base font-semibold tracking-tight text-slate-900 sm:text-lg">
        {children}
      </h2>
      {action}
    </div>
  );
}
