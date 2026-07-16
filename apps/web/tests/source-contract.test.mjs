import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function source(relativePath) {
  return readFile(path.join(webRoot, relativePath), "utf8");
}

test("case routes remain protected by the server session boundary", async () => {
  const layout = await source("app/cases/layout.tsx");
  const session = await source("lib/auth/session.ts");

  assert.match(layout, /await getAuthenticatedSession\(\)/);
  assert.match(layout, /if \(session === null\)/);
  assert.match(layout, /<AuthenticationRequired \/>/);
  assert.match(session, /getStoredSession\(\)/);
});

test("OIDC entry uses a document navigation instead of an RSC route fetch", async () => {
  const home = await source("app/page.tsx");
  const gate = await source("components/auth/authentication-required.tsx");

  assert.match(home, /<a className="button button-primary" href="\/auth\/login">/);
  assert.match(gate, /<a className="button button-primary" href="\/auth\/login">/);
  assert.doesNotMatch(home, /import Link from "next\/link"/);
});

test("local Compose aliases can submit protected server actions", async () => {
  const nextConfig = await source("next.config.ts");

  assert.match(nextConfig, /serverActions/);
  assert.match(nextConfig, /allowedOrigins/);
  assert.match(nextConfig, /"0\.0\.0\.0:3000"/);
  assert.match(nextConfig, /"localhost:3000"/);
});

test("the server API boundary requires a verified token provider", async () => {
  const cases = await source("lib/api/cases.ts");

  assert.match(cases, /import "server-only";/);
  assert.match(cases, /getAuthenticatedAccessToken\(\)/);
  assert.match(cases, /if \(accessToken === null \|\| accessToken\.length === 0\)/);
  assert.match(cases, /accessToken: async \(\) => accessToken/);
  assert.doesNotMatch(cases, /localStorage|sessionStorage/);
});

test("case workbench keeps state changes behind the authenticated generated-client boundary", async () => {
  const workbench = await source("app/cases/[caseId]/case-workbench.tsx");
  const actions = await source("app/cases/[caseId]/actions.ts");

  assert.match(workbench, /Immutable activity/);
  assert.match(workbench, /pending customer answer/);
  assert.match(workbench, /Question for customer/);
  assert.match(workbench, /Mark pending customer answer/);
  assert.match(workbench, /expected_version/);
  assert.match(workbench, /Refresh case/);
  assert.match(workbench, /aria-current=\{state === caseRecord\.state \? "step" : undefined\}/);
  assert.match(workbench, /Case activity in recorded order/);
  assert.match(workbench, /resultRef\.current\?\.focus\(\)/);
  assert.match(actions, /getAuthenticatedCaseApi/);
  assert.match(actions, /NetOpsApiError/);
  assert.match(actions, /error\.status === 409/);
  assert.doesNotMatch(workbench, /mock|demo record/i);
});

