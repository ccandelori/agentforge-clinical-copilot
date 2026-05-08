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
# Only the cache-verification helper goes into the builder; the rest
# of scripts/ is dev tooling that never needs to ship in any stage.
COPY scripts/verify_model_cache.py ./scripts/verify_model_cache.py
# Sensitivity policy YAML lives at /app/config/sensitivity_policy.yaml
# in production — Settings.sensitivity_policy_path resolves there via
# Path(__file__).resolve().parents[2]. Without this layer the YAML is
# absent at runtime and load_sensitivity_policy fails (mitigated by
# SENSITIVITY_POLICY_REQUIRED=false in dev, but production must run
# with the policy loaded).
COPY config/ ./config/
# Prompt library lives at /app/prompts/ (Task 43 — versioned templates).
# load_prompt() walks up from /app/src/agentforge/prompts.py to find a
# sibling 'prompts' dir with a version.json — without this layer
# orchestrator/__init__.py crashes at module import.
#
# Sourced from a named build context ('prompts') so the Dockerfile can
# pull from outside the sidecar/ build context. Local dev builds use
# `additional_contexts` in docker-compose; the deploy script passes
# --build-context prompts=<repo-root>/prompts to docker build.
COPY --from=prompts . ./prompts/
# Guideline corpus (W2 RAG, Task 9 + MR 7). The retriever's loader
# resolves Settings.guidelines_index_path to /app/data/guidelines/index.json
# at startup; without this layer the file is absent and
# ``_build_evidence_retriever`` short-circuits to None — turns with
# ``evidence_query`` quietly produce empty chunk lists. ~48 KB total
# (BM25 index + per-doc chunk text), so the layer cost is trivial.
COPY data/ ./data/

# Install only the production dependencies into /app/.venv.
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Pre-bake HuggingFace model weights into a staging cache so the
# production image starts without any network model download. The
# sentence-transformers DenseRetriever (rag/dense.py) defaults to
# all-MiniLM-L6-v2 — ~90 MB; without this layer the first request
# blocks on a 90 MB download from huggingface.co.
ENV HF_HOME=/opt/hf-cache \
    TRANSFORMERS_CACHE=/opt/hf-cache/hub \
    SENTENCE_TRANSFORMERS_HOME=/opt/hf-cache/sentence-transformers
RUN /app/.venv/bin/python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"
# CrossEncoderReranker (rag/cross_encoder.py) defaults to
# BAAI/bge-reranker-base — ~280 MB safetensors. Pre-baked here so the
# reranker initializes from disk on first request instead of pulling
# from HF. Cohere reranker stays optional (env-gated) and is
# intentionally NOT baked.
#
# allow_patterns excludes the legacy pytorch_model.bin (1.1 GB) — the
# safetensors weight file plus tokenizer/config is the modern path and
# what transformers prefers when both are present.
RUN /app/.venv/bin/python -c "from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-reranker-base', cache_dir='/opt/hf-cache/sentence-transformers', allow_patterns=['*.json','*.txt','tokenizer*','*.safetensors'])"
# Build-time gate: load both models with HF_HUB_OFFLINE=1 so a missing
# or corrupt cache fails the build instead of the first production
# request. Runs in the builder stage so the script never ships in the
# production image.
RUN /app/.venv/bin/python /app/scripts/verify_model_cache.py

# ---------- Production stage ----------
FROM python:${PYTHON_VERSION} AS production

# Non-root user for the runtime process.
RUN groupadd --gid 1000 agentforge \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin agentforge

WORKDIR /app

# Copy resolved virtualenv and source from the builder stage.
COPY --from=builder --chown=agentforge:agentforge /app/.venv /app/.venv
COPY --from=builder --chown=agentforge:agentforge /app/src /app/src
# Sensitivity policy YAML (Settings.sensitivity_policy_path resolves to
# /app/config/sensitivity_policy.yaml via Path(__file__).parents[2]).
# Without this layer the file is absent at runtime and load_sensitivity_policy
# fails — fine in dev (SENSITIVITY_POLICY_REQUIRED=false) but production
# must run with the policy loaded.
COPY --from=builder --chown=agentforge:agentforge /app/config /app/config
# Prompt library (Task 43). load_prompt() walks up from
# /app/src/agentforge/prompts.py looking for a sibling 'prompts' dir
# with version.json; this layer puts it there. Sourced from the
# 'prompts' named build context (see notes in the builder stage).
COPY --from=builder --chown=agentforge:agentforge /app/prompts /app/prompts
# Guideline corpus (W2 RAG). See the builder-stage COPY above.
COPY --from=builder --chown=agentforge:agentforge /app/data /app/data
# Pre-baked HuggingFace model cache (Task 21). The builder downloads
# all-MiniLM-L6-v2 + bge-reranker-base safetensors into /opt/hf-cache;
# we relocate them into the agentforge user's home so the runtime user
# (uid 1000) can read them and the HF env vars (set below) point here.
# Without this layer the first request blocks on a ~370 MB download
# from huggingface.co.
COPY --from=builder --chown=agentforge:agentforge /opt/hf-cache /home/agentforge/.cache/huggingface

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DASHBOARD_DIST_DIR=/app/dashboard/dist \
    HF_HOME=/home/agentforge/.cache/huggingface \
    TRANSFORMERS_CACHE=/home/agentforge/.cache/huggingface/hub \
    SENTENCE_TRANSFORMERS_HOME=/home/agentforge/.cache/huggingface/sentence-transformers

# Dashboard SPA mount point (T38.14, recon §8 Plan A step 2). The
# Vue dashboard's `npm run build` output is bind-mounted here at
# container start (`docker run -v /opt/agentforge/dashboard/dist:/app/dashboard/dist:ro`).
# Bind-mounting (vs COPY-into-image) keeps the sidecar image stable
# across dashboard-only redeploys — `./scripts/deploy-droplet.sh dashboard`
# rsyncs to the host path and the running container sees the new
# files on the next request without a rebuild. The mkdir below means
# the sidecar boots cleanly even with no mount: agentforge.main logs
# a warning and skips the static mount, leaving the API surface
# (/auth/*, /api/*, /health, /turn) reachable.
RUN mkdir -p /app/dashboard/dist && chown -R agentforge:agentforge /app/dashboard

USER agentforge

# Health probe uses the stdlib (no curl install needed).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health',timeout=3).status==200 else 1)" \
    || exit 1

EXPOSE 8000

ENTRYPOINT ["uvicorn", "agentforge.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
