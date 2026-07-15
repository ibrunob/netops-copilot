import Link from "next/link";
import type { ReactNode } from "react";

import type { AuthenticatedSession } from "@/lib/auth/session";

const navigation = [
  { href: "/cases", label: "Cases", marker: "01" },
] as const;

export function ProductShell({
  children,
  session,
}: Readonly<{
  children: ReactNode;
  session: AuthenticatedSession;
}>) {
  return (
    <div className="product-shell">
      <aside className="product-rail" aria-label="Primary navigation">
        <Link className="wordmark" href="/cases">
          <span aria-hidden="true">N/</span>
          NetOps
        </Link>

        <nav>
          <ul>
            {navigation.map((item) => (
              <li key={item.href}>
                <Link href={item.href}>
                  <span aria-hidden="true">{item.marker}</span>
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>

        <div className="session-stamp">
          <span>VERIFIED SESSION</span>
          <strong>{session.organizationName}</strong>
          <small>{session.displayName}</small>
        </div>
      </aside>
      <main className="product-main">{children}</main>
    </div>
  );
}
