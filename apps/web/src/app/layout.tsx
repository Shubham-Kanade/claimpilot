import type { Metadata } from "next";
// Self-hosted fonts (next/font/local): builds never fetch from Google Fonts, which is
// unreachable behind some proxies and inside the Docker build.
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import "./globals.css";

export const metadata: Metadata = {
  title: "ClaimPilot",
  description: "AI expense & reimbursement agent: from a pile of receipts to ready-to-submit.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col">{children}</body>
    </html>
  );
}
