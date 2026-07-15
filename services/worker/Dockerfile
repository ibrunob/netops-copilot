# syntax=docker/dockerfile:1.7
# Build from the repository root:
# docker build --file services/worker/Dockerfile --tag netops-copilot-worker:local .

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
COPY services/worker ./services/worker

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
COPY --from=builder /build/services/worker/docker-healthcheck.py /app/docker-healthcheck.py
RUN python -m pip install --no-index --find-links=/wheels "netops-copilot-api==0.1.0" && \
    rm -rf /wheels

USER netops

EXPOSE 8081

HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=4 \
    CMD ["python", "/app/docker-healthcheck.py"]

ENTRYPOINT ["netops-worker"]
