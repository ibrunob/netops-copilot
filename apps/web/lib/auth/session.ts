import "server-only";

/**
 * The single interface the OIDC implementation must satisfy. Keeping it here
 * prevents UI routes from accepting a user or organization from request input.
 */
export type AuthenticatedSession = Readonly<{
  subject: string;
  displayName: string;
  organizationName: string;
  roles: readonly string[];
}>;

/**
 * Deny by default until the enterprise OIDC adapter is implemented.
 *
 * This is intentionally not a development login or a configurable fallback.
 * The later adapter must validate issuer, audience, expiry, and session binding
 * before returning a session.
 */
export async function getAuthenticatedSession(): Promise<AuthenticatedSession | null> {
  return null;
}