test("configuration preview is server-mediated and retains only redacted output in the workbench", async () => {
  const caseApi = await source("lib/api/cases.ts");
  const previewActions = await source("app/cases/[caseId]/config-preview-actions.ts");
  const preview = await source("app/cases/[caseId]/config-preview.tsx");

  assert.match(caseApi, /previewCaseConfig: async/);
  assert.match(caseApi, /\(await authenticatedClient\(\)\)\.previewCaseConfig/);
  assert.match(previewActions, /getAuthenticatedCaseApi\(\)\.previewCaseConfig/);
  assert.match(previewActions, /redacted_content/);
  assert.match(preview, /createConfigUploadIntentAction/);
  assert.match(preview, /completeConfigUploadAction/);
  assert.match(preview, /fetch\(capability\.uploadUrl/);
  assert.match(preview, /headers: capability\.requiredHeaders/);
  assert.match(preview, /crypto\.subtle\.digest/);
  assert.match(preview, /Upload reviewed configuration/);
  assert.match(preview, /router\.refresh\(\)/);
  assert.match(preview, /Redacted derivative SHA-256/);
  assert.match(previewActions, /createArtifactUploadIntent/);
  assert.match(previewActions, /completeArtifactUploadIntent/);
  assert.doesNotMatch(preview, /localStorage|sessionStorage|console\.|useState\([^)]*config/i);
  assert.doesNotMatch(previewActions, /console\.|localStorage|sessionStorage/);
  assert.match(caseApi, /listCaseArtifactStatuses/);
});

test("audio evidence uses a metadata-only server action and direct browser upload", async () => {
  const workbench = await source("app/cases/[caseId]/case-workbench.tsx");
  const audio = await source("app/cases/[caseId]/audio-intake.tsx");
  const actions = await source("app/cases/[caseId]/audio-intake-actions.ts");

  assert.match(workbench, /<AudioIntake caseId=\{caseRecord\.id\} \/>/);
  assert.match(audio, /MediaRecorder/);
  assert.match(audio, /getUserMedia\(\{ audio: true \}\)/);
  assert.match(audio, /fetch\(capability\.uploadUrl/);
  assert.match(audio, /headers: capability\.requiredHeaders/);
  assert.match(audio, /completeAudioUploadAction/);
  assert.match(audio, /No client-side speech-to-text is used/);
  assert.doesNotMatch(audio, /SpeechRecognition|webkitSpeechRecognition|localStorage|sessionStorage|console\./);
  assert.match(actions, /artifact_kind: "incident-audio"/);
  assert.match(actions, /createArtifactUploadIntent/);
  assert.match(actions, /completeArtifactUploadIntent/);
  assert.doesNotMatch(actions, /console\.|localStorage|sessionStorage/);
});

test("case UI preserves keyboard bypass and focuses dynamic action failures", async () => {
  const shell = await source("components/shell/product-shell.tsx");
  const intake = await source("app/cases/new/new-case-intake.tsx");
  const styles = await source("app/globals.css");

  assert.match(shell, /href="#main-content"/);
  assert.match(shell, /id="main-content" tabIndex=\{-1\}/);
  assert.match(intake, /resultRef\.current\?\.focus\(\)/);
  assert.match(intake, /aria-atomic="true"/);
  assert.match(styles, /\.skipLink:focus-visible/);
  assert.match(styles, /prefers-reduced-motion: reduce/);
});

test("read-only roles are not offered case-write controls", async () => {
  const session = await source("lib/auth/session.ts");
  const queue = await source("app/cases/page.tsx");
  const intake = await source("app/cases/new/page.tsx");
  const workbench = await source("app/cases/[caseId]/case-workbench.tsx");

  assert.match(session, /export function canWriteCases/);
  assert.match(session, /"org_admin", "operator", "approver", "platform_admin"/);
  assert.match(queue, /canWriteCases\(session\)/);
  assert.match(intake, /case-create-forbidden/);
  assert.match(workbench, /Your verified role is read-only/);
});

test("case intake creates through the server generated-client boundary with retry-safe intent", async () => {
  const intake = await source("app/cases/new/new-case-intake.tsx");
  const actions = await source("app/cases/new/actions.ts");
  const intakeRoute = await source("app/api/cases/route.ts");

  assert.match(intake, /globalThis\.crypto\.randomUUID\(\)/);
  assert.match(intake, /idempotency_key/);
  assert.match(intake, /fetch\("\/api\/cases"/);
  assert.match(intakeRoute, /createCaseAction\(\{ status: "idle", message: "" \}, await request\.formData\(\)\)/);
  assert.match(intake, /router\.replace\(/);
  assert.match(actions, /getAuthenticatedCaseApi\(\)\.createCase/);
  assert.match(actions, /revalidatePath\("\/cases"\)/);
  assert.match(actions, /z\.uuid\(\)/);
  assert.doesNotMatch(intake, /mock|demo|localStorage|sessionStorage/i);
  assert.doesNotMatch(actions, /organizationId|actorId|localStorage|sessionStorage/);
});

test("the queue is API-backed and preserves operator filters in URL controls", async () => {
  const queue = await source("app/cases/page.tsx");
  const caseApi = await source("lib/api/cases.ts");

  assert.match(queue, /searchParams: Promise<SearchParameters>/);
  assert.match(queue, /<form className=\{styles\.controls\} action="\/cases" method="get"/);
  assert.match(queue, /listTenantCases\(session, \{/);
  assert.match(queue, /next_cursor/);
  assert.match(queue, /Load next cases/);
  assert.match(caseApi, /new NetOpsCaseClient/);
  assert.match(caseApi, /baseUrl: getServerEnvironment\(\)\.NETOPS_API_BASE_URL/);
  assert.doesNotMatch(queue, /demo|mock|localStorage|sessionStorage/i);
});

test("case BFF uses the generated contract with a server-session token only", async () => {
  const cases = await source("lib/api/cases.ts");
  const session = await source("lib/auth/session.ts");

  assert.match(cases, /import "server-only";/);
  assert.match(cases, /NetOpsCaseClient/);
  assert.match(cases, /getAuthenticatedAccessToken\(\)/);
  assert.match(cases, /if \(accessToken === null \|\| accessToken\.length === 0\)/);
  assert.match(cases, /baseUrl: getServerEnvironment\(\)\.NETOPS_API_BASE_URL/);
  assert.match(cases, /streamCaseEvents/);
  assert.doesNotMatch(cases, /organizationId|actorId|localStorage|sessionStorage/);
  assert.match(session, /getAuthenticatedAccessToken\(\): Promise<string \| null>/);
});

test("OIDC bridge uses authorization-code PKCE and an encrypted HttpOnly cookie", async () => {
  const session = await source("lib/auth/session.ts");
  const login = await source("app/auth/login/route.ts");
  const callback = await source("app/auth/callback/route.ts");

  assert.match(session, /response_type", "code"/);
  assert.match(session, /code_challenge_method", "S256"/);
  assert.match(session, /code_verifier: transaction\.codeVerifier/);
  assert.match(session, /webcrypto\.subtle\.encrypt/);
  assert.match(session, /webcrypto\.subtle\.decrypt/);
  assert.match(session, /new URL\("\/v1\/auth\/me"/);
  assert.match(session, /authorization: `Bearer \$\{accessToken\}`/);
  assert.match(session, /NETOPS_OIDC_DISCOVERY_URL/);
  assert.match(session, /NETOPS_OIDC_TOKEN_ENDPOINT/);
  assert.match(session, /configuration\.issuer\.replace/);
  assert.match(session, /NETOPS_COOKIE_SECURE !== "false"/);
  assert.match(session, /secure: useSecureCookies/);
  assert.match(session, /httpOnly: true/);
  assert.doesNotMatch(session, /localStorage|sessionStorage|development login/i);
  assert.match(login, /OIDC_TRANSACTION_COOKIE_NAME/);
  assert.match(login, /NETOPS_OIDC_REDIRECT_URI/);
  assert.match(login, /x-forwarded-host/);
  assert.match(login, /NETOPS_COOKIE_SECURE === "false"/);
  assert.match(login, /new URL\("\/auth\/login", redirectUri\.origin\)/);
  assert.match(callback, /SESSION_COOKIE_NAME/);
});

test("SSE relay authenticates from the server session and preserves recovery IDs", async () => {
  const events = await source("app/api/events/route.ts");

  assert.match(events, /getAuthenticatedAccessToken\(\)/);
  assert.match(events, /request\.headers\.get\("last-event-id"\)/);
  assert.match(events, /getAuthenticatedCaseApi\(\)\.streamCaseEvents/);
  assert.match(events, /id: \$\{event\.id\}/);
  assert.match(events, /content-type": "text\/event-stream/);
  assert.doesNotMatch(events, /Authorization|Bearer|localStorage|sessionStorage/);
});
