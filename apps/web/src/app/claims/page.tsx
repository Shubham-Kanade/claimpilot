import type { Metadata } from "next";

import { ClaimsScreen } from "@/components/claims/ClaimsScreen";

export const metadata: Metadata = {
  title: "My claims",
};

export default function ClaimsPage() {
  return <ClaimsScreen />;
}
