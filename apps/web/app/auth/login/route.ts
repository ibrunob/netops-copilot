import { NextRequest, NextResponse } from "next/server";

import {
  OIDC_TRANSACTION_COOKIE_NAME,
  beginOidcLogin,
  secureCookieOptions,
} from "@/lib/auth/session";
import { getOidcEnvironment } from "@/lib/config/server";

/**
 * OIDC binds the callback to both its registered redirect URI and the browser
 * transaction cookie.  Start the flow at that exact origin so opening a local
 * dev server through an alias such as 0.0.0.0 cannot strand the transaction
 * cookie on a different host than the callback.
 */
function canonicalLoginOrigin(request: NextRequest): NextResponse | null {
  const redirectUri = new URL(getOidcEnvironment().NETOPS_OIDC_REDIRECT_URI);
  // The Codex local browser presents 0.0.0.0 but forwards localhost to the
  // container. In the explicit local HTTP mode, trying to canonicalize that
  // forwarded host turns /auth/login into a redirect loop. The callback URI
  // itself is still fixed to 0.0.0.0 and Keycloak validates it.
  if (process.env.NETOPS_COOKIE_SECURE === "false") {
    return null;
  }
  // In Compose, Next receives the connection on its internal port (3000), so
  // request.nextUrl.origin can describe the container instead of the address
  // the browser used. Host is preserved by the Docker port forward and is the
  // value that matters for host-only transaction cookies.
  const requestHost = request.headers.get("x-forwarded-host") ?? request.headers.get("host");

  if (requestHost === redirectUri.host) {
    return null;
  }

  return NextResponse.redirect(new URL("/auth/login", redirectUri.origin), 307);
}

export async function GET(request: NextRequest): Promise<NextResponse> {
  const canonicalResponse = canonicalLoginOrigin(request);
  if (canonicalResponse !== null) {
    return canonicalResponse;
  }

  let login: Awaited<ReturnType<typeof beginOidcLogin>>;
  try {
    login = await beginOidcLogin();
  } catch {
    return new NextResponse("Authentication is temporarily unavailable.", { status: 503 });
  }
  const response = NextResponse.redirect(login.authorizationUrl);

  response.cookies.set(OIDC_TRANSACTION_COOKIE_NAME, login.transactionCookieValue, {
    ...secureCookieOptions,
    maxAge: 10 * 60,
  });

  return response;
}
