import "server-only";

import { webcrypto } from "node:crypto";

import { cookies } from "next/headers";

import { getOidcEnvironment, getServerEnvironment } from "@/lib/config/server";

// A development browser reached through 0.0.0.0 is plain HTTP, which is not a
// secure context for `__Host-` cookies. This opt-in exists only for that local
// Compose mode; the default keeps host-prefixed, Secure cookies everywhere.
const useSecureCookies = process.env.NETOPS_COOKIE_SECURE !== "false";

export const SESSION_COOKIE_NAME = useSecureCookies ? "__Host-netops-session" : "netops-session";
export const OIDC_TRANSACTION_COOKIE_NAME = useSecureCookies
  ? "__Host-netops-oidc-transaction"
  : "netops-oidc-transaction";

const SESSION_LIFETIME_SECONDS = 60 * 60 * 12;
const OIDC_TRANSACTION_LIFETIME_SECONDS = 10 * 60;

/** The display-safe portion of a verified server session. */
export type AuthenticatedSession = Readonly<{
  subject: string;
  displayName: string;
  organizationName: string;
  roles: readonly string[];
}>;

const CASE_WRITE_ROLES = new Set(["org_admin", "operator", "approver", "platform_admin"]);

/**
 * Mirrors the API's case-write role boundary for rendering controls. The API
 * remains authoritative: this only prevents read-only operators from being
 * offered actions that the server will reject.
 */
export function canWriteCases(session: AuthenticatedSession): boolean {
  return session.roles.some((role) => CASE_WRITE_ROLES.has(role));
}

type StoredSession = AuthenticatedSession &
  Readonly<{
    accessToken: string;
    expiresAt: number;
  }>;

type OidcTransaction = Readonly<{
  state: string;
  codeVerifier: string;
  expiresAt: number;
}>;

type OpenIdConfiguration = Readonly<{
  issuer: string;
  authorization_endpoint: string;
  token_endpoint: string;
}>;

type TokenResponse = Readonly<{
  access_token?: unknown;
  expires_in?: unknown;
  token_type?: unknown;
}>;

type VerifiedIdentityResponse = Readonly<{
  subject?: unknown;
  organization_id?: unknown;
  roles?: unknown;
}>;

export type OidcLoginStart = Readonly<{
  authorizationUrl: string;
  transactionCookieValue: string;
}>;

export type OidcCallbackCompletion = Readonly<{
  sessionCookieValue: string;
  maxAge: number;
}>;

/**
 * Loads a display-safe session from an authenticated, encrypted cookie.
 * It never derives a principal from a request header, a URL parameter, or an
 * unsigned browser claim.
 */
export async function getAuthenticatedSession(): Promise<AuthenticatedSession | null> {
  const session = await getStoredSession();

  if (session === null) {
    return null;
  }

  return toPublicSession(session);
}

/**
 * Returns the bearer credential only to server-side API helpers. UI code gets
 * the display-safe AuthenticatedSession instead, so access tokens cannot cross
 * a React server/client boundary by accident.
 */
export async function getAuthenticatedAccessToken(): Promise<string | null> {
  const session = await getStoredSession();
  return session?.accessToken ?? null;
}

/** Starts a Keycloak/OIDC authorization-code flow with PKCE and opaque state. */
export async function beginOidcLogin(): Promise<OidcLoginStart> {
  const environment = getOidcEnvironment();
  const configuration = await getOpenIdConfiguration(environment);
  const state = randomBase64Url(32);
  const codeVerifier = randomBase64Url(64);
  const codeChallenge = await sha256Base64Url(codeVerifier);
  const transactionCookieValue = await seal({
    state,
    codeVerifier,
    expiresAt: Date.now() + OIDC_TRANSACTION_LIFETIME_SECONDS * 1000,
  });
  const authorizationUrl = new URL(configuration.authorization_endpoint);

  authorizationUrl.searchParams.set("response_type", "code");
  authorizationUrl.searchParams.set("client_id", environment.NETOPS_OIDC_CLIENT_ID);
  authorizationUrl.searchParams.set("redirect_uri", environment.NETOPS_OIDC_REDIRECT_URI);
  authorizationUrl.searchParams.set("scope", "openid profile");
  authorizationUrl.searchParams.set("state", state);
  authorizationUrl.searchParams.set("code_challenge", codeChallenge);
  authorizationUrl.searchParams.set("code_challenge_method", "S256");

  return { authorizationUrl: authorizationUrl.toString(), transactionCookieValue };
}

