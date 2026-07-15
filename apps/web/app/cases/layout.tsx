import type { ReactNode } from "react";

import { AuthenticationRequired } from "@/components/auth/authentication-required";
import { ProductShell } from "@/components/shell/product-shell";
import { getAuthenticatedSession } from "@/lib/auth/session";

export default async function CasesLayout({
  children,
}: Readonly<{ children: ReactNode }>) {
  const session = await getAuthenticatedSession();

  if (session === null) {
    return <AuthenticationRequired />;
  }

  return <ProductShell session={session}>{children}</ProductShell>;
}
