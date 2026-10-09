"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/cn";
import { usePersona } from "@/lib/persona/PersonaProvider";

import { LogoMark } from "./Logo";
import { PersonaSwitcher } from "./PersonaSwitcher";

interface NavItem {
  href: string;
  label: string;
  /** Extra path prefixes that also mark this item as current. */
  match: readonly string[];
  approverOnly?: boolean;
}

const NAV: readonly NavItem[] = [
  { href: "/", label: "Upload", match: ["/batches"] },
  { href: "/claims", label: "My claims", match: ["/claims"] },
  { href: "/approvals", label: "Approvals", match: ["/approvals"], approverOnly: true },
  { href: "/impact", label: "Impact", match: ["/impact"] },
  { href: "/operations", label: "AI ops", match: ["/operations"] },
];

function isCurrent(item: NavItem, pathname: string): boolean {
  if (item.href === "/") return pathname === "/" || item.match.some((p) => pathname.startsWith(p));
  return item.match.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

export function HeaderSkeleton() {
  return (
    <header className="sticky top-0 z-40 border-b border-slate-200 bg-white/85 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-6xl items-center gap-3 px-4 sm:px-6 lg:px-8">
        <span className="text-lg font-semibold tracking-tight text-slate-900">ClaimPilot</span>
      </div>
    </header>
  );
}

export function AppHeader() {
  const pathname = usePathname();
  const { isApprover } = usePersona();
  const items = NAV.filter((item) => !item.approverOnly || isApprover);

  return (
    <header className="sticky top-0 z-40 border-b border-slate-200 bg-white/85 backdrop-blur supports-[backdrop-filter]:bg-white/75">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 px-4 sm:px-6 lg:px-8">
        <Link
          href="/"
          aria-label="ClaimPilot home"
          className="flex h-16 items-center gap-2.5 rounded-lg"
        >
          <LogoMark />
          <span className="text-lg font-semibold tracking-tight text-slate-900">ClaimPilot</span>
        </Link>

        <nav
          aria-label="Main"
          className="order-last -mx-1 flex w-full gap-1 overflow-x-auto pb-2 md:order-none md:mx-0 md:ml-4 md:w-auto md:pb-0"
        >
          {items.map((item) => {
            const current = isCurrent(item, pathname);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={current ? "page" : undefined}
                className={cn(
                  "inline-flex h-10 items-center rounded-lg px-2.5 text-sm font-semibold whitespace-nowrap transition-colors sm:px-3.5",
                  current
                    ? "bg-indigo-50 text-indigo-700"
                    : "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
                )}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto flex h-16 items-center">
          <PersonaSwitcher />
        </div>
      </div>
    </header>
  );
}
