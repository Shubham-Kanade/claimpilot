import { Loader2 } from "lucide-react";
import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/cn";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "accent" | "success";
export type ButtonSize = "sm" | "md" | "lg";

const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "bg-indigo-600 text-white shadow-sm hover:bg-indigo-700 active:bg-indigo-800 disabled:bg-slate-200 disabled:text-slate-500 disabled:shadow-none",
  secondary:
    "bg-white text-slate-800 shadow-sm ring-1 ring-inset ring-slate-300 hover:bg-slate-50 active:bg-slate-100 disabled:bg-slate-50 disabled:text-slate-400 disabled:shadow-none",
  ghost:
    "text-slate-700 hover:bg-slate-100 active:bg-slate-200 disabled:text-slate-400 disabled:hover:bg-transparent",
  danger:
    "bg-rose-700 text-white shadow-sm hover:bg-rose-800 active:bg-rose-900 disabled:bg-slate-200 disabled:text-slate-500 disabled:shadow-none",
  accent:
    "bg-teal-700 text-white shadow-sm hover:bg-teal-800 active:bg-teal-900 disabled:bg-slate-200 disabled:text-slate-500 disabled:shadow-none",
  success:
    "bg-emerald-700 text-white shadow-sm hover:bg-emerald-800 active:bg-emerald-900 disabled:bg-slate-200 disabled:text-slate-500 disabled:shadow-none",
};

const SIZES: Record<ButtonSize, string> = {
  sm: "h-9 gap-1.5 px-3 text-sm",
  md: "h-11 gap-2 px-4 text-sm",
  lg: "h-12 gap-2 px-6 text-base",
};

export interface ButtonProps extends ComponentProps<"button"> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Shows a spinner and disables the button (the label stays, so the width does not jump). */
  loading?: boolean;
  /** An icon rendered before the label (decorative: aria-hidden). */
  icon?: ReactNode;
}

/** Class names of a button, for links that should look like one. */
export function buttonClasses(variant: ButtonVariant = "primary", size: ButtonSize = "md"): string {
  return cn(
    "inline-flex shrink-0 select-none items-center justify-center rounded-xl font-semibold transition-colors disabled:cursor-not-allowed",
    VARIANTS[variant],
    SIZES[size],
  );
}

export function Button({
  variant = "primary",
  size = "md",
  loading = false,
  icon,
  className,
  children,
  disabled,
  type = "button",
  ...props
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cn(buttonClasses(variant, size), className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading ? (
        <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
      ) : (
        icon
      )}
      {children}
    </button>
  );
}
