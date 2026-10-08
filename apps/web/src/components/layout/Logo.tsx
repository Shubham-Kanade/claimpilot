import { cn } from "@/lib/cn";

/** The ClaimPilot mark: a receipt with a check, on an indigo-to-teal tile. */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      className={cn("size-8", className)}
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <linearGradient
          id="cp-logo-gradient"
          x1="0"
          y1="0"
          x2="32"
          y2="32"
          gradientUnits="userSpaceOnUse"
        >
          <stop offset="0" stopColor="#4f46e5" />
          <stop offset="1" stopColor="#0d9488" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill="url(#cp-logo-gradient)" />
      <path
        d="M11.5 6.5h9A1.5 1.5 0 0 1 22 8v16.5l-1.5-1.5-1.5 1.5-1.5-1.5-1.5 1.5-1.5-1.5-1.5 1.5-1.5-1.5-1.5 1.5V8a1.5 1.5 0 0 1 1.5-1.5Z"
        fill="#fff"
      />
      <path
        d="m12.8 15.6 2.3 2.3 4.2-4.6"
        fill="none"
        stroke="#4338ca"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
