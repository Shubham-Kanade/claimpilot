import type { Metadata } from "next";

import { ApprovalsScreen } from "@/components/approvals/ApprovalsScreen";

export const metadata: Metadata = {
  title: "Approvals",
};

export default function ApprovalsPage() {
  return <ApprovalsScreen />;
}
