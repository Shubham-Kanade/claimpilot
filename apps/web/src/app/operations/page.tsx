import type { Metadata } from "next";

import { OperationsScreen } from "@/components/ops/OperationsScreen";

export const metadata: Metadata = {
  title: "AI ops",
};

export default function OperationsPage() {
  return <OperationsScreen />;
}
