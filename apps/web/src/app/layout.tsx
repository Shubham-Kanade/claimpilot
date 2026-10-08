import type { Metadata, Viewport } from "next";
// Self-hosted fonts (next/font/local): builds never fetch from Google Fonts, which is
// unreachable behind some proxies and inside the Docker build.
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import { Suspense } from "react";

import { AppHeader, HeaderSkeleton } from "@/components/layout/AppHeader";
import { DemoBanner } from "@/components/layout/DemoBanner";
import { Toaster } from "@/components/layout/Toaster";
import { Providers } from "@/components/Providers";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "ClaimPilot", template: "%s · ClaimPilot" },
  description: "AI expense & reimbursement agent: from a pile of receipts to ready-to-submit.",
  applicationName: "ClaimPilot",
};

export const viewport: Viewport = {
  themeColor: "#4f46e5",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col">
        <a href="#main" className="skip-link">
          Skip to main content
        </a>
        <Providers>
          <div id="app-root" className="flex min-h-dvh flex-col">
            <DemoBanner />
            {/* usePathname() inside the header reads URL data: Suspense keeps the rest static. */}
            <Suspense fallback={<HeaderSkeleton />}>
              <AppHeader />
            </Suspense>
            <main id="main" tabIndex={-1} className="flex-1 outline-none">
              {children}
            </main>
            <footer className="border-t border-slate-200 bg-white py-5 text-center text-xs text-slate-600">
              ClaimPilot demo · synthetic receipts and fictional people only · AI Innovation Lab S2
            </footer>
          </div>
          <Toaster />
        </Providers>
      </body>
    </html>
  );
}
