import type { Metadata } from "next";

import { UploadScreen } from "@/components/upload/UploadScreen";

export const metadata: Metadata = {
  title: "Upload receipts",
};

export default function Home() {
  return <UploadScreen />;
}
