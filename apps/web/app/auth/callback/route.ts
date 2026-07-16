import { NextRequest, NextResponse } from "next/server";

import {
  OIDC_TRANSACTION_COOKIE_NAME,
  SESSION_COOKIE_NAME,
  completeOidcCallback,
  secureCookieOptions,
} from "@/lib/auth/session";

function failedAuthentication(request: NextRequest): NextResponse {
  const response = NextResponse.redirect(new URL("/", request.url));
  response.cookies.delete(OIDC_TRANSACTION_COOKIE_NAME);
  return response;
}

export async function GET(request: NextRequest): Promise<NextResponse> {
  let completed: Awaited<ReturnType<typeof completeOidcCallback>>;
  try {
    completed = await completeOidcCallback({
      code: request.nextUrl.searchParams.get("code"),
      state: request.nextUrl.searchParams.get("state"),
      transactionCookieValue: request.cookies.get(OIDC_TRANSACTION_COOKIE_NAME)?.value,
    });
  } catch {
    return failedAuthentication(request);
  }

  if (completed === null) {
    return failedAuthentication(request);
  }

  const response = NextResponse.redirect(new URL("/cases", request.url));
  response.cookies.set(SESSION_COOKIE_NAME, completed.sessionCookieValue, {
    ...secureCookieOptions,
    maxAge: completed.maxAge,
  });
  response.cookies.delete(OIDC_TRANSACTION_COOKIE_NAME);
  return response;
}
