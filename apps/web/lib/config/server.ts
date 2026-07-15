import "server-only";

import { z } from "zod";

const serverEnvironmentSchema = z.object({
  NETOPS_API_BASE_URL: z.url(),
});

export type ServerEnvironment = z.infer<typeof serverEnvironmentSchema>;

export function getServerEnvironment(
  environment: NodeJS.ProcessEnv = process.env,
): ServerEnvironment {
  return serverEnvironmentSchema.parse(environment);
}
