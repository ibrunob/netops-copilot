# syntax=docker/dockerfile:1.7
# Build from the repository root:
# docker build --file services/api/Dockerfile --tag netops-copilot-api:local .

ARG PYTHON_VERSION=3.12.8

FROM ghcr.io/astral-sh/uv:0.5.18 AS uv

FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

COPY --from=uv /uv /uvx /bin/

COPY pyproject.toml README.md ./
COPY uv.lock ./
COPY services/api ./services/api

# The dependency graph is read exclusively from the committed uv.lock. A build
# cannot silently resolve newer transitive dependencies from the package index.
RUN uv export --frozen --no-dev --no-emit-project \
    --format requirements-txt --output-file requirements.txt && \
    python -m pip wheel --wheel-dir /wheels --require-hashes -r requirements.txt && \
    python -m pip wheel --no-deps --wheel-dir /wheels .

FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --gid 10001 netops && \
    useradd --uid 10001 --gid netops --create-home --shell /usr/sbin/nologin netops

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY --from=builder /build/services/api/docker-healthcheck.py /app/docker-healthcheck.py
COPY --from=builder /build/services/api/alembic /app/alembic
COPY --from=builder /build/services/api/alembic.ini /app/alembic.ini
RUN python -m pip install --no-index --find-links=/wheels "netops-copilot-api==0.1.0" && \
    rm -rf /wheels

USER netops

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "/app/docker-healthcheck.py"]

# netops-api respects NETOPS_HOST and NETOPS_PORT, runs without reload by default,
# and does not trust arbitrary proxy forwarding headers.
ENTRYPOINT ["netops-api"]
