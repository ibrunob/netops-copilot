import "server-only";

import { z } from "zod";

const serverEnvironmentSchema = z.object({
  NETOPS_API_BASE_URL: z.url(),
});

const oidcEnvironmentSchema = serverEnvironmentSchema.extend({
  // The browser-visible issuer. This must be the same value embedded in the
  // access token and configured at the API resource server.
  NETOPS_OIDC_ISSUER: z.url(),
  // A trusted server-only discovery endpoint is useful when the issuer is
  // public but the BFF lives on a private network (for example Docker Compose).
  // The discovered metadata is still required to name the public issuer.
  NETOPS_OIDC_DISCOVERY_URL: z.url().optional(),
  // A trusted server-only override for the code-exchange endpoint. It must not
  // be sent to the browser: authorization always uses discovery metadata.
  NETOPS_OIDC_TOKEN_ENDPOINT: z.url().optional(),
  NETOPS_OIDC_CLIENT_ID: z.string().min(1),
  NETOPS_OIDC_REDIRECT_URI: z.url(),
  // A base64url-encoded, 32-byte AES-GCM key. It is never exposed as a
  // NEXT_PUBLIC variable or passed into a client component.
  NETOPS_SESSION_ENCRYPTION_SECRET: z.string().regex(/^[A-Za-z0-9_-]{43}$/),
});

export type ServerEnvironment = z.infer<typeof serverEnvironmentSchema>;

export function getServerEnvironment(
  environment: NodeJS.ProcessEnv = process.env,
): ServerEnvironment {
  return serverEnvironmentSchema.parse(environment);
}

export type OidcEnvironment = z.infer<typeof oidcEnvironmentSchema>;

export function getOidcEnvironment(
  environment: NodeJS.ProcessEnv = process.env,
): OidcEnvironment {
  return oidcEnvironmentSchema.parse(environment);
}
