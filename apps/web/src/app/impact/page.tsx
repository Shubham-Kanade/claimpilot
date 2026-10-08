import type { Metadata } from "next";

import { ImpactScreen } from "@/components/impact/ImpactScreen";

export const metadata: Metadata = {
  title: "Impact",
};

export default function ImpactPage() {
  return <ImpactScreen />;
}
