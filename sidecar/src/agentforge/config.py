"""Pydantic Settings for the AgentForge sidecar.

Configuration is loaded from environment variables (and an optional .env
file). Secrets without sensible defaults — JWT signing key and the HMAC
key used for Langfuse pseudonyms — are required at startup so the
application fails fast in misconfigured environments rather than degrading
silently. See ARCHITECTURE.md §2 (auth gateway), §7.2 (HMAC scheme).
"""

from functools import lru_cache
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

    # Langfuse observability (HMAC-pseudonymous; never PHI; see §7.3)
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3000"

    # HMAC key for pseudonymizing user/patient IDs in Langfuse (§7.2)
    hmac_key: str


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide Settings instance (cached)."""
    return Settings()  # type: ignore[call-arg]  # fields populated from env
