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

    # Streaming verifier on the user-visible reply (Task 28). Off by
    # default to preserve the legacy MVP behavior; production deployments
    # set VERIFIER_ENABLED=true so every assistant sentence is gated
    # against the per-turn citation cache before the user sees it.
    verifier_enabled: bool = False

    # Streaming /turn (week1-gaps Task #10). When True, the /turn
    # endpoint returns ``fastapi.responses.StreamingResponse`` over
    # SSE instead of the buffered ``TurnResponse``. Off by default
    # until the verify-BEFORE-emit gate ships in #13 — streaming
    # unverified clinical text and "rewriting" it after would be a
    # clinical-safety violation regardless of how fast the rewrite
    # arrives. The wire path lands in #10-#12; the safety gate is
    # what flips this flag in production.
    streaming_enabled: bool = False


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide Settings instance (cached)."""
    return Settings()  # type: ignore[call-arg]  # fields populated from env
