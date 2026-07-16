import type { NextConfig } from "next";
import { fileURLToPath } from "node:url";

const repositoryRoot = fileURLToPath(new URL("../..", import.meta.url));

const nextConfig = {
  reactStrictMode: true,
  typedRoutes: true,
  // The generated API contract is a sibling package. Setting the repository
  // root lets Turbopack resolve that source during the monorepo build.
  turbopack: {
    root: repositoryRoot,
  },
  // The local in-app browser presents the Compose port as 0.0.0.0 while its
  // forwarding layer reaches Next with localhost as the host. Server Actions
  // correctly reject mismatched origins by default, so explicitly allow only
  // the two local development origins used by this project. Production hosts
  // are intentionally not broadened here.
  experimental: {
    serverActions: {
      allowedOrigins: ["0.0.0.0:3000", "localhost:3000"],
    },
  },
} satisfies NextConfig;

export default nextConfig;
