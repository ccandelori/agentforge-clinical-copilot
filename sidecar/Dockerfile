# syntax=docker/dockerfile:1.7
# AgentForge Clinical Co-Pilot sidecar service.
# Multi-stage build: deps installed in a builder stage, runtime is a slim
# non-root image with only the resolved virtualenv and source.

ARG PYTHON_VERSION=3.12-slim-bookworm

# ---------- Builder stage ----------
FROM python:${PYTHON_VERSION} AS builder

# Pull a static uv binary from the official image (no apt install needed).
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

WORKDIR /app

# Copy only files needed to resolve and install dependencies first, so the
# layer caches when source changes but deps don't.
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

# Install only the production dependencies into /app/.venv.
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---------- Production stage ----------
FROM python:${PYTHON_VERSION} AS production

# Non-root user for the runtime process.
RUN groupadd --gid 1000 agentforge \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin agentforge

WORKDIR /app

# Copy resolved virtualenv and source from the builder stage.
COPY --from=builder --chown=agentforge:agentforge /app/.venv /app/.venv
COPY --from=builder --chown=agentforge:agentforge /app/src /app/src

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER agentforge

# Health probe uses the stdlib (no curl install needed).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health',timeout=3).status==200 else 1)" \
    || exit 1

EXPOSE 8000

ENTRYPOINT ["uvicorn", "agentforge.main:app", "--host", "0.0.0.0", "--port", "8000"]
