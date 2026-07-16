# syntax=docker/dockerfile:1.7
# Build from the repository root so the shared .dockerignore protects both apps:
# docker build --file apps/web/Dockerfile --tag netops-copilot-web:local .

ARG NODE_VERSION=24.0.0

FROM node:${NODE_VERSION}-bookworm-slim AS base

ENV NEXT_TELEMETRY_DISABLED=1 \
    NPM_CONFIG_UPDATE_NOTIFIER=false

WORKDIR /repo/apps/web

FROM base AS dependencies

# A build without this lock is a deliberate failure: resolving a new package
# graph during an image build would make the result non-reproducible.
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --ignore-scripts

FROM dependencies AS builder

COPY apps/web/ ./
# The generated, checked OpenAPI client is a sibling package referenced by the
# web app. Preserve its repository-relative path during the monorepo build.
COPY packages/api-client/ /repo/packages/api-client/
RUN npm run build

FROM base AS runner

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000

# Use a fixed, unprivileged identity and ship only production runtime packages.
RUN groupadd --gid 10001 netops && \
    useradd --uid 10001 --gid netops --create-home --shell /usr/sbin/nologin netops

COPY --from=builder --chown=netops:netops /repo/apps/web/package.json ./package.json
COPY --from=builder --chown=netops:netops /repo/apps/web/next.config.ts ./next.config.ts
COPY --from=builder --chown=netops:netops /repo/apps/web/.next ./.next
COPY --from=builder --chown=netops:netops /repo/packages/api-client /repo/packages/api-client
COPY --from=dependencies --chown=netops:netops /repo/apps/web/node_modules ./node_modules

RUN npm prune --omit=dev --ignore-scripts && npm cache clean --force

USER netops

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/').then((response) => process.exit(response.ok ? 0 : 1)).catch(() => process.exit(1))"]

CMD ["node", "node_modules/next/dist/bin/next", "start", "-H", "0.0.0.0", "-p", "3000"]
