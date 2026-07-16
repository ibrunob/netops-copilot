import { NextResponse } from "next/server";

import { createCaseAction } from "@/app/cases/new/actions";

/**
 * Same-origin BFF endpoint for the intake form.
 *
 * The Codex local browser reaches the published 0.0.0.0 address through a
 * localhost forwarding layer. Normal route requests retain that same-origin
 * contract without relying on the browser's Server Action origin transport.
 * Authentication and the API bearer token remain server-only in the action.
 */
export async function POST(request: Request): Promise<NextResponse> {
  const result = await createCaseAction({ status: "idle", message: "" }, await request.formData());
  const expectsJson = request.headers.get("accept")?.includes("application/json") ?? false;

  if (expectsJson) {
    const status = result.status === "success" ? 201 : 400;
    return NextResponse.json(result, { status });
  }

  // A normal HTML form POST is deliberate: it remains usable when the local
  // browser cannot run or transport client-side React actions. The client
  // component intercepts this in the usual case and asks for JSON instead.
  if (result.status === "success" && result.caseId !== undefined) {
    return NextResponse.redirect(new URL(`/cases/${result.caseId}`, request.url), 303);
  }

  const retryUrl = new URL("/cases/new", request.url);
  retryUrl.searchParams.set("intake_error", result.message);
  return NextResponse.redirect(retryUrl, 303);
}
