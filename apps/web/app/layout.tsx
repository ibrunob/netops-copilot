import type { Metadata } from "next";
import type { ReactNode } from "react";

import { QueryProvider } from "@/providers/query-provider";

import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "NetOps Copilot",
    template: "%s · NetOps Copilot",
  },
  description:
    "Evidence-first network incident triage and configuration analysis.",
  applicationName: "NetOps Copilot",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <QueryProvider>{children}</QueryProvider>
      </body>
    </html>
  );
}
