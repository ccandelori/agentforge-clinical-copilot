"""Pydantic Settings for the AgentForge sidecar.

Configuration is loaded from environment variables (and an optional .env
file). Secrets without sensible defaults — JWT signing key and the HMAC
key used for Langfuse pseudonyms — are required at startup so the
application fails fast in misconfigured environments rather than degrading
silently. See ARCHITECTURE.md §2 (auth gateway), §7.2 (HMAC scheme).
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProvider = Literal["claude", "openai", "vllm"]


class Settings(BaseSettings):
    """Sidecar configuration loaded from env vars / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Application
    app_name: str = "AgentForge Clinical Co-Pilot"
    debug: bool = False

    # JWT — shared signing secret with the OpenEMR PHP module that mints tokens
    jwt_secret: str
    jwt_algorithm: str = "HS256"

    # Redis — tool result cache + session memory (PHI store; see §7.1).
    # Required at startup so a missing config fails fast rather than
    # silently bypassing the encounter session store.
    redis_url: str
    session_ttl_seconds: int = 75 * 60  # 75 min encounter window per ARCHITECTURE.md §3
    tool_cache_ttl_seconds: int = 60  # per ARCHITECTURE.md §3

    # LLM providers
    llm_provider: LLMProvider = "claude"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    vllm_base_url: str = ""
    # Optional model override. Empty string falls back to the provider
    # client's bundled default (see ``agentforge.llm.claude.DEFAULT_MODEL``).
    # Set to ``claude-haiku-4-5-20251001`` for ~3× faster turn responses
    # on chart Q&A; the synthesizer is citation-grounded so quality
    # regression is bounded.
    claude_model: str = ""

    # OpenEMR integration boundary (HTTP only — never direct MariaDB; see §1)
    openemr_base_url: str = "http://localhost:80"
    openemr_fhir_endpoint: str = "/apis/fhir/r4"
    openemr_internal_endpoint: str = "/agentforge/internal"

    # Langfuse observability (HMAC-pseudonymous; never PHI; see §7.3).
    # All three must be set for traces to be sent; if any is missing the
    # sidecar wires NullLangfuseClient instead so dev/test runs don't
    # need a Langfuse instance. The secret key is held only in memory
    # and is never logged or echoed in error messages.
    langfuse_host: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None

    # HMAC key for pseudonymizing user/patient IDs and hashing tool
    # args/results in Langfuse traces (§7.2). Required at startup —
    # without it pseudonyms are not reversible across rotations and
    # callers can't compute payload hashes at the boundary.
    hmac_key: str

    # Sensitivity policy (Tasks 9 + 10). Loaded into Redis at startup.
    # Default points at the `sidecar/config/sensitivity_policy.yaml`
    # bundled with the package; deployments can override per environment.
    sensitivity_policy_path: Path = (
        Path(__file__).resolve().parents[2] / "config" / "sensitivity_policy.yaml"
    )
    sensitivity_policy_required: bool = True

    # Guideline corpus for the W2 evidence-retriever LangGraph node
    # (Task 9 / Task 15). Default points at the bundled corpus index
    # produced by ``scripts/chunk_guidelines.py``; deployments with a
    # custom corpus override the path. See ``rag/loader.py``.
    guidelines_index_path: Path = (
        Path(__file__).resolve().parents[2] / "data" / "guidelines" / "index.json"
    )

    # When True, ``create_app`` builds the full RAG pipeline (BM25 +
    # SentenceTransformer dense + RRF + cross-encoder reranker) at
    # startup and passes it to the W2 graph. The dense + cross-encoder
    # models load ~190 MB of weights on construction (3-5 seconds),
    # which is meaningful enough that the default is OFF — production
    # deployments opt in via ``.env`` (``EVIDENCE_RETRIEVER_ENABLED=true``)
    # so unit-test fixtures and dev-time imports don't pay the cost
    # by accident. The graph node degrades to a no-op when the
    # retriever is None: evidence-query turns surface an empty chunk
    # list rather than a hard failure, so the W2 graph still serves
    # intake-extraction turns even on a deployment without a corpus.
    evidence_retriever_enabled: bool = False

    # Streaming verifier on the user-visible reply (Task 28). On by
    # default — the verify-before-emit gate shipped in week1-gaps #13,
    # so every assistant sentence is now gated against the per-turn
    # citation cache before the user sees it. Set VERIFIER_ENABLED=false
    # only to restore legacy pass-through for debugging.
    verifier_enabled: bool = True

    # Streaming /turn (week1-gaps Task #10). When True, the /turn
    # endpoint returns ``fastapi.responses.StreamingResponse`` over
    # SSE instead of the buffered ``TurnResponse``. Enabled once the
    # verify-BEFORE-emit gate shipped in #13 — only verified sentences
    # reach the wire, so streaming is now safe in production.
    streaming_enabled: bool = True

    # ------------------------------------------------------------------
    # Dashboard BFF (W2 Task 38.2 v2). The Vue dashboard at /dashboard
    # is a public SPA that talks to OpenEMR exclusively through the
    # sidecar's /auth/* + /api/fhir/* surface. The sidecar holds the
    # confidential OAuth2 client_secret server-side so it never ships
    # to the browser, and proxies FHIR reads with the user/* scopes
    # OpenEMR rejects for public clients. Defaults are empty so the
    # bulk of unit tests (which don't exercise the dashboard surface)
    # don't need to set these env vars; the auth routes degrade to
    # 503 "BFF not configured" when client_id is unset.
    # ------------------------------------------------------------------
    dashboard_oauth_authority: str = ""  # e.g. https://localhost:9300/oauth2/default
    dashboard_oauth_client_id: str = ""
    dashboard_oauth_client_secret: str = ""
    dashboard_oauth_redirect_uri: str = "http://localhost:5173/auth/callback"
    dashboard_oauth_post_logout_redirect_uri: str = "http://localhost:5173/"
    dashboard_oauth_scope: str = (
        "openid offline_access fhirUser "
        "user/Patient.read user/AllergyIntolerance.read user/Condition.read "
        "user/MedicationRequest.read user/CareTeam.read user/Observation.read "
        "user/Encounter.read user/Practitioner.read user/Organization.read"
    )
    # OpenEMR-specific aud query parameter on /authorize. Required by
    # the authorize endpoint; binds the issued access token to the
    # FHIR resource server. Empty means "don't add aud" — production
    # against dev-easy needs the FHIR base URL.
    dashboard_oauth_audience: str = ""

    # Where the Vue dashboard lives — used as the default landing
    # destination after a successful sign-in when no ``next`` query
    # parameter is provided to /auth/login. Same-origin in production
    # (served from the sidecar host); cross-origin in dev (Vite dev
    # server at :5173 proxies /auth/* + /api/* to the sidecar).
    dashboard_app_url: str = "http://localhost:5173/"

    # FHIR proxy target. Resource server URL the BFF forwards FHIR
    # reads to. Typically ``${OPENEMR_BASE_URL_HTTPS}/apis/<site>/fhir``
    # — kept distinct from ``openemr_base_url`` because the FHIR API
    # lives behind the HTTPS port (:9300 on dev-easy) while the
    # legacy REST integration the W1 fetchers use lives behind :8300.
    dashboard_fhir_base_url: str = ""

    # Session cookie + Redis key configuration.
    dashboard_session_cookie_name: str = "agentforge_session"
    dashboard_session_ttl_seconds: int = 8 * 3600  # 8h working day
    dashboard_pending_auth_ttl_seconds: int = 10 * 60  # 10 min to finish OAuth dance
    # Secure cookie flag — must be False in dev (browser at http://) and
    # True in production (https-only). Defaulting False so dev works
    # out-of-the-box; production deploys override via .env.
    dashboard_session_cookie_secure: bool = False


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide Settings instance (cached)."""
    return Settings()  # type: ignore[call-arg]  # fields populated from env