/**
 * Exchanges a PKCE authorization code and verifies its principal by calling
 * the API resource server. The API checks the signed access token; no decoded
 * browser JWT claims are accepted as identity input here.
 */
export async function completeOidcCallback({
  code,
  state,
  transactionCookieValue,
}: Readonly<{
  code: string | null;
  state: string | null;
  transactionCookieValue: string | undefined;
}>): Promise<OidcCallbackCompletion | null> {
  if (code === null || state === null || transactionCookieValue === undefined) {
    return null;
  }

  const transaction = await unseal<OidcTransaction>(transactionCookieValue);
  if (
    transaction === null ||
    transaction.expiresAt <= Date.now() ||
    transaction.state !== state ||
    transaction.codeVerifier.length === 0
  ) {
    return null;
  }

  const environment = getOidcEnvironment();
  const configuration = await getOpenIdConfiguration(environment);
  const response = await fetch(environment.NETOPS_OIDC_TOKEN_ENDPOINT ?? configuration.token_endpoint, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    cache: "no-store",
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: environment.NETOPS_OIDC_CLIENT_ID,
      redirect_uri: environment.NETOPS_OIDC_REDIRECT_URI,
      code,
      code_verifier: transaction.codeVerifier,
    }),
  });

  if (!response.ok) {
    return null;
  }

  const token = (await response.json()) as TokenResponse;
  if (
    typeof token.access_token !== "string" ||
    token.access_token.length === 0 ||
    typeof token.token_type !== "string" ||
    token.token_type.toLowerCase() !== "bearer"
  ) {
    return null;
  }

  const identity = await fetchVerifiedIdentity(token.access_token);
  if (identity === null) {
    return null;
  }

  const expiresIn = saneExpiresIn(token.expires_in);
  const maxAge = Math.min(expiresIn, SESSION_LIFETIME_SECONDS);
  const session: StoredSession = {
    ...identity,
    accessToken: token.access_token,
    expiresAt: Date.now() + maxAge * 1000,
  };

  return { sessionCookieValue: await seal(session), maxAge };
}

export const secureCookieOptions = Object.freeze({
  httpOnly: true,
  secure: useSecureCookies,
  sameSite: "lax" as const,
  path: "/",
});

async function getStoredSession(): Promise<StoredSession | null> {
  const value = (await cookies()).get(SESSION_COOKIE_NAME)?.value;
  if (value === undefined) {
    return null;
  }

  const session = await unseal<StoredSession>(value);
  if (
    session === null ||
    session.expiresAt <= Date.now() ||
    typeof session.accessToken !== "string" ||
    session.accessToken.length === 0 ||
    !isPublicSession(session)
  ) {
    return null;
  }
  return session;
}

async function fetchVerifiedIdentity(accessToken: string): Promise<AuthenticatedSession | null> {
  let response: Response;
  try {
    response = await fetch(new URL("/v1/auth/me", getServerEnvironment().NETOPS_API_BASE_URL), {
      headers: { authorization: `Bearer ${accessToken}`, accept: "application/json" },
      cache: "no-store",
    });
  } catch {
    return null;
  }

  if (!response.ok) {
    return null;
  }

  let identity: VerifiedIdentityResponse;
  try {
    identity = (await response.json()) as VerifiedIdentityResponse;
  } catch {
    return null;
  }
  if (
    typeof identity.subject !== "string" ||
    identity.subject.length === 0 ||
    typeof identity.organization_id !== "string" ||
    identity.organization_id.length === 0 ||
    !Array.isArray(identity.roles) ||
    !identity.roles.every((role) => typeof role === "string")
  ) {
    return null;
  }

  return {
    subject: identity.subject,
    // The resource server deliberately returns no presentation profile. Until a
    // verified profile endpoint is added, subject/organization are the only
    // truthful labels the web tier has.
    displayName: identity.subject,
    organizationName: identity.organization_id,
    roles: identity.roles,
  };
}

async function getOpenIdConfiguration(
  environment: ReturnType<typeof getOidcEnvironment>,
): Promise<OpenIdConfiguration> {
  const discoveryUrl =
    environment.NETOPS_OIDC_DISCOVERY_URL ??
    new URL(
      ".well-known/openid-configuration",
      `${environment.NETOPS_OIDC_ISSUER.replace(/\/$/, "")}/`,
    ).toString();
  const response = await fetch(discoveryUrl, { cache: "no-store" });
  if (!response.ok) {
    throw new Error("OIDC discovery failed.");
  }
  const configuration = (await response.json()) as Partial<OpenIdConfiguration>;
  if (
    typeof configuration.issuer !== "string" ||
    configuration.issuer.replace(/\/$/, "") !== environment.NETOPS_OIDC_ISSUER.replace(/\/$/, "") ||
    typeof configuration.authorization_endpoint !== "string" ||
    typeof configuration.token_endpoint !== "string"
  ) {
    throw new Error("OIDC discovery response is incomplete.");
  }
  return configuration as OpenIdConfiguration;
}

function isPublicSession(value: Partial<AuthenticatedSession>): value is AuthenticatedSession {
  return (
    typeof value.subject === "string" &&
    value.subject.length > 0 &&
    typeof value.displayName === "string" &&
    typeof value.organizationName === "string" &&
    Array.isArray(value.roles) &&
    value.roles.every((role) => typeof role === "string")
  );
}

function toPublicSession(session: StoredSession): AuthenticatedSession {
  return {
    subject: session.subject,
    displayName: session.displayName,
    organizationName: session.organizationName,
    roles: session.roles,
  };
}

function saneExpiresIn(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value <= 0) {
    return 60;
  }
  return value;
}

async function seal(value: OidcTransaction | StoredSession): Promise<string> {
  const iv = webcrypto.getRandomValues(new Uint8Array(12));
  const ciphertext = await webcrypto.subtle.encrypt(
    { name: "AES-GCM", iv },
    await getSessionEncryptionKey(),
    new TextEncoder().encode(JSON.stringify(value)),
  );
  return `${base64UrlEncode(iv)}.${base64UrlEncode(new Uint8Array(ciphertext))}`;
}

async function unseal<T>(value: string): Promise<T | null> {
  const [encodedIv, encodedCiphertext, extra] = value.split(".");
  if (encodedIv === undefined || encodedCiphertext === undefined || extra !== undefined) {
    return null;
  }
  try {
    const plaintext = await webcrypto.subtle.decrypt(
      { name: "AES-GCM", iv: base64UrlDecode(encodedIv) },
      await getSessionEncryptionKey(),
      base64UrlDecode(encodedCiphertext),
    );
    return JSON.parse(new TextDecoder().decode(plaintext)) as T;
  } catch {
    return null;
  }
}

async function getSessionEncryptionKey() {
  const secret = base64UrlDecode(getOidcEnvironment().NETOPS_SESSION_ENCRYPTION_SECRET);
  if (secret.length !== 32) {
    throw new Error("NETOPS_SESSION_ENCRYPTION_SECRET must decode to 32 bytes.");
  }
  return webcrypto.subtle.importKey("raw", secret, { name: "AES-GCM" }, false, ["encrypt", "decrypt"]);
}

async function sha256Base64Url(value: string): Promise<string> {
  const digest = await webcrypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return base64UrlEncode(new Uint8Array(digest));
}

function randomBase64Url(byteLength: number): string {
  return base64UrlEncode(webcrypto.getRandomValues(new Uint8Array(byteLength)));
}

function base64UrlEncode(value: Uint8Array): string {
  return Buffer.from(value).toString("base64url");
}

function base64UrlDecode(value: string): Uint8Array {
  if (!/^[A-Za-z0-9_-]+$/.test(value)) {
    throw new Error("Invalid base64url value.");
  }
  return new Uint8Array(Buffer.from(value, "base64url"));
}
